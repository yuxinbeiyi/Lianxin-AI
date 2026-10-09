"""Cross-feature interaction events used by diary generation and recall."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from memory.sqlite_coordination import connect_database

from utils.paths import get_user_data_dir


EVENT_DB_PATH = get_user_data_dir() / "interaction_events.db"
_SCHEMA_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def classify_importance(event_type: str, content: str = "", metadata: dict | None = None) -> str:
    """Deterministic first-pass ranking; the model is not asked to score every event."""
    event = str(event_type or "")
    text = str(content or "")
    meta = metadata or {}
    if event in {"user_diary_sealed", "tree_reply_finished", "focus_completed"}:
        return "important"
    if any(token in text for token in ("记住", "决定", "计划", "目标", "第一次", "重要")):
        return "important"
    if event in {"tree_note_created", "diary_saved", "focus_interrupted"}:
        return "normal"
    if int(meta.get("duration_seconds", 0) or 0) >= 25 * 60:
        return "important"
    return "normal"


class InteractionEventStore:
    """Small SQLite event store with idempotent writes and bounded queries."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or EVENT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = connect_database(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with _SCHEMA_LOCK, self._connection() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS interaction_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    feature TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    importance TEXT NOT NULL DEFAULT 'normal',
                    visibility TEXT NOT NULL DEFAULT 'private',
                    searchable INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_interaction_date "
                "ON interaction_events(local_date, occurred_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_interaction_feature "
                "ON interaction_events(feature, event_type, occurred_at DESC)"
            )
            conn.commit()

    def record(
        self,
        *,
        feature: str,
        event_type: str,
        content: str = "",
        summary: str = "",
        local_date: str | None = None,
        occurred_at: str | None = None,
        source_id: str | int = "",
        importance: str | None = None,
        visibility: str = "private",
        searchable: bool = True,
        metadata: dict | None = None,
        event_key: str | None = None,
    ) -> int:
        occurred = occurred_at or _now()
        day = local_date or occurred[:10]
        source = str(source_id or "")
        if event_key is None:
            digest = hashlib.sha256(
                "|".join((feature, event_type, day, source, content)).encode("utf-8")
            ).hexdigest()
            event_key = f"{feature}:{event_type}:{digest}"
        importance = importance if importance in {"important", "normal", "noise"} else classify_importance(event_type, content, metadata)
        with self._connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO interaction_events
                (event_key, feature, event_type, local_date, occurred_at,
                 source_id, content, summary, importance, visibility,
                 searchable, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_key, feature, event_type, day, occurred, source,
                 str(content or ""), str(summary or ""), importance,
                 visibility, int(bool(searchable)),
                 json.dumps(metadata or {}, ensure_ascii=False), _now()),
            )
            row = conn.execute(
                "SELECT id FROM interaction_events WHERE event_key = ?", (event_key,)
            ).fetchone()
            conn.commit()
        return int(row["id"])

    def list_for_date(self, local_date: str, *, limit: int = 100) -> list[dict]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM interaction_events WHERE local_date = ? "
                "AND searchable = 1 ORDER BY occurred_at ASC, id ASC LIMIT ?",
                (local_date, max(1, min(int(limit), 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, query: str, *, limit: int = 8) -> list[dict]:
        term = f"%{str(query or '').strip()}%"
        if term == "%%":
            return []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM interaction_events WHERE searchable = 1 "
                "AND (content LIKE ? OR summary LIKE ? OR metadata_json LIKE ?) "
                "ORDER BY importance = 'important' DESC, occurred_at DESC LIMIT ?",
                (term, term, term, max(1, min(int(limit), 50))),
            ).fetchall()
        return [dict(row) for row in rows]


    def list_events(
        self,
        *,
        feature: str | None = None,
        event_type: str | None = None,
        source_id: str | None = None,
        limit: int = 2000,
    ) -> list[dict]:
        """Read events in chronological order, optionally filtered by feature/type."""
        clauses: list[str] = []
        params: list[object] = []
        if feature:
            clauses.append("feature = ?")
            params.append(str(feature))
        if event_type:
            clauses.append("event_type = ?")
            params.append(str(event_type))
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(str(source_id))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit), 50000)))
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM interaction_events" + where
                + " ORDER BY occurred_at ASC, id ASC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def event_exists(self, event_key: str) -> bool:
        """Return True when an event with this idempotency key already exists."""
        if not event_key:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM interaction_events WHERE event_key = ? LIMIT 1",
                (str(event_key),),
            ).fetchone()
        return row is not None


def record_interaction(**kwargs) -> int:
    return InteractionEventStore().record(**kwargs)
