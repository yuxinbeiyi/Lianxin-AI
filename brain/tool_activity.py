"""莲心工具活动记录（跨客户端共享）。

AgentCore 每执行一次工具调用，这里写入一条 ``call`` 事件；执行结束再写入一
条 ``result`` 事件。新版界面、桌宠等前端通过 ``/api/tools/events`` 轮询这些
事件，因此无论是主界面、桌宠、QQ 还是主动消息触发的工具调用，界面上都能看
到莲心先后调用了什么。

记录只存在于进程内存：莲心重启后自然清空，不需要落库。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque

_MAX_EVENTS = 600

_LOCK = threading.RLock()
_EVENTS: deque = deque(maxlen=_MAX_EVENTS)
_SEQ = 0
_EVENT_ID = 0
_LOCAL = threading.local()


def _preview(value, limit: int = 240) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _append(event: dict) -> int:
    global _EVENT_ID
    with _LOCK:
        _EVENT_ID += 1
        event["id"] = _EVENT_ID
        _EVENTS.append(event)
        return _EVENT_ID


def _next_seq() -> int:
    global _SEQ
    with _LOCK:
        _SEQ += 1
        return _SEQ


def _base_event(kind: str, seq: int, name, session_id, round_num, turn) -> dict:
    return {
        "kind": kind,
        "seq": seq,
        "ts": time.time(),
        "sessionId": int(session_id or 0),
        "turn": int(turn or 0),
        "round": int(round_num or 0),
        "name": str(name or ""),
    }


def begin_call(name, args=None, session_id=None, round_num=0, turn=0) -> None:
    """记录一次工具开始执行；与同线程的 finish_call 按工具名配对。"""
    try:
        seq = _next_seq()
        event = _base_event("call", seq, name, session_id, round_num, turn)
        event["argsPreview"] = _preview(args)
        _append(event)
        pending = getattr(_LOCAL, "pending", None)
        if pending is None:
            pending = _LOCAL.pending = {}
        pending.setdefault(str(name or ""), []).append(seq)
    except Exception:
        pass


def finish_call(name, result="", is_error=False, elapsed_ms=0.0,
                session_id=None, round_num=0, turn=0) -> None:
    """记录一次工具执行结果；没有配对的 call 时补一条，保证界面完整。"""
    try:
        pending = getattr(_LOCAL, "pending", None) or {}
        queue = pending.get(str(name or ""))
        seq = queue.pop(0) if queue else None
        if seq is None:
            seq = _next_seq()
            event = _base_event("call", seq, name, session_id, round_num, turn)
            event["argsPreview"] = ""
            _append(event)
        event = _base_event("result", seq, name, session_id, round_num, turn)
        event["status"] = "error" if is_error else "done"
        event["preview"] = _preview(result)
        event["elapsedMs"] = float(elapsed_ms or 0.0)
        _append(event)
    except Exception:
        pass


def events_after(after: int = 0) -> dict:
    """返回 id 大于 after 的事件，供界面轮询增量读取。"""
    with _LOCK:
        try:
            cursor = int(after or 0)
        except (TypeError, ValueError):
            cursor = 0
        items = [dict(event) for event in _EVENTS if int(event.get("id") or 0) > cursor]
    return {"items": items, "latest": items[-1]["id"] if items else cursor}
