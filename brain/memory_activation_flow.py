"""Optional association after higher-priority recall, using its remaining budget."""

from __future__ import annotations

import json
import logging
import time

from brain.memory_activation import decide_mention
from brain.memory_activation_retrieval import reload_activation_facts
from brain.request_context import parse_request_context
from brain.memory_activation_debug import update_activation_debug


logger = logging.getLogger("MemoryActivation")


def _denied(agent, route, disable_tools, recall_plan, disabled_tools):
    from config import get_builtin_tool_config, get_decision_config
    from brain.request_router import RequestMode
    from brain.request_tool_policy import request_tool_allowlist

    config = get_decision_config()
    context = agent._request_context
    cancel_event = getattr(agent, "_cancel_event", None)
    if cancel_event and cancel_event.is_set():
        return "cancelled"
    if not config["enabled"]:
        return "disabled"
    if (route.mode != RequestMode.CHAT_LIGHT or recall_plan is not None
            or getattr(agent, "_background_recall_plan", None) is not None
            or getattr(agent, "_memory_injected_count", 0)
            or context.is_quote_ack or context.has_image_blocks
            or getattr(agent, "_manual_forced_tool", None)
            or getattr(agent, "_manual_preferred_tool", None)):
        return "higher_priority_path"
    allowed = request_tool_allowlist(context.routing_text)
    if (disable_tools or not getattr(agent, "_owner_scope", False)
            or getattr(agent, "_use_local", True)
            or not get_builtin_tool_config().get("search_graph_memory", True)
            or "search_graph_memory" in disabled_tools
            or allowed is not None and "search_graph_memory" not in allowed):
        return "memory_not_allowed"
    client = getattr(agent, "_request_decision_client", None)
    if client is None or getattr(agent, "_request_decision_choice", None) != "reply":
        return "route_decision_unavailable_or_prioritized"
    if config.get("provider", "api") != client.provider:
        return "provider_changed"
    if client.provider == "api" and config["allow_cloud_context"] is not True:
        return "cloud_not_allowed"
    if time.monotonic() >= min(client.deadline, agent._request_decision_deadline):
        return "budget_exhausted"
    return ""


def inject_activation(agent, route, messages, *, disable_tools, recall_plan=None,
                      snapshot=None, disabled_tools=()) -> dict:
    previous = getattr(agent, "_memory_mention_outcome", None)
    if previous is not None:
        return previous
    outcome = {"status": "skipped", "reason": "no_candidates", "action": "suppress",
               "candidate_id": None, "decision_called": False, "prompt_injected": False,
               "expression_status": "unknown"}
    agent._memory_mention_outcome = outcome
    session_id = getattr(agent, "_session_id", None)
    turn = getattr(agent, "_activity_turn", 0)

    def denied():
        if (session_id != getattr(agent, "_session_id", None)
                or turn != getattr(agent, "_activity_turn", 0)):
            return "request_changed"
        return _denied(agent, route, disable_tools, recall_plan, disabled_tools)

    try:
        reason = denied()
        if reason:
            outcome["reason"] = reason
            return outcome
        activation = getattr(agent, "_memory_activation_outcome", None)
        if activation is None or not activation.candidates:
            outcome["reason"] = getattr(activation, "reason", "no_candidates")
            return outcome
        client = agent._request_decision_client
        deadline = min(client.deadline, agent._request_decision_deadline)
        client.deadline = deadline
        mention_session, mentioned = getattr(agent, "_memory_activation_mentions", (None, ()))
        excluded = set(mentioned) if mention_session == session_id else set()
        candidates = tuple(item for item in activation.candidates if item.memory_id not in excluded)
        rows = reload_activation_facts(candidates, deadline=deadline, snapshot=snapshot)
        if not rows:
            outcome["reason"] = "candidates_changed_or_repeated"
            return outcome
        reason = denied()
        if reason:
            outcome["reason"] = reason
            return outcome
        old_callback = client.on_event

        def activity(event):
            if event.get("kind") == "call":
                outcome["decision_called"] = True
            update_activation_debug(
                agent, phase="deciding", decision_called=outcome["decision_called"],
                decision_status="running" if event.get("kind") == "call" else
                "failed" if event.get("is_error") else "validated",
                provider=event.get("provider"), model=event.get("model"),
            )
            if old_callback:
                old_callback(event)

        client.on_event = activity
        try:
            recent = []
            for item in getattr(agent, "history", [])[-5:]:
                if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                    continue
                content = item["content"]
                if content == agent._raw_request_text:
                    continue
                if item.get("role") == "user":
                    content = parse_request_context(content).active_text
                recent.append({"role": item.get("role"), "content": content})
            result = decide_mention(
                client, agent._request_context.active_text, recent, rows,
                memory_allowed=True, cloud_allowed=True, snapshot=snapshot,
                cancelled=lambda: bool(denied()),
            )
        finally:
            client.on_event = old_callback
        outcome.update(status=result.status, reason=result.reason, action=result.action)
        reason = denied()
        if reason:
            outcome.update(status="suppressed", reason=reason, action="suppress")
            return outcome
        if not result.prompt or result.candidate is None:
            return outcome
        if not reload_activation_facts((result.candidate,), deadline=deadline, snapshot=snapshot):
            outcome.update(status="suppressed", reason="candidate_changed", action="suppress")
            return outcome
        reason = denied()
        if reason:
            outcome.update(status="suppressed", reason=reason, action="suppress")
            return outcome
        messages.append({"role": "system", "content": result.prompt,
                         "_module": "memory_association"})
        outcome.update(status="injected", candidate_id=result.candidate.memory_id,
                       prompt_injected=True)
        previous_ids = mentioned if mention_session == session_id else ()
        agent._memory_activation_mentions = (session_id, (*previous_ids, result.candidate.memory_id)[-32:])
        return outcome
    except Exception:
        outcome.update(status="failed", reason="association_unavailable", action="suppress")
        logger.debug("Optional association unavailable", exc_info=True)
        return outcome
    finally:
        update_activation_debug(agent, phase="completed", **outcome)
        logger.debug("[MemoryMention] %s", json.dumps(outcome, ensure_ascii=False))
        trace_id = getattr(agent, "_active_memory_trace_id", None)
        if trace_id:
            try:
                from brain.memory_diagnostics import record_memory_event
                record_memory_event(trace_id, "memory_activation_mention", reason=outcome["reason"],
                                    payload=outcome)
            except Exception:
                logger.debug("Association diagnostics unavailable", exc_info=True)
