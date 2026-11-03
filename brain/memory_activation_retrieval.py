"""Local candidate discovery for optional association; never calls a model."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass

from brain.decision_client import redact_context
from brain.memory_activation import MAX_INSPECTED, MemoryCandidate, _SENSITIVE, filter_candidates


logger = logging.getLogger("MemoryActivation")
MAX_QUERY_CHARS = 1600
MAX_TERMS = 32
MAX_RETRIEVAL_SECONDS = 0.2
PULSE_GAP = 8
_STOP_CHARS = frozenset("我你他她它的了啊呢吗呀吧是有在这那个就都也又很真还没不和与及着过一时会要想说到来去对把被让给得地么们")


@dataclass(frozen=True)
class ActivationResult:
    status: str
    reason: str
    candidates: tuple[MemoryCandidate, ...] = ()
    channels: tuple[str, ...] = ()
    inspected: int = 0
    elapsed_ms: float = 0
    candidate_paths: tuple[tuple[int, str], ...] = ()

    def diagnostics(self) -> dict:
        return {
            "status": self.status, "reason": self.reason,
            "candidate_ids": [item.memory_id for item in self.candidates],
            "channels": list(self.channels), "inspected": self.inspected,
            "candidate_paths": dict(self.candidate_paths),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "decision_called": False, "prompt_injected": False,
        }


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _terms(text: str) -> dict[str, int]:
    value = unicodedata.normalize("NFKC", text).casefold()
    terms = dict.fromkeys(re.findall(r"[a-z0-9_]{2,}", value), 4)
    runs = re.findall(r"[\u4e00-\u9fff]+", value)
    for run in runs:
        for offset in range(len(run) - 1):
            pair = run[offset:offset + 2]
            if not all(char in _STOP_CHARS for char in pair):
                terms[pair] = 4
    for run in runs:
        for char in run:
            if char not in _STOP_CHARS:
                terms.setdefault(char, 1)
    return terms


def ensure_activation_index(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_activation_entries (
            fact_id INTEGER PRIMARY KEY,
            content_hash TEXT NOT NULL,
            cues_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_activation_terms (
            term TEXT NOT NULL,
            fact_id INTEGER NOT NULL,
            weight INTEGER NOT NULL,
            PRIMARY KEY (term, fact_id)
        );
        CREATE INDEX IF NOT EXISTS idx_activation_terms_fact
            ON memory_activation_terms(fact_id);
        CREATE TRIGGER IF NOT EXISTS memory_activation_delete
        AFTER DELETE ON memory_facts BEGIN
            DELETE FROM memory_activation_terms WHERE fact_id=old.id;
            DELETE FROM memory_activation_entries WHERE fact_id=old.id;
        END;
        CREATE TRIGGER IF NOT EXISTS memory_activation_change
        AFTER UPDATE OF content ON memory_facts WHEN new.content<>old.content BEGIN
            DELETE FROM memory_activation_terms WHERE fact_id=old.id;
            DELETE FROM memory_activation_entries WHERE fact_id=old.id;
        END;
    """)


def store_associative_entries(conn, fact_id: int, content: str, entries) -> bool:
    if type(fact_id) is not int or fact_id <= 0 or not isinstance(entries, list):
        return False
    if not 1 <= len(entries) <= 3:
        return False
    cues = []
    for value in entries:
        if not isinstance(value, str):
            return False
        cue = " ".join(value.split())
        if (not 4 <= len(cue) <= 96 or _SENSITIVE.search(cue)
                or redact_context(cue) != cue):
            return False
        if cue not in cues:
            cues.append(cue)
    facts = _load_facts(conn, [fact_id])
    if not facts or facts[0]["content"] != content or not filter_candidates(facts):
        return False
    terms = _terms(" ".join(cues))
    if not terms:
        return False
    conn.execute("SAVEPOINT memory_activation_write")
    try:
        conn.execute("DELETE FROM memory_activation_terms WHERE fact_id=?", (fact_id,))
        conn.execute(
            "INSERT OR REPLACE INTO memory_activation_entries VALUES (?, ?, ?)",
            (fact_id, _digest(content), json.dumps(cues, ensure_ascii=False)),
        )
        conn.executemany(
            "INSERT INTO memory_activation_terms(term,fact_id,weight) VALUES (?,?,?)",
            [(term, fact_id, weight) for term, weight in terms.items()],
        )
        conn.execute("RELEASE memory_activation_write")
    except Exception:
        conn.execute("ROLLBACK TO memory_activation_write")
        conn.execute("RELEASE memory_activation_write")
        raise
    return True


def _load_facts(conn, memory_ids: list[int]) -> list[dict]:
    if not memory_ids:
        return []
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('memory_conflict_candidates','memory_fact_relations')"
    )}
    conflict_checks = []
    if "memory_conflict_candidates" in tables:
        conflict_checks.append("""EXISTS (
            SELECT 1 FROM memory_conflict_candidates c
            WHERE (c.existing_fact_id=f.id OR c.new_fact_id=f.id)
              AND c.status IN ('pending','needs_confirmation'))""")
    if "memory_fact_relations" in tables:
        conflict_checks.append("""EXISTS (
            SELECT 1 FROM memory_fact_relations r
            WHERE (r.source_fact_id=f.id OR r.target_fact_id=f.id)
              AND r.relation IN ('contradicts','supersedes'))""")
    conflicts = " OR ".join(conflict_checks) or "0"
    placeholders = ",".join("?" for _ in memory_ids)
    rows = conn.execute(
        f"""SELECT f.id AS memory_id,f.content,f.source,f.status,f.review_status,
                   f.created_at,f.updated_at,({conflicts}) AS pending_conflicts,
                   (SELECT COUNT(*) FROM memory_fragments mf
                    WHERE mf.fact_id=f.id AND mf.status='active') AS evidence_count
            FROM memory_facts f WHERE f.id IN ({placeholders})""", memory_ids,
    ).fetchall()
    facts = {row["memory_id"]: dict(row) for row in rows}
    return [facts[memory_id] for memory_id in memory_ids if memory_id in facts]


def collect_activation(
    message: str, *, deadline: float, memory_allowed: bool, eligible: bool,
    enabled: bool, force: bool = False, turn: int = 0, last_attempt_turn: int = -PULSE_GAP,
    existing_rows=(), excluded_ids=(), recent_text=(), cancelled=lambda: False,
    snapshot=None, conn=None,
) -> ActivationResult:
    started = time.monotonic()
    channels = []
    inspected = 0
    origins = {}
    force = force is True

    def result(status, reason, candidates=()):
        return ActivationResult(status, reason, tuple(candidates), tuple(channels),
                                inspected, (time.monotonic() - started) * 1000,
                                tuple((item.memory_id, origins[item.memory_id]) for item in candidates))

    if enabled is not True:
        return result("skipped", "disabled")
    if memory_allowed is not True:
        return result("skipped", "memory_not_allowed")
    if cancelled():
        return result("skipped", "cancelled")
    if eligible is not True:
        return result("skipped", "higher_priority_path")
    if (not isinstance(message, str) or not message.strip()
            or len(message) > MAX_QUERY_CHARS):
        return result("skipped", "invalid_message")
    if _SENSITIVE.search(message) or redact_context(message) != message:
        return result("skipped", "sensitive_context")
    if not force and (len(message.strip()) < 8 or not re.search(r"我|自己|最近", message)):
        return result("skipped", "low_signal")
    if not force and turn - last_attempt_turn < PULSE_GAP:
        return result("skipped", "cooldown")
    if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline) or deadline <= started):
        return result("skipped", "budget_exhausted")
    stop_at = min(deadline, started + MAX_RETRIEVAL_SECONDS)
    owned_connection = conn is None

    def stopped():
        return cancelled() or time.monotonic() >= stop_at

    try:
        if owned_connection:
            from brain.graph_memory import _DB_PATH
            conn = sqlite3.connect(_DB_PATH.resolve().as_uri() + "?mode=ro",
                                   uri=True, timeout=max(0.001, stop_at - time.monotonic()))
            conn.row_factory = sqlite3.Row
        conn.set_progress_handler(stopped, 200)
        if stopped():
            return result("skipped", "cancelled" if cancelled() else "budget_exhausted")
        memory_ids = []
        hashes = {}
        for index, row in enumerate(existing_rows):
            if index >= MAX_INSPECTED:
                break
            memory_id = row.get("memory_id", row.get("id")) if isinstance(row, dict) else None
            if type(memory_id) is int and memory_id > 0 and memory_id not in memory_ids:
                memory_ids.append(memory_id)
                origins[memory_id] = "reused"
        if memory_ids:
            channels.append("reused")
        terms = list(_terms(message))[:MAX_TERMS]
        if terms and len(memory_ids) < MAX_INSPECTED:
            channels.append("situational")
            try:
                rows = conn.execute(
                    "SELECT p.fact_id,e.content_hash,SUM(p.weight) AS score "
                    "FROM memory_activation_terms p JOIN memory_activation_entries e "
                    "ON e.fact_id=p.fact_id WHERE p.term IN ("
                    + ",".join("?" for _ in terms)
                    + ") GROUP BY p.fact_id HAVING SUM(p.weight)>=4 OR COUNT(*)>=2 "
                    "ORDER BY score DESC,p.fact_id DESC LIMIT ?",
                    [*terms, MAX_INSPECTED - len(memory_ids)],
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                rows = []
            for row in rows:
                if row["fact_id"] not in memory_ids:
                    memory_ids.append(row["fact_id"])
                    origins[row["fact_id"]] = "situational"
                    hashes[row["fact_id"]] = row["content_hash"]
        if len(memory_ids) < MAX_INSPECTED and not stopped():
            channels.append("lexical")
            from brain.memory_rag import _lexical_memory_results
            rows = _lexical_memory_results(
                message, limit=MAX_INSPECTED - len(memory_ids), conn=conn, subjects=[],
            )
            for _score, row in rows:
                memory_id = row["memory_id"]
                if memory_id not in memory_ids:
                    memory_ids.append(memory_id)
                    origins[memory_id] = "lexical"
        inspected = len(memory_ids)
        facts = _load_facts(conn, memory_ids)
        recent = [text[:1600].casefold() for text in list(recent_text)[-8:]
                  if isinstance(text, str)]
        facts = [row for row in facts
                 if (row["memory_id"] not in hashes
                     or hashes[row["memory_id"]] == _digest(row["content"]))
                 and not any(row["content"].casefold() in text for text in recent)]
        candidates = filter_candidates(facts, excluded_ids=excluded_ids, snapshot=snapshot)
        if stopped():
            return result("skipped", "cancelled" if cancelled() else "budget_exhausted")
        return result("candidates_ready" if candidates else "no_candidates",
                      "observation_only" if candidates else "no_safe_candidates", candidates)
    except sqlite3.OperationalError:
        reason = ("cancelled" if cancelled() else "budget_exhausted" if stopped()
                  else "storage_unavailable")
        return result("skipped", reason)
    except Exception:
        logger.debug("Candidate discovery unavailable", exc_info=True)
        return result("failed", "retrieval_unavailable")
    finally:
        if conn is not None:
            conn.set_progress_handler(None, 0)
            if owned_connection:
                conn.close()


def observe_activation(agent, route, *, request_started: float, disable_tools: bool,
                       recall_plan=None, snapshot=None, request_metadata=None) -> ActivationResult:
    from config import get_builtin_tool_config, get_decision_config
    from brain.request_router import RequestMode
    from brain.request_tool_policy import request_tool_allowlist

    config = get_decision_config()
    context = agent._request_context
    allowed_tools = request_tool_allowlist(context.routing_text)
    session_id = getattr(agent, "_session_id", None)
    turn = getattr(agent, "_activity_turn", 0)
    last_session, last_turn = getattr(agent, "_memory_activation_attempt", (None, -PULSE_GAP))
    cancel_event = getattr(agent, "_cancel_event", None)
    eligible = (
        route.mode == RequestMode.CHAT_LIGHT and recall_plan is None
        and getattr(agent, "_background_recall_plan", None) is None
        and not getattr(agent, "_memory_injected_count", 0)
        and not context.is_quote_ack and not context.has_image_blocks
        and not getattr(agent, "_manual_forced_tool", None)
        and not getattr(agent, "_manual_preferred_tool", None)
    )
    memory_allowed = (
        getattr(agent, "_owner_scope", False) and not getattr(agent, "_use_local", True)
        and not disable_tools
    )
    if eligible and config["enabled"] and memory_allowed:
        memory_allowed = (
            get_builtin_tool_config().get("search_graph_memory", True)
            and (allowed_tools is None or "search_graph_memory" in allowed_tools)
        )
    deadline = request_started + config["budget_seconds"]
    outcome = collect_activation(
        context.active_text, deadline=deadline, enabled=config["enabled"],
        memory_allowed=bool(memory_allowed), eligible=eligible, turn=turn,
        force=isinstance(request_metadata, dict) and request_metadata.get("force_memory_activation") is True,
        last_attempt_turn=last_turn if last_session == session_id else -PULSE_GAP,
        recent_text=[item.get("content", "") for item in getattr(agent, "history", [])[-8:]],
        cancelled=cancel_event.is_set if cancel_event else lambda: False, snapshot=snapshot,
    )
    agent._memory_activation_outcome = outcome
    if outcome.channels:
        agent._memory_activation_attempt = (session_id, turn)
    diagnostics = outcome.diagnostics()
    logger.debug("[MemoryActivation] %s", json.dumps(diagnostics, ensure_ascii=False))
    trace_id = getattr(agent, "_active_memory_trace_id", None)
    if trace_id:
        try:
            from brain.memory_diagnostics import record_memory_event
            record_memory_event(trace_id, "memory_activation_observed", reason=outcome.reason,
                                payload=diagnostics)
        except Exception:
            logger.debug("Activation diagnostics unavailable", exc_info=True)
    return outcome
