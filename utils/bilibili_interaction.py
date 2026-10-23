"""Shared settings and fail-closed guards for Bilibili interactions."""

import base64
from http.cookies import SimpleCookie
from io import BytesIO
import json
import re
import secrets
import sys
import time
from threading import Lock
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from config import get_bilibili_interaction_config, save_bilibili_interaction_config


_BVID_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
_UID_PATTERN = re.compile(r"[0-9]{1,20}")
_MODES = {"read_only", "test"}
_MODE_LABELS = {"read_only": "只读冲浪", "test": "指定测试区（手动一次）"}
_WRITE_BLOCKER = "评论接口尚未验证按 UID 创建真实提及；公开写入保持关闭。"
_CREDENTIAL_TARGET = "Lianxin/BilibiliInteraction"
_LOGIN_GENERATE_URL = (
    "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    "?source=main-fe-header"
)
_LOGIN_POLL_URL = (
    "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    "?qrcode_key={key}&source=main-fe-header"
)
_BILIBILI_SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
_BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


class _WindowsCredentialStore:
    """Keep Bilibili credentials in Windows Credential Manager, never config.json."""

    def __init__(self, module):
        self._module = module

    def read(self) -> dict:
        try:
            entry = self._module.CredRead(
                _CREDENTIAL_TARGET, self._module.CRED_TYPE_GENERIC, 0
            )
        except Exception:
            return {}
        raw = entry.get("CredentialBlob", b"") if isinstance(entry, dict) else b""
        if isinstance(raw, bytes):
            for encoding in ("utf-8", "utf-16-le"):
                try:
                    value = json.loads(raw.decode(encoding))
                    if isinstance(value, dict):
                        return value
                except (UnicodeError, ValueError):
                    continue
            return {}
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def write(self, value: dict) -> None:
        raw = json.dumps(value, ensure_ascii=False)
        self._module.CredWrite(
            {
                "Type": self._module.CRED_TYPE_GENERIC,
                "TargetName": _CREDENTIAL_TARGET,
                "UserName": "lianxin",
                "CredentialBlob": raw,
                "Persist": self._module.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )

    def delete(self) -> None:
        try:
            self._module.CredDelete(_CREDENTIAL_TARGET, self._module.CRED_TYPE_GENERIC, 0)
        except Exception:
            pass


def _build_credential_store():
    if sys.platform != "win32":
        return None
    try:
        import win32cred
    except (ImportError, OSError):
        return None
    return _WindowsCredentialStore(win32cred)


def parse_bilibili_video_id(value: str) -> str:
    """Return a BV id only from a BV id or a canonical Bilibili video URL."""
    candidate = str(value or "").strip()
    match = _BVID_PATTERN.fullmatch(candidate)
    if match:
        return match.group(0)

    try:
        parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"bilibili.com", "www.bilibili.com"}:
        return ""
    match = re.search(r"/video/(BV[0-9A-Za-z]{10})(?:/|$)", parsed.path)
    return match.group(1) if match else ""


def normalize_bilibili_uid(value: str) -> str:
    uid = str(value or "").strip()
    if not uid:
        return ""
    if not _UID_PATTERN.fullmatch(uid) or int(uid) <= 0:
        raise ValueError("用户 UID 必须是正整数。")
    return str(int(uid))


class BilibiliAPIError(ValueError):
    def __init__(self, url, result):
        super().__init__("B 站接口返回错误。")
        self.endpoint = urlparse(url).path
        self.api_code = result.get("code")
        self.api_message = str(result.get("message") or result.get("msg") or "")


class BilibiliInteractionService:
    def __init__(
        self,
        config_loader=None,
        config_saver=None,
        profile_lookup=None,
        video_lookup=None,
        credential_store=None,
        journal_path=None,
        use_bilibili_api=None,
    ):
        self._load_config = config_loader or get_bilibili_interaction_config
        self._save_config = config_saver or save_bilibili_interaction_config
        self._profile_lookup = profile_lookup or self._fetch_public_user_profile
        self._video_lookup = video_lookup or self._fetch_public_video_info
        self._lock = Lock()
        self._login_lock = Lock()
        self._credential_store = (
            credential_store if credential_store is not None else _build_credential_store()
        )
        self._use_bilibili_api = (
            credential_store is None if use_bilibili_api is None else bool(use_bilibili_api)
        )
        self._login_session = None
        self._draft = None
        self._send_lock = Lock()
        from utils.bilibili_comment_sender import CommentJournal
        self._journal = CommentJournal(journal_path)

    @staticmethod
    def _request_json(url: str, headers=None, data=None, include_headers=False):
        request_headers = dict(_BILIBILI_HEADERS)
        if isinstance(headers, dict):
            request_headers.update(headers)
        if data is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, headers=request_headers, data=data)
        with urlopen(request, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
            response_headers = response.headers
        if not isinstance(result, dict):
            raise ValueError("B 站接口没有返回有效 JSON 数据。")
        if include_headers:
            return result, response_headers
        return result

    @classmethod
    def _fetch_json(cls, url: str, headers=None) -> dict:
        result = cls._request_json(url, headers=headers)
        if result.get("code") != 0:
            raise BilibiliAPIError(url, result)
        return result

    @staticmethod
    def _cookie_headers(credential: dict) -> dict:
        parts = []
        for key in ("SESSDATA", "bili_jct", "buvid3", "buvid4", "DedeUserID"):
            value = str(credential.get(key.lower(), credential.get(key, "")) or "").strip()
            if value:
                parts.append(f"{key}={value}")
        headers = dict(_BILIBILI_HEADERS)
        parts.append("opus-goback=1")
        if parts:
            headers["Cookie"] = "; ".join(parts)
        return headers

    def _ensure_buvid_cookies(self, credential: dict) -> dict:
        """Match the reference client's automatic buvid hydration for old logins."""
        if credential.get("buvid3") and credential.get("buvid4"):
            return credential
        try:
            response = self._request_json(_BILIBILI_SPI_URL, headers=_BILIBILI_HEADERS)
            data = response.get("data") or {}
            buvid3 = str(data.get("b_3") or "").strip()
            buvid4 = str(data.get("b_4") or "").strip()
            if buvid3:
                credential["buvid3"] = buvid3
            if buvid4:
                credential["buvid4"] = buvid4
        except (ValueError, URLError, TimeoutError, OSError):
            return credential
        if credential.get("buvid3") or credential.get("buvid4"):
            try:
                self._credential_store.write(credential)
            except Exception:
                pass
        return credential

    @staticmethod
    def _parse_set_cookie(headers) -> dict:
        cookies = {}
        values = []
        if headers is not None:
            try:
                values = headers.get_all("Set-Cookie") or []
            except (AttributeError, TypeError):
                values = []
            if not values:
                try:
                    values = [value for key, value in headers.items() if key.lower() == "set-cookie"]
                except (AttributeError, TypeError):
                    values = []
        for raw in values:
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except (TypeError, ValueError):
                continue
            for key, morsel in jar.items():
                cookies[key] = morsel.value
        return cookies

    @staticmethod
    def _render_qr_data_url(url: str) -> str:
        try:
            import qrcode

            image = qrcode.make(url)
            stream = BytesIO()
            image.save(stream, format="PNG")
            encoded = base64.b64encode(stream.getvalue()).decode("ascii")
            return f"data:image/png;base64,{encoded}"
        except (ImportError, OSError, ValueError):
            return ""

    @classmethod
    def _fetch_public_user_profile(cls, uid: str) -> dict:
        response = cls._fetch_json(f"https://api.bilibili.com/x/web-interface/card?mid={uid}")
        data = response.get("data") or {}
        card = data.get("card") or {}
        resolved_uid = normalize_bilibili_uid(card.get("mid", ""))
        name = str(card.get("name", "")).strip()
        if resolved_uid != uid or not name:
            raise ValueError("公开资料中的 UID 或昵称无效。")
        return {"uid": resolved_uid, "name": name}

    @classmethod
    def _fetch_public_video_info(cls, bvid: str) -> dict:
        from urllib.parse import quote
        response = cls._fetch_json(
            f"https://api.bilibili.com/x/web-interface/view?bvid={quote(bvid, safe='')}"
        )
        data = response.get("data") or {}
        if data.get("bvid") != bvid or not data.get("title"):
            raise ValueError("B 站没有返回匹配的公开视频信息。")
        owner = data.get("owner") or {}
        aid = int(data.get("aid") or 0)
        if not aid:
            raise ValueError("B 站公开视频缺少 aid。")
        return {
            "bvid": bvid,
            "title": str(data["title"]).strip(),
            "author": str(owner.get("name", "")).strip(),
            "commentOpen": cls._fetch_public_comment_status(aid),
            "aid": aid,
        }

    @classmethod
    def _fetch_public_comment_status(cls, aid: int) -> bool:
        """Use the reply endpoint; video details do not expose rights.comment reliably."""
        response = cls._fetch_json(
            "https://api.bilibili.com/x/v2/reply"
            f"?type=1&oid={quote(str(aid), safe='')}&pn=1&ps=1&sort=2"
        )
        data = response.get("data") or {}
        config = data.get("config") or {}
        control = data.get("control") or {}
        if control.get("input_disable") in (True, 1):
            return False
        if config.get("read_only") in (True, 1):
            return False
        if "input_disable" not in control and "read_only" not in config:
            raise ValueError("B 站评论接口未返回评论开放状态。")
        return True

    @staticmethod
    def _credential_store_available() -> bool:
        return _build_credential_store() is not None

    @staticmethod
    def _valid_credential(credential) -> bool:
        return isinstance(credential, dict) and bool(
            str(credential.get("sessdata", "")).strip()
            and str(credential.get("bili_jct", "")).strip()
            and str(credential.get("dedeuserid", credential.get("DedeUserID", ""))).strip()
        )

    def _read_credential(self) -> dict:
        if self._credential_store is None:
            return {}
        try:
            credential = self._credential_store.read()
        except Exception:
            return {}
        return credential if self._valid_credential(credential) else {}

    def _account_snapshot(self) -> dict:
        credential = self._read_credential()
        if not credential:
            return {"connected": False, "uid": "", "name": ""}
        try:
            stored_uid = normalize_bilibili_uid(credential.get("account_uid", ""))
            stored_name = str(credential.get("account_name", "")).strip()
            if stored_uid and stored_name:
                return {"connected": True, "uid": stored_uid, "name": stored_name}
            profile = self._fetch_account_profile(credential)
        except (ValueError, URLError, TimeoutError, OSError):
            return {"connected": False, "uid": "", "name": ""}
        return {"connected": True, **profile}

    @classmethod
    def _fetch_account_profile(cls, credential: dict) -> dict:
        uid = normalize_bilibili_uid(
            credential.get("dedeuserid", credential.get("DedeUserID", ""))
        )
        if not uid:
            raise ValueError("登录凭证没有 UID。")
        response = cls._fetch_json(
            f"https://api.bilibili.com/x/web-interface/card?mid={uid}",
            headers=cls._cookie_headers(credential),
        )
        card = ((response.get("data") or {}).get("card") or {})
        resolved_uid = normalize_bilibili_uid(card.get("mid", ""))
        name = str(card.get("name", "")).strip()
        if resolved_uid != uid or not name:
            raise ValueError("登录账号资料与凭证 UID 不匹配。")
        return {"uid": uid, "name": name}

    def start_login(self) -> dict:
        if self._credential_store is None:
            return {"ok": False, "error": "当前系统没有可用的本机安全凭据存储，无法开始扫码授权。"}
        try:
            response = self._fetch_json(_LOGIN_GENERATE_URL)
            data = response.get("data") or {}
            key = str(data.get("qrcode_key", "")).strip()
            url = str(data.get("url", "")).strip()
            if response.get("code") != 0 or not key or not url:
                raise ValueError("B 站没有返回有效的扫码登录二维码。")
            with self._login_lock:
                self._login_session = {"key": key, "started_at": time.monotonic()}
            return {
                "ok": True,
                "key": key,
                "url": url,
                "qrDataUrl": self._render_qr_data_url(url),
                "expiresIn": 180,
            }
        except (ValueError, URLError, TimeoutError, OSError):
            return {"ok": False, "error": "无法生成 B 站登录二维码，请检查网络后重试。"}

    def poll_login(self, key: str) -> dict:
        if self._credential_store is None:
            return {"ok": False, "state": "unavailable", "error": "本机安全凭据存储不可用。"}
        with self._login_lock:
            session = self._login_session
            if not session or not secrets.compare_digest(str(session["key"]), str(key or "")):
                return {"ok": False, "state": "no_session", "error": "登录会话不存在或已失效，请重新生成二维码。"}
            if time.monotonic() - session["started_at"] > 180:
                self._login_session = None
                return {"ok": False, "state": "expired", "error": "二维码已过期，请重新生成。"}
            login_key = session["key"]

        try:
            response, response_headers = self._request_json(
                _LOGIN_POLL_URL.format(key=quote(login_key, safe="")),
                headers={"User-Agent": _BILIBILI_HEADERS["User-Agent"]},
                include_headers=True,
            )
        except (ValueError, URLError, TimeoutError, OSError):
            return {"ok": False, "state": "unknown", "error": "查询扫码状态失败，请稍后重试。"}

        data = response.get("data") or {}
        code = data.get("code")
        if code == 86101:
            return {"ok": True, "state": "waiting"}
        if code == 86090:
            return {"ok": True, "state": "scanned"}
        if code == 86038:
            with self._login_lock:
                self._login_session = None
            return {"ok": False, "state": "expired", "error": "二维码已过期，请重新生成。"}
        if code != 0:
            return {"ok": False, "state": "unknown", "error": "B 站返回未知扫码状态，请重新尝试。"}

        credential = self._parse_set_cookie(response_headers)
        credential = {
            "sessdata": credential.get("SESSDATA", ""),
            "bili_jct": credential.get("bili_jct", ""),
            "buvid3": credential.get("buvid3", ""),
            "buvid4": credential.get("buvid4", ""),
            "dedeuserid": credential.get("DedeUserID", ""),
        }
        if not self._valid_credential(credential):
            return {"ok": False, "state": "unknown", "error": "登录响应缺少必要凭证，未保存任何信息。"}
        try:
            profile = self._fetch_account_profile(credential)
        except Exception as exc:
            return {
                "ok": False,
                "state": "profile_error",
                "error": f"B站已完成扫码，但登录账号资料核验失败（{type(exc).__name__}）；请重新扫码。",
            }
        credential.update({"account_uid": profile["uid"], "account_name": profile["name"]})
        try:
            self._credential_store.write(credential)
        except Exception as exc:
            return {
                "ok": False,
                "state": "credential_error",
                "error": f"账号资料核验通过，但写入 Windows 凭据管理器失败（{type(exc).__name__}）；未完成授权。",
            }
        with self._login_lock:
            self._login_session = None
        return {"ok": True, "state": "connected", "account": profile}

    def logout(self) -> dict:
        if self._credential_store is None:
            return {"ok": False, "error": "本机安全凭据存储不可用。"}
        try:
            self._credential_store.delete()
        except Exception:
            return {"ok": False, "error": "删除本机授权凭据失败。"}
        with self._login_lock:
            self._login_session = None
        return {"ok": True, "state": self.get_state()}

    def check_authorization(self) -> dict:
        credential = self._read_credential()
        if not credential:
            return {"ok": False, "authorization": "missing", "error": "没有可读取的本机凭据，请扫码授权。"}
        try:
            response = self._request_json("https://api.bilibili.com/x/web-interface/nav",
                headers=self._cookie_headers(credential))
            code = response.get("code")
            data = response.get("data") or {}
            if code == -101 or (code == 0 and data.get("isLogin") is False):
                return {"ok": False, "authorization": "expired", "error": "凭据已保存，但登录已失效，请重新扫码。"}
            if code != 0 or not data.get("isLogin"):
                return {"ok": False, "authorization": "unknown", "error": "平台未能确认登录状态，请稍后检查。", "diagnostic": f"api_code={code}" if isinstance(code, int) else "响应格式异常"}
            if str(data.get("mid")) != str(credential.get("dedeuserid", "")):
                return {"ok": False, "authorization": "mismatch", "error": "联网账号 UID 与本机凭据不一致，请重新授权。"}
            return {"ok": True, "authorization": "valid", "message": "实时登录校验通过；凭据跨重启保存，无需重复扫码。", "checkedAt": int(time.time())}
        except Exception as error:
            return {"ok": False, "authorization": "unknown", "error": "联网校验失败，不代表授权失效，请稍后再检查。", "diagnostic": f"exception={type(error).__name__}"}

    def _settings(self) -> dict:
        settings = self._load_config()
        if not isinstance(settings, dict):
            settings = {}
        mode = settings.get("mode")
        if not isinstance(mode, str) or mode not in _MODES:
            mode = "read_only"
        raw_video = str(settings.get("test_video", "") or "").strip()[:500]
        try:
            user_uid = normalize_bilibili_uid(settings.get("user_uid", ""))
        except ValueError:
            user_uid = ""
        return {
            "mode": mode,
            "user_uid": user_uid,
            "user_name": str(settings.get("user_name", "") or ""),
            "user_uid_confirmed": settings.get("user_uid_confirmed") is True,
            "test_video": raw_video,
            "test_bvid": parse_bilibili_video_id(raw_video),
            "allow_other_replies": settings.get("allow_other_replies") is True,
            "auto_comment_enabled": settings.get("auto_comment_enabled") is True,
            "auto_reply_enabled": settings.get("auto_reply_enabled") is True,
            "reply_enabled_at": settings.get("reply_enabled_at", 0),
            "mention_reply_enabled": settings.get("mention_reply_enabled") is True,
            "mention_enabled_at": settings.get("mention_enabled_at", 0),
            "paused": settings.get("paused") is True,
        }

    def _write_blockers(self, settings: dict, target_bvid: str = "") -> list[str]:
        blockers = []
        if settings["mode"] != "test":
            blockers.append("请先切换到指定测试区模式。")
        if settings["paused"]:
            blockers.append("B 站互动当前已暂停。")
        if not settings["test_bvid"]:
            blockers.append("请填写有效的 B 站 BV 号或视频链接。")
        elif target_bvid and target_bvid != settings["test_bvid"]:
            blockers.append("目标视频与设置的测试视频不一致，已拒绝请求。")
        if not settings["user_uid"]:
            blockers.append("尚未填写有效的用户 UID。")
        elif not settings["user_uid_confirmed"]:
            blockers.append("用户 UID 尚未通过公开资料核验。")
        account = self._account_snapshot()
        if not account["connected"]:
            blockers.append("互动账号尚未完成本机授权和 UID 核验。")
        elif account["uid"] == settings["user_uid"]:
            blockers.append("请使用区别于主号的互动小号。")
        return blockers

    def get_state(self) -> dict:
        settings = self._settings()
        account = self._account_snapshot()
        reply_blockers = []
        if not settings["auto_reply_enabled"]:
            reply_blockers.append("自动回复我的B站留言尚未开启。")
        if settings["paused"]:
            reply_blockers.append("B站互动当前已暂停。")
        if not settings["user_uid"] or not settings["user_uid_confirmed"]:
            reply_blockers.append("请先填写并核验主号UID。")
        if not account["connected"] or account["uid"] == settings["user_uid"]:
            reply_blockers.append("请授权区别于主号的互动小号。")
        with self._lock:
            draft = dict(self._draft) if isinstance(self._draft, dict) else None
        return {
            "settings": {
                "mode": settings["mode"],
                "modeLabel": _MODE_LABELS[settings["mode"]],
                "userUid": settings["user_uid"],
                "userName": settings["user_name"],
                "userUidConfirmed": settings["user_uid_confirmed"],
                "testVideo": settings["test_video"],
                "testBvid": settings["test_bvid"],
                "allowOtherReplies": settings["allow_other_replies"],
                "autoCommentEnabled": settings["auto_comment_enabled"],
                "autoReplyEnabled": settings["auto_reply_enabled"],
                "mentionReplyEnabled": settings["mention_reply_enabled"],
                "paused": settings["paused"],
            },
            "account": {**account, "credentialStoreAvailable": self._credential_store_available()},
            "login": {"pending": self._login_session is not None},
            "draft": draft,
            "writeCapability": {
                "ready": not self._write_blockers(settings),
                "structuredMentionSupported": False,
                "blockers": self._write_blockers(settings),
            },
            "replyCapability": {
                "ready": not reply_blockers,
                "allowOtherReplies": False,
                "blockers": reply_blockers,
            },
            "records": self._journal.records(),
            "attempts": self._journal.attempts(),
        }

    def save_settings(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            return {"ok": False, "error": "设置内容无效。"}
        with self._lock:
            current = self._settings()
            updated = {
                "mode": current["mode"],
                "user_uid": current["user_uid"],
                "user_name": current["user_name"],
                "user_uid_confirmed": current["user_uid_confirmed"],
                "test_video": current["test_video"],
                "allow_other_replies": current["allow_other_replies"],
                "auto_comment_enabled": current["auto_comment_enabled"],
                "auto_reply_enabled": current["auto_reply_enabled"],
                "reply_enabled_at": current["reply_enabled_at"],
                "mention_reply_enabled": current["mention_reply_enabled"],
                "mention_enabled_at": current["mention_enabled_at"],
                "paused": current["paused"],
            }
            if "mode" in payload:
                mode = payload["mode"]
                if not isinstance(mode, str) or mode not in _MODES:
                    return {"ok": False, "error": "常规互动暂未开放，未知模式已拒绝。"}
                updated["mode"] = mode
            if "userUid" in payload:
                try:
                    new_uid = normalize_bilibili_uid(payload["userUid"])
                except ValueError as exc:
                    return {"ok": False, "error": str(exc)}
                if new_uid != current["user_uid"]:
                    updated["user_name"] = ""
                    updated["user_uid_confirmed"] = False
                updated["user_uid"] = new_uid
            if "testVideo" in payload:
                updated["test_video"] = str(payload["testVideo"] or "").strip()[:500]
            if "allowOtherReplies" in payload:
                if not isinstance(payload["allowOtherReplies"], bool):
                    return {"ok": False, "error": "回复设置必须是布尔值。"}
                updated["allow_other_replies"] = payload["allowOtherReplies"]
            if "autoCommentEnabled" in payload:
                if not isinstance(payload["autoCommentEnabled"], bool):
                    return {"ok": False, "error": "自动评论设置必须是布尔值。"}
                updated["auto_comment_enabled"] = payload["autoCommentEnabled"]
            if "paused" in payload:
                if not isinstance(payload["paused"], bool):
                    return {"ok": False, "error": "暂停设置必须是布尔值。"}
                updated["paused"] = payload["paused"]
            if "autoReplyEnabled" in payload:
                if not isinstance(payload["autoReplyEnabled"], bool):
                    return {"ok": False, "error": "自动回复设置必须是布尔值。"}
                updated["auto_reply_enabled"] = payload["autoReplyEnabled"]
                if payload["autoReplyEnabled"] and not current["auto_reply_enabled"]:
                    updated["reply_enabled_at"] = time.time()
            if updated["user_uid"] != current["user_uid"]:
                updated["auto_reply_enabled"] = False
                updated["reply_enabled_at"] = 0
            if "mentionReplyEnabled" in payload:
                if not isinstance(payload["mentionReplyEnabled"], bool):
                    return {"ok": False, "error": "@召唤设置必须是布尔值。"}
                updated["mention_reply_enabled"] = payload["mentionReplyEnabled"]
                if payload["mentionReplyEnabled"] and not current["mention_reply_enabled"]:
                    updated["mention_enabled_at"] = time.time()
            if updated["user_uid"] != current["user_uid"]:
                updated["mention_reply_enabled"] = False
                updated["mention_enabled_at"] = 0
            try:
                self._save_config(updated)
            except Exception:
                return {"ok": False, "error": "设置保存失败，请检查本机配置目录权限。"}
        from utils.bilibili_reply_log import reply_log
        reply_log("互动设置已保存", main_uid=updated["user_uid"], auto_comment=updated["auto_comment_enabled"],
                  auto_reply=updated["auto_reply_enabled"], mention_reply=updated["mention_reply_enabled"], paused=updated["paused"])
        return {"ok": True, "state": self.get_state()}

    def verify_user_uid(self, uid: str) -> dict:
        try:
            normalized_uid = normalize_bilibili_uid(uid)
            if not normalized_uid:
                return {"ok": False, "error": "请先填写用户 UID。"}
            profile = self._profile_lookup(normalized_uid)
            if not isinstance(profile, dict) or normalize_bilibili_uid(profile.get("uid", "")) != normalized_uid:
                raise ValueError("公开资料 UID 与查询目标不匹配。")
            name = str(profile.get("name", "")).strip()
            if not name:
                raise ValueError("公开资料缺少昵称。")
            return {"ok": True, "uid": normalized_uid, "name": name}
        except (ValueError, URLError, TimeoutError, OSError):
            return {"ok": False, "error": "无法读取该 UID 的 B 站公开资料，请检查 UID 后重试。"}

    def confirm_user_uid(self, uid: str) -> dict:
        try:
            normalized_uid = normalize_bilibili_uid(uid)
            if not normalized_uid:
                return {"ok": False, "error": "请先填写用户 UID。"}
            profile = self._profile_lookup(normalized_uid)
            if not isinstance(profile, dict) or normalize_bilibili_uid(profile.get("uid", "")) != normalized_uid:
                raise ValueError("公开资料 UID 与查询目标不匹配。")
            profile_name = str(profile.get("name", "")).strip()
            if not profile_name:
                raise ValueError("公开资料缺少昵称。")
        except (ValueError, URLError, TimeoutError, OSError):
            return {"ok": False, "error": "UID 核验失败，未保存身份确认。"}
        with self._lock:
            current = self._settings()
            if current["user_uid"] != normalized_uid:
                return {"ok": False, "error": "设置中的 UID 已变化，请重新查询并确认。"}
            saved = {
                "mode": current["mode"],
                "user_uid": normalized_uid,
                "user_name": profile_name,
                "user_uid_confirmed": True,
                "test_video": current["test_video"],
                "allow_other_replies": current["allow_other_replies"],
                "auto_comment_enabled": current["auto_comment_enabled"],
                "auto_reply_enabled": current["auto_reply_enabled"],
                "reply_enabled_at": current["reply_enabled_at"],
                "mention_reply_enabled": current["mention_reply_enabled"],
                "mention_enabled_at": current["mention_enabled_at"],
                "paused": current["paused"],
            }
            try:
                self._save_config(saved)
            except Exception:
                return {"ok": False, "error": "UID 确认保存失败。"}
        return {"ok": True, "state": self.get_state()}

    def inspect_test_video(self, target: str = "") -> dict:
        settings = self._settings()
        bvid = parse_bilibili_video_id(target or settings["test_video"])
        if not bvid:
            return {"ok": False, "error": "请输入 BV 号或 bilibili.com/video 视频链接。"}
        if settings["test_bvid"] and bvid != settings["test_bvid"]:
            return {"ok": False, "error": "目标视频与设置的测试视频不一致。"}
        try:
            video = self._video_lookup(bvid)
            if not isinstance(video, dict) or video.get("bvid") != bvid or not str(video.get("title", "")).strip():
                raise ValueError("公开视频资料与查询目标不匹配。")
            return {"ok": True, "video": video}
        except (ValueError, URLError, TimeoutError, OSError):
            return {"ok": False, "error": "无法读取该视频的公开信息，请检查链接或稍后重试。"}

    def create_comment_draft(self, target: str, text: str) -> dict:
        settings = self._settings()
        target_bvid = parse_bilibili_video_id(target)
        blockers = []
        if settings["mode"] != "test":
            blockers.append("请先切换到指定测试区模式，草稿不会进入普通冲浪流程。")
        if settings["paused"]:
            blockers.append("B 站互动当前已暂停。")
        if not settings["test_bvid"]:
            blockers.append("请先填写有效的测试视频 BV 号或链接。")
        elif target_bvid != settings["test_bvid"]:
            blockers.append("草稿目标视频与设置的测试视频不一致。")
        if not settings["user_uid"] or not settings["user_uid_confirmed"]:
            blockers.append("请先查询并确认用户 UID 对应的公开昵称。")
        if not target_bvid:
            blockers.append("草稿目标不是可识别的 B 站视频。")
        if blockers:
            return {
                "ok": False,
                "status": "blocked",
                "blockers": list(dict.fromkeys(blockers)),
                "state": self.get_state(),
            }

        candidate = re.sub(r"\s+", " ", str(text or "").strip())
        if not candidate:
            return {"ok": False, "status": "invalid", "error": "评论草稿不能为空。"}
        mention = f"@{settings['user_name']}"
        if mention not in candidate:
            candidate = f"{mention} {candidate}"
        video_result = self.inspect_test_video(target_bvid)
        if not video_result.get("ok"):
            return video_result
        video = video_result["video"]
        if video.get("commentOpen") is False:
            return {"ok": False, "status": "blocked", "error": "该测试视频当前未开放评论。"}

        draft = {
            "id": secrets.token_urlsafe(12),
            "status": "pending_confirmation",
            "bvid": target_bvid,
            "text": candidate,
            "mentionUid": settings["user_uid"],
            "mentionName": settings["user_name"],
            "video": video,
            "createdAt": int(time.time()),
        }
        with self._lock:
            self._draft = draft
        return {"ok": True, "status": draft["status"], "draft": draft, "state": self.get_state()}

    def clear_comment_draft(self) -> dict:
        with self._lock:
            self._draft = None
        return {"ok": True, "state": self.get_state()}

    def request_test_comment(self, target: str = "", draft_id: str = "", confirmed=False) -> dict:
        from utils.bilibili_comment_sender import send_test_comment
        with self._send_lock:
            attempt_id = secrets.token_hex(8)
            result = {"ok": False, "sent": False, "stage": "received", "status": "received"}
            try:
                self._journal.save_attempt(attempt_id, result)
            except Exception as error:
                return {"ok": False, "sent": False, "error": "诊断记录无法保存，已禁止发送。", "diagnostic": f"exception={type(error).__name__}"}
            try:
                result = send_test_comment(self, target, draft_id, confirmed)
            except Exception as error:
                result = {"ok": False, "sent": False, "stage": "internal", "status": "unknown",
                    "error": "后端处理异常，发送结果未确认，请勿重复发送。",
                    "diagnostic": f"exception={type(error).__name__}"}
            result["attemptId"] = attempt_id
            try:
                self._journal.save_attempt(attempt_id, result)
                result["state"] = self.get_state()
            except Exception as error:
                result["diagnostic"] = result.get("diagnostic", "") + f"; journal_exception={type(error).__name__}"
            return result

    def recheck_comment(self, rpid: str = "") -> dict:
        from utils.bilibili_comment_sender import recheck_comment
        with self._send_lock:
            return recheck_comment(self, str(rpid).strip())

_service = None
_service_lock = Lock()


def get_bilibili_interaction_service() -> BilibiliInteractionService:
    global _service
    with _service_lock:
        if _service is None:
            _service = BilibiliInteractionService()
        return _service
