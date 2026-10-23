"""Durable main-account-only conversations in locally known public threads."""

import json
import time
import uuid
from contextlib import closing

from utils import bilibili_api_transport as transport


def identity(value):
    text = str(value or "")
    return text if text.isascii() and text.isdigit() and int(text) > 0 else ""


class BilibiliReplyService:
    def __init__(self, service, generate, cancelled=lambda: False, clock=time.time):
        self.service = service
        self.generate = generate
        self.cancelled = cancelled
        self.clock = clock
        self.owner = uuid.uuid4().hex
        with closing(self.connect()) as database, database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS bili_reply_jobs (
                    account TEXT NOT NULL, source TEXT NOT NULL, main_uid TEXT NOT NULL,
                    root TEXT NOT NULL, bvid TEXT NOT NULL, aid TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', owner TEXT NOT NULL DEFAULT '',
                    lease REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    text TEXT NOT NULL DEFAULT '', rpid TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account,source));
                CREATE TABLE IF NOT EXISTS bili_reply_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def connect(self):
        return self.service._journal.connect()

    def snapshot(self):
        settings = self.service._settings()
        credential = self.service._read_credential()
        account = identity((credential or {}).get("dedeuserid", (credential or {}).get("DedeUserID")))
        try:
            enabled_at = float(settings["reply_enabled_at"])
        except (ValueError, TypeError):
            return None
        if (self.cancelled() or not settings["auto_reply_enabled"] or settings["paused"]
                or not settings["user_uid_confirmed"] or not account
                or not settings["user_uid"] or account == settings["user_uid"]
                or not 0 < enabled_at <= self.clock()):
            return None
        return account, settings["user_uid"], enabled_at, dict(credential)

    def meta(self, database, key, default="0"):
        row = database.execute("SELECT value FROM bili_reply_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, database, key, value):
        database.execute("INSERT OR REPLACE INTO bili_reply_meta VALUES (?,?)", (key, str(value)))

    def thread(self, credential, job, page=1):
        result = transport.get_comment_thread(credential, job["aid"], job["root"], page)
        root = result.get("root") or {}
        if (identity(root.get("rpid")) != job["root"]
                or identity((root.get("member") or {}).get("mid")) != job["account"]
                or identity(root.get("oid")) != job["aid"]):
            raise ValueError("Thread identity mismatch")
        replies = result.get("replies") or []
        if not isinstance(replies, list):
            raise ValueError("Invalid thread")
        return root, replies, result.get("page") or {}

    def own_parents(self, job):
        with closing(self.connect()) as database:
            parents = {row[0] for row in database.execute(
                "SELECT rpid FROM bili_reply_jobs WHERE account=? AND root=? AND state='sent'",
                (job["account"], job["root"])) if identity(row[0])}
        return parents | {job["root"]}

    def valid_source(self, reply, job, enabled_at):
        try:
            return bool(identity(reply.get("rpid"))
                and identity((reply.get("member") or {}).get("mid")) == job["main_uid"]
                and identity(reply.get("root")) == job["root"]
                and identity(reply.get("oid")) == job["aid"]
                and identity(reply.get("parent")) in self.own_parents(job)
                and float(reply.get("ctime", 0)) > enabled_at
                and str((reply.get("content") or {}).get("message", "")).strip())
        except (TypeError, ValueError):
            return False

    def scan(self, snapshot):
        account, main_uid, enabled_at, credential = snapshot
        with closing(self.connect()) as database, database:
            roots = [dict(row) for row in database.execute(
                "SELECT * FROM comments WHERE account_uid=? AND mention_uid=? "
                "AND status IN ('sent_verified','sent_unverified') AND rpid != '' ORDER BY created_at DESC",
                (account, main_uid))]
            offset = int(self.meta(database, f"root:{account}"))
            self.set_meta(database, f"root:{account}", (offset + min(3, len(roots))) % max(1, len(roots)))
        for index in range(min(3, len(roots))):
            if self.cancelled():
                return
            record = roots[(offset + index) % len(roots)]
            try:
                self.scan_root(record, snapshot)
            except Exception as error:
                print(f"[B站续聊] 评论串读取暂缓: {type(error).__name__}")
        if roots and not self.cancelled():
            try:
                notifications = transport.get_received_replies(credential)
                hints = {identity((item.get("item") or {}).get("root_id"))
                         for item in (notifications.get("items") or []) if isinstance(item, dict)}
                scanned = {str(roots[(offset + index) % len(roots)]["rpid"]) for index in range(min(3, len(roots)))}
                hinted = next((record for record in roots if str(record["rpid"]) in hints - scanned), None)
                if hinted:
                    self.scan_root(hinted, snapshot)
            except Exception as error:
                print(f"[B站续聊] 通知读取暂缓，已使用评论串轮扫: {type(error).__name__}")

    def scan_root(self, record, snapshot):
        account, main_uid, enabled_at, credential = snapshot
        video = self.service._video_lookup(record["bvid"])
        if video.get("bvid") != record["bvid"] or not identity(video.get("aid")):
            return
        job = {"account": account, "main_uid": main_uid, "root": identity(record["rpid"]),
               "bvid": record["bvid"], "aid": identity(video["aid"])}
        if not job["root"]:
            return
        page_key = f"page:{account}:{job['root']}"
        with closing(self.connect()) as database:
            page = int(self.meta(database, page_key, "1"))
        root, replies, paging = self.thread(credential, job, page)
        candidates = [reply for reply in replies if self.valid_source(reply, job, enabled_at)]
        with closing(self.connect()) as database, database:
            total = int(paging.get("count", 0))
            self.set_meta(database, page_key, page + 1 if page * 20 < total else 1)
            for reply in candidates:
                payload = {"reply": reply, "page": page, "title": str(video.get("title", ""))[:500]}
                database.execute("INSERT OR IGNORE INTO bili_reply_jobs "
                    "(account,source,main_uid,root,bvid,aid,payload,created) VALUES (?,?,?,?,?,?,?,?)",
                    (account, identity(reply["rpid"]), main_uid, job["root"], job["bvid"], job["aid"],
                     json.dumps(payload, ensure_ascii=False), float(reply["ctime"])))

    def claim(self, snapshot):
        account, main_uid, enabled_at, credential = snapshot
        with closing(self.connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            if float(self.meta(database, "last_send")) + 120 > self.clock():
                return None
            row = database.execute("SELECT * FROM bili_reply_jobs WHERE account=? AND main_uid=? "
                "AND created>? AND attempts<3 AND (state='pending' OR (state='generating' AND lease<?)) "
                "ORDER BY created,source LIMIT 1", (account, main_uid, enabled_at, self.clock())).fetchone()
            if row is None:
                return None
            database.execute("UPDATE bili_reply_jobs SET state='generating',owner=?,lease=?,attempts=attempts+1 "
                "WHERE account=? AND source=?", (self.owner, self.clock()+600, account, row["source"]))
            return dict(row)

    def mark(self, job, state):
        with closing(self.connect()) as database, database:
            database.execute("UPDATE bili_reply_jobs SET state=? WHERE account=? AND source=? "
                "AND owner=? AND state='generating' AND lease>?",
                (state, job["account"], job["source"], self.owner, self.clock()))

    def source_at(self, snapshot, job, page):
        root, replies, paging = self.thread(snapshot[3], job, page)
        source = next((reply for reply in replies if identity(reply.get("rpid")) == job["source"]), None)
        if not source or not self.valid_source(source, job, snapshot[2]):
            return root, None
        return root, source

    def process(self):
        snapshot = self.snapshot()
        if not snapshot:
            return "disabled"
        self.scan(snapshot)
        job = self.claim(snapshot)
        if not job:
            return "idle"
        try:
            payload = json.loads(job["payload"])
            root, source = self.source_at(snapshot, job, payload["page"])
            if source is None:
                self.mark(job, "skipped")
                return "skipped"
            with closing(self.connect()) as database:
                history = [dict(row) for row in database.execute(
                    "SELECT payload,text FROM bili_reply_jobs WHERE account=? AND root=? AND main_uid=? "
                    "AND state='sent' ORDER BY created DESC LIMIT 4", (job["account"], job["root"], job["main_uid"]))]
            context = {"video_title": payload["title"],
                "root_comment": str((root.get("content") or {}).get("message", ""))[:2000],
                "previous_turns": [{"user": str((json.loads(turn["payload"])["reply"].get("content") or {}).get("message", ""))[:2000],
                                    "assistant": turn["text"][:2000]} for turn in reversed(history)],
                "new_reply": str((source.get("content") or {}).get("message", ""))[:3000]}
            text = self.generate(context)
            if not isinstance(text, str) or not text.strip() or text.strip().upper() == "SKIP":
                self.mark(job, "skipped")
                return "skipped"
            latest = self.snapshot()
            if not latest or latest != snapshot or not self.service.check_authorization().get("ok"):
                self.mark(job, "skipped")
                return "cancelled"
            video = self.service._video_lookup(job["bvid"])
            if (video.get("bvid") != job["bvid"] or identity(video.get("aid")) != job["aid"]
                    or video.get("commentOpen") is not True):
                self.mark(job, "skipped")
                return "skipped"
            verified_root, verified = self.source_at(latest, job, payload["page"])
            if (verified is None or (verified.get("content") or {}).get("message") != (source.get("content") or {}).get("message")
                    or (verified_root.get("content") or {}).get("message") != (root.get("content") or {}).get("message")):
                self.mark(job, "skipped")
                return "skipped"
            with closing(self.connect()) as database, database:
                database.execute("BEGIN IMMEDIATE")
                if float(self.meta(database, "last_send")) + 120 > self.clock():
                    return "cooldown"
                if self.snapshot() != snapshot:
                    return "cancelled"
                changed = database.execute("UPDATE bili_reply_jobs SET state='sending',text=? "
                    "WHERE account=? AND source=? AND owner=? AND state='generating' AND lease>?",
                    (text.strip(), job["account"], job["source"], self.owner, self.clock())).rowcount
                if not changed:
                    return "lease_lost"
                self.set_meta(database, "last_send", self.clock())
            if self.snapshot() != snapshot:
                return "cancelled"
            result = transport.send_comment_reply(latest[3], job["aid"], job["root"], job["source"], text.strip())
            data = result.get("data", result)
            rpid = identity(data.get("rpid") or (data.get("reply") or {}).get("rpid"))
            if result.get("code", 0) != 0 or not rpid:
                return "unknown"
            with closing(self.connect()) as database, database:
                database.execute("UPDATE bili_reply_jobs SET state='sent',rpid=? WHERE account=? AND source=? "
                    "AND owner=? AND state='sending'", (rpid, job["account"], job["source"], self.owner))
            return "sent"
        except Exception as error:
            print(f"[B站续聊] 任务暂缓或发送结果待核对: {type(error).__name__}")
            return "deferred_or_unknown"
