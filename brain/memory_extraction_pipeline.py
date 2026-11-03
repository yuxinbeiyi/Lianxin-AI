"""Persistent, restart-safe background pipeline for automatic memory extraction."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from memory.sqlite_coordination import connect_database
from typing import Callable

import litellm

from brain.graph_memory import ALL_CATEGORIES, ENTITY_TYPES, build_extraction_prompt


logger = logging.getLogger("MemoryExtraction")
_WAL_INIT_LOCK = threading.Lock()


def _json_completion(completion: Callable, **kwargs):
    """Use JSON mode when supported, otherwise keep the same prompt and retry plainly."""
    try:
        return completion(response_format={"type": "json_object"}, **kwargs)
    except Exception as exc:
        message = str(exc).lower()
        unsupported = (
            "grammar-backend" in message
            or "grammar-based generation" in message
            or "response_format" in message and "not support" in message
        )
        if not unsupported:
            raise
        logger.warning("模型服务不支持 JSON 结构化输出，降级为提示词约束: %s", exc)
        return completion(**kwargs)


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_memory_provenance(
    memory: dict, source_rows: list[dict]
) -> tuple[list[int], float, str]:
    """Validate model evidence against persisted user messages in this batch."""
    allowed_user_ids = {
        int(row["id"])
        for row in source_rows
        if row.get("role") == "user" and row.get("id") is not None
    }
    source_message_ids: list[int] = []
    raw_source_ids = memory.get("source_message_ids", [])
    if not isinstance(raw_source_ids, list):
        raw_source_ids = []
    for value in raw_source_ids:
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id in allowed_user_ids and message_id not in source_message_ids:
            source_message_ids.append(message_id)

    try:
        confidence = min(1.0, max(0.0, float(memory.get("confidence", 0.7))))
    except (TypeError, ValueError):
        confidence = 0.7
    if not source_message_ids:
        confidence = min(confidence, 0.5)

    occurred_at = str(memory.get("occurred_at", "") or "").strip()
    if not occurred_at and source_message_ids:
        timestamps = {
            int(row["id"]): row.get("timestamp", "")
            for row in source_rows
            if row.get("id") is not None
        }
        occurred_at = str(timestamps.get(source_message_ids[-1], "") or "")
    return source_message_ids, confidence, occurred_at


@dataclass(frozen=True)
class ExtractionBatch:
    run_id: int
    session_id: int
    from_message_id: int
    to_message_id: int
    attempt: int
    trigger: str
    rows: list[dict]


class MemoryExtractionStore:
    """Own extraction cursors, leases, and auditable run records."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = connect_database(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # SQLite changes journal mode at the database level.  Serialize this
        # transition because scheduler/history dialogs can initialize together.
        with _WAL_INIT_LOCK:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_extraction_state (
                    session_id                 INTEGER PRIMARY KEY,
                    last_processed_message_id  INTEGER NOT NULL DEFAULT 0,
                    active_run_id              INTEGER,
                    lease_expires_at           REAL NOT NULL DEFAULT 0,
                    consecutive_failures       INTEGER NOT NULL DEFAULT 0,
                    next_retry_at              REAL NOT NULL DEFAULT 0,
                    paused_until               REAL NOT NULL DEFAULT 0,
                    pause_reason               TEXT NOT NULL DEFAULT '',
                    last_attempt_at            TEXT NOT NULL DEFAULT '',
                    last_success_at            TEXT NOT NULL DEFAULT '',
                    updated_at                 TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_extraction_runs (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id        INTEGER NOT NULL,
                    from_message_id   INTEGER NOT NULL,
                    to_message_id     INTEGER NOT NULL,
                    attempt           INTEGER NOT NULL DEFAULT 1,
                    trigger           TEXT NOT NULL DEFAULT 'scheduled',
                    status            TEXT NOT NULL,
                    message_count     INTEGER NOT NULL DEFAULT 0,
                    model             TEXT NOT NULL DEFAULT '',
                    facts_created     INTEGER NOT NULL DEFAULT 0,
                    fragments_created INTEGER NOT NULL DEFAULT 0,
                    conflicts_reviewed INTEGER NOT NULL DEFAULT 0,
                    graph_items_created INTEGER NOT NULL DEFAULT 0,
                    input_tokens      INTEGER NOT NULL DEFAULT 0,
                    output_tokens     INTEGER NOT NULL DEFAULT 0,
                    duration_ms       REAL NOT NULL DEFAULT 0,
                    contract_version  TEXT NOT NULL DEFAULT '',
                    graph_fallback_used INTEGER NOT NULL DEFAULT 0,
                    retry_of_run_id   INTEGER,
                    error             TEXT NOT NULL DEFAULT '',
                    started_at        TEXT NOT NULL,
                    finished_at       TEXT NOT NULL DEFAULT '',
                    UNIQUE(session_id, from_message_id, to_message_id, attempt)
                );

                CREATE INDEX IF NOT EXISTS idx_memory_extraction_runs_session
                ON memory_extraction_runs(session_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_extraction_runs_status
                ON memory_extraction_runs(status, started_at);
                """
            )
            existing = {
                row[1] for row in conn.execute("PRAGMA table_info(memory_extraction_state)")
            }
            for column_sql in (
                "ALTER TABLE memory_extraction_state ADD COLUMN next_retry_at REAL NOT NULL DEFAULT 0",
                "ALTER TABLE memory_extraction_state ADD COLUMN paused_until REAL NOT NULL DEFAULT 0",
                "ALTER TABLE memory_extraction_state ADD COLUMN pause_reason TEXT NOT NULL DEFAULT ''",
            ):
                column = column_sql.split()[5]
                if column not in existing:
                    conn.execute(column_sql)
            run_existing = {
                row[1] for row in conn.execute("PRAGMA table_info(memory_extraction_runs)")
            }
            for column_sql in (
                "ALTER TABLE memory_extraction_runs ADD COLUMN contract_version TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE memory_extraction_runs ADD COLUMN graph_fallback_used INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE memory_extraction_runs ADD COLUMN retry_of_run_id INTEGER",
            ):
                column = column_sql.split()[5]
                if column not in run_existing:
                    conn.execute(column_sql)

    def bootstrap_session(self, session_id: int, baseline_message_id: int = 0) -> dict:
        """Create state once; never overwrite a cursor restored after restart."""
        now = _now_text()
        with self._connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO memory_extraction_state
                   (session_id, last_processed_message_id, updated_at)
                   VALUES (?, ?, ?)""",
                (int(session_id), max(0, int(baseline_message_id)), now),
            )
        return self.get_state(session_id)

    def get_state(self, session_id: int) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM memory_extraction_state WHERE session_id=?",
                (int(session_id),),
            ).fetchone()
        return dict(row) if row else {}

    def get_run(self, run_id: int) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM memory_extraction_runs WHERE id=?", (int(run_id),)
            ).fetchone()
        return dict(row) if row else {}

    def list_runs(self, session_id: int | None = None, limit: int = 20) -> list[dict]:
        with self._connection() as conn:
            if session_id is None:
                rows = conn.execute(
                    "SELECT * FROM memory_extraction_runs ORDER BY id DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM memory_extraction_runs
                       WHERE session_id=? ORDER BY id DESC LIMIT ?""",
                    (int(session_id), max(1, int(limit))),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_run_source_messages(self, run_id: int) -> list[dict]:
        """Return the immutable message rows covered by one extraction attempt."""
        run = self.get_run(run_id)
        if not run:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT id,session_id,role,content,timestamp
                   FROM messages
                   WHERE session_id=? AND id BETWEEN ? AND ?
                   ORDER BY id ASC""",
                (
                    int(run["session_id"]),
                    int(run["from_message_id"]),
                    int(run["to_message_id"]),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_pending_summary(self, session_id: int) -> dict:
        """Return persisted gate state and the unprocessed message range."""
        with self._connection() as conn:
            row = conn.execute(
                """SELECT st.*, s.channel, s.owner_scope,
                          COUNT(m.id) AS pending_count,
                          MIN(m.id) AS first_pending_message_id,
                          MAX(m.id) AS last_pending_message_id,
                          MAX(m.timestamp) AS last_message_at
                   FROM memory_extraction_state st
                   JOIN sessions s ON s.id=st.session_id
                   LEFT JOIN messages m
                     ON m.session_id=st.session_id
                    AND m.id>st.last_processed_message_id
                   WHERE st.session_id=?
                   GROUP BY st.session_id""",
                (int(session_id),),
            ).fetchone()
        return dict(row) if row else {}

    def list_pending_sessions(self, *, owner_only: bool = True, limit: int = 50) -> list[dict]:
        """List initialized sessions with pending messages, largest backlog first."""
        sql = """SELECT st.*, s.channel, s.owner_scope,
                        COUNT(m.id) AS pending_count,
                        MIN(m.id) AS first_pending_message_id,
                        MAX(m.id) AS last_pending_message_id,
                        MAX(m.timestamp) AS last_message_at
                 FROM memory_extraction_state st
                 JOIN sessions s ON s.id=st.session_id
                 JOIN messages m
                   ON m.session_id=st.session_id
                  AND m.id>st.last_processed_message_id
                 WHERE 1=1"""
        params: list = []
        if owner_only:
            sql += " AND s.owner_scope=1"
        sql += " GROUP BY st.session_id HAVING COUNT(m.id)>0 ORDER BY pending_count DESC, st.updated_at ASC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def resume_session(self, session_id: int, *, reset_failures: bool = False) -> dict:
        """Clear automatic pause/backoff, usually before an operator retry."""
        now = _now_text()
        with self._connection() as conn:
            conn.execute(
                """UPDATE memory_extraction_state SET
                   next_retry_at=0, paused_until=0, pause_reason='',
                   consecutive_failures=CASE WHEN ? THEN 0 ELSE consecutive_failures END,
                   updated_at=? WHERE session_id=?""",
                (int(bool(reset_failures)), now, int(session_id)),
            )
        return self.get_state(session_id)

    def claim_pending_batch(
        self,
        session_id: int,
        *,
        limit: int = 20,
        min_messages: int = 3,
        trigger: str = "scheduled",
        lease_seconds: int = 300,
        force: bool = False,
    ) -> ExtractionBatch | None:
        """Atomically lease one message range; concurrent claimers get no work."""
        session_id = int(session_id)
        now_epoch = time.time()
        now_text = _now_text()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT OR IGNORE INTO memory_extraction_state
                   (session_id, last_processed_message_id, updated_at)
                   VALUES (?, 0, ?)""",
                (session_id, now_text),
            )
            state = conn.execute(
                "SELECT * FROM memory_extraction_state WHERE session_id=?",
                (session_id,),
            ).fetchone()

            active_run_id = int(state["active_run_id"] or 0)
            if active_run_id:
                active = conn.execute(
                    "SELECT status FROM memory_extraction_runs WHERE id=?",
                    (active_run_id,),
                ).fetchone()
                if active and active["status"] == "running" and float(
                    state["lease_expires_at"] or 0
                ) > now_epoch:
                    conn.commit()
                    return None
                if active and active["status"] == "running":
                    conn.execute(
                        """UPDATE memory_extraction_runs
                           SET status='failed', error='lease expired before completion',
                               finished_at=? WHERE id=?""",
                        (now_text, active_run_id),
                    )
                    conn.execute(
                        """UPDATE memory_extraction_state
                           SET consecutive_failures=consecutive_failures+1
                           WHERE session_id=?""",
                        (session_id,),
                    )
                conn.execute(
                    """UPDATE memory_extraction_state
                       SET active_run_id=NULL, lease_expires_at=0, updated_at=?
                       WHERE session_id=?""",
                    (now_text, session_id),
                )

            state = conn.execute(
                "SELECT * FROM memory_extraction_state WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if not force and (
                float(state["paused_until"] or 0) > now_epoch
                or float(state["next_retry_at"] or 0) > now_epoch
            ):
                conn.commit()
                return None
            cursor = int(state["last_processed_message_id"] or 0)
            rows = conn.execute(
                """SELECT id, session_id, role, content, timestamp
                   FROM messages
                   WHERE session_id=? AND id>?
                   ORDER BY id ASC LIMIT ?""",
                (session_id, cursor, max(1, int(limit))),
            ).fetchall()
            if len(rows) < max(1, int(min_messages)):
                conn.commit()
                return None

            from_message_id = int(rows[0]["id"])
            to_message_id = int(rows[-1]["id"])
            previous_attempt = conn.execute(
                """SELECT COALESCE(MAX(attempt), 0) AS attempt,
                          MAX(CASE WHEN status='failed' THEN id END) AS failed_run_id
                   FROM memory_extraction_runs
                   WHERE session_id=? AND from_message_id=? AND to_message_id=?""",
                (session_id, from_message_id, to_message_id),
            ).fetchone()
            attempt = int(previous_attempt["attempt"] or 0) + 1
            cur = conn.execute(
                """INSERT INTO memory_extraction_runs
                   (session_id, from_message_id, to_message_id, attempt, trigger,
                    status, message_count, retry_of_run_id, started_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)""",
                (
                    session_id,
                    from_message_id,
                    to_message_id,
                    attempt,
                    str(trigger or "scheduled"),
                    len(rows),
                    previous_attempt["failed_run_id"],
                    now_text,
                ),
            )
            run_id = int(cur.lastrowid)
            conn.execute(
                """UPDATE memory_extraction_state
                   SET active_run_id=?, lease_expires_at=?, last_attempt_at=?, updated_at=?
                   WHERE session_id=?""",
                (
                    run_id,
                    now_epoch + max(30, int(lease_seconds)),
                    now_text,
                    now_text,
                    session_id,
                ),
            )
            conn.commit()
            return ExtractionBatch(
                run_id=run_id,
                session_id=session_id,
                from_message_id=from_message_id,
                to_message_id=to_message_id,
                attempt=attempt,
                trigger=str(trigger or "scheduled"),
                rows=[dict(row) for row in rows],
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_success(self, batch: ExtractionBatch, result: dict) -> dict:
        now = _now_text()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT status FROM memory_extraction_runs WHERE id=?", (batch.run_id,)
            ).fetchone()
            if not run or run["status"] != "running":
                conn.commit()
                return self.get_run(batch.run_id)
            conn.execute(
                """UPDATE memory_extraction_runs SET
                   status='success', model=?, facts_created=?, fragments_created=?,
                   conflicts_reviewed=?, graph_items_created=?, input_tokens=?,
                   output_tokens=?, duration_ms=?, contract_version=?,
                   graph_fallback_used=?, finished_at=?, error=''
                   WHERE id=?""",
                (
                    str(result.get("model", "") or ""),
                    int(result.get("facts_created", 0) or 0),
                    int(result.get("fragments_created", 0) or 0),
                    int(result.get("conflicts_reviewed", 0) or 0),
                    int(result.get("graph_items_created", 0) or 0),
                    int(result.get("input_tokens", 0) or 0),
                    int(result.get("output_tokens", 0) or 0),
                    float(result.get("duration_ms", 0) or 0),
                    str(result.get("contract_version", "") or ""),
                    int(bool(result.get("graph_fallback_used", False))),
                    now,
                    batch.run_id,
                ),
            )
            conn.execute(
                """UPDATE memory_extraction_state SET
                   last_processed_message_id=MAX(last_processed_message_id, ?),
                   active_run_id=CASE WHEN active_run_id=? THEN NULL ELSE active_run_id END,
                   lease_expires_at=CASE WHEN active_run_id=? THEN 0 ELSE lease_expires_at END,
                   consecutive_failures=0, next_retry_at=0, paused_until=0,
                   pause_reason='', last_success_at=?, updated_at=?
                   WHERE session_id=?""",
                (
                    batch.to_message_id,
                    batch.run_id,
                    batch.run_id,
                    now,
                    now,
                    batch.session_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_run(batch.run_id)

    def complete_failure(
        self,
        batch: ExtractionBatch,
        error: Exception | str,
        *,
        retry_base_seconds: int = 300,
        retry_max_seconds: int = 3600,
        pause_threshold: int = 5,
        pause_seconds: int = 21600,
    ) -> dict:
        now = _now_text()
        error_text = str(error)[:2000]
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """UPDATE memory_extraction_runs
                   SET status='failed', error=?, finished_at=?
                   WHERE id=? AND status='running'""",
                (error_text, now, batch.run_id),
            )
            if cur.rowcount:
                state = conn.execute(
                    """SELECT consecutive_failures FROM memory_extraction_state
                       WHERE session_id=?""",
                    (batch.session_id,),
                ).fetchone()
                failures = int(state["consecutive_failures"] or 0) + 1
                retry_delay = min(
                    max(1, int(retry_max_seconds)),
                    max(1, int(retry_base_seconds)) * (2 ** max(0, failures - 1)),
                )
                paused_until = 0.0
                pause_reason = ""
                if failures >= max(1, int(pause_threshold)):
                    paused_until = time.time() + max(60, int(pause_seconds))
                    pause_reason = f"连续失败 {failures} 次，已自动暂停"
                conn.execute(
                    """UPDATE memory_extraction_state SET
                       active_run_id=CASE WHEN active_run_id=? THEN NULL ELSE active_run_id END,
                       lease_expires_at=CASE WHEN active_run_id=? THEN 0 ELSE lease_expires_at END,
                       consecutive_failures=?, next_retry_at=?, paused_until=?,
                       pause_reason=?, updated_at=?
                       WHERE session_id=?""",
                    (
                        batch.run_id,
                        batch.run_id,
                        failures,
                        time.time() + retry_delay,
                        paused_until,
                        pause_reason,
                        now,
                        batch.session_id,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_run(batch.run_id)

    def skip_through_latest(self, session_id: int, reason: str = "memory writes blocked") -> int:
        """Advance the cursor without extraction for an explicitly excluded turn."""
        now = _now_text()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            latest = conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM messages WHERE session_id=?",
                (int(session_id),),
            ).fetchone()[0]
            conn.execute(
                """INSERT OR IGNORE INTO memory_extraction_state
                   (session_id, last_processed_message_id, updated_at)
                   VALUES (?, 0, ?)""",
                (int(session_id), now),
            )
            state = conn.execute(
                "SELECT last_processed_message_id FROM memory_extraction_state WHERE session_id=?",
                (int(session_id),),
            ).fetchone()
            cursor = int(state[0] or 0)
            latest = int(latest or 0)
            if latest > cursor:
                conn.execute(
                    """INSERT INTO memory_extraction_runs
                       (session_id, from_message_id, to_message_id, trigger, status,
                        message_count, error, started_at, finished_at)
                       VALUES (?, ?, ?, 'policy_skip', 'skipped', ?, ?, ?, ?)""",
                    (
                        int(session_id), cursor + 1, latest, latest - cursor,
                        str(reason)[:2000], now, now,
                    ),
                )
                conn.execute(
                    """UPDATE memory_extraction_state
                       SET last_processed_message_id=?, updated_at=? WHERE session_id=?""",
                    (latest, now, int(session_id)),
                )
            conn.commit()
            return latest
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _usage_counts(response) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0

    def value(*names: str) -> int:
        for name in names:
            raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if raw is not None:
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    pass
        return 0

    return value("prompt_tokens", "input_tokens"), value(
        "completion_tokens", "output_tokens"
    )


def validate_extraction_contract(payload: object) -> dict:
    """Validate the v2 combined response while exposing graph fallback intent."""
    if not isinstance(payload, dict):
        raise ValueError("memory extraction response must be a JSON object")
    memories = payload.get("memories")
    if not isinstance(memories, list):
        raise ValueError("memory extraction response has no memories list")

    graph_fallback_required = "quintuples" not in payload
    raw_quintuples = payload.get("quintuples", [])
    if not isinstance(raw_quintuples, list):
        raw_quintuples = []
        graph_fallback_required = True
    quintuples = []
    for item in raw_quintuples:
        if not isinstance(item, list) or len(item) != 5:
            graph_fallback_required = True
            continue
        values = [" ".join(str(value or "").split()).strip() for value in item]
        if not all(values):
            graph_fallback_required = True
            continue
        head, head_type, relation, tail, tail_type = values
        if head_type not in ENTITY_TYPES or tail_type not in ENTITY_TYPES:
            graph_fallback_required = True
            continue
        from brain.persona.authority import is_assistant_identity_fact
        if is_assistant_identity_fact(f"{head} {relation} {tail}"):
            continue
        quintuples.append((head, head_type, relation, tail, tail_type))
    return {
        "contract_version": str(payload.get("contract_version", "") or ""),
        "memories": memories,
        "quintuples": quintuples,
        "graph_fallback_required": graph_fallback_required,
        "growth_candidates": payload.get("growth_candidates", []),
    }


class LLMExtractionProcessor:
    """Model-backed processor kept independent from AgentCore lifecycle state."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        api_base: str,
        source_channel: str = "desktop",
        persona_id: str = "",
        graph_enabled: bool = True,
        extract_quintuples: bool = True,
        completion: Callable | None = None,
        quintuple_fallback: Callable | None = None,
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.source_channel = source_channel
        self.persona_id = persona_id
        self.graph_enabled = bool(graph_enabled)
        self.extract_quintuples = bool(extract_quintuples)
        self.completion = completion or litellm.completion
        self.quintuple_fallback = quintuple_fallback

    def __call__(self, batch: ExtractionBatch) -> dict:
        started = time.perf_counter()
        lines = [
            f"[消息#{row['id']}][{'用户' if row['role'] == 'user' else '助手'}]"
            f"[{row.get('timestamp', '')}]: {row.get('content', '')}"
            for row in batch.rows
            if row.get("content")
        ]
        text = "\n".join(lines)
        if len(text) < 30:
            return {"model": self.model, "duration_ms": 0}

        response = _json_completion(
            self.completion,
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个专业的记忆提取助手，从对话中提取值得长期记住的信息。",
                },
                {"role": "user", "content": build_extraction_prompt(text)},
            ],
            temperature=0.1,
            api_key=self.api_key,
            api_base=self.api_base,
            timeout=90,
        )
        raw = response.choices[0].message.content or "{}"
        contract = validate_extraction_contract(json.loads(raw))
        memories = contract["memories"]
        growth_candidates = contract["growth_candidates"]
        if growth_candidates:
            try:
                from brain.persona.growth import get_persona_growth_service
                get_persona_growth_service().ingest_candidates(
                    growth_candidates, batch.rows, persona_id=self.persona_id,
                    source_session_id=batch.session_id, source_channel=self.source_channel,
                )
            except Exception:
                logger.warning("成长候选未写入；保留原有记忆提取流程", exc_info=True)

        from brain.graph_memory import add_fact, add_memory_fragment

        facts_created = 0
        fragments_created = 0
        pending_conflicts: dict[int, list[int]] = {}
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            category = str(memory.get("category", "knowledge"))
            content = str(memory.get("content", "") or "").strip()
            if not content or category not in ALL_CATEGORIES:
                continue
            from brain.persona.authority import is_assistant_identity_fact
            if is_assistant_identity_fact(content):
                # 助手自身设定属于人格枢控，不得混入用户长期记忆。
                continue
            source_ids, confidence, occurred_at = normalize_memory_provenance(
                memory, batch.rows
            )
            fact_id = add_fact(
                content,
                category,
                source="auto_extracted",
                source_session_id=batch.session_id,
                source_channel=self.source_channel,
                occurred_at=occurred_at,
            )
            if not fact_id:
                continue
            facts_created += 1
            fragment_id = add_memory_fragment(
                fact_id,
                content,
                category,
                source="auto_extracted",
                source_session_id=batch.session_id,
                source_channel=self.source_channel,
                source_message_ids=source_ids,
                persona_id=self.persona_id,
                confidence=confidence,
                extraction_model=self.model,
                occurred_at=occurred_at,
            )
            fragments_created += int(bool(fragment_id))
            if fragment_id and source_ids and confidence >= 0.8 and memory.get("associative_triggers"):
                try:
                    from brain.graph_memory import _get_conn
                    from brain.memory_activation_retrieval import store_associative_entries
                    store_associative_entries(
                        _get_conn(), fact_id, content, memory["associative_triggers"],
                    )
                except Exception:
                    logger.warning("Associative entry indexing failed; fact extraction retained", exc_info=True)
            try:
                from brain.memory_conflicts import list_conflict_candidates

                for candidate in list_conflict_candidates(
                    status="pending", fact_id=fact_id, limit=3
                ):
                    pending_conflicts[int(candidate["id"])] = source_ids
            except Exception:
                pass

        input_tokens, output_tokens = _usage_counts(response)
        conflicts_reviewed, review_input, review_output = self._review_conflicts(
            pending_conflicts, batch
        )
        graph_items = 0
        graph_input = 0
        graph_output = 0
        if self.graph_enabled and self.extract_quintuples:
            from brain.graph_memory import store_quintuple

            for head, head_type, relation, tail, tail_type in contract["quintuples"]:
                if store_quintuple(
                    head,
                    head_type,
                    relation,
                    tail,
                    tail_type,
                    source="auto_combined",
                    source_session_id=batch.session_id,
                    source_channel=self.source_channel,
                ):
                    graph_items += 1
            if contract["graph_fallback_required"]:
                fallback = self.quintuple_fallback
                if fallback is None:
                    from brain.quintuple_extractor import extract_and_store_with_config

                    fallback = extract_and_store_with_config
                graph_result = fallback(
                    text,
                    self.model,
                    self.api_key,
                    self.api_base,
                    raise_on_error=True,
                    return_details=True,
                    source_session_id=batch.session_id,
                    source_channel=self.source_channel,
                )
                graph_items += int(graph_result.get("count", 0) or 0)
                graph_input = int(graph_result.get("input_tokens", 0) or 0)
                graph_output = int(graph_result.get("output_tokens", 0) or 0)
        return {
            "model": self.model,
            "facts_created": facts_created,
            "fragments_created": fragments_created,
            "conflicts_reviewed": conflicts_reviewed,
            "graph_items_created": graph_items,
            "input_tokens": input_tokens + review_input + graph_input,
            "output_tokens": output_tokens + review_output + graph_output,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "contract_version": contract["contract_version"] or "legacy-compatible",
            "graph_fallback_used": bool(contract["graph_fallback_required"]),
        }

    def _review_conflicts(
        self, pending: dict[int, list[int]], batch: ExtractionBatch
    ) -> tuple[int, int, int]:
        if not pending:
            return 0, 0, 0
        try:
            from brain.memory_conflicts import (
                DECISIONS,
                get_conflict_candidate,
                resolve_conflict_candidate,
            )

            candidates = [get_conflict_candidate(cid) for cid in pending]
            candidates = [item for item in candidates if item]
            review_payload = [
                {
                    "candidate_id": item["id"],
                    "old_fact": item["existing_content"],
                    "new_fact": item["new_content"],
                    "category": item["category"],
                }
                for item in candidates
            ]
            response = _json_completion(
                self.completion,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是长期记忆语义裁决器。逐对判断：duplicate、complements、"
                            "contradicts、supersedes、unrelated。只有明确纠正、变化或时间先后"
                            "才能使用 supersedes。返回JSON对象 reviews 数组。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(review_payload, ensure_ascii=False)},
                ],
                temperature=0.0,
                api_key=self.api_key,
                api_base=self.api_base,
                timeout=90,
            )
            reviews = json.loads(response.choices[0].message.content or "{}").get(
                "reviews", []
            )
            reviewed = 0
            for review in reviews if isinstance(reviews, list) else []:
                if not isinstance(review, dict):
                    continue
                try:
                    candidate_id = int(review.get("candidate_id"))
                except (TypeError, ValueError):
                    continue
                decision = str(review.get("decision", "")).lower()
                if candidate_id not in pending or decision not in DECISIONS:
                    continue
                resolve_conflict_candidate(
                    candidate_id,
                    decision,
                    confidence=review.get("confidence", 0.0),
                    rationale=review.get("rationale", ""),
                    review_model=self.model,
                    source_session_id=batch.session_id,
                    source_channel=self.source_channel,
                    source_message_ids=pending[candidate_id],
                    persona_id=self.persona_id,
                )
                reviewed += 1
            input_tokens, output_tokens = _usage_counts(response)
            return reviewed, input_tokens, output_tokens
        except Exception as exc:
            logger.warning("自动记忆冲突裁决失败，候选将保留待处理: %s", exc)
            return 0, 0, 0


class MemoryExtractionPipeline:
    """Claim persisted work, process it, and commit or retry transactionally."""

    def __init__(
        self,
        *,
        store: MemoryExtractionStore,
        processor: Callable[[ExtractionBatch], dict],
        max_messages: int = 20,
        min_messages: int = 3,
        lease_seconds: int = 300,
        retry_base_seconds: int = 300,
        retry_max_seconds: int = 3600,
        pause_threshold: int = 5,
        pause_seconds: int = 21600,
    ):
        self.store = store
        self.processor = processor
        self.max_messages = max(1, int(max_messages))
        self.min_messages = max(1, int(min_messages))
        self.lease_seconds = max(30, int(lease_seconds))
        self.retry_base_seconds = max(1, int(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, int(retry_max_seconds))
        self.pause_threshold = max(1, int(pause_threshold))
        self.pause_seconds = max(60, int(pause_seconds))

    def run_once(
        self, session_id: int, trigger: str = "scheduled", *, force: bool = False
    ) -> dict:
        batch = self.store.claim_pending_batch(
            session_id,
            limit=self.max_messages,
            min_messages=self.min_messages,
            trigger=trigger,
            lease_seconds=self.lease_seconds,
            force=force,
        )
        if batch is None:
            return {"status": "skipped", "session_id": int(session_id)}
        try:
            result = dict(self.processor(batch) or {})
            run = self.store.complete_success(batch, result)
            return {"status": "success", "run": run, "result": result}
        except Exception as exc:
            logger.warning(
                "记忆提取运行失败 session=%s run=%s: %s",
                batch.session_id,
                batch.run_id,
                exc,
            )
            run = self.store.complete_failure(
                batch,
                exc,
                retry_base_seconds=self.retry_base_seconds,
                retry_max_seconds=self.retry_max_seconds,
                pause_threshold=self.pause_threshold,
                pause_seconds=self.pause_seconds,
            )
            return {"status": "failed", "run": run, "error": str(exc)}
