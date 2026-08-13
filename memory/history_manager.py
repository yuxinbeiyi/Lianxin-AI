"""
HistoryManager：会话历史管理（SQLite）
负责创建会话、保存消息、读取历史记录。
数据库路径：memory/conversations.db

线程安全说明：
- 每个线程通过 threading.local() 获得独立的 SQLite 连接
- WAL 模式允许多个连接并发读取，写入时自动等待 busy_timeout
- 不需要外部锁，sqlite3 模块内部序列化对单个连接的操作
"""

import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
import json

from memory.sqlite_coordination import connect_database, get_database_lock

_DB_PATH = Path(__file__).parent / "conversations.db"
_local = threading.local()


def _get_connection(db_path: Path = _DB_PATH) -> sqlite3.Connection:
    """为当前线程获取（或创建）独立的 SQLite 连接。"""
    key = str(db_path.resolve())
    if not hasattr(_local, "connections"):
        _local.connections = {}
    conn = _local.connections.get(key)
    if conn is None:
        with get_database_lock(db_path):
            conn = connect_database(key, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=2000")     # 写冲突最多等 2 秒（太长会导致界面卡顿，太短会频繁 SQLITE_BUSY）
            conn.execute("PRAGMA wal_autocheckpoint=500")
            conn.execute("PRAGMA synchronous=NORMAL")     # WAL 模式下 NORMAL 安全且更快
            _init_db(conn)
            _local.connections[key] = conn
    return conn


def _init_db(conn: sqlite3.Connection):
    """初始化数据库表结构（首次运行时创建），并执行迁移。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT    NOT NULL DEFAULT '新对话',
            created_at TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role       TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            timestamp  TEXT    NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
    """)
    conn.commit()
    _migrate_db(conn)


def _migrate_db(conn: sqlite3.Connection):
    """安全为 sessions 表添加新列（幂等，已存在时忽略）。"""
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    needs_memory_v3 = "updated_at" not in existing_columns
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    needs_context_v2 = "compression_snapshots" not in existing_tables
    if needs_memory_v3 or needs_context_v2:
        message_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        db_row = conn.execute("PRAGMA database_list").fetchone()
        if message_count and db_row and db_row[2]:
            db_path = Path(db_row[2])
            backup_names = []
            if needs_memory_v3:
                backup_names.append(f"{db_path.stem}.pre-memory-v3.db")
            if needs_context_v2:
                backup_names.append(f"{db_path.stem}.pre-context-v2.db")
            for backup_name in backup_names:
                backup_path = db_path.with_name(backup_name)
                if not backup_path.exists():
                    backup_conn = sqlite3.connect(str(backup_path))
                    try:
                        conn.backup(backup_conn)
                    finally:
                        backup_conn.close()

    for sql in (
        "ALTER TABLE sessions ADD COLUMN summary   TEXT    DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN is_pinned INTEGER DEFAULT 0",
        "ALTER TABLE sessions ADD COLUMN updated_at TEXT DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN channel TEXT DEFAULT 'desktop'",
        "ALTER TABLE sessions ADD COLUMN participant_id TEXT DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN owner_scope INTEGER DEFAULT 1",
    ):
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # 列已存在，跳过

    # 旧数据没有 updated_at，用最后一条消息时间回填；空会话退回创建时间。
    conn.execute("""
        UPDATE sessions
        SET updated_at = COALESCE(
            (SELECT MAX(timestamp) FROM messages WHERE session_id = sessions.id),
            created_at
        )
        WHERE updated_at IS NULL OR updated_at = ''
    """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS compression_snapshots (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id            INTEGER NOT NULL,
            summary               TEXT    NOT NULL,
            covered_message_count INTEGER NOT NULL DEFAULT 0,
            covered_user_turns    INTEGER NOT NULL DEFAULT 0,
            model                 TEXT    DEFAULT '',
            persona_id            TEXT    DEFAULT '',
            persona_revision      INTEGER DEFAULT 0,
            trigger               TEXT    DEFAULT '',
            input_tokens          INTEGER DEFAULT 0,
            created_at            TEXT    NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_channel ON sessions(channel);
        CREATE INDEX IF NOT EXISTS idx_sessions_owner_scope ON sessions(owner_scope);
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
        CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp
            ON messages(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_compression_snapshots_session_id
            ON compression_snapshots(session_id, id);
    """)
    conn.commit()


class HistoryManager:
    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _DB_PATH
        self._write_lock = get_database_lock(self._db_path)

    @property
    def db_path(self) -> Path:
        """Return the database path shared by history and memory pipelines."""
        return self._db_path

    # ── 会话管理 ─────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接。"""
        return _get_connection(self._db_path)

    def new_session(self, channel: str = "desktop", participant_id: str = "",
                    owner_scope: bool = True) -> int:
        """创建新会话，返回 session_id。"""
        with self._write_lock:
            conn = self._conn()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                """INSERT INTO sessions
                   (title, created_at, updated_at, channel, participant_id, owner_scope)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("新对话", now, now, channel, str(participant_id), int(owner_scope))
            )
            conn.commit()
            return cur.lastrowid

    def update_title(self, session_id: int, title: str):
        """更新会话标题。"""
        with self._write_lock:
            conn = self._conn()
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title[:30], session_id)
            )
            conn.commit()

    # ── 消息管理 ─────────────────────────────────────────────

    def save_message(self, session_id: int, role: str, content: str) -> int:
        """保存一条消息（role: 'user' | 'assistant'）。"""
        with self._write_lock:
            conn = self._conn()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now)
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id)
            )
            conn.commit()
            return int(cur.lastrowid)

    # ── 读取接口 ─────────────────────────────────────────────

    def get_last_session_id(self) -> int | None:
        """返回最近活跃会话的 ID，若无历史则返回 None。"""
        conn = self._conn()
        cur = conn.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC, id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row[0] if row else None

    def get_latest_session_id(self, *, channel: str | None = None,
                              owner_only: bool = True,
                              exclude_session_ids: set[int] | None = None) -> int | None:
        """按最后活动时间获取会话，不受 UI 置顶状态影响。"""
        sql = "SELECT id FROM sessions WHERE 1=1"
        params: list = []
        if channel:
            sql += " AND channel = ?"
            params.append(channel)
        if owner_only:
            sql += " AND owner_scope = 1"
        excluded = sorted(exclude_session_ids or set())
        if excluded:
            sql += f" AND id NOT IN ({','.join('?' for _ in excluded)})"
            params.extend(excluded)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT 1"
        row = self._conn().execute(sql, params).fetchone()
        return int(row[0]) if row else None

    def update_session_metadata(self, session_id: int, *, channel: str,
                                participant_id: str = "",
                                owner_scope: bool = True) -> None:
        self._conn().execute(
            """UPDATE sessions SET channel=?, participant_id=?, owner_scope=?
               WHERE id=?""",
            (channel, str(participant_id), int(owner_scope), session_id),
        )
        self._conn().commit()

    def sync_legacy_channel_maps(self) -> None:
        """用旧版 QQ/微信映射为历史 session 回填来源和隐私边界。"""
        memory_dir = self._db_path.parent
        try:
            from config import get_qq_bridge_config, get_wechat_bridge_config
            owner_qq = str(get_qq_bridge_config().get("owner_qq", ""))
            owner_wechat = str(get_wechat_bridge_config().get("owner_id", ""))
        except Exception:
            owner_qq = owner_wechat = ""

        updates: list[tuple[str, str, int, int]] = []
        qq_path = memory_dir / "qq_session_map.json"
        try:
            data = json.loads(qq_path.read_text(encoding="utf-8")) if qq_path.exists() else {}
            for key, session_id in data.items():
                parts = str(key).split("_")
                is_group = str(key).startswith("qq_group_")
                participant = parts[-1] if parts else ""
                channel = "qq_group" if is_group else "qq_private"
                updates.append((channel, participant, int(bool(owner_qq and participant == owner_qq)), int(session_id)))
        except Exception:
            pass

        wechat_path = memory_dir / "wechat_session_map.json"
        try:
            data = json.loads(wechat_path.read_text(encoding="utf-8")) if wechat_path.exists() else {}
            for key, session_id in data.items():
                is_group = not str(key).startswith("private:")
                participant = str(key).split(":")[-1]
                channel = "wechat_group" if is_group else "wechat_private"
                updates.append((channel, participant, int(bool(owner_wechat and participant == owner_wechat)), int(session_id)))
        except Exception:
            pass

        if updates:
            self._conn().executemany(
                """UPDATE sessions SET channel=?, participant_id=?, owner_scope=?
                   WHERE id=?""",
                updates,
            )
            self._conn().commit()

    def get_session(self, session_id: int) -> dict | None:
        row = self._conn().execute(
            """SELECT id, title, created_at, updated_at, channel, participant_id,
                      owner_scope, summary, is_pinned
               FROM sessions WHERE id=?""",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_session_title(self, session_id: int, new_title: str):
        """重命名会话标题（供历史对话框双击修改使用）。"""
        conn = self._conn()
        conn.execute(
            "UPDATE sessions SET title = ? WHERE id = ?",
            (new_title[:50], session_id)
        )
        conn.commit()

    def get_sessions(self) -> list[dict]:
        """返回所有会话列表：置顶优先，其余按时间倒序。"""
        conn = self._conn()
        cur = conn.execute(
            "SELECT id, title, created_at, updated_at, channel, participant_id, "
            "owner_scope, summary, is_pinned "
            "FROM sessions ORDER BY is_pinned DESC, updated_at DESC, id DESC"
        )
        return [dict(row) for row in cur.fetchall()]
    def get_sessions_by_date(self, date_str: str) -> list[dict]:
        """返回指定日期创建的所有会话（按创建时间正序）。"""
        conn = self._conn()
        cur = conn.execute(
            "SELECT id, title, created_at, summary, is_pinned "
            "FROM sessions WHERE created_at LIKE ? || '%' ORDER BY id ASC",
            (date_str,)
        )
        return [dict(row) for row in cur.fetchall()]
    def search_sessions(self, keyword: str) -> list[dict]:
        """按关键词搜索标题、摘要、消息内容，返回匹配会话列表。"""
        conn = self._conn()
        kw = f"%{keyword}%"
        cur = conn.execute(
            "SELECT DISTINCT s.id, s.title, s.created_at, s.summary, s.is_pinned "
            "FROM sessions s LEFT JOIN messages m ON m.session_id = s.id "
            "WHERE s.title LIKE ? OR s.summary LIKE ? OR m.content LIKE ? "
            "ORDER BY s.is_pinned DESC, s.id DESC",
            (kw, kw, kw),
        )
        return [dict(row) for row in cur.fetchall()]

    def update_summary(self, session_id: int, summary: str):
        """保存 AI 生成的摘要。"""
        conn = self._conn()
        conn.execute(
            "UPDATE sessions SET summary = ? WHERE id = ?",
            (summary, session_id),
        )
        conn.commit()

    # ── 会话内上下文压缩快照 ────────────────────────────────

    def save_compression_snapshot(
        self, session_id: int, summary: str, covered_message_count: int, *,
        covered_user_turns: int = 0, model: str = "", persona_id: str = "",
        persona_revision: int = 0, trigger: str = "", input_tokens: int = 0,
    ) -> int:
        """保存一份可恢复的会话摘要游标，并限制每个会话的快照数量。"""
        summary = str(summary or "").strip()
        covered_message_count = max(0, int(covered_message_count))
        if not summary or covered_message_count <= 0:
            return 0

        conn = self._conn()
        latest = conn.execute(
            """SELECT id, summary, covered_message_count
               FROM compression_snapshots
               WHERE session_id=? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        if (latest and latest["summary"] == summary
                and latest["covered_message_count"] == covered_message_count):
            return int(latest["id"])

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            """INSERT INTO compression_snapshots (
                   session_id, summary, covered_message_count, covered_user_turns,
                   model, persona_id, persona_revision, trigger, input_tokens, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, summary, covered_message_count,
                max(0, int(covered_user_turns)), str(model or ""),
                str(persona_id or ""), max(0, int(persona_revision or 0)),
                str(trigger or ""), max(0, int(input_tokens or 0)), now,
            ),
        )
        conn.execute(
            """DELETE FROM compression_snapshots
               WHERE session_id=? AND id NOT IN (
                   SELECT id FROM compression_snapshots
                   WHERE session_id=? ORDER BY id DESC LIMIT 20
               )""",
            (session_id, session_id),
        )
        conn.commit()
        return int(cur.lastrowid)

    def get_latest_compression_snapshot(self, session_id: int) -> dict | None:
        row = self._conn().execute(
            """SELECT id, session_id, summary, covered_message_count,
                      covered_user_turns, model, persona_id, persona_revision,
                      trigger, input_tokens, created_at
               FROM compression_snapshots
               WHERE session_id=? ORDER BY id DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def delete_compression_snapshots(self, session_id: int) -> None:
        conn = self._conn()
        conn.execute(
            "DELETE FROM compression_snapshots WHERE session_id=?", (session_id,)
        )
        conn.commit()

    def toggle_pin(self, session_id: int):
        """切换置顶状态。"""
        conn = self._conn()
        conn.execute(
            "UPDATE sessions SET is_pinned = CASE WHEN is_pinned=1 THEN 0 ELSE 1 END "
            "WHERE id = ?",
            (session_id,),
        )
        conn.commit()

    def delete_session(self, session_id: int):
        """删除会话及其所有消息。"""
        conn = self._conn()
        conn.execute("DELETE FROM compression_snapshots WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()

    def get_messages(self, session_id: int, limit: int = None) -> list[dict]:
        """返回指定会话的消息，按时间正序。
        如果指定了 limit，只返回最近 N 条。
        """
        conn = self._conn()
        if limit is not None:
            cur = conn.execute(
                "SELECT role, content, timestamp FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit)
            )
            rows = list(cur.fetchall())
            rows.reverse()
            return [dict(row) for row in rows]
        cur = conn.execute(
            "SELECT role, content, timestamp FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_messages_with_ids(self, session_id: int, limit: int | None = None) -> list[dict]:
        """Return persisted chat messages with stable ids for memory provenance."""
        if limit is not None:
            rows = self._conn().execute(
                """SELECT id, session_id, role, content, timestamp
                   FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?""",
                (session_id, max(1, int(limit))),
            ).fetchall()
            rows = list(rows)
            rows.reverse()
            return [dict(row) for row in rows]
        rows = self._conn().execute(
            """SELECT id, session_id, role, content, timestamp
               FROM messages WHERE session_id=? ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_message_id(self, session_id: int) -> int:
        """Return the newest persisted message id in a session, or zero."""
        row = self._conn().execute(
            "SELECT COALESCE(MAX(id), 0) AS latest_id FROM messages WHERE session_id=?",
            (int(session_id),),
        ).fetchone()
        return int(row["latest_id"] if row else 0)

    def get_messages_by_ids(self, message_ids: list[int]) -> list[dict]:
        """Resolve provenance ids to their original message and channel."""
        clean_ids = []
        for value in message_ids:
            try:
                message_id = int(value)
            except (TypeError, ValueError):
                continue
            if message_id > 0 and message_id not in clean_ids:
                clean_ids.append(message_id)
        if not clean_ids:
            return []
        placeholders = ",".join("?" for _ in clean_ids)
        rows = self._conn().execute(
            f"""SELECT m.id, m.session_id, m.role, m.content, m.timestamp,
                       s.channel, s.participant_id, s.owner_scope
                FROM messages m JOIN sessions s ON s.id=m.session_id
                WHERE m.id IN ({placeholders})""",
            clean_ids,
        ).fetchall()
        by_id = {int(row["id"]): dict(row) for row in rows}
        return [by_id[mid] for mid in clean_ids if mid in by_id]

    @staticmethod
    def _time_bounds(time_range: str) -> tuple[str | None, str | None]:
        now = datetime.now()
        if time_range == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif time_range == "yesterday":
            end = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start = end - timedelta(days=1)
        elif time_range == "7d":
            start, end = now - timedelta(days=7), None
        elif time_range == "30d":
            start, end = now - timedelta(days=30), None
        else:
            return None, None
        fmt = "%Y-%m-%d %H:%M:%S"
        return start.strftime(fmt), end.strftime(fmt) if end else None

    def search_conversation_history(
        self, query: str = "", *, mode: str = "recent", time_range: str = "all",
        channels: list[str] | None = None, participant_id: str | None = None,
        owner_only: bool = True, limit: int = 20,
        exclude_session_id: int | None = None,
    ) -> list[dict]:
        """统一检索会话消息：支持按真实时间回顾和关键词搜索。"""
        sql = (
            "SELECT m.id, m.session_id, m.role, m.content, m.timestamp, "
            "s.channel, s.participant_id, s.title "
            "FROM messages m JOIN sessions s ON s.id=m.session_id WHERE 1=1"
        )
        params: list = []
        if owner_only:
            sql += " AND s.owner_scope=1"
        if channels:
            sql += f" AND s.channel IN ({','.join('?' for _ in channels)})"
            params.extend(channels)
        if participant_id is not None:
            sql += " AND s.participant_id=?"
            params.append(str(participant_id))
        if exclude_session_id is not None:
            sql += " AND s.id<>?"
            params.append(exclude_session_id)
        start, end = self._time_bounds(time_range)
        if start:
            sql += " AND m.timestamp>=?"
            params.append(start)
        if end:
            sql += " AND m.timestamp<?"
            params.append(end)
        query = query.strip()
        if mode == "keyword" and query:
            sql += " AND m.content LIKE ?"
            params.append(f"%{query}%")
        sql += " ORDER BY m.timestamp DESC, m.id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 100)))
        rows = [dict(row) for row in self._conn().execute(sql, params).fetchall()]
        rows.reverse()
        return rows

    def query_other_user_recent(self, *, days: int = 7, per_contact_limit: int = 3,
                                max_contacts: int = 10) -> list[dict]:
        """查询最近有互动的其他用户会话（owner_scope=0），供主人回顾。

        按最后活跃时间倒序返回每个会话及其最近若干条消息，只读不改写。
        """
        conn = self._conn()
        try:
            days = max(1, min(int(days), 365))
        except (TypeError, ValueError):
            days = 7
        try:
            per_contact_limit = max(1, min(int(per_contact_limit), 10))
        except (TypeError, ValueError):
            per_contact_limit = 3
        try:
            max_contacts = max(1, min(int(max_contacts), 50))
        except (TypeError, ValueError):
            max_contacts = 10
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            """SELECT id, title, channel, participant_id, updated_at
               FROM sessions
               WHERE owner_scope = 0 AND participant_id != ''
                     AND updated_at >= ?
               ORDER BY updated_at DESC, id DESC
               LIMIT ?""",
            (start, max_contacts),
        ).fetchall()
        result = []
        for row in rows:
            msgs = conn.execute(
                """SELECT role, content, timestamp FROM messages
                   WHERE session_id = ? ORDER BY id DESC LIMIT ?""",
                (row["id"], per_contact_limit),
            ).fetchall()
            msgs = list(msgs)
            msgs.reverse()
            result.append({
                "session_id": int(row["id"]),
                "title": row["title"] or "新对话",
                "channel": row["channel"],
                "participant_id": str(row["participant_id"]),
                "updated_at": row["updated_at"],
                "messages": [dict(m) for m in msgs],
            })
        return result

    def get_messages_by_date(self, date_str: str, *, owner_only: bool = True,
                             channels: list[str] | None = None) -> list[dict]:
        """按日期聚合多个会话，供日记等跨窗口功能使用。"""
        sql = (
            "SELECT m.role, m.content, m.timestamp, m.session_id, s.channel "
            "FROM messages m JOIN sessions s ON s.id=m.session_id "
            "WHERE m.timestamp LIKE ?"
        )
        params: list = [f"{date_str}%"]
        if owner_only:
            sql += " AND s.owner_scope=1"
        if channels:
            sql += f" AND s.channel IN ({','.join('?' for _ in channels)})"
            params.extend(channels)
        sql += " ORDER BY m.timestamp ASC, m.id ASC"
        return [dict(row) for row in self._conn().execute(sql, params).fetchall()]

    def get_message_count(self, session_id: int) -> int:
        """返回指定会话的消息数量。"""
        conn = self._conn()
        cur = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        )
        return cur.fetchone()[0]

    def search_session_messages(self, session_id: int, keyword: str, limit: int = 10) -> list[dict]:
        """在指定会话中搜索包含关键词的消息，按时间正序返回。"""
        conn = self._conn()
        kw = f"%{keyword}%"
        cur = conn.execute(
            "SELECT role, content, timestamp FROM messages "
            "WHERE session_id = ? AND content LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, kw, limit)
        )
        rows = list(cur.fetchall())
        rows.reverse()
        return [dict(row) for row in rows]

    # ── 清理 ─────────────────────────────────────────────────

    def close(self):
        """关闭当前线程的数据库连接。"""
        if hasattr(_local, "connections"):
            key = str(self._db_path.resolve())
            conn = _local.connections.pop(key, None)
            if conn is not None:
                conn.close()
