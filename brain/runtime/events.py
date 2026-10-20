"""莲心执行运行时事件。

阶段 1 的设计要点：**前端零改动**。
工具类事件直接写进已有的 ``brain/tool_activity.py`` 事件流（前端已在轮询
``/api/tools/events``），因此新版界面的"工具信息栏"无需任何修改就能显示
执行引擎调用了什么。

本模块另外维护一条**运行时事件**环（任务开始/结束/审批/熔断），供
``/api/runtime/events`` 使用；阶段 3 的任务栏会消费它。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

# ── 运行时事件类型 ──────────────────────────────────────────────
TASK_STARTED = "task_started"
TASK_PROGRESS = "task_progress"
TOOL_STARTED = "tool_started"
TOOL_FINISHED = "tool_finished"
APPROVAL_REQUESTED = "approval_requested"
APPROVAL_RESOLVED = "approval_resolved"
PLAN_UPDATED = "plan_updated"
TOKEN_USAGE = "token_usage"
TASK_COMPLETED = "task_completed"
TASK_FAILED = "task_failed"
TASK_INTERRUPTED = "task_interrupted"
NOTE = "note"
APPROVAL_TIMEOUT = "approval_timeout"

# ??????? TaskRuntime ?????? turn ???????????
TURN_FINISHED = "turn_finished"

_MAX_RUNTIME_EVENTS = 400

_LOCK = threading.RLock()
_EVENTS: deque = deque(maxlen=_MAX_RUNTIME_EVENTS)
_EVENT_ID = 0


@dataclass
class RuntimeEvent:
    """一条运行时事件（阶段 1 只做记录，不做前端渲染）。"""

    kind: str
    ts: float = field(default_factory=time.time)
    thread_id: str = ""
    turn_id: str = ""
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ts": self.ts,
            "threadId": self.thread_id,
            "turnId": self.turn_id,
            "payload": self.payload,
        }


def publish(kind: str, *, thread_id: str = "", turn_id: str = "", **payload: Any) -> dict:
    """写入运行时事件环并返回带 id 的事件字典。异常不影响主流程。"""
    global _EVENT_ID
    try:
        with _LOCK:
            _EVENT_ID += 1
            event = {
                "id": _EVENT_ID,
                "kind": kind,
                "ts": time.time(),
                "threadId": thread_id,
                "turnId": turn_id,
                "payload": payload,
            }
            _EVENTS.append(event)
            return event
    except Exception:
        return {}


def events_after(after: int = 0) -> dict:
    """返回 id 大于 after 的事件，供界面/脚本增量读取。"""
    with _LOCK:
        try:
            cursor = int(after or 0)
        except (TypeError, ValueError):
            cursor = 0
        items = [dict(event) for event in _EVENTS if int(event.get("id") or 0) > cursor]
    return {"items": items, "latest": items[-1]["id"] if items else cursor}


def clear() -> None:
    with _LOCK:
        _EVENTS.clear()


# ── 工具事件：直接复用现有工具信息栏总线 ────────────────────────

def tool_started(name, args=None, session_id: Optional[int] = None, turn: int = 0) -> None:
    """等价于 AgentCore 的 begin_call，让工具信息栏多出一行。"""
    try:
        from brain.tool_activity import begin_call

        begin_call(name, args, session_id=session_id, round_num=0, turn=turn)
    except Exception:
        pass


def tool_finished(name, result="", is_error: bool = False, elapsed_ms: float = 0.0,
                  session_id: Optional[int] = None, turn: int = 0) -> None:
    """等价于 AgentCore 的 finish_call，把那一行标记为完成/失败。"""
    try:
        from brain.tool_activity import finish_call

        finish_call(name, result, is_error=is_error, elapsed_ms=elapsed_ms,
                    session_id=session_id, round_num=0, turn=turn)
    except Exception:
        pass
