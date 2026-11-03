"""Progress snapshots, distinct from real decision transport events."""

from __future__ import annotations

import logging


logger = logging.getLogger("MemoryActivation")


def begin_activation_debug(agent, request_metadata=None):
    forced = isinstance(request_metadata, dict) and request_metadata.get("force_memory_activation") is True
    agent._memory_activation_debug = {
        "session_id": getattr(agent, "_session_id", 0),
        "turn": getattr(agent, "_activity_turn", 0),
        "visible": forced,
        "data": {
            "trigger": "forced" if forced else "ordinary", "phase": "started",
            "status": "running", "reason": "pending", "retrieval_status": "not_started",
            "retrieval_reason": "pending", "candidate_ids": [], "channels": [],
            "inspected": 0, "retrieval_elapsed_ms": 0, "decision_called": False,
            "decision_status": "not_called", "provider": None, "model": None,
            "action": "suppress", "prompt_injected": False, "candidate_id": None,
            "expression_status": "unknown", "reply_status": "pending",
        },
    }
    update_activation_debug(agent)


def update_activation_debug(agent, **changes):
    try:
        state = getattr(agent, "_memory_activation_debug", None)
        if not state or (state["session_id"], state["turn"]) != (
                getattr(agent, "_session_id", 0), getattr(agent, "_activity_turn", 0)):
            return
        data = state["data"]
        data.update({key: value for key, value in changes.items() if key in data})
        state["visible"] = state["visible"] or bool(data["channels"]) or data["decision_called"]
        if state["visible"]:
            from brain.tool_activity import record_memory_activation
            record_memory_activation(data, session_id=state["session_id"], turn=state["turn"])
    except Exception:
        logger.debug("Activation progress unavailable", exc_info=True)


def finish_activation_debug(agent, *, failed=False):
    state = getattr(agent, "_memory_activation_debug", None)
    if not state:
        return
    cancelled = getattr(agent, "_cancel_event", None)
    cancelled = bool(cancelled and cancelled.is_set())
    changes = {"reply_status": "cancelled" if cancelled else "failed" if failed else "finished"}
    if state["data"]["phase"] != "completed":
        changes.update(phase="completed", status="failed" if failed else "skipped",
                       reason="cancelled" if cancelled else "request_failed" if failed
                       else "higher_priority_or_early_exit")
    update_activation_debug(agent, **changes)
