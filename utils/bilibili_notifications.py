"""Bounded notification discovery with durable page edges and verification inbox."""

import hashlib
import json
import time
from contextlib import closing

from utils import bilibili_api_transport as transport
from utils.bilibili_reply_log import reply_log
from utils.bilibili_thread_index import positive_id, timestamp


class DiscoveryStopped(Exception):
    pass


class DiscoveryBudget:
    def __init__(self, active, monotonic=time.monotonic):
        self.active = active
        self.monotonic = monotonic
        self.started = monotonic()
        self.reads = 0
        self.pages = 0
        self.stored = 0
        self.candidates = 0
        self.blocked = False
        self.threads = set()
        self.cache = {}

    def read(self, function, *args, cache_key=None, **kwargs):
        if self.blocked or not self.active():
            raise DiscoveryStopped("Discovery authorization changed")
        if cache_key is not None and cache_key in self.cache:
            return self.cache[cache_key]
        if self.reads >= 12 or self.monotonic()-self.started >= 30:
            raise DiscoveryStopped("Discovery budget or authorization changed")
        self.reads += 1
        result = function(*args, **kwargs)
        if cache_key is not None:
            self.cache[cache_key] = result
        return result


class NotificationInbox:
    def __init__(self, connect):
        self.connect = connect
        with closing(connect()) as database, database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS bili_notification_inbox (
                    account TEXT NOT NULL, main_uid TEXT NOT NULL, stream TEXT NOT NULL,
                    notice_id TEXT NOT NULL, notice_time REAL NOT NULL, payload TEXT NOT NULL,
                    locator_key TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'pending', source TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(account,main_uid,stream,notice_id,locator_key));
                CREATE INDEX IF NOT EXISTS bili_notice_pending ON bili_notification_inbox
                    (account,main_uid,stream,notice_time DESC,length(notice_id) DESC,notice_id DESC,locator_key)
                    WHERE state IN ('pending','deferred');
                CREATE TABLE IF NOT EXISTS bili_notification_pages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                    main_uid TEXT NOT NULL, stream TEXT NOT NULL, cursor_id TEXT NOT NULL,
                    cursor_time INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    failures INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                    UNIQUE(account,main_uid,stream,cursor_id,cursor_time));
                CREATE INDEX IF NOT EXISTS bili_notice_pages_due ON bili_notification_pages
                    (account,main_uid,stream,state,retry_at,sequence);
                CREATE TABLE IF NOT EXISTS bili_notification_streams (
                    account TEXT NOT NULL, main_uid TEXT NOT NULL, stream TEXT NOT NULL,
                    failures INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(account,main_uid,stream));
                CREATE TABLE IF NOT EXISTS bili_notification_accounts (
                    account TEXT PRIMARY KEY, retry_at REAL NOT NULL);
            """)

    def blocked_until(self, account):
        with closing(self.connect()) as database:
            row = database.execute("SELECT retry_at FROM bili_notification_accounts WHERE account=?", (account,)).fetchone()
            return row[0] if row else 0

    def summary(self, item, stream):
        if not isinstance(item, dict) or not positive_id(item.get("id")):
            raise ValueError("Notification ID missing")
        locator = item.get("item") or {}
        user = item.get("user") or {}
        if not isinstance(locator, dict) or not isinstance(user, dict):
            raise ValueError("Notification locator malformed")
        fields = {key: locator[key] for key in (
            "type", "business_id", "subject_id", "root_id", "source_id", "target_id", "id") if key in locator}
        if any(not isinstance(value, (str, int, float, type(None))) for value in fields.values()):
            raise ValueError("Notification locator malformed")
        event_time = timestamp(item.get("reply_time" if stream == "reply" else "at_time"))
        return {"id": positive_id(item["id"]), "user": {"mid": positive_id(user.get("mid"))},
                "item": fields, "event_time": event_time}

    def persist_page(self, snapshot, stream, response, now, budget, page=None):
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            raise ValueError("Invalid notification page")
        items = response["items"]
        if len(items) > 100-budget.stored:
            raise DiscoveryStopped("Notification storage budget exhausted")
        summaries = [self.summary(item, stream) for item in items]
        cursor = response.get("cursor")
        cursor = {} if cursor is None else cursor
        if not isinstance(cursor, dict):
            raise ValueError("Invalid notification cursor")
        if (cursor or page) and cursor.get("is_end") not in (True, False, 0, 1):
            raise ValueError("Notification end marker missing")
        next_cursor = None
        if not cursor.get("is_end", True):
            cursor_id = positive_id(cursor.get("id"))
            cursor_time = timestamp(cursor.get("time"))
            if not cursor_id or not cursor_time:
                raise ValueError("Incomplete notification cursor")
            next_cursor = (cursor_id, int(cursor_time))
            if page and next_cursor == (page["cursor_id"], page["cursor_time"]):
                raise ValueError("Notification cursor did not advance")
        account, main_uid = snapshot[:2]
        with closing(self.connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            for summary in summaries:
                author = summary["user"]["mid"]
                state = "ignored" if author and author != main_uid else "pending"
                locator_key = hashlib.sha256(json.dumps({"user": summary["user"], "item": summary["item"]}, sort_keys=True).encode()).hexdigest()
                database.execute("INSERT OR IGNORE INTO bili_notification_inbox "
                    "(account,main_uid,stream,notice_id,notice_time,payload,locator_key,state,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (account, main_uid, stream, summary["id"], summary["event_time"],
                     json.dumps(summary, ensure_ascii=False), locator_key, state, now, now))
            if next_cursor:
                database.execute("INSERT OR IGNORE INTO bili_notification_pages "
                    "(account,main_uid,stream,cursor_id,cursor_time) VALUES (?,?,?,?,?)",
                    (account, main_uid, stream, *next_cursor))
            if page:
                database.execute("UPDATE bili_notification_pages SET state='done',failures=0,retry_at=0 WHERE sequence=?",
                    (page["sequence"],))
            else:
                database.execute("INSERT INTO bili_notification_streams (account,main_uid,stream) VALUES (?,?,?) "
                    "ON CONFLICT(account,main_uid,stream) DO UPDATE SET failures=0,retry_at=0", (account, main_uid, stream))
        budget.stored += len(summaries)
        return len(summaries)

    def fetch(self, worker, snapshot, stream, budget, history=False):
        if worker.snapshot(stream) != snapshot or budget.pages >= 4 or budget.stored >= 100:
            return
        account, main_uid = snapshot[:2]
        now = worker.clock()
        with closing(self.connect()) as database:
            if history:
                row = database.execute("SELECT * FROM bili_notification_pages WHERE account=? AND main_uid=? "
                    "AND stream=? AND state='pending' AND retry_at<=? ORDER BY sequence LIMIT 1",
                    (account, main_uid, stream, now)).fetchone()
                if not row:
                    return
                page = dict(row)
            else:
                row = database.execute("SELECT retry_at FROM bili_notification_streams "
                    "WHERE account=? AND main_uid=? AND stream=?", (account, main_uid, stream)).fetchone()
                if row and row[0] > now:
                    reply_log("通知流退避", stream=stream, retry_at=row[0])
                    return
                page = None
        try:
            options = {}
            if page:
                options = ({"last_reply_id": int(page["cursor_id"]), "reply_time": page["cursor_time"]}
                           if stream == "reply" else {"last_id": int(page["cursor_id"]), "at_time": page["cursor_time"]})
            budget.pages += 1
            reader = transport.get_received_replies if stream == "reply" else transport.get_received_mentions
            response = budget.read(reader, snapshot[3], **options)
            if worker.snapshot(stream) != snapshot:
                raise DiscoveryStopped("Notification authorization changed")
            count = self.persist_page(snapshot, stream, response, now, budget, page)
            reply_log("读取@通知" if stream == "mention" else "读取回复通知", account=account, count=count,
                      page="history" if history else "latest", persisted=True)
        except DiscoveryStopped:
            reply_log("通知读取暂缓", stream=stream, reason="预算、暂停或身份变化；检查点未推进")
        except Exception as error:
            self.fail(snapshot, stream, now, error, budget, page)

    def fail(self, snapshot, stream, now, error, budget, page=None):
        account, main_uid = snapshot[:2]
        with closing(self.connect()) as database, database:
            if page:
                failures = page["failures"] + 1
                retry_at = now + min(1800, 120 * 2 ** min(failures-1, 4))
                database.execute("UPDATE bili_notification_pages SET failures=?,retry_at=? WHERE sequence=? AND state='pending'",
                    (failures, retry_at, page["sequence"]))
            else:
                row = database.execute("SELECT failures FROM bili_notification_streams WHERE account=? AND main_uid=? AND stream=?",
                    (account, main_uid, stream)).fetchone()
                failures = (row[0] if row else 0) + 1
                retry_at = now + min(1800, 120 * 2 ** min(failures-1, 4))
                database.execute("INSERT INTO bili_notification_streams VALUES (?,?,?,?,?) "
                    "ON CONFLICT(account,main_uid,stream) DO UPDATE SET failures=excluded.failures,retry_at=excluded.retry_at",
                    (account, main_uid, stream, failures, retry_at))
        self.throttle(account, now, error, budget)
        reply_log("@通知读取失败" if stream == "mention" else "回复通知读取失败", error=type(error).__name__,
                  page="history" if page else "latest", retry_at=retry_at, account_backoff=budget.blocked)

    def throttle(self, account, now, error, budget):
        code = getattr(error, "code", None) or getattr(error, "api_code", None)
        if str(code) not in ("429", "-412", "-352", "-509"):
            return
        budget.blocked = True
        with closing(self.connect()) as database, database:
            database.execute("INSERT INTO bili_notification_accounts VALUES (?,?) "
                "ON CONFLICT(account) DO UPDATE SET retry_at=MAX(retry_at,excluded.retry_at)", (account, now+1800))

    def candidates(self, snapshots, now):
        groups = []
        with closing(self.connect()) as database:
            for stream, snapshot in snapshots.items():
                groups.append([dict(row) for row in database.execute("SELECT * FROM bili_notification_inbox "
                    "WHERE account=? AND main_uid=? AND stream=? AND state IN ('pending','deferred') "
                    "AND retry_at<=? ORDER BY notice_time DESC,length(notice_id) DESC,notice_id DESC,locator_key LIMIT 4", (*snapshot[:2], stream, now))])
        chosen = []
        while len(chosen) < 4 and any(groups):
            for group in groups:
                if group and len(chosen) < 4:
                    chosen.append(group.pop(0))
        return chosen

    def finish(self, notice, state, now, source=""):
        attempts = notice["attempts"] + (state == "deferred")
        if state == "deferred" and attempts >= 6:
            state = "exhausted"
        retry_at = now + min(1800, 120 * 2 ** min(max(0, attempts-1), 4)) if state == "deferred" else 0
        with closing(self.connect()) as database, database:
            database.execute("UPDATE bili_notification_inbox SET state=?,source=?,attempts=?,retry_at=?,updated=? "
                "WHERE account=? AND main_uid=? AND stream=? AND notice_id=? AND locator_key=? AND state IN ('pending','deferred')",
                (state, source, attempts, retry_at, now, notice["account"], notice["main_uid"], notice["stream"], notice["notice_id"], notice["locator_key"]))
        reply_log("通知核验结果", stream=notice["stream"], notice=notice["notice_id"], state=state,
                  source=source, attempts=attempts, retry_at=retry_at)
