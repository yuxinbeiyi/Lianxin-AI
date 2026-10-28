"""莲心执行运行时。

对外只暴露三样东西：``TaskRuntime``（骨架）、``ExecutionBackend``（可替换后端协议）、
``RuntimeEvent``（事件）。调用方一般只需要 ``get_task_runtime()``。
"""

from __future__ import annotations

from . import events
from .backend import (
    APPROVAL_NEVER,
    APPROVAL_ON_REQUEST,
    APPROVAL_UNTRUSTED,
    DECISION_ACCEPT,
    DECISION_ACCEPT_FOR_SESSION,
    DECISION_CANCEL,
    DECISION_DECLINE,
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    BackendStatus,
    ExecutionBackend,
    SessionHandle,
    TaskSpec,
)
from .events import RuntimeEvent
from .goals import GoalContract, GoalContractError, GoalProgress, VerificationEvidence
from .task_runtime import TaskRuntime, get_task_runtime

__all__ = [
    "TaskRuntime",
    "get_task_runtime",
    "ExecutionBackend",
    "TaskSpec",
    "SessionHandle",
    "BackendStatus",
    "RuntimeEvent",
    "GoalContract",
    "GoalContractError",
    "GoalProgress",
    "VerificationEvidence",
    "events",
    "SANDBOX_READ_ONLY",
    "SANDBOX_WORKSPACE_WRITE",
    "SANDBOX_DANGER_FULL_ACCESS",
    "APPROVAL_NEVER",
    "APPROVAL_ON_REQUEST",
    "APPROVAL_UNTRUSTED",
    "DECISION_ACCEPT",
    "DECISION_ACCEPT_FOR_SESSION",
    "DECISION_DECLINE",
    "DECISION_CANCEL",
]
