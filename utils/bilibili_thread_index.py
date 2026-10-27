"""Recoverable local projections of audited Bilibili conversation activity."""

import json
import math
import re
from contextlib import closing


def positive_id(value):
    text = str(value or "")
    return text if len(text) <= 24 and text.isascii() and text.isdigit() and int(text) > 0 else ""


def timestamp(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


class BilibiliThreadIndex:
    def __init__(self, connect):
        self.connect = connect

    def prepare(self, database):
        database.execute("CREATE TABLE IF NOT EXISTS bili_threads ("
            "account TEXT NOT NULL, main_uid TEXT NOT NULL, aid TEXT NOT NULL, root TEXT NOT NULL, "
            "bvid TEXT NOT NULL, root_author TEXT NOT NULL, created_at REAL NOT NULL, "
            "last_activity_at REAL NOT NULL, last_scan_at REAL NOT NULL DEFAULT 0, "
            "next_scan_at REAL NOT NULL DEFAULT 0, page INTEGER NOT NULL DEFAULT 1, "
            "failures INTEGER NOT NULL DEFAULT 0, backoff_until REAL NOT NULL DEFAULT 0, "
            "PRIMARY KEY(account,main_uid,aid,root))")
        database.execute("CREATE INDEX IF NOT EXISTS bili_threads_due ON bili_threads "
            "(account,main_uid,next_scan_at,last_activity_at DESC)")
        database.execute("CREATE INDEX IF NOT EXISTS bili_threads_activity ON bili_threads "
            "(account,main_uid,last_activity_at DESC,next_scan_at)")
        database.execute("CREATE INDEX IF NOT EXISTS bili_threads_video_root ON bili_threads "
            "(account,main_uid,bvid,root)")
        database.execute("CREATE TABLE IF NOT EXISTS bili_thread_unresolved ("
            "account TEXT NOT NULL, main_uid TEXT NOT NULL, bvid TEXT NOT NULL, root TEXT NOT NULL, "
            "root_author TEXT NOT NULL, created_at REAL NOT NULL, last_activity_at REAL NOT NULL, "
            "PRIMARY KEY(account,main_uid,bvid,root))")
        columns = {row[1] for row in database.execute("PRAGMA table_info(bili_thread_unresolved)")}
        for name, definition in (("last_scan_at", "REAL NOT NULL DEFAULT 0"),
                ("next_scan_at", "REAL NOT NULL DEFAULT 0"), ("page", "INTEGER NOT NULL DEFAULT 1"),
                ("failures", "INTEGER NOT NULL DEFAULT 0"), ("backoff_until", "REAL NOT NULL DEFAULT 0")):
            if name not in columns:
                database.execute(f"ALTER TABLE bili_thread_unresolved ADD COLUMN {name} {definition}")
        database.execute("CREATE INDEX IF NOT EXISTS bili_unresolved_activity ON bili_thread_unresolved "
            "(account,main_uid,last_activity_at DESC,next_scan_at)")

    def project(self, database, event):
        account, main_uid, aid, root, bvid, author, created, activity = event
        if not all(positive_id(value) for value in (account, main_uid, root, author)):
            return False
        if account == main_uid or not isinstance(bvid, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid) or not created or not activity:
            return False
        if not aid:
            existing = database.execute("SELECT aid FROM bili_threads WHERE account=? AND main_uid=? "
                "AND bvid=? AND root=? LIMIT 1", (account, main_uid, bvid, root)).fetchone()
            if existing:
                aid = existing[0]
        if not aid:
            staged = database.execute("SELECT root_author FROM bili_thread_unresolved WHERE account=? "
                "AND main_uid=? AND bvid=? AND root=?", (account, main_uid, bvid, root)).fetchone()
            if staged and staged[0] != author:
                raise ValueError("Thread author conflict")
            database.execute("INSERT INTO bili_thread_unresolved "
                "(account,main_uid,bvid,root,root_author,created_at,last_activity_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(account,main_uid,bvid,root) DO UPDATE SET "
                "created_at=MIN(created_at,excluded.created_at), "
                "last_activity_at=MAX(last_activity_at,excluded.last_activity_at), "
                "next_scan_at=CASE WHEN excluded.last_activity_at>last_activity_at THEN last_scan_at+600 ELSE next_scan_at END "
                "WHERE root_author=excluded.root_author",
                (account, main_uid, bvid, root, author, created, activity))
            return True
        if not positive_id(aid):
            return False
        staged = database.execute("SELECT * FROM bili_thread_unresolved WHERE account=? AND main_uid=? "
            "AND bvid=? AND root=?", (account, main_uid, bvid, root)).fetchone()
        if staged:
            if staged["root_author"] != author:
                raise ValueError("Thread author conflict")
            created = min(created, staged["created_at"])
            activity = max(activity, staged["last_activity_at"])
        existing = database.execute("SELECT bvid,root_author FROM bili_threads "
            "WHERE account=? AND main_uid=? AND aid=? AND root=?", (account, main_uid, aid, root)).fetchone()
        if existing and (existing["bvid"] != bvid or existing["root_author"] != author):
            raise ValueError("Thread identity conflict")
        scan = {key: staged[key] if staged else value for key, value in (
            ("last_scan_at", 0), ("next_scan_at", 0), ("page", 1), ("failures", 0), ("backoff_until", 0))}
        database.execute("INSERT INTO bili_threads "
            "(account,main_uid,aid,root,bvid,root_author,created_at,last_activity_at,last_scan_at,next_scan_at,page,failures,backoff_until) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account,main_uid,aid,root) DO UPDATE SET "
            "created_at=MIN(created_at,excluded.created_at), "
            "last_activity_at=MAX(last_activity_at,excluded.last_activity_at), "
            "next_scan_at=CASE WHEN excluded.last_activity_at>last_activity_at THEN last_scan_at+600 ELSE next_scan_at END",
            (account, main_uid, aid, root, bvid, author, created, activity,
             scan["last_scan_at"], scan["next_scan_at"], scan["page"], scan["failures"], scan["backoff_until"]))
        database.execute("DELETE FROM bili_thread_unresolved WHERE account=? AND main_uid=? AND bvid=? AND root=?",
            (account, main_uid, bvid, root))
        return True

    def maintain(self, batch_size=100):
        limit = max(1, min(int(batch_size), 500))
        summary = {"processed": 0, "projected": 0, "invalid": 0}
        with closing(self.connect()) as database, database:
            self.prepare(database)
            database.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("comments", "bili_reply_jobs"):
                if table not in tables:
                    continue
                allocation = limit if "bili_reply_jobs" not in tables else max(1, limit // 2)
                rows = database.execute(f"SELECT rowid AS migration_rowid,* FROM {table} "
                    "WHERE index_dirty=1 ORDER BY rowid LIMIT ?",
                    (min(allocation, max(0, limit-summary["processed"])),)).fetchall()
                for row in rows:
                    event = self.comment_event(row) if table == "comments" else self.job_event(row)
                    dirty = 0
                    try:
                        if event and self.project(database, event):
                            summary["projected"] += 1
                        elif event is not None:
                            dirty = -1
                    except ValueError:
                        dirty = -1
                    if dirty == -1:
                        summary["invalid"] += 1
                    database.execute(f"UPDATE {table} SET index_dirty=? WHERE rowid=?", (dirty, row["migration_rowid"]))
                    summary["processed"] += 1
        return summary

    def comment_event(self, row):
        if row["status"] not in ("sent_verified", "sent_unverified"):
            return None
        created = timestamp(row["created_at"])
        return (row["account_uid"], row["mention_uid"], row["aid"], row["rpid"], row["bvid"],
                row["account_uid"], created, timestamp(row["sent_at"]) or created)

    def job_event(self, row):
        if row["state"] not in ("pending", "generating", "sending", "sent", "skipped", "exhausted"):
            return None
        try:
            payload = json.loads(row["payload"])
            reply = payload["reply"]
            author = payload.get("root_author", row["account"])
            if (positive_id((reply.get("member") or {}).get("mid")) != row["main_uid"]
                    or positive_id(reply.get("rpid")) != row["source"]
                    or positive_id(reply.get("oid")) != row["aid"]):
                return False
            if row["source"] == row["root"]:
                if positive_id(reply.get("root")) or row["kind"] != "mention":
                    return False
            elif positive_id(reply.get("root")) != row["root"]:
                return False
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
        created = timestamp(row["created"])
        activity = max(created, timestamp(row["sent_at"])) if row["state"] == "sent" else created
        return (row["account"], row["main_uid"], row["aid"], row["root"], row["bvid"], author, created, activity)

    def resolve(self, account, main_uid, aid, root, bvid, root_author):
        with closing(self.connect()) as database, database:
            self.prepare(database)
            database.execute("BEGIN IMMEDIATE")
            staged = database.execute("SELECT * FROM bili_thread_unresolved WHERE "
                "account=? AND main_uid=? AND bvid=? AND root=?", (account, main_uid, bvid, root)).fetchone()
            if not staged:
                return False
            if staged["root_author"] != root_author:
                raise ValueError("Thread author conflict")
            return self.project(database, (account, main_uid, aid, root, bvid, root_author,
                staged["created_at"], staged["last_activity_at"]))

    def due(self, account, main_uid, now, minimum_activity, limit=3):
        with closing(self.connect()) as database:
            return [dict(row) for row in database.execute("SELECT * FROM bili_threads INDEXED BY bili_threads_activity "
                "WHERE account=? AND main_uid=? AND last_activity_at>=? "
                "AND next_scan_at<=? AND backoff_until<=? "
                "ORDER BY next_scan_at,last_activity_at DESC,root LIMIT ?",
                (account, main_uid, minimum_activity, now, now, max(1, min(int(limit), 3))))]

    def recent_due(self, account, main_uid, now):
        candidates = self.due(account, main_uid, now, now-7*86400, limit=2)
        with closing(self.connect()) as database:
            candidates.extend(dict(row) for row in database.execute(
                "SELECT * FROM bili_thread_unresolved INDEXED BY bili_unresolved_activity "
                "WHERE account=? AND main_uid=? AND last_activity_at>=? AND next_scan_at<=? AND backoff_until<=? "
                "ORDER BY next_scan_at,last_activity_at DESC,root LIMIT 2", (account, main_uid, now-7*86400, now, now)))
        return sorted(candidates, key=lambda row: (row["next_scan_at"], -row["last_activity_at"], row["root"]))[:2]

    def scanned(self, record, aid, now, next_page, activity=0):
        account, main_uid, root, bvid = (record[key] for key in ("account", "main_uid", "root", "bvid"))
        created = timestamp(record.get("created_at"))
        latest = max(timestamp(record.get("last_activity_at")), timestamp(record.get("sent_at")), created, timestamp(activity))
        with closing(self.connect()) as database, database:
            self.prepare(database)
            database.execute("BEGIN IMMEDIATE")
            if latest:
                self.project(database, (account, main_uid, aid, root, bvid, record["root_author"], created or latest, latest))
            database.execute("UPDATE bili_threads SET last_scan_at=?,next_scan_at=?,page=?,failures=0,backoff_until=0 "
                "WHERE account=? AND main_uid=? AND aid=? AND root=?", (now, now+600, next_page, account, main_uid, aid, root))

    def scan_failed(self, record, now):
        with closing(self.connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            for table in ("bili_threads", "bili_thread_unresolved"):
                row = database.execute(f"SELECT failures FROM {table} WHERE account=? AND main_uid=? AND bvid=? AND root=?",
                    (record["account"], record["main_uid"], record["bvid"], record["root"])).fetchone()
                if not row:
                    continue
                failures = row[0]+1
                retry_at = now+min(1800, 120*2**min(failures-1, 4))
                database.execute(f"UPDATE {table} SET failures=?,backoff_until=?,next_scan_at=? "
                    "WHERE account=? AND main_uid=? AND bvid=? AND root=?",
                    (failures, retry_at, retry_at, record["account"], record["main_uid"], record["bvid"], record["root"]))
