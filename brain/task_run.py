"""Request-scoped state and budgets for the Agent tool loop.

This is deliberately in-memory.  The durable audit record remains the existing
Workflow run; TaskRun supplies the control-plane state needed by later phases.
"""

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from uuid import uuid4
import hashlib
import json
from pathlib import PurePath
from urllib.parse import urlsplit, urlunsplit


class TaskType(str, Enum):
    CHAT = "CHAT"
    READ_MEMORY = "READ_MEMORY"
    FILE_REVIEW = "FILE_REVIEW"
    WEB_RESEARCH = "WEB_RESEARCH"
    VISION = "VISION"
    ACTION = "ACTION"
    MIXED = "MIXED"


class TaskPhase(str, Enum):
    PLANNING = "PLANNING"
    RESEARCHING = "RESEARCHING"
    NEAR_BUDGET = "NEAR_BUDGET"
    FORCE_FINAL = "FORCE_FINAL"
    FINAL = "FINAL"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Budget:
    soft_rounds: int
    hard_rounds: int
    no_progress_rounds: int


_BUDGETS = {
    TaskType.CHAT: Budget(3, 8, 1),
    TaskType.READ_MEMORY: Budget(4, 10, 2),
    TaskType.FILE_REVIEW: Budget(8, 16, 2),
    TaskType.WEB_RESEARCH: Budget(8, 24, 2),
    TaskType.VISION: Budget(2, 8, 1),
    TaskType.ACTION: Budget(5, 12, 2),
    TaskType.MIXED: Budget(8, 24, 2),
}


def infer_task_type(text: str, *, route_mode: str = "") -> TaskType:
    value = f"{route_mode} {text}".lower()
    if any(x in value for x in ("web_research", "web research", "网页", "搜索", "网站")):
        return TaskType.WEB_RESEARCH
    if any(x in value for x in ("memory", "recall", "记忆", "回忆", "日记", "时间胶囊")):
        return TaskType.READ_MEMORY
    if any(x in value for x in ("vision", "视觉", "摄像头", "图片", "人脸", "手势")):
        return TaskType.VISION
    if any(x in value for x in ("file", "文件", "代码", "检查", "修改", "测试")):
        return TaskType.FILE_REVIEW
    if any(x in value for x in ("servo", "发送", "执行", "控制", "提醒", "保存")):
        return TaskType.ACTION
    return TaskType.CHAT


def resource_fingerprint(tool_name: str, arguments: dict) -> str:
    """Return a stable request-local identity for a readable resource.

    Chunk indexes remain part of the identity: reading another chunk is useful
    progress, while requesting the same chunk twice is redundant.
    """
    args = dict(arguments or {})
    resource = args.get("path") or args.get("url") or args.get("query") or args.get("keyword") or ""
    if not resource:
        return ""
    if "url" in args:
        parts = urlsplit(str(resource).strip())
        resource = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                               parts.query, ""))
    elif resource:
        resource = str(PurePath(str(resource).strip()))
    name = str(tool_name)
    if name in {"read_file", "read_file_chunk", "read_file_lines"}:
        name = "file_read"
    elif name.startswith("fetch_webpage"):
        name = "webpage_read"
    chunk_index = args.get("chunk_index", 0) if name == "file_read" else args.get("chunk_index")
    relevant = {"resource": resource, "chunk_index": chunk_index,
                "mode": args.get("mode"), "tool": name}
    raw = json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class TaskRun:
    user_goal: str
    task_type: TaskType = TaskType.CHAT
    request_id: str = field(default_factory=lambda: uuid4().hex)
    workflow_run_id: int = 0
    parent_request_id: str = ""
    source_channel: str = "desktop"
    phase: TaskPhase = TaskPhase.PLANNING
    status: str = "running"
    started_at: float = field(default_factory=monotonic)
    iterations: int = 0
    tool_calls: int = 0
    errors: int = 0
    no_progress_rounds: int = 0
    resource_records: dict[str, dict] = field(default_factory=dict)
    result_digests: set[str] = field(default_factory=set)
    evidence_count: int = 0
    budget: Budget = field(default_factory=lambda: _BUDGETS[TaskType.CHAT])

    @classmethod
    def create(cls, goal: str, *, route_mode: str = "", source_channel: str = "desktop"):
        task_type = infer_task_type(goal, route_mode=route_mode)
        return cls(goal, task_type=task_type, source_channel=source_channel,
                   budget=_BUDGETS[task_type])

    def bind_workflow(self, run_id: int) -> None:
        self.workflow_run_id = int(run_id or 0)

    def begin_round(self) -> None:
        self.iterations += 1
        if self.phase == TaskPhase.PLANNING:
            self.phase = TaskPhase.RESEARCHING
        if self.iterations >= self.budget.soft_rounds and self.phase == TaskPhase.RESEARCHING:
            self.phase = TaskPhase.NEAR_BUDGET

    def record_tool_call(self) -> None:
        self.tool_calls += 1

    def record_resource(self, tool_name: str, arguments: dict, *, cached: bool = False) -> str:
        fingerprint = resource_fingerprint(tool_name, arguments)
        if not fingerprint:
            return ""
        record = self.resource_records.setdefault(fingerprint, {
            "tool": str(tool_name), "calls": 0, "cached": False,
        })
        record["calls"] += 1
        record["cached"] = bool(record["cached"] or cached)
        return fingerprint

    def has_resource(self, tool_name: str, arguments: dict) -> bool:
        fingerprint = resource_fingerprint(tool_name, arguments)
        return bool(fingerprint and fingerprint in self.resource_records)

    def record_error(self) -> None:
        self.errors += 1

    def record_result(self, result: str, *, is_error: bool = False) -> bool:
        """Record whether a tool result added new content this request."""
        if is_error:
            self.record_error()
            return False
        digest = hashlib.sha256(str(result or "").strip().encode("utf-8")).hexdigest()
        if not str(result or "").strip() or digest in self.result_digests:
            self.record_progress(False)
            return False
        self.result_digests.add(digest)
        self.evidence_count += 1
        self.record_progress(True)
        return True

    def record_progress(self, progressed: bool) -> None:
        self.no_progress_rounds = 0 if progressed else self.no_progress_rounds + 1

    def should_force_final(self) -> bool:
        return self.iterations >= self.budget.hard_rounds

    def mark_force_final(self) -> None:
        self.phase = TaskPhase.FORCE_FINAL

    def mark_finished(self, *, success: bool = True) -> None:
        self.status = "success" if success else "failed"
        self.phase = TaskPhase.FINAL if success else TaskPhase.FAILED

    def snapshot(self) -> dict:
        return {
            "request_id": self.request_id,
            "workflow_run_id": self.workflow_run_id,
            "task_type": self.task_type.value,
            "phase": self.phase.value,
            "status": self.status,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "errors": self.errors,
            "no_progress_rounds": self.no_progress_rounds,
            "resources": len(self.resource_records),
            "evidence_count": self.evidence_count,
            "soft_rounds": self.budget.soft_rounds,
            "hard_rounds": self.budget.hard_rounds,
            "elapsed_seconds": round(monotonic() - self.started_at, 3),
        }
