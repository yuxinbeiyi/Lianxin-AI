"""Manually confirmed test comments with a durable, fail-closed send journal."""

import hashlib
import json
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode


_CONTENT_DEDUPLICATION_ENABLED = False


class CommentJournal:
    def __init__(self, path=None):
        if path is None:
            from utils.paths import get_user_data_dir
            path = get_user_data_dir() / "bilibili_interactions.sqlite3"
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE IF NOT EXISTS comments (fingerprint TEXT PRIMARY KEY, "
            "account_uid TEXT, bvid TEXT, mention_uid TEXT, text TEXT, "
            "created_at INTEGER, status TEXT, rpid TEXT, mention_verified INTEGER DEFAULT 0)"
        )
        columns = {column[1] for column in connection.execute("PRAGMA table_info(comments)")}
        if "diagnostic" not in columns:
            connection.execute("ALTER TABLE comments ADD COLUMN diagnostic TEXT DEFAULT ''")
            connection.commit()
        if "verification" not in columns:
            connection.execute("ALTER TABLE comments ADD COLUMN verification TEXT DEFAULT '{}'")
            connection.commit()
        connection.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, created_at INTEGER, result TEXT)")
        return connection

    def save_attempt(self, attempt_id, result):
        safe = {key: result[key] for key in (
            "ok", "sent", "status", "stage", "diagnostic", "rpid", "mentionVerified", "verification"
        ) if key in result}
        connection = self.connect()
        try:
            with connection:
                connection.execute("INSERT OR REPLACE INTO attempts VALUES (?,?,?)",
                    (attempt_id, int(time.time()), json.dumps(safe, ensure_ascii=False)))
        finally:
            connection.close()

    def attempts(self):
        connection = self.connect()
        try:
            return [{"id": row["id"], "createdAt": row["created_at"], **json.loads(row["result"])}
                    for row in connection.execute("SELECT * FROM attempts ORDER BY created_at DESC, rowid DESC LIMIT 20")]
        finally:
            connection.close()

    def records(self):
        connection = self.connect()
        try:
            records = [dict(record) for record in connection.execute(
                "SELECT * FROM comments ORDER BY created_at DESC, rowid DESC LIMIT 20")]
            for record in records:
                record["verification"] = json.loads(record["verification"] or "{}")
            return records
        finally:
            connection.close()

    def reserve(self, fingerprint, account_uid, bvid, mention_uid, text):
        connection = self.connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT 1 FROM comments WHERE fingerprint=?", (fingerprint,)).fetchone():
                    return "此内容已有发送记录，已阻止重复发送。请先在 B 站核对。"
                connection.execute("INSERT INTO comments (fingerprint, account_uid, bvid, mention_uid, text, created_at, status, rpid, mention_verified) VALUES (?,?,?,?,?,?,?,?,?)", (
                    fingerprint, account_uid, bvid, mention_uid, text,
                    int(time.time()), "unknown", "", 0))
            return ""
        finally:
            connection.close()

    def finish(self, fingerprint, status, rpid, verified, diagnostic="", verification=None):
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    "UPDATE comments SET status=?, rpid=?, mention_verified=?, diagnostic=?, verification=? WHERE fingerprint=?",
                    (status, rpid, int(verified), diagnostic, json.dumps(verification or {}, ensure_ascii=False), fingerprint))
        finally:
            connection.close()


def safe_api_message(message, credential):
    safe = str(message)
    for value in credential.values():
        if isinstance(value, str) and value:
            safe = safe.replace(value, "[redacted]")
    safe = re.sub(r"(?i)(SESSDATA|bili_jct|buvid3|cookie|csrf)\s*[:=]\s*[^\s;]+", r"\1=[redacted]", safe)
    return " ".join(safe.split())[:180]


def error_diagnostic(error, credential):
    diagnostic = f"exception={type(error).__name__}"
    if hasattr(error, "api_code"):
        diagnostic += f"; endpoint={error.endpoint}; api_code={error.api_code}; api_message={safe_api_message(error.api_message, credential)}"
    elif getattr(error, "code", None) is not None:
        diagnostic += f"; http_status={error.code}"
    return diagnostic


def submit_comment(service, credential, aid, text):
    """Prefer the DSH transport and retain HTTP only for test/minimal installs."""
    if getattr(service, "_use_bilibili_api", True):
        try:
            from utils.bilibili_api_transport import send_video_comment
            response = send_video_comment(credential, aid, text)
            if "code" not in response:
                response = {"code": 0, "data": response}
            return response, "bilibili_api_python"
        except ImportError:
            raise
        except Exception as error:
            from utils.bilibili_api_transport import BilibiliApiUnavailable
            if not isinstance(error, BilibiliApiUnavailable):
                raise

    payload = {"oid": aid, "type": 1, "message": text, "plat": 1,
               "statistics": json.dumps({"appId": 100, "platform": 5}, separators=(",", ":")),
               "gaia_source": "main_web", "csrf": credential["bili_jct"],
               "csrf_token": credential["bili_jct"]}
    response = service._request_json(
        "https://api.bilibili.com/x/v2/reply/add",
        headers=service._cookie_headers(credential), data=urlencode(payload).encode("utf-8"))
    return response, "web_sdk_compatible_v2; implementation=legacy_http_fallback"


def verify_comment(service, aid, record, credential):
    url = f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=2&next=0&ps=20"
    verification = {"checkedAt": int(time.time()), "selfReadback": "unknown",
                    "anonymousReadback": "unknown", "mentionVerified": False,
                    "recipientVisibility": "unknown", "notificationDelivery": "unknown"}
    diagnostics = ["stage=readback; endpoint=/x/v2/reply/main"]
    for perspective, headers in (("selfReadback", service._cookie_headers(credential)),
                                 ("anonymousReadback", {})):
        try:
            response = service._fetch_json(url, headers=headers)
            replies = (response.get("data") or {}).get("replies") or []
            root = next((reply for reply in replies if str(reply.get("rpid")) == record["rpid"]), None)
            verification[perspective] = "not_found"
            if root:
                verification[perspective] = "hidden" if root.get("invisible") else "found"
                content = root.get("content") or {}
                mapping = content.get("at_name_to_mid") or {}
                matches = (str((root.get("member") or {}).get("mid")) == record["account_uid"]
                           and content.get("message") == record["text"]
                           and any(str(uid) == record["mention_uid"] for uid in mapping.values()))
                if perspective == "selfReadback":
                    verification["mentionVerified"] = matches
                if not matches:
                    verification[perspective] = "content_mismatch"
            diagnostics.append(f"{perspective}={verification[perspective]}; api_code=0")
        except Exception as error:
            diagnostics.append(f"{perspective}: {error_diagnostic(error, credential)}")
    diagnostics.append(f"uid_mention_verified={verification['mentionVerified']}; recipient_visibility=unknown; notification_delivery=unknown")
    return verification, "; ".join(diagnostics)


def verification_message(verification):
    own = "小号回读已确认正文和 UID 提及" if verification.get("mentionVerified") else "尚未核实小号端的正文和 UID 提及"
    anonymous = {"found": "未登录查询也查到了该评论", "hidden": "未登录查询返回了隐藏标记",
                 "content_mismatch": "未登录查询内容与记录不一致", "not_found": "未登录最新一页未查到（不代表未发送）"}.get(
                     verification.get("anonymousReadback"), "未登录查询未完成")
    return f"B站已受理；{own}；{anonymous}。大号可见性及 @ 通知送达仍需用大号确认，勿重复发送。"


def recheck_comment(service, rpid):
    try:
        credential = service._read_credential()
    except Exception as error:
        return {"ok": False, "error": "无法读取本机互动账号凭据；没有发送或重发评论。",
                "diagnostic": f"exception={type(error).__name__}"}
    account_uid = str(credential.get("dedeuserid", ""))
    records = service._journal.records()
    record = next((item for item in records if item["account_uid"] == account_uid
                   and item["rpid"] and (not rpid or item["rpid"] == rpid)), None)
    if not record or not account_uid:
        return {"ok": False, "error": "当前互动账号没有可复核的近期评论，请先检查授权及发送记录。"}
    try:
        nav = service._fetch_json("https://api.bilibili.com/x/web-interface/nav",
                                  headers=service._cookie_headers(credential)).get("data") or {}
        if not nav.get("isLogin") or str(nav.get("mid")) != account_uid:
            return {"ok": False, "error": "互动账号登录失效或账号不一致，请先检查授权有效性。"}
        video = service._video_lookup(record["bvid"])
        aid = int(video["aid"])
        if aid <= 0:
            raise ValueError("invalid aid")
        verification, diagnostic = verify_comment(service, aid, record, credential)
        status = "sent_verified" if verification["mentionVerified"] else "sent_unverified"
        service._journal.finish(record["fingerprint"], status, record["rpid"], verification["mentionVerified"],
                                diagnostic, verification)
        return {"ok": True, "rpid": record["rpid"], "verification": verification,
                "message": verification_message(verification), "diagnostic": diagnostic, "state": service.get_state()}
    except Exception as error:
        return {"ok": False, "error": "只读复核未完成，没有发送或重发评论。",
                "diagnostic": error_diagnostic(error, credential)}


def send_test_comment(service, target, draft_id, confirmed):
    from utils.bilibili_interaction import parse_bilibili_video_id
    settings = service._settings()
    bvid = parse_bilibili_video_id(target) if target else settings["test_bvid"]
    blockers = service._write_blockers(settings, bvid)
    with service._lock:
        draft = dict(service._draft) if service._draft else {}
    if confirmed is not True or not draft_id or draft.get("id") != draft_id:
        blockers.append("请先保存草稿，并明确确认发送当前草稿。")
    if (not bvid or draft.get("bvid") != bvid
            or draft.get("mentionUid") != settings["user_uid"]
            or draft.get("mentionName") != settings["user_name"]):
        blockers.append("草稿与当前设置不一致，请重新生成。")
    if draft and draft.get("status") != "pending_confirmation":
        blockers.append("此草稿已尝试发送，请先核对发送记录。")
    if blockers:
        return {"ok": False, "sent": False, "status": "blocked", "blockers": blockers,
                "stage": "guards", "diagnostic": "草稿、设置或人工确认校验未通过；未提交 B 站。",
                "error": " ".join(blockers), "state": service.get_state()}
    inspected = service.inspect_test_video(bvid)
    if not inspected.get("ok") or inspected["video"].get("commentOpen") is not True:
        return {"ok": False, "sent": False, "stage": "video", "diagnostic": "视频评论状态校验失败；未提交。", "error": "无法确认测试视频评论开放，请重新查询。"}
    credential = service._read_credential()
    credential = service._ensure_buvid_cookies(credential)
    account_uid = str(credential.get("dedeuserid", ""))
    try:
        nav = service._fetch_json("https://api.bilibili.com/x/web-interface/nav",
            headers=service._cookie_headers(credential)).get("data") or {}
        if not nav.get("isLogin") or str(nav.get("mid")) != account_uid:
            raise ValueError("登录失效")
        aid = int(inspected["video"]["aid"])
        if aid <= 0:
            raise ValueError("视频标识无效")
    except Exception as error:
        return {"ok": False, "sent": False, "stage": "account", "diagnostic": f"账号/视频标识校验失败；exception={type(error).__name__}；未提交。", "error": "账号或视频校验失败，请先点击检查授权有效性。"}
    fingerprint = hashlib.sha256(json.dumps(
        [account_uid, bvid, settings["user_uid"], draft["text"]],
        ensure_ascii=False).encode("utf-8")).hexdigest() if _CONTENT_DEDUPLICATION_ENABLED else secrets.token_hex(32)
    local_checks = f"local_deduplication={'enabled' if _CONTENT_DEDUPLICATION_ENABLED else 'disabled'}; local_cooldown=disabled"
    error = service._journal.reserve(fingerprint, account_uid, bvid, settings["user_uid"], draft["text"])
    if error:
        return {"ok": False, "sent": False, "stage": "reserve",
                "diagnostic": f"reserve_reason={error}; {local_checks}; 未提交 B 站；transport=web_sdk_compatible_v2",
                "error": error}
    payload = {"oid": aid, "type": 1, "message": draft["text"], "plat": 1,
        "statistics": json.dumps({"appId": 100, "platform": 5}, separators=(",", ":")),
        "gaia_source": "main_web",
        "csrf": credential["bili_jct"], "csrf_token": credential["bili_jct"]}
    rpid = ""
    verified = False
    status = "unknown"
    diagnostic = f"stage=submit; {local_checks}; transport=web_sdk_compatible_v2"
    verification = {}
    try:
        response, transport = submit_comment(service, credential, aid, draft["text"])
        diagnostic = diagnostic.replace("transport=web_sdk_compatible_v2", f"transport={transport}")
        diagnostic += f"; endpoint=/x/v2/reply/add; api_code={response.get('code')}; api_message={safe_api_message(response.get('message') or response.get('msg') or '', credential)}"
        if response.get("code") != 0:
            status = "rejected"
        else:
            data = response.get("data") or {}
            returned_id = str(data.get("rpid") or (data.get("reply") or {}).get("rpid") or "")
            if returned_id.isdigit() and int(returned_id) > 0:
                rpid = returned_id
                status = "sent_unverified"
                diagnostic += "; stage=readback"
                service._journal.finish(fingerprint, status, rpid, False, diagnostic)
                verification, readback_diagnostic = verify_comment(service, aid, {
                    "rpid": rpid, "account_uid": account_uid, "text": draft["text"],
                    "mention_uid": settings["user_uid"]}, credential)
                verified = verification["mentionVerified"]
                if verified:
                    status = "sent_verified"
                diagnostic += "; " + readback_diagnostic
    except Exception as error:
        diagnostic += "; " + error_diagnostic(error, credential)
    service._journal.finish(fingerprint, status, rpid, verified, diagnostic, verification)
    with service._lock:
        if service._draft and service._draft["id"] == draft_id:
            service._draft["status"] = status
    messages = {
        "sent_verified": verification_message(verification),
        "sent_unverified": verification_message(verification),
        "rejected": "B 站拒绝发送，请检查账号状态；本次不会自动重试。",
        "unknown": "发送结果不确定，请先在 B 站检查，系统不会自动重试。",
    }
    return {"ok": bool(rpid), "sent": bool(rpid), "status": status, "rpid": rpid,
        "mentionVerified": verified, "message": messages[status], "blockers": [],
        "stage": "readback" if rpid else "submit", "verification": verification,
        "error": (messages[status] + " " + diagnostic) if not rpid else "",
        "diagnostic": diagnostic, "state": service.get_state()}


def send_auto_comment(service, target, text):
    """Send one explicitly enabled proactive comment through the normal journal."""
    from utils.bilibili_interaction import parse_bilibili_video_id
    settings = service._settings()
    bvid = parse_bilibili_video_id(target)
    blockers = []
    if not settings["auto_comment_enabled"]:
        blockers.append("automatic_comment_disabled")
    if settings["paused"]:
        blockers.append("interactions_paused")
    if not settings["user_uid"] or not settings["user_uid_confirmed"]:
        blockers.append("main_uid_unconfirmed")
    if not bvid:
        blockers.append("invalid_video")
    account = service._account_snapshot()
    if not account["connected"]:
        blockers.append("interactive_account_missing")
    elif account["uid"] == settings["user_uid"]:
        blockers.append("interactive_account_is_main")
    elif any(
        row.get("bvid") == bvid and row.get("account_uid") == account["uid"]
        and row.get("status") in {"unknown", "sent_unverified", "sent_verified"}
        for row in service._journal.records()
    ):
        blockers.append("video_comment_already_recorded")
    if blockers:
        return {"ok": False, "sent": False, "status": "blocked", "blockers": blockers}

    candidate = re.sub(r"\s+", " ", str(text or "").strip())
    mention = f"@{settings['user_name']} "
    if not candidate or not candidate.startswith(mention):
        return {"ok": False, "sent": False, "status": "invalid_text"}
    video = service._video_lookup(bvid)
    if video.get("bvid") != bvid or video.get("commentOpen") is not True:
        return {"ok": False, "sent": False, "status": "video_not_commentable"}

    # Reuse draft validation, journal, transport, and readback without changing saved settings.
    auto_settings = dict(service._load_config())
    auto_settings["mode"] = "test"
    auto_settings["test_video"] = f"https://www.bilibili.com/video/{bvid}"
    auto_settings["auto_comment_enabled"] = True
    from utils.bilibili_interaction import BilibiliInteractionService
    auto_service = BilibiliInteractionService(
        config_loader=lambda: auto_settings,
        credential_store=service._credential_store,
        journal_path=service._journal.path,
        use_bilibili_api=True,
    )
    draft_result = auto_service.create_comment_draft(bvid, candidate)
    if not draft_result.get("ok"):
        return {"ok": False, "sent": False, "status": "draft_rejected",
                "error": str(draft_result.get("error") or draft_result.get("blockers") or "draft rejected")}
    draft = draft_result["draft"]
    return auto_service.request_test_comment(bvid, draft["id"], True)
