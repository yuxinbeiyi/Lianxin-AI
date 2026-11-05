"""Time Capsule 的本地 SQLite 数据层与旧日记无损迁移。"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from utils.paths import get_legacy_memory_dir, get_user_data_dir


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class TimeCapsuleDatabase:
    """Shared-memory store.  Every migration is additive and idempotent."""

    def __init__(self, path: Path | str | None = None, *, migrate_legacy: bool = True,
                 legacy_sources: list[Path | str] | None = None):
        self.path = Path(path or (get_user_data_dir() / "time_capsule.db"))
        self.legacy_sources = [Path(item) for item in legacy_sources] if legacy_sources is not None else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        if migrate_legacy:
            self.migrate_legacy_diaries()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            # WAL is persistent database metadata. Re-applying it for every
            # short read connection made opening the capsule needlessly slow.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS capsule_days (
                    date TEXT PRIMARY KEY,
                    user_content TEXT NOT NULL DEFAULT '',
                    lianxin_content TEXT NOT NULL DEFAULT '',
                    weather TEXT NOT NULL DEFAULT '',
                    sealed INTEGER NOT NULL DEFAULT 0,
                    sealed_at TEXT NOT NULL DEFAULT '',
                    is_red_line INTEGER NOT NULL DEFAULT 0,
                    echo_text TEXT NOT NULL DEFAULT '',
                    source_json TEXT NOT NULL DEFAULT '{}',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    favorited_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capsule_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capsule_date TEXT NOT NULL,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(capsule_date, author, content)
                );
                CREATE INDEX IF NOT EXISTS idx_capsule_revisions_date
                    ON capsule_revisions(capsule_date, id DESC);
                CREATE TABLE IF NOT EXISTS capsule_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capsule_date TEXT NOT NULL,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_capsule_traces_date
                    ON capsule_traces(capsule_date, id ASC);
                CREATE TABLE IF NOT EXISTS capsule_collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capsule_date TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    uri TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_capsule_collections_date
                    ON capsule_collections(capsule_date, id DESC);
                CREATE TABLE IF NOT EXISTS tree_hole_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    echo_of INTEGER,
                    opened INTEGER NOT NULL DEFAULT 1,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    archived_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tree_hole_created
                    ON tree_hole_notes(created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tree_hole_echo
                    ON tree_hole_notes(echo_of, author, id DESC);
                CREATE TABLE IF NOT EXISTS tree_reply_jobs (
                    note_id INTEGER PRIMARY KEY,
                    scheduled_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tree_hole_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id INTEGER NOT NULL,
                    related_note_id INTEGER,
                    notification_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    read_at TEXT NOT NULL DEFAULT '',
                    unique_key TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_tree_notifications_unread
                    ON tree_hole_notifications(read_at, notification_type, created_at DESC);
                CREATE TABLE IF NOT EXISTS capsule_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    source_date TEXT NOT NULL DEFAULT '',
                    cover_kind TEXT NOT NULL DEFAULT 'story',
                    favorite INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(title, source_date)
                );
                CREATE TABLE IF NOT EXISTS capsule_visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER,
                    capsule_date TEXT NOT NULL DEFAULT '',
                    visited_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capsule_migrations (
                    migration_key TEXT PRIMARY KEY,
                    completed_at TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            day_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(capsule_days)").fetchall()
            }
            if "favorite" not in day_columns:
                conn.execute("ALTER TABLE capsule_days ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
            if "favorited_at" not in day_columns:
                conn.execute("ALTER TABLE capsule_days ADD COLUMN favorited_at TEXT NOT NULL DEFAULT ''")
            tree_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(tree_hole_notes)").fetchall()
            }
            if "archived" not in tree_columns:
                conn.execute("ALTER TABLE tree_hole_notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            if "archived_at" not in tree_columns:
                conn.execute("ALTER TABLE tree_hole_notes ADD COLUMN archived_at TEXT NOT NULL DEFAULT ''")
            reply_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(tree_reply_jobs)").fetchall()
            }
            if "lease_owner" not in reply_columns:
                conn.execute("ALTER TABLE tree_reply_jobs ADD COLUMN lease_owner TEXT NOT NULL DEFAULT ''")
            if "lease_expires_at" not in reply_columns:
                conn.execute("ALTER TABLE tree_reply_jobs ADD COLUMN lease_expires_at REAL NOT NULL DEFAULT 0")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_capsule_days_favorite ON capsule_days(favorite, favorited_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tree_hole_archive ON tree_hole_notes(archived, favorite, created_at DESC)"
            )

    @staticmethod
    def _day_dict(row) -> dict:
        if row is None:
            return {}
        data = dict(row)
        data["sealed"] = bool(data.get("sealed"))
        data["is_red_line"] = bool(data.get("is_red_line"))
        data["favorite"] = bool(data.get("favorite"))
        try:
            data["source"] = json.loads(data.pop("source_json") or "{}")
        except json.JSONDecodeError:
            data["source"] = {}
        return data

    def ensure_day(self, day: str) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO capsule_days(date, created_at, updated_at)
                   VALUES (?, ?, ?)""",
                (day, now, now),
            )

    def get_day(self, day: str, *, include_revisions: bool = False) -> dict:
        self.ensure_day(day)
        return self.read_day(day, include_revisions=include_revisions)

    def read_day(self, day: str, *, include_revisions: bool = False) -> dict:
        """Read one capsule without creating an empty row on a miss."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM capsule_days WHERE date = ?", (day,)).fetchone()
            traces = conn.execute(
                "SELECT * FROM capsule_traces WHERE capsule_date = ? AND archived = 0 ORDER BY id",
                (day,),
            ).fetchall()
            collections = conn.execute(
                "SELECT * FROM capsule_collections WHERE capsule_date = ? ORDER BY id DESC",
                (day,),
            ).fetchall()
            revisions = []
            if include_revisions:
                revisions = conn.execute(
                    "SELECT * FROM capsule_revisions WHERE capsule_date = ? ORDER BY id DESC",
                    (day,),
                ).fetchall()
        result = self._day_dict(row)
        result["traces"] = [dict(item) for item in traces]
        result["collections"] = [self._collection_dict(item) for item in collections]
        if include_revisions:
            result["revisions"] = [dict(item) for item in revisions]
        return result

    def remove_invalid_empty_days(self) -> int:
        """Remove query artifacts such as a literal '昨天' row, never real content."""
        with self._connect() as conn:
            cur = conn.execute(
                """DELETE FROM capsule_days
                   WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                     AND user_content='' AND lianxin_content=''
                     AND NOT EXISTS (SELECT 1 FROM capsule_traces t WHERE t.capsule_date=capsule_days.date)
                     AND NOT EXISTS (SELECT 1 FROM capsule_collections c WHERE c.capsule_date=capsule_days.date)"""
            )
            return int(cur.rowcount or 0)

    def _save_content(self, day: str, author: str, content: str, **fields) -> dict:
        column = "user_content" if author == "user" else "lianxin_content"
        self.ensure_day(day)
        now = _now()
        with self._connect() as conn:
            # 编辑器会自动保存。将每一次停顿输入都写成"历史版本"会让
            # 同一篇日记在阅读侧栏中反复出现；日记采用最后一次写入覆盖。
            # 旧版 revision 表仅保留兼容数据，不再向其中追加自动保存片段。
            assignments = [f"{column} = ?", "updated_at = ?"]
            values = [str(content or ""), now]
            allowed = {"weather", "is_red_line", "echo_text", "source_json"}
            for key, value in fields.items():
                if key in allowed:
                    assignments.append(f"{key} = ?")
                    values.append(value)
            values.append(day)
            conn.execute(
                f"UPDATE capsule_days SET {', '.join(assignments)} WHERE date = ?", values
            )
        return self.get_day(day)

    def save_user_content(self, day: str, content: str) -> dict:
        return self._save_content(day, "user", content)

    def save_lianxin_content(self, day: str, content: str, *, weather: str = "",
                             is_red_line: bool = False, echo_text: str = "",
                             source: dict | None = None) -> dict:
        result = self._save_content(
            day, "lianxin", content, weather=str(weather or ""),
            is_red_line=int(bool(is_red_line)), echo_text=str(echo_text or ""),
            source_json=json.dumps(source or {}, ensure_ascii=False),
        )
        if echo_text and not any(
            trace["author"] == "lianxin" and trace["content"] == echo_text
            for trace in result.get("traces", [])
        ):
            self.add_trace(day, "lianxin", echo_text)
            result = self.get_day(day)
        return result

    def seal_day(self, day: str) -> dict:
        self.ensure_day(day)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE capsule_days SET sealed = 1, sealed_at = CASE
                       WHEN sealed_at = '' THEN ? ELSE sealed_at END, updated_at = ? WHERE date = ?""",
                (now, now, day),
            )
        return self.get_day(day)

    def toggle_day_favorite(self, day: str) -> dict:
        self.ensure_day(day)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE capsule_days
                   SET favorite = CASE favorite WHEN 0 THEN 1 ELSE 0 END,
                       favorited_at = CASE favorite WHEN 0 THEN ? ELSE '' END,
                       updated_at = ?
                   WHERE date = ?""",
                (now, now, str(day)),
            )
        return self.get_day(str(day))

    def link_memory_fact(self, day: str, fact_id: int) -> None:
        if not fact_id:
            return
        self.ensure_day(day)
        with self._connect() as conn:
            row = conn.execute("SELECT source_json FROM capsule_days WHERE date = ?", (day,)).fetchone()
            try:
                source = json.loads(row["source_json"] or "{}") if row else {}
            except json.JSONDecodeError:
                source = {}
            source["memory_fact_id"] = int(fact_id)
            source["memory_linked_at"] = _now()
            conn.execute(
                "UPDATE capsule_days SET source_json = ?, updated_at = ? WHERE date = ?",
                (json.dumps(source, ensure_ascii=False), _now(), day),
            )

    def record_visit(self, memory_id: int, capsule_date: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO capsule_visits(memory_id, capsule_date, visited_at) VALUES (?, ?, ?)",
                (int(memory_id), str(capsule_date or ""), _now()),
            )

    @staticmethod
    def _memory_title(day: str, content: str) -> str:
        first = next((line.strip("# *-\t") for line in content.splitlines() if line.strip()), "")
        return first[:24] or f"{day} 的故事"

    def add_trace(self, day: str, author: str, content: str) -> dict:
        text = str(content or "").strip()
        if not text:
            return self.get_day(day)
        self.ensure_day(day)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO capsule_traces(capsule_date, author, content, created_at) VALUES (?, ?, ?, ?)",
                (day, "lianxin" if author == "lianxin" else "user", text, _now()),
            )
        return self.get_day(day)

    @staticmethod
    def _collection_dict(row) -> dict:
        data = dict(row)
        data["favorite"] = bool(data.get("favorite"))
        try:
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            data["metadata"] = {}
        return data

    def add_collection(self, day: str, kind: str, title: str = "", uri: str = "",
                       metadata: dict | None = None) -> dict:
        allowed = {"photo", "music", "study", "game", "chat", "file", "location"}
        kind = kind if kind in allowed else "file"
        default_titles = {
            "photo": "一张照片", "music": "一起听过的音乐", "study": "一段专注",
            "game": "一起玩过的游戏", "chat": "一句想记住的话",
            "file": "一份共同文件", "location": "一起到过的地方",
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO capsule_collections
                   (capsule_date, kind, title, uri, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (day, kind, str(title or default_titles[kind]), str(uri or ""),
                 json.dumps(metadata or {}, ensure_ascii=False), _now()),
            )
        return self.get_day(day)

    def toggle_collection_favorite(self, collection_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE capsule_collections
                   SET favorite = CASE favorite WHEN 0 THEN 1 ELSE 0 END WHERE id = ?""",
                (int(collection_id),),
            )

    def add_tree_note(self, author: str, content: str, echo_of: int | None = None) -> int:
        text = str(content or "").strip()
        if not text:
            return 0
        with self._connect() as conn:
            if author == "lianxin" and echo_of:
                existing = conn.execute(
                    "SELECT id FROM tree_hole_notes WHERE author = 'lianxin' AND echo_of = ? ORDER BY id DESC LIMIT 1",
                    (int(echo_of),),
                ).fetchone()
                if existing:
                    return int(existing["id"])
            cur = conn.execute(
                """INSERT INTO tree_hole_notes(author, content, echo_of, created_at)
                   VALUES (?, ?, ?, ?)""",
                ("lianxin" if author == "lianxin" else "user", text, echo_of, _now()),
            )
            note_id = int(cur.lastrowid)
            if author == "lianxin":
                notification_type = "reply" if echo_of else "lianxin_note"
                unique_key = f"reply:{note_id}" if echo_of else f"note:{note_id}"
                self._insert_tree_notification(conn, note_id, echo_of, notification_type, unique_key)
            return note_id

    def get_tree_note(self, note_id: int) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tree_hole_notes WHERE id = ?", (int(note_id),)).fetchone()
            reply = conn.execute(
                "SELECT * FROM tree_hole_notes WHERE author = 'lianxin' AND echo_of = ? ORDER BY id DESC LIMIT 1",
                (int(note_id),),
            ).fetchone()
        if not row:
            return {}
        result = {**dict(row), "favorite": bool(row["favorite"]), "opened": bool(row["opened"])}
        result["archived"] = bool(result.get("archived"))
        result["reply"] = dict(reply) if reply else None
        return result

    def schedule_tree_reply(self, note_id: int, scheduled_at: float) -> dict:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tree_reply_jobs(
                       note_id, scheduled_at, status, attempts, last_error,
                       lease_owner, lease_expires_at, updated_at
                   ) VALUES (?, ?, 'waiting', 0, '', '', 0, ?)
                   ON CONFLICT(note_id) DO UPDATE SET
                       scheduled_at=excluded.scheduled_at,
                       status=CASE WHEN tree_reply_jobs.status = 'done'
                                   THEN tree_reply_jobs.status ELSE 'waiting' END,
                       lease_owner='', lease_expires_at=0,
                       updated_at=excluded.updated_at""",
                (int(note_id), float(scheduled_at), _now()),
            )
        return self.get_tree_reply_job(note_id) or {}

    def reconcile_tree_reply_jobs(self, *, now: float | None = None,
                                  max_delay_seconds: int = 300) -> int:
        """Create missing durable jobs for every unanswered user note."""
        import random
        base = float(now if now is not None else time.time())
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT n.id FROM tree_hole_notes n
                   LEFT JOIN tree_reply_jobs j ON j.note_id=n.id
                   WHERE n.author='user' AND j.note_id IS NULL
                     AND NOT EXISTS (SELECT 1 FROM tree_hole_notes r
                                     WHERE r.author='lianxin' AND r.echo_of=n.id)"""
            ).fetchall()
            for row in rows:
                conn.execute(
                    """INSERT OR IGNORE INTO tree_reply_jobs(
                           note_id, scheduled_at, status, attempts, last_error,
                           lease_owner, lease_expires_at, updated_at
                       ) VALUES (?, ?, 'waiting', 0, '', '', 0, ?)""",
                    (int(row["id"]), base + random.uniform(0, max(0, int(max_delay_seconds))), _now()),
                )
        return len(rows)

    def force_tree_reply(self, note_id: int) -> dict:
        """Put one note back into the durable queue for an immediate retry."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tree_reply_jobs(
                       note_id, scheduled_at, status, attempts, last_error,
                       lease_owner, lease_expires_at, updated_at
                   ) VALUES (?, ?, 'waiting', 0, '', '', 0, ?)
                   ON CONFLICT(note_id) DO UPDATE SET
                       scheduled_at=excluded.scheduled_at, status='waiting',
                       attempts=0, last_error='', lease_owner='',
                       lease_expires_at=0, updated_at=excluded.updated_at
                   WHERE tree_reply_jobs.status <> 'running'
                      OR tree_reply_jobs.lease_expires_at <= ?""",
                (int(note_id), time.time(), _now(), time.time()),
            )
        return self.get_tree_reply_job(note_id) or {}

    def get_tree_reply_job(self, note_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tree_reply_jobs WHERE note_id = ?", (int(note_id),)).fetchone()
        return dict(row) if row else None

    def pending_tree_reply_jobs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tree_reply_jobs WHERE status IN ('waiting','retrying','running')"
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_expired_tree_reply_jobs(self, now: float | None = None) -> int:
        now = float(now if now is not None else time.time())
        with self._connect() as conn:
            conn.execute(
                """UPDATE tree_reply_jobs
                   SET status='done', lease_owner='', lease_expires_at=0, updated_at=?
                   WHERE status <> 'done' AND EXISTS (
                       SELECT 1 FROM tree_hole_notes r
                       WHERE r.author='lianxin' AND r.echo_of=tree_reply_jobs.note_id
                   )""",
                (_now(),),
            )
            cur = conn.execute(
                """UPDATE tree_reply_jobs
                   SET status='retrying', scheduled_at=?, lease_owner='',
                       lease_expires_at=0, updated_at=?
                   WHERE status='running' AND lease_expires_at > 0
                     AND lease_expires_at <= ?""",
                (now, _now(), now),
            )
            return int(cur.rowcount or 0)

    def claim_due_tree_reply_job(self, owner: str, now: float | None = None,
                                 lease_seconds: int = 300) -> dict:
        """Atomically claim one due job; safe across concurrent schedulers."""
        now = float(now if now is not None else time.time())
        owner = str(owner or "tree-reply-duty")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE tree_reply_jobs
                   SET status='done', lease_owner='', lease_expires_at=0, updated_at=?
                   WHERE status <> 'done' AND EXISTS (
                       SELECT 1 FROM tree_hole_notes r
                       WHERE r.author='lianxin' AND r.echo_of=tree_reply_jobs.note_id
                   )""",
                (_now(),),
            )
            conn.execute(
                """UPDATE tree_reply_jobs
                   SET status='retrying', scheduled_at=?, lease_owner='',
                       lease_expires_at=0, updated_at=?
                   WHERE status='running' AND lease_expires_at > 0
                     AND lease_expires_at <= ?""",
                (now, _now(), now),
            )
            row = conn.execute(
                """SELECT j.* FROM tree_reply_jobs j
                   JOIN tree_hole_notes n ON n.id = j.note_id
                   WHERE j.status IN ('waiting','retrying')
                     AND j.scheduled_at <= ? AND n.author='user'
                     AND NOT EXISTS (
                         SELECT 1 FROM tree_hole_notes r
                         WHERE r.author='lianxin' AND r.echo_of=n.id
                     )
                   ORDER BY j.scheduled_at ASC, j.note_id ASC LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                return {}
            expires = now + max(1, int(lease_seconds))
            conn.execute(
                """UPDATE tree_reply_jobs
                   SET status='running', attempts=attempts+1,
                       lease_owner=?, lease_expires_at=?, updated_at=?
                   WHERE note_id=? AND status IN ('waiting','retrying')""",
                (owner, expires, _now(), int(row["note_id"])),
            )
            claimed = conn.execute(
                "SELECT * FROM tree_reply_jobs WHERE note_id = ?", (int(row["note_id"]),)
            ).fetchone()
        return dict(claimed) if claimed else {}

    def mark_tree_reply_running(self, note_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE tree_reply_jobs SET status='running', attempts=attempts+1,
                   lease_owner='legacy-bridge', lease_expires_at=?, updated_at=? WHERE note_id=?""",
                (time.time() + 300, _now(), int(note_id)),
            )

    def mark_tree_reply_result(self, note_id: int, success: bool, error: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE tree_reply_jobs SET status=?, last_error=?,
                   lease_owner='', lease_expires_at=0, updated_at=? WHERE note_id=?""",
                ("done" if success else "failed", str(error or ""), _now(), int(note_id)),
            )

    def reschedule_tree_reply(self, note_id: int, scheduled_at: float, error: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tree_reply_jobs SET status='retrying', scheduled_at=?, last_error=?, updated_at=? WHERE note_id=?",
                (float(scheduled_at), str(error or ""), _now(), int(note_id)),
            )

    def add_tree_reply_if_missing(self, note_id: int, content: str) -> dict:
        """Create one reply and its unread notification atomically."""
        text = str(content or "").strip()
        if not text:
            return {}
        with self._connect() as conn:
            source = conn.execute(
                "SELECT id FROM tree_hole_notes WHERE id=? AND author='user'", (int(note_id),)
            ).fetchone()
            if not source:
                return {}
            existing = conn.execute(
                "SELECT * FROM tree_hole_notes WHERE author='lianxin' AND echo_of=? ORDER BY id DESC LIMIT 1",
                (int(note_id),),
            ).fetchone()
            if existing:
                return dict(existing)
            cur = conn.execute(
                """INSERT INTO tree_hole_notes(author, content, echo_of, created_at)
                   VALUES ('lianxin', ?, ?, ?)""",
                (text[:800], int(note_id), _now()),
            )
            reply_id = int(cur.lastrowid)
            self._insert_tree_notification(
                conn, reply_id, int(note_id), "reply", f"reply:{reply_id}"
            )
            row = conn.execute("SELECT * FROM tree_hole_notes WHERE id=?", (reply_id,)).fetchone()
        return dict(row) if row else {}

    @staticmethod
    def _insert_tree_notification(conn, note_id: int, related_note_id: int | None,
                                  notification_type: str, unique_key: str) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO tree_hole_notifications(
                   note_id, related_note_id, notification_type, created_at, unique_key
               ) VALUES (?, ?, ?, ?, ?)""",
            (int(note_id), related_note_id, str(notification_type), _now(), str(unique_key)),
        )

    def tree_unread_counts(self) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS count FROM tree_hole_notifications WHERE read_at=''"
            ).fetchone()["count"]
            replies = conn.execute(
                """SELECT COUNT(*) AS count FROM tree_hole_notifications
                   WHERE read_at='' AND notification_type='reply'"""
            ).fetchone()["count"]
        return {"tree_unread_count": int(total or 0), "tree_reply_unread_count": int(replies or 0)}

    def mark_tree_notifications_read(self, note_id: int | None = None,
                                     notification_type: str | None = None) -> int:
        clauses = ["read_at=''"]
        params: list = []
        if note_id is not None:
            clauses.append("note_id=?")
            params.append(int(note_id))
        if notification_type:
            clauses.append("notification_type=?")
            params.append(str(notification_type))
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE tree_hole_notifications SET read_at=? WHERE {' AND '.join(clauses)}",
                [_now(), *params],
            )
            return int(cur.rowcount or 0)

    def mark_tree_thread_read(self, source_note_id: int) -> int:
        """Mark only notifications visible in one paper thread."""
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE tree_hole_notifications SET read_at=?
                   WHERE read_at='' AND (note_id=? OR related_note_id=?)""",
                (_now(), int(source_note_id), int(source_note_id)),
            )
            return int(cur.rowcount or 0)

    def toggle_tree_favorite(self, note_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE tree_hole_notes
                   SET favorite = CASE favorite WHEN 0 THEN 1 ELSE 0 END WHERE id = ?""",
                (int(note_id),),
            )

    def timeline(self, limit: int = 120) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM capsule_days
                   WHERE user_content <> '' OR lianxin_content <> '' OR sealed = 1
                   ORDER BY date DESC LIMIT ?""",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [self._day_dict(row) for row in rows]

    def timeline_page(self, page: int = 1, page_size: int = 15,
                      author: str = "all", query: str = "",
                      start_date: str = "", end_date: str = "") -> dict:
        page_size = max(5, min(50, int(page_size)))
        author = author if author in {"user", "lianxin"} else "all"
        query = str(query or "").strip()
        try:
            start_date = date.fromisoformat(str(start_date or "")).isoformat() if start_date else ""
        except ValueError:
            start_date = ""
        try:
            end_date = date.fromisoformat(str(end_date or "")).isoformat() if end_date else ""
        except ValueError:
            end_date = ""
        if start_date and end_date and start_date > end_date:
            start_date, end_date = end_date, start_date

        def build_select(author_name: str, content_column: str, author_order: int):
            conditions = [f"{content_column} <> ''"]
            params = []
            if query:
                like = f"%{query}%"
                conditions.append(f"({content_column} LIKE ? OR weather LIKE ?)")
                params.extend([like, like])
            if start_date:
                conditions.append("date >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("date <= ?")
                params.append(end_date)
            sql = f"""SELECT date, '{author_name}' AS author, {content_column} AS content,
                                 weather, sealed, favorite, favorited_at, updated_at,
                                 {author_order} AS author_order
                          FROM capsule_days WHERE {' AND '.join(conditions)}"""
            return sql, params

        user_select, user_params = build_select("user", "user_content", 0)
        lianxin_select, lianxin_params = build_select("lianxin", "lianxin_content", 1)
        if author == "user":
            entries_sql = user_select
            query_params = user_params
        elif author == "lianxin":
            entries_sql = lianxin_select
            query_params = lianxin_params
        else:
            entries_sql = f"{user_select} UNION ALL {lianxin_select}"
            query_params = [*user_params, *lianxin_params]
        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM ({entries_sql})", query_params
            ).fetchone()
            total_items = int(total_row["count"] if total_row else 0)
            total_pages = max(1, (total_items + page_size - 1) // page_size)
            page = max(1, min(int(page), total_pages))
            rows = conn.execute(
                f"""SELECT * FROM ({entries_sql})
                    ORDER BY date DESC, author_order ASC LIMIT ? OFFSET ?""",
                [*query_params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [
                {
                    **dict(row),
                    "sealed": bool(row["sealed"]),
                    "favorite": bool(row["favorite"]),
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "author": author,
            "query": query,
            "start_date": start_date,
            "end_date": end_date,
        }

    def favorite_diaries_page(self, page: int = 1, page_size: int = 12,
                              query: str = "", sort: str = "favorite") -> dict:
        page_size = max(6, min(48, int(page_size)))
        term = str(query or "").strip()
        where = ["favorite = 1", "(user_content <> '' OR lianxin_content <> '')"]
        params: list = []
        if term:
            where.append("(user_content LIKE ? OR lianxin_content LIKE ? OR weather LIKE ?)")
            like = f"%{term}%"
            params.extend([like, like, like])
        order = "date DESC" if sort == "date" else "favorited_at DESC, date DESC"
        where_sql = " AND ".join(where)
        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM capsule_days WHERE {where_sql}", params
            ).fetchone()
            total_items = int(total_row["count"] if total_row else 0)
            total_pages = max(1, (total_items + page_size - 1) // page_size)
            page = max(1, min(int(page), total_pages))
            rows = conn.execute(
                f"""SELECT * FROM capsule_days WHERE {where_sql}
                    ORDER BY {order} LIMIT ? OFFSET ?""",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        items = []
        for row in rows:
            day = self._day_dict(row)
            content = str(day.get("user_content") or day.get("lianxin_content") or "")
            items.append({
                **day,
                "title": self._memory_title(day.get("date", ""), content),
                "description": " ".join(content.split())[:180],
                "source_date": day.get("date", ""),
            })
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "query": term,
            "sort": sort,
        }

    def tree_notes(self, limit: int = 100) -> list[dict]:
        return self.tree_notes_page(1, limit).get("items", [])

    def tree_notes_page(self, page: int = 1, page_size: int = 20,
                        filter_name: str = "all", query: str = "",
                        sort: str = "newest", archived: bool = False) -> dict:
        page_size = max(5, min(50, int(page_size)))
        where = ["echo_of IS NULL", "archived = ?"]
        params: list = [int(bool(archived))]
        if filter_name == "favorite":
            where.append("favorite = 1")
        elif filter_name == "user":
            where.append("author = 'user'")
        elif filter_name == "replied":
            where.append("EXISTS (SELECT 1 FROM tree_hole_notes r WHERE r.echo_of = tree_hole_notes.id)")
        term = str(query or "").strip()
        if term:
            where.append("content LIKE ?")
            params.append(f"%{term}%")
        where_sql = " AND ".join(where)
        order = "created_at ASC, id ASC" if sort == "oldest" else "created_at DESC, id DESC"
        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM tree_hole_notes WHERE {where_sql}", params
            ).fetchone()
            total_items = int(total_row["count"] if total_row else 0)
            total_pages = max(1, (total_items + page_size - 1) // page_size)
            page = max(1, min(int(page), total_pages))
            rows = conn.execute(
                f"""SELECT * FROM tree_hole_notes WHERE {where_sql}
                    ORDER BY {order} LIMIT ? OFFSET ?""",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            replies = {}
            if ids:
                placeholders = ",".join("?" for _ in ids)
                reply_rows = conn.execute(
                    f"""SELECT * FROM tree_hole_notes
                        WHERE author = 'lianxin' AND echo_of IN ({placeholders})
                        ORDER BY id DESC""",
                    ids,
                ).fetchall()
                for reply in reply_rows:
                    replies.setdefault(int(reply["echo_of"]), dict(reply))
        result = []
        for row in rows:
            item = dict(row)
            item["favorite"] = bool(item["favorite"])
            item["opened"] = bool(item["opened"])
            item["archived"] = bool(item.get("archived"))
            item["reply"] = replies.get(int(item["id"]))
            result.append(item)
        return {
            "items": result,
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "filter": filter_name,
            "query": term,
            "sort": sort,
            "archived": bool(archived),
            **self.tree_unread_counts(),
        }

    def toggle_tree_archive(self, note_id: int) -> dict:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE tree_hole_notes
                   SET archived = CASE archived WHEN 0 THEN 1 ELSE 0 END,
                       archived_at = CASE archived WHEN 0 THEN ? ELSE '' END
                   WHERE id = ? AND echo_of IS NULL""",
                (now, int(note_id)),
            )
        return self.get_tree_note(int(note_id))

    def memories(self, limit: int = 100) -> list[dict]:
        return self.favorite_diaries_page(1, limit).get("items", [])

    def recent_collections(self, days: int = 7, limit: int = 20) -> list[dict]:
        start = (date.today() - timedelta(days=max(1, int(days)) - 1)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM capsule_collections WHERE capsule_date >= ? AND kind = 'photo'
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (start, max(1, min(100, int(limit)))),
            ).fetchall()
        return [self._collection_dict(row) for row in rows]

    def contribution(self, weeks: int = 53) -> dict:
        today = date.today()
        start = today - timedelta(days=today.weekday() + (max(1, weeks) - 1) * 7)
        with self._connect() as conn:
            capsule_rows = conn.execute(
                """SELECT date,
                          (CASE WHEN user_content <> '' THEN 1 ELSE 0 END +
                           CASE WHEN lianxin_content <> '' THEN 1 ELSE 0 END +
                           sealed) AS activity
                   FROM capsule_days WHERE date >= ? AND date <= ?""",
                (start.isoformat(), today.isoformat()),
            ).fetchall()
            trace_rows = conn.execute(
                """SELECT capsule_date AS date, COUNT(*) AS total FROM capsule_traces
                   WHERE capsule_date >= ? AND capsule_date <= ? GROUP BY capsule_date""",
                (start.isoformat(), today.isoformat()),
            ).fetchall()
            collection_rows = conn.execute(
                """SELECT capsule_date AS date, COUNT(*) AS total FROM capsule_collections
                   WHERE capsule_date >= ? AND capsule_date <= ? GROUP BY capsule_date""",
                (start.isoformat(), today.isoformat()),
            ).fetchall()
        activity = {row["date"]: int(row["activity"]) for row in capsule_rows}
        for rows in (trace_rows, collection_rows):
            for row in rows:
                activity[row["date"]] = activity.get(row["date"], 0) + int(row["total"])
        days = []
        running = longest = 0
        for offset in range(weeks * 7):
            current = start + timedelta(days=offset)
            value = activity.get(current.isoformat(), 0)
            days.append({"date": current.isoformat(), "value": value, "future": current > today})
            if current <= today and value:
                running += 1
                longest = max(longest, running)
            elif current <= today:
                running = 0
        current_streak = 0
        cursor = today
        while activity.get(cursor.isoformat(), 0):
            current_streak += 1
            cursor -= timedelta(days=1)
        return {
            "days": days,
            "active_days": sum(1 for value in activity.values() if value),
            "longest_streak": longest,
            "current_streak": current_streak,
        }

    def search(self, query: str, limit: int = 80) -> list[dict]:
        term = f"%{str(query or '').strip()}%"
        if term == "%%":
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT date, user_content, lianxin_content, weather, sealed
                   FROM capsule_days d
                   WHERE d.user_content LIKE ? OR d.lianxin_content LIKE ?
                      OR d.weather LIKE ?
                      OR EXISTS (
                          SELECT 1 FROM capsule_traces t
                          WHERE t.capsule_date = d.date AND t.content LIKE ?
                      )
                      OR EXISTS (
                          SELECT 1 FROM capsule_collections c
                          WHERE c.capsule_date = d.date AND (c.title LIKE ? OR c.uri LIKE ?)
                      )
                      OR EXISTS (
                          SELECT 1 FROM capsule_memories m
                          WHERE m.source_date = d.date
                            AND (m.title LIKE ? OR m.description LIKE ?)
                      )
                   ORDER BY date DESC LIMIT ?""",
                (
                    term, term, term, term, term, term, term, term,
                    max(1, min(200, int(limit))),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def initial_state(self, today: str | None = None) -> dict:
        today = today or date.today().isoformat()
        timeline = self.timeline()
        return {
            "today": self.get_day(today),
            "timeline": timeline,
            "contribution": self.contribution(),
            "recent_collections": self.recent_collections(),
            "tree_notes": self.tree_notes(),
            "memories": self.memories(),
            "companion": self.companion_state(timeline),
        }

    def timeline_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS count FROM capsule_days
                   WHERE user_content <> '' OR lianxin_content <> '' OR sealed = 1"""
            ).fetchone()
        return int(row["count"] if row else 0)

    def companion_state(self, timeline: list[dict] | None = None) -> dict:
        timeline = timeline if timeline is not None else self.timeline(10)
        latest = timeline[0] if timeline else {}
        content = str(latest.get("lianxin_content") or latest.get("user_content") or "").strip()
        message = "今天的书页还很安静。我在这里，等你一起留下新的故事。"
        if content:
            message = f"我刚刚又想起了 {latest.get('date', '那一天')}。有些时间被写下来以后，就不会轻易走丢了。"
        return {
            "message": message,
            "recent": [
                {"date": item.get("date", ""), "title": self._memory_title(item.get("date", ""),
                 str(item.get("user_content") or item.get("lianxin_content") or ""))}
                for item in timeline[:3]
            ],
        }

    def migrate_legacy_diaries(self) -> dict:
        sources = self.legacy_sources or [
            get_legacy_memory_dir() / "diary.db", get_user_data_dir() / "diary.db"
        ]
        imported = 0
        for source in sources:
            if not source.exists() or source.resolve() == self.path.resolve():
                continue
            key = f"legacy_diary:{source.resolve()}"
            with self._connect() as conn:
                if conn.execute(
                    "SELECT 1 FROM capsule_migrations WHERE migration_key = ?", (key,)
                ).fetchone():
                    continue
            try:
                legacy = sqlite3.connect(str(source), timeout=5)
                legacy.row_factory = sqlite3.Row
                rows = legacy.execute("SELECT * FROM diary ORDER BY date").fetchall()
                legacy.close()
            except sqlite3.Error:
                continue
            for row in rows:
                payload = dict(row)
                self.save_lianxin_content(
                    str(payload.get("date", "")), str(payload.get("content", "")),
                    weather=str(payload.get("weather", "") or ""),
                    is_red_line=bool(payload.get("is_red_line", 0)),
                    echo_text=str(payload.get("echo_text", "") or ""),
                    source={"legacy_diary": str(source)},
                )
                if int(payload.get("status", 1) or 0):
                    self.seal_day(str(payload.get("date", "")))
                imported += 1
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO capsule_migrations
                       (migration_key, completed_at, details_json) VALUES (?, ?, ?)""",
                    (key, _now(), json.dumps({"rows": len(rows)}, ensure_ascii=False)),
                )
        return {"imported": imported}
