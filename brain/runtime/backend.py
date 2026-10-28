"""执行后端接口与数据结构。

莲心自有的运行骨架（TaskRuntime）通过这个接口驱动外部执行引擎，
本阶段唯一的实现是 ``CodexExecutionBackend``（codex app-server）。

原则：**骨架必须自建（我们拥有它），外部后端必须可替换（我们不依赖它）**。
因此这里不出现任何 Codex 专有类型，Codex 的协议细节全部封在
``codex_backend.py`` 里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from .goals import GoalContract, GoalContractError

# 沙箱档位（与 Codex 的 SandboxMode 枚举同名，便于直接透传）
SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"

# 审批策略（与 Codex 的 AskForApproval 枚举同名）
APPROVAL_NEVER = "never"
APPROVAL_ON_REQUEST = "on-request"
APPROVAL_UNTRUSTED = "untrusted"

# 审批回执取值
DECISION_ACCEPT = "accept"
DECISION_ACCEPT_FOR_SESSION = "acceptForSession"
DECISION_DECLINE = "decline"
DECISION_CANCEL = "cancel"


@dataclass
class TaskSpec:
    """一次执行任务的入参。"""

    goal: str
    cwd: str
    session_id: Optional[int] = None
    turn: int = 0
    sandbox: str = SANDBOX_READ_ONLY
    approval_policy: str = APPROVAL_ON_REQUEST
    # 原样透传给 codex 的配置（模型窗口、可写根等）。
    config: dict = field(default_factory=dict)
    # 非空表示「接着上次中断的任务」：复用这个 codex thread，不再新建。
    resume_thread_id: str = ""
    generation: int = 0
    goal_contract: Optional[GoalContract] = None

    def __post_init__(self) -> None:
        if self.goal_contract is not None and not isinstance(self.goal_contract, GoalContract):
            raise GoalContractError("TaskSpec.goal_contract 必须是已校验的目标契约")


@dataclass
class SessionHandle:
    """执行会话句柄。"""

    thread_id: str
    turn_id: str = ""
    backend: str = "codex"
    generation: int = 0


@dataclass
class ControlResult:
    """accepted confirms RPC receipt, not task completion; uncertain must not be replayed."""

    status: str
    thread_id: str = ""
    turn_id: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "accepted"

    def to_dict(self) -> dict:
        return {"ok": self.ok, "status": self.status, "threadId": self.thread_id,
                "turnId": self.turn_id, "message": self.message}


@dataclass
class BackendStatus:
    """后端可用性快照（用于设置面板与降级判断）。"""

    available: bool
    backend: str = "codex"
    version: str = ""
    model: str = ""
    provider: str = ""
    reason: str = ""
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "available": bool(self.available),
            "backend": self.backend,
            "version": self.version,
            "model": self.model,
            "provider": self.provider,
            "reason": self.reason,
            "extras": self.extras,
        }


@runtime_checkable
class ExecutionBackend(Protocol):
    """可替换的执行后端。"""

    name: str

    def probe(self) -> BackendStatus:
        """探测后端是否可用（含版本与模型通道校验）。"""

    def start(self, spec: TaskSpec) -> SessionHandle:
        """建立会话并发起一轮任务。"""

    def steer(self, handle: SessionHandle, text: str) -> ControlResult:
        """把用户的新信息并入当前任务（阶段 3 使用）。"""

    def interrupt(self, handle: SessionHandle) -> ControlResult:
        """中断当前任务。"""

    def approve(self, handle: SessionHandle, request_id, decision: str) -> bool:
        """回执一次审批（可延后调用，用于"先展示 diff 再确认"）。"""

    def events(self, timeout: float = 0.5) -> list:
        """增量取出运行时事件。"""

    def close(self, handle: SessionHandle) -> None:
        """结束会话。"""

    def shutdown(self) -> None:
        """释放进程级资源。"""
