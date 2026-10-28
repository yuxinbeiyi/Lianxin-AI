"""莲心任务执行运行时（阶段 2：受控写入）。

职责边界（设计文档 §2 / §11 / §13）：
- **骨架属于莲心**：任务生命周期、并发闸门、熔断、审批裁决、事件转发都在这里。
- **后端可替换**：真正的执行交给 ``ExecutionBackend``（当前是 Codex）。

三条硬约束（贯穿所有阶段）：
1. 工作目录锁在专用沙箱目录内，**绝不进入莲心源码仓库**；
2. 写文件必须**先给用户看 diff 再落盘** —— 默认 ``read-only`` 沙箱，写入必然升级成审批；
3. 单任务串行：同一时刻只允许一个执行任务，后来的请求直接拒绝。

审批三档 ``approval_mode``：
- ``ask``（阶段 2 默认）：挂起等用户确认，可"本轮都允许"（acceptForSession，解决重复弹窗）；
- ``auto_decline``：一律拒绝（阶段 1 语义，纯观察）；
- ``auto_accept``：全部放行（沙箱内放手模式，用户显式选择）。

熔断四阈值：40 轮动作 / 连续 3 轮无进展 / 15 分钟 / 同一 turn 连续 3 次审批被拒。
"""

from __future__ import annotations

import atexit
import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from brain.runtime import events as runtime_events
from brain.runtime import session_store
from brain.runtime.goals import GoalContract, GoalContractError, goal_mode_status
from brain.runtime.backend import (
    APPROVAL_ON_REQUEST,
    DECISION_ACCEPT,
    DECISION_ACCEPT_FOR_SESSION,
    DECISION_DECLINE,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    TaskSpec,
)

DEFAULT_MAX_ROUNDS = 40
DEFAULT_MAX_STALE_ROUNDS = 3
DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_MAX_DECLINES = 3
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 180.0

# 审批模式
APPROVAL_MODE_ASK = "ask"
APPROVAL_MODE_DECLINE = "auto_decline"
APPROVAL_MODE_ACCEPT = "auto_accept"

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 交给执行引擎的目标前缀：边界说明 + 不重复试探被拒操作 + 中文结论
DEFAULT_GOAL_PREFIX = (
    "【莲心执行引擎 · 只读会话】\n"
    "- 你现在是莲心的执行引擎，只做只读的阅读、检索、排障，不要写文件、不要改配置、不要安装依赖。\n"
    "- 工作目录是一个独立沙箱，与莲心的源码仓库无关；找不到东西就直接说明，不要反复换路径重试。\n"
    "- 需要审批的操作不会有人批准，被拒后立刻改用只读方案或直接结束，不要重复发起同类请求。\n"
    "- 最后用中文给莲心一句可以直接转述给用户的结论。"
)

# 允许写入时换一套说法：沙箱内可写，但边界依旧明确
DEFAULT_GOAL_PREFIX_WRITE = (
    "【莲心执行引擎 · 沙箱会话】\n"
    "- 你是莲心的执行引擎。当前工作目录是专用沙箱目录，**允许在这个目录内创建或修改文件**。\n"
    "- **改动文件一律用 apply_patch 工具**，不要用 shell 重定向、Set-Content 之类的命令去写文件；"
    "这样用户才能看到清晰的 diff 预览再决定是否放行。\n"
    "- 不要为了试探权限而创建临时文件或做写测试；直接提交你真正想做的改动。\n"
    "- 不许碰沙箱以外的任何路径，不许改莲心的源码仓库，不许安装系统级依赖。\n"
    "- 用户会看到你的改动摘要再决定是否放行；被拒绝后立刻换方案或直接结束，不要重复发起同类请求。\n"
    "- 最后用中文给莲心一句可以直接转述给用户的结论，并列出你实际创建/修改了哪些文件。"
)

_PID_DIR = Path(__file__).resolve().parents[2] / "data" / "codex_runtime"
_PID_FILE = _PID_DIR / "runtime.pid"

_MAX_AGENT_TEXT = 4000
# 模型逐字输出每攒够这么多字符才发一条进度提示，避免把事件环刷满
_DELTA_PUBLISH_STEP = 400


def _default_log(tag, message) -> None:
    try:
        from utils import bridge_log

        bridge_log.log(str(tag), str(message))
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    """判断 PID 是否仍在运行（跨平台，失败时保守返回 True）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259  # STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return True


def _resolve_cwd(cwd: str, workspace: str) -> str:
    """把工作目录夹在沙箱目录内；越界一律回落到沙箱根。"""
    workspace = str(workspace or "").strip()
    if not workspace:
        return ""
    base = Path(workspace).expanduser()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        base = base.resolve()
    except Exception:
        return str(base)
    if not str(cwd or "").strip():
        return str(base)
    try:
        target = Path(str(cwd)).expanduser().resolve()
    except Exception:
        return str(base)
    if target == base or base in target.parents:
        return str(target)
    return str(base)


def _short_text(value, limit: int = 200) -> str:
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "..."


_PATCH_FILE_RE = re.compile(r"^\*\*\* (Update|Add|Delete) File:\s*(.+?)\s*$", re.M)
_PATCH_KIND = {"Update": "update", "Add": "add", "Delete": "delete"}


def _files_from_patch_text(text: str, limit: int = 20000) -> list:
    """模型常把补丁正文塞在 shell 命令里（apply_patch 命令行），这里把它还原成 diff 预览。

    这样即使拿到的是"命令审批"而不是 fileChange 条目，用户依然先看到清晰的改动。
    """
    raw = str(text or "")
    start = raw.find("*** Begin Patch")
    if start < 0:
        return []
    end = raw.find("*** End Patch", start)
    body = raw[start:end + len("*** End Patch")] if end > 0 else raw[start:]
    # 换行可能来自 JSON 转义(\n)、PowerShell 反引号(`n) 或真实换行
    body = body.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "")
    body = body.replace("`r`n", "\n").replace("`n", "\n").replace("`r", "")

    files: list = []
    current = None
    for line in body.split("\n"):
        line = line.rstrip("\r")
        match = _PATCH_FILE_RE.match(line)
        if match:
            current = {
                "path": match.group(2).strip(),
                "movePath": "",
                "kind": _PATCH_KIND.get(match.group(1), "update"),
                "lines": [],
            }
            files.append(current)
            continue
        if current is None or line.startswith("*** "):
            continue
        current["lines"].append(line)

    for item in files:
        diff = "\n".join(item.pop("lines")).strip("\n")
        item["diff"] = diff[:limit]
        item["truncated"] = len(diff) > limit
        item["diffBytes"] = len(diff.encode("utf-8", "replace"))
        # 命令行里的补丁常被 shell 缩进，计数放宽为"去掉前导空白后看首字符"
        item["additions"] = sum(1 for line in diff.splitlines()
                                if line.lstrip().startswith("+") and not line.lstrip().startswith("+++"))
        item["deletions"] = sum(1 for line in diff.splitlines()
                                if line.lstrip().startswith("-") and not line.lstrip().startswith("---"))
    return files[:20]


def _resolve_workspace(workspace: str):
    """沙箱目录必须可用且**不在莲心仓库内**（边界：不允许外部 Agent 碰莲心仓库）。"""
    text = str(workspace or "").strip()
    if not text:
        return "", "沙箱目录未配置"
    base = Path(text).expanduser()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        base = base.resolve()
    except Exception:
        return "", "沙箱目录不可解析：%s" % text
    try:
        repo = _REPO_ROOT.resolve()
    except Exception:
        repo = _REPO_ROOT
    if base == repo or repo in base.parents:
        return "", "沙箱目录不能落在莲心仓库内：%s" % base
    return str(base), ""


class TaskRuntime:
    """单例任务运行时：串行、可熔断、可审批、可中断。"""

    def __init__(self, backend=None, log=None) -> None:
        self._lock = threading.RLock()
        self._backend = backend
        self._log = log or _default_log
        self._thread: Optional[threading.Thread] = None
        self._handle = None
        self._task: dict = {}
        self._last_result: Optional[dict] = None
        self._stop_requested = False
        self._stop_reason = ""
        self._interrupt_key = None
        self._declines = 0
        self._rounds = 0
        self._stale = 0
        self._generation = 0
        self._final: dict = {}
        self._agent_text: list = []
        self._delta_chars = 0
        self._delta_published = 0
        self._seen_sigs: set = set()
        self._probe_status = None
        self._closed = False
        self._pending: dict = {}
        self._card_seq = 0
        self._session_grants: set = set()
        self._approval_mode = APPROVAL_MODE_DECLINE
        self._sandbox = SANDBOX_READ_ONLY
        self._network_access = False
        self._terminal_callbacks: list = []
        self._delivered_tasks: list = []
        self._check_orphan_pid()

    # ── 配置 ──────────────────────────────────────────────
    @staticmethod
    def _runtime_config() -> dict:
        try:
            from config import get_runtime_config

            return dict(get_runtime_config() or {})
        except Exception:
            return {}

    def _limits(self) -> dict:
        cfg = self._runtime_config()

        def _num(key, default, cast):
            try:
                return cast(cfg.get(key, default))
            except (TypeError, ValueError):
                return default

        return {
            "max_rounds": max(1, _num("max_rounds", DEFAULT_MAX_ROUNDS, int)),
            "max_stale_rounds": max(1, _num("max_stale_rounds", DEFAULT_MAX_STALE_ROUNDS, int)),
            "timeout_seconds": max(30.0, _num("timeout_seconds", DEFAULT_TIMEOUT_SECONDS, float)),
            "max_declines": max(1, _num("max_declines", DEFAULT_MAX_DECLINES, int)),
            "approval_timeout_seconds": max(
                10.0, _num("approval_timeout_seconds", DEFAULT_APPROVAL_TIMEOUT_SECONDS, float)
            ),
        }

    def _model_config(self, sandbox: str = "", network: bool = False) -> dict:
        """P1：补模型元数据缺失时的窗口/压缩阈值；顺带按任务下发写入根与网络授权。"""
        cfg = self._runtime_config()
        out: dict = {}
        for key in ("model_context_window", "model_auto_compact_token_limit"):
            try:
                value = int(cfg.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                out[key] = value
        if sandbox == SANDBOX_WORKSPACE_WRITE:
            # writable_roots 把可写范围再收紧到沙箱目录，网络默认关闭（D-3）
            roots = [str(cfg.get("workspace") or "").strip()]
            out["sandbox_workspace_write"] = {
                "network_access": bool(network),
                "writable_roots": [root for root in roots if root],
            }
        return out

    def _goal_text(self, goal: str, mode: str = "") -> str:
        configured = str(self._runtime_config().get("goal_prefix") or "").strip()
        if configured:
            prefix = configured
        elif str(mode or "") == APPROVAL_MODE_DECLINE:
            prefix = DEFAULT_GOAL_PREFIX
        else:
            prefix = DEFAULT_GOAL_PREFIX_WRITE
        goal = str(goal or "").strip()
        return ("%s\n\n%s" % (prefix, goal)) if prefix else goal

    # ── 后端 ──────────────────────────────────────────────
    def _ensure_backend(self):
        with self._lock:
            if self._backend is not None:
                return self._backend
        from brain.runtime.codex_backend import CodexExecutionBackend

        cfg = self._runtime_config()
        backend = CodexExecutionBackend(
            exe=str(cfg.get("codex_bin") or ""),
            pinned_version=str(cfg.get("codex_version") or ""),
            log=lambda tag, msg: self._log("执行引擎/%s" % tag, msg),
        )
        backend.set_approval_decider(self._decide)
        with self._lock:
            if self._backend is None:
                self._backend = backend
            return self._backend

    def backend_name(self) -> str:
        with self._lock:
            return str(getattr(self._backend, "name", "codex") or "codex")

    # ── 对外状态 ──────────────────────────────────────────
    def state(self) -> dict:
        with self._lock:
            thread = self._thread
            running = bool(thread is not None and thread.is_alive()
                           and self._task.get("state") != "idle")
            task = copy.deepcopy(self._task) if self._task else None
            if task is not None:
                task["elapsedSeconds"] = round(time.time() - float(task.get("startedAt") or time.time()), 1)
                task["rounds"] = self._rounds
                task["staleRounds"] = self._stale
                task["declines"] = self._declines
            last = copy.deepcopy(self._last_result) if self._last_result else None
            status = self._probe_status.to_dict() if self._probe_status is not None else None
            backend = self._backend
            closed = self._closed
            pending = copy.deepcopy(list(self._pending.values()))
            approval_mode = self._approval_mode
            sandbox_value = self._sandbox
            network = self._network_access
        cfg = self._runtime_config()
        workspace = str(cfg.get("workspace") or "")
        if not workspace:
            try:
                from brain.runtime.codex_backend import default_workspace

                workspace = default_workspace()
            except Exception:
                workspace = ""
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "closed": closed,
            "running": running,
            "backend": str(getattr(backend, "name", "codex") or "codex"),
            "backendStatus": status,
            "goalMode": goal_mode_status(),
            "task": task,
            "lastResult": last,
            "limits": self._limits(),
            "workspace": workspace,
            "sandbox": sandbox_value,
            "approvalMode": approval_mode,
            "networkAccess": network,
            "pendingApprovals": pending,
        }

    def probe(self, force: bool = False) -> dict:
        backend = self._ensure_backend()
        status = backend.probe(force=force)
        with self._lock:
            self._probe_status = status
        return self.state()

    def on_terminal(self, callback) -> bool:
        """注册「任务进入终态」回调。

        回调在后台线程里被调用（不阻塞任务收尾线程），参数是终态结果 dict：
        ``status / reason / goal / taskId / sessionId / threadId / turnId /
        rounds / declines / elapsedSeconds / finishedAt / summary``。
        回灌通道（§3.3）用它把结论送回会话，不影响运行时本身的生命周期。
        """
        if not callable(callback):
            return False
        with self._lock:
            if callback not in self._terminal_callbacks:
                self._terminal_callbacks.append(callback)
        return True

    def mark_delivered(self, task_id: str) -> None:
        """调用方声明「这个任务的终态我已经在本轮同步带走了」。

        回灌通道据此跳过，避免同一件事在聊天里说两遍（工具直接等到结论的场景）。
        """
        task_id = str(task_id or "")
        if not task_id:
            return
        with self._lock:
            if task_id in self._delivered_tasks:
                self._delivered_tasks.remove(task_id)
            self._delivered_tasks.append(task_id)
            del self._delivered_tasks[:-50]

    def was_delivered(self, task_id: str) -> bool:
        task_id = str(task_id or "")
        if not task_id:
            return False
        with self._lock:
            return task_id in self._delivered_tasks

    def _dispatch_terminal(self, result: dict) -> None:
        # 注意：不论是同步带回还是回灌，都要派发 —— 回灌通道负责"落执行卡片"，
        # 是否再补一句转述由它自己按 was_delivered() 判断。
        with self._lock:
            callbacks = list(self._terminal_callbacks)
        for callback in callbacks:
            try:
                threading.Thread(
                    target=callback, args=(dict(result),),
                    name="lianxin-terminal-sink", daemon=True,
                ).start()
            except Exception as exc:
                self._log("执行引擎", "终态回调启动失败: %s" % exc)

    def server_events(self, after: int = 0) -> dict:
        data = runtime_events.events_after(after)
        with self._lock:
            data["running"] = bool(self._thread is not None and self._thread.is_alive())
        return data

    # ── 任务控制 ──────────────────────────────────────────
    def start(self, goal: str, *, session_id=None, turn: int = 0,
              sandbox: str = "", cwd: str = "", wait: bool = False,
              wait_timeout: float = 180.0, network_access: bool = False,
              approval_mode: str = "", resume_thread_id: str = "",
              goal_contract=None,
              _continuation: Optional[dict] = None) -> dict:
        goal = str(goal or "").strip()
        if not goal:
            return {"ok": False, "error": "任务目标为空"}
        if goal_contract is not None:
            try:
                if not isinstance(goal_contract, GoalContract):
                    GoalContract.from_dict(goal_contract)
            except GoalContractError as exc:
                return {"ok": False, "code": "goal_contract_invalid", "error": str(exc),
                        "goalMode": goal_mode_status()}
            capability = goal_mode_status()
            return {"ok": False, "code": "goal_mode_not_ready",
                    "error": capability["reason"], "goalMode": capability}
        cfg = self._runtime_config()
        workspace = str(cfg.get("workspace") or "")
        if not workspace:
            try:
                from brain.runtime.codex_backend import default_workspace

                workspace = default_workspace()
            except Exception:
                workspace = ""
        workspace, workspace_error = _resolve_workspace(workspace)
        if not workspace:
            return {"ok": False, "error": workspace_error}

        mode = str(approval_mode or cfg.get("approval_mode") or APPROVAL_MODE_DECLINE).lower()
        if mode in ("", "default"):
            mode = APPROVAL_MODE_DECLINE
        if mode not in (APPROVAL_MODE_ASK, APPROVAL_MODE_ACCEPT, APPROVAL_MODE_DECLINE):
            return {"ok": False, "error": "未知的审批模式：%s" % mode}
        requested = str(sandbox or cfg.get("sandbox") or "").lower()
        sandbox_value = (SANDBOX_WORKSPACE_WRITE if requested in
                         (SANDBOX_WORKSPACE_WRITE, "write", "workspace", "trusted")
                         else SANDBOX_READ_ONLY)
        network = bool(network_access) or bool(cfg.get("network_access"))
        resume_id = str(resume_thread_id or "").strip()
        if resume_id and _continuation is None:
            # 防呆：只有「被中断/被停止」的任务才值得接着做。过期卡片不能把
            # 已经完成的旧 thread 再拉起来重跑一遍。
            meta = session_store.read_meta(resume_id)
            if not meta or int(meta.get("lianxinSessionId") or 0) != int(session_id or 0):
                return {"ok": False, "error": "找不到属于当前会话的任务记录"}
            if meta:
                meta_status = str(meta.get("status") or "")
                if meta_status not in ("interrupted", "stopped"):
                    label = {
                        "running": "还在跑",
                        "completed": "已经做完了",
                        "failed": "已经失败收尾",
                    }.get(meta_status, "已经收尾")
                    return {"ok": False, "error": "这个任务%s，不需要接着做。" % label}
        with self._lock:
            if self._closed:
                return {"ok": False, "error": "执行运行时已关闭"}
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "error": "已有任务在执行中，请先停止它或等它结束",
                        "state": self.state()}
            if _continuation is not None:
                meta = session_store.read_meta(resume_id)
                error = self._continuation_error(meta, _continuation)
                if error:
                    return {"ok": False, "error": error}
                goal = str(meta.get("goal") or "")
                instruction = str(_continuation.get("text") or "").strip()
                if instruction:
                    goal += "\n\n本次追加要求：" + instruction
                cwd = str(meta.get("cwd") or "")
            self._generation += 1
            generation = self._generation
            self._stop_requested = False
            self._stop_reason = ""
            self._interrupt_key = None
            self._declines = 0
            self._rounds = 0
            self._stale = 0
            self._final = {}
            self._agent_text = []
            self._delta_chars = 0
            self._delta_published = 0
            self._seen_sigs = set()
            self._handle = None
            self._pending = {}
            self._card_seq = 0
            self._session_grants = set()
            self._approval_mode = mode
            self._sandbox = sandbox_value
            self._network_access = network
            self._task = {
                "taskId": "t-" + uuid.uuid4().hex,
                "goal": goal,
                # 终态投递方式：sync=本轮对话同步带回，sink=由回灌通道送会话。
                "delivery": "sync" if wait else "sink",
                "sessionId": int(session_id or 0),
                "turn": int(turn or 0),
                "startedAt": time.time(),
                "sandbox": sandbox_value,
                "requestedSandbox": requested or SANDBOX_READ_ONLY,
                "approvalMode": mode,
                "networkAccess": network,
                "cwd": _resolve_cwd(cwd, workspace),
                "workspace": workspace,
                "state": "starting",
                "plan": [],
                "currentStep": "",
                "planExplanation": "",
                "continuationMode": str((_continuation or {}).get("mode") or ""),
                # 非空 = 接着这个 codex thread 继续，不再新建会话。
                "resumeThreadId": resume_id,
            }
            thread = threading.Thread(target=self._run, args=(generation,),
                                      name="lianxin-task-runtime", daemon=True)
            self._thread = thread
            thread.start()
        self._log("执行引擎", "任务已受理: %s" % goal[:80])
        if wait:
            thread.join(timeout=max(1.0, float(wait_timeout)))
            if self.state().get("running"):
                # 等超时、任务还在跑：本轮已经拿不到结论了，改由回灌通道收尾，
                # 否则调用方被上层超时掐断后，结论两头落空。
                with self._lock:
                    if self._task:
                        self._task["delivery"] = "sink"
        return {"ok": True, "state": self.state()}

    @staticmethod
    def _continuation_error(meta: dict, request: dict) -> str:
        if not meta or int(meta.get("lianxinSessionId") or 0) != request.get("session_id"):
            return "找不到属于当前会话的任务记录"
        if (not meta.get("taskId") or meta.get("taskId") != request.get("task_id")
                or str(meta.get("lastTurnId") or "") != request.get("turn_id")):
            return "这张任务卡片已过期，请使用最新的任务卡片"
        allowed = {"resume": ("interrupted", "stopped"), "revise": ("completed",), "retry": ("failed",)}
        mode = request.get("mode")
        if str(meta.get("status") or "") not in allowed.get(mode, ()):
            return "任务状态已改变，不能执行这项后续操作"
        if mode in ("revise", "retry") and not str(request.get("text") or "").strip():
            return "请先填写本次修改要求或重试条件"
        return ""

    def continue_task(self, *, session_id, task_id: str, thread_id: str,
                      turn_id: str, mode: str, text: str = "") -> dict:
        request = {"session_id": session_id, "task_id": task_id, "turn_id": turn_id,
                   "mode": mode, "text": str(text or "").strip()}
        if not session_id or not task_id or not thread_id:
            return {"ok": False, "error": "缺少原任务身份，不能继续执行"}
        if len(request["text"]) > 12000:
            return {"ok": False, "error": "追加要求过长，请精简后重试"}
        meta = session_store.read_meta(thread_id)
        error = self._continuation_error(meta, request)
        if error:
            return {"ok": False, "error": error}
        return self.start(str(meta.get("goal") or "继续原任务"), session_id=session_id,
                          turn=int(meta.get("lianxinTurn") or 0), resume_thread_id=thread_id,
                          _continuation=request)

    def _scope_error(self, scope: Optional[dict], ready: bool = False) -> str:
        if scope is None:
            return ""
        task = self._task
        if (not scope.get("session_id") or not scope.get("task_id")
                or any(scope.get(key) != task.get(field, "") for key, field in
                       (("session_id", "sessionId"), ("task_id", "taskId"),
                        ("thread_id", "threadId"), ("turn_id", "turnId")))):
            return "任务身份已改变，请刷新执行面板后再操作"
        if (task.get("state") == "idle" or self._stop_requested or self._thread is None
                or not self._thread.is_alive()):
            return "任务已结束或正在停止，不能再追加操作"
        if ready and (self._handle is None or not task.get("turnId")):
            return "任务仍在启动，请稍后再补充要求"
        return ""

    def steer(self, text: str, *, scope: dict) -> dict:
        text = str(text or "").strip()
        if not text or len(text) > 12000:
            return {"ok": False, "error": "请填写不超过 12000 字的补充要求"}
        with self._lock:
            error = self._scope_error(scope, ready=True)
            if error:
                return {"ok": False, "error": error}
            handle = self._handle
            task_id = self._task.get("taskId")
        try:
            result = self._ensure_backend().steer(handle, text)
            response = result.to_dict()
        except Exception as exc:
            response = {"ok": False, "status": "uncertain", "message": str(exc)}
        if not response.get("ok"):
            response["error"] = response.get("message") or "补充要求未被当前执行轮确认，请勿自动重发"
        with self._lock:
            if task_id == self._task.get("taskId"):
                self._task["lastControl"] = {"status": response.get("status"),
                                             "message": response.get("message", ""), "at": time.time()}
        return response

    def stop(self, reason: str = "user", *, scope: Optional[dict] = None) -> dict:
        with self._lock:
            error = self._scope_error(scope)
            if error:
                return {"ok": False, "error": error}
            running = bool(self._thread is not None and self._thread.is_alive()
                           and self._task.get("state") != "idle")
            if not running:
                return {"ok": True, "stopped": False, "state": self.state()}
            task_id = self._task.get("taskId")
        self._request_stop(reason or "user", expected_task_id=task_id)
        return {"ok": True, "stopped": True, "state": self.state()}

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._stop_requested = True
            self._stop_reason = self._stop_reason or "运行时关闭"
            handle = self._handle
            backend = self._backend
        if handle is not None and backend is not None:
            try:
                backend.interrupt(handle)
            except Exception:
                pass
        if backend is not None:
            try:
                backend.shutdown()
            except Exception as exc:
                self._log("执行引擎", "关闭后端失败: %s" % exc)
        self._clear_pid()

    # ── 审批与中断 ────────────────────────────────────────
    def _decide(self, handle, method: str, params: dict, request_id=None) -> Optional[str]:
        """审批裁决。返回 None 表示挂起，等 approve() 回执（先预览再落盘）。"""
        with self._lock:
            if (handle is None or handle.generation != self._generation or self._closed
                    or self._stop_requested or self._task.get("state") == "idle"
                    or handle.thread_id != str(params.get("threadId") or "")
                    or handle.turn_id != str(params.get("turnId") or "")):
                return DECISION_DECLINE
            return self._decide_scoped(handle, method, params, request_id)

    def _decide_scoped(self, handle, method: str, params: dict, request_id=None) -> Optional[str]:
        mode = self._approval_mode
        if mode in (APPROVAL_MODE_DECLINE, APPROVAL_MODE_ACCEPT) or \
                str(method) in self._session_grants:
            auto_accept = (mode == APPROVAL_MODE_ACCEPT
                           or str(method) in self._session_grants)
            with self._lock:
                self._card_seq += 1
                card_id = "%s-ap%d" % (self._task.get("taskId"), self._card_seq)
            card = self._build_card(request_id, method, params)
            card["requestId"] = card_id
            card["rawRequestId"] = request_id
            card["auto"] = True
            self._publish_card(params, card)
            runtime_events.publish(
                runtime_events.APPROVAL_RESOLVED,
                thread_id=str(params.get("threadId") or ""),
                turn_id=str(params.get("turnId") or ""),
                requestId=card_id, itemId=params.get("itemId"),
                decision=DECISION_ACCEPT_FOR_SESSION if auto_accept else DECISION_DECLINE,
                paths=[str(item.get("path") or "") for item in (card.get("files") or [])],
                auto=True,
            )
            if auto_accept:
                with self._lock:
                    self._declines = 0
                return DECISION_ACCEPT_FOR_SESSION
            with self._lock:
                self._declines += 1
                declines = self._declines
                limit = self._limits()["max_declines"]
            if declines >= limit:
                self._request_stop("连续 %d 次审批被拒，任务已放弃" % declines)
            return DECISION_DECLINE

        with self._lock:
            self._card_seq += 1
            card_id = "%s-ap%d" % (self._task.get("taskId"), self._card_seq)
        card = self._build_card(request_id, method, params)
        card["requestId"] = card_id
        card["rawRequestId"] = request_id
        card["auto"] = False
        with self._lock:
            self._pending[card_id] = card
        self._publish_card(params, card)
        self._log("执行引擎", "等待用户确认: %s（%s）" % (card.get("title"), card.get("approvalKind")))
        return None

    def _publish_card(self, params: dict, card: dict) -> None:
        runtime_events.publish(
            runtime_events.APPROVAL_REQUESTED,
            thread_id=str(params.get("threadId") or ""),
            turn_id=str(params.get("turnId") or ""),
            **card,
        )

    def _request_stop(self, reason: str, expected_task_id: Optional[str] = None) -> None:
        with self._lock:
            if expected_task_id is not None and (expected_task_id != self._task.get("taskId")
                                                 or self._task.get("state") == "idle"):
                return
            self._stop_requested = True
            if not self._stop_reason:
                self._stop_reason = str(reason or "任务已停止")
            self._task["state"] = "stopping"
            self._task["stopReason"] = self._stop_reason
            handle = self._handle
        self._decline_pending("任务停止")
        if handle is not None:
            self._interrupt_async(handle)

    def _interrupt_async(self, handle) -> None:
        """必须异步：调用方可能在 stdout 泵线程上，同步请求会死锁。"""
        with self._lock:
            key = (handle.generation, handle.thread_id, handle.turn_id)
            if self._interrupt_key == key:
                return
            self._interrupt_key = key
            backend = self._backend

        def _worker():
            try:
                if backend is not None:
                    result = backend.interrupt(handle)
                    if result is not None and not result.ok:
                        self._log("执行引擎", "中断确认: %s %s" % (result.status, result.message))
            except Exception as exc:
                self._log("执行引擎", "中断失败: %s" % exc)

        threading.Thread(target=_worker, name="lianxin-runtime-interrupt", daemon=True).start()

    # ── 审批生命周期（阶段 2 核心：先预览再落盘）───────────
    def _backend_changes(self, item_id, method: str = "") -> list:
        try:
            return self._ensure_backend().changes_for(item_id, method)
        except Exception:
            return []

    def _build_card(self, request_id, method: str, params: dict) -> dict:
        method = str(method or "")
        kind = {
            "item/fileChange/requestApproval": "fileChange",
            "applyPatchApproval": "fileChange",
            "item/commandExecution/requestApproval": "command",
            "execCommandApproval": "command",
            "item/permissions/requestApproval": "permissions",
        }.get(method, "other")
        files = self._backend_changes(params.get("itemId"), method)
        if not files:
            # 兜底：补丁正文藏在命令里时，也能给出 diff 预览
            files = _files_from_patch_text(params.get("command"))
            if files and kind == "command":
                kind = "command+patch"
        if kind in ("fileChange", "command+patch"):
            title = ("莲心准备改动 %d 个文件" % len(files)) if files else "莲心准备写入文件"
        elif kind == "command":
            title = "莲心准备执行一条命令"
        elif kind == "permissions":
            title = "莲心申请额外权限"
        else:
            title = "莲心在请求确认"
        return {
            "requestId": request_id,
            "taskId": self._task.get("taskId"),
            "sessionId": self._task.get("sessionId"),
            "threadId": str(params.get("threadId") or ""),
            "turnId": str(params.get("turnId") or ""),
            "rawRequestId": request_id,
            "method": method,
            "approvalKind": kind,
            "title": title,
            "itemId": params.get("itemId"),
            "command": _short_text(params.get("command"), 400),
            "cwd": str(params.get("cwd") or ""),
            "reason": _short_text(params.get("reason"), 200),
            "files": files,
            "additions": sum(int(item.get("additions") or 0) for item in files),
            "deletions": sum(int(item.get("deletions") or 0) for item in files),
            "askedAt": time.time(),
            "turnDeclines": self._declines,
            "decisions": [DECISION_ACCEPT, DECISION_ACCEPT_FOR_SESSION, DECISION_DECLINE, "cancel"],
        }

    def approve(self, request_id, decision: str = DECISION_ACCEPT, remember: bool = False,
                *, scope: Optional[dict] = None, reason: str = "") -> dict:
        """回执一次挂起的审批；remember=True 等价于"本轮都允许"（acceptForSession）。"""
        declines = 0
        limit = self._limits()["max_declines"]
        with self._lock:
            error = self._scope_error(scope, ready=True)
            if error:
                return {"ok": False, "error": error}
            card = self._pending.pop(str(request_id), None)
            if card is None:
                return {"ok": False, "error": "审批请求不存在或已处理"}
            handle = self._handle
            raw_request_id = card.get("rawRequestId")
            decision = str(decision or DECISION_DECLINE)
            if decision not in (DECISION_ACCEPT, DECISION_ACCEPT_FOR_SESSION,
                                DECISION_DECLINE, "cancel"):
                decision = DECISION_DECLINE
            if remember and decision == DECISION_ACCEPT:
                decision = DECISION_ACCEPT_FOR_SESSION
            task_id = self._task.get("taskId")
            thread_id = str(self._task.get("threadId") or "")
            turn_id = str(self._task.get("turnId") or "")
        emitted = False
        try:
            if handle is not None:
                emitted = self._ensure_backend().approve(handle, raw_request_id, decision) is True
        except Exception as exc:
            self._log("执行引擎", "审批回执失败: %s" % exc)
        with self._lock:
            if task_id != self._task.get("taskId"):
                return {"ok": False, "error": "审批所属任务已改变"}
            self._task["lastApproval"] = {
                "requestId": str(request_id), "decision": decision,
                "reason": reason or ("" if emitted else "审批回执未确认，任务已停止"),
                "emitted": emitted, "at": time.time(),
            }
            if emitted and decision in (DECISION_ACCEPT, DECISION_ACCEPT_FOR_SESSION):
                if decision == DECISION_ACCEPT_FOR_SESSION:
                    self._session_grants.add(str(card.get("method") or ""))
                self._declines = 0
            elif emitted:
                self._declines += 1
            declines = self._declines
        runtime_events.publish(
            runtime_events.APPROVAL_RESOLVED, thread_id=thread_id, turn_id=turn_id,
            requestId=card.get("requestId"), rawRequestId=raw_request_id,
            decision=decision, itemId=card.get("itemId"),
            paths=[str(item.get("path") or "") for item in (card.get("files") or [])],
            auto=False, emitted=emitted, reason=reason,
        )
        if not emitted:
            self._request_stop("审批回执未确认，已停止任务；请核对结果后再操作", expected_task_id=task_id)
            return {"ok": False, "decision": decision, "emitted": False,
                    "error": "审批回执未确认，已停止任务，请勿重复授权"}
        if decision == "cancel":
            self._request_stop("用户取消了这一步", expected_task_id=task_id)
        elif decision == DECISION_DECLINE and declines >= limit:
            self._request_stop("连续 %d 次审批被拒，任务已放弃" % declines, expected_task_id=task_id)
        return {"ok": True, "decision": decision, "emitted": emitted}

    def _expire_approvals(self) -> list:
        timeout = self._limits()["approval_timeout_seconds"]
        now = time.time()
        with self._lock:
            expired = [rid for rid, card in self._pending.items()
                       if now - float(card.get("askedAt") or now) >= timeout]
        for rid in expired:
            self._log("执行引擎", "审批超时 %.0fs 未响应，自动拒绝: %s" % (timeout, rid))
            runtime_events.publish(runtime_events.APPROVAL_TIMEOUT, requestId=rid,
                                   timeoutSeconds=timeout)
            self.approve(rid, DECISION_DECLINE, reason="审批等待超时，已自动拒绝")
        return expired

    def _decline_pending(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
            handle = self._handle
            if pending:
                self._task["lastApproval"] = {"decision": DECISION_DECLINE,
                                             "reason": reason, "at": time.time()}
        if not pending:
            return
        self._log("执行引擎", "清理 %d 个未决审批（%s）" % (len(pending), reason))
        for card in pending:
            try:
                if handle is not None:
                    self._ensure_backend().approve(handle, card.get("rawRequestId"), DECISION_DECLINE)
            except Exception as exc:
                self._log("执行引擎", "清理审批回执失败: %s" % exc)

    # ── 工作线程 ──────────────────────────────────────────
    def _run(self, generation: int) -> None:
        backend = self._ensure_backend()
        with self._lock:
            task = dict(self._task)
            limits = self._limits()
        spec = TaskSpec(
            goal=self._goal_text(task.get("goal") or "", str(task.get("approvalMode") or "")),
            cwd=str(task.get("cwd") or ""),
            session_id=int(task.get("sessionId") or 0) or None,
            turn=int(task.get("turn") or 0),
            sandbox=str(task.get("sandbox") or SANDBOX_READ_ONLY),
            approval_policy=APPROVAL_ON_REQUEST,
            config=self._model_config(str(task.get("sandbox") or ""),
                                      bool(task.get("networkAccess"))),
            resume_thread_id=str(task.get("resumeThreadId") or ""),
            generation=generation,
        )
        with self._lock:
            stopped = self._stop_requested or self._closed
        if stopped:
            self._finish(generation, status="stopped", reason=self._stop_reason or "任务已停止")
            return
        try:
            handle = backend.start(spec)
        except Exception as exc:
            self._finish(generation, status="failed", reason="执行引擎启动失败：%s" % exc)
            return
        with self._lock:
            if generation != self._generation:
                return
            self._handle = handle
            self._task["threadId"] = handle.thread_id
            self._task["turnId"] = handle.turn_id
            self._task["state"] = "running"
        self._write_pid(handle)
        self._write_meta(handle, task)
        runtime_events.publish(
            runtime_events.TASK_STARTED, thread_id=handle.thread_id, turn_id=handle.turn_id,
            goal=str(task.get("goal") or ""), cwd=str(task.get("cwd") or ""),
            sandbox=SANDBOX_READ_ONLY, model=self._backend_model(),
        )
        self._log("执行引擎", "任务开始: thread=%s cwd=%s" % (handle.thread_id, task.get("cwd")))

        deadline = time.time() + limits["timeout_seconds"]
        reason = ""
        while True:
            if self._stop_requested:
                reason = self._stop_reason or "任务已停止"
                break
            for event in backend.events(0.5):
                self._consume(event, generation)
            if self._stop_requested:
                reason = self._stop_reason or "任务已停止"
                break
            if self._final:
                break
            if time.time() > deadline:
                reason = "超过 %d 分钟上限" % int(limits["timeout_seconds"] // 60)
                break
            if self._rounds >= limits["max_rounds"]:
                reason = "达到 %d 轮动作上限" % limits["max_rounds"]
                break
            if self._stale >= limits["max_stale_rounds"]:
                reason = "连续 %d 轮无进展" % limits["max_stale_rounds"]
                break
            if self._declines >= limits["max_declines"]:
                reason = "连续 %d 次审批被拒，任务已放弃" % limits["max_declines"]
                break
            if self._expire_approvals():
                continue
            if not self._backend_alive(backend):
                reason = "执行引擎进程已退出"
                break

        final_status = str(self._final.get("finalStatus") or "")
        if self._stop_requested:
            status = "stopped"
            reason = self._stop_reason or "任务已停止"
        elif final_status == "completed":
            status = "completed"
        elif final_status == "interrupted":
            status = "interrupted"
        elif final_status == "failed":
            status = "failed"
            reason = reason or str(self._final.get("error") or "执行失败")
        else:
            status = "stopped" if reason else "failed"
        if not self._final:
            self._interrupt_async(handle)
        self._finish(generation, status=status, reason=reason)

    def _consume(self, event, generation: Optional[int] = None) -> None:
        with self._lock:
            handle = self._handle
            if (handle is None or self._stop_requested or self._final
                    or (generation is not None and generation != self._generation)):
                return
            if str(getattr(event, "thread_id", "") or "") != handle.thread_id:
                return
            if int(getattr(event, "generation", 0)) != handle.generation:
                return
            event_turn = str(getattr(event, "turn_id", "") or "")
            if event_turn != handle.turn_id:
                if event_turn or str(getattr(event, "kind", "")) != runtime_events.NOTE:
                    return
        kind = str(getattr(event, "kind", "") or "")
        payload = dict(getattr(event, "payload", None) or {})
        if not kind:
            return
        if kind == runtime_events.TURN_FINISHED:
            # 内部事件：只用于判定任务结束，不进入界面事件流
            with self._lock:
                self._final = payload
            return

        if kind == runtime_events.APPROVAL_REQUESTED:
            # 后端只留原始记录；对外的卡片由本层 _build_card 发（带 diff 与计数）
            return

        published = payload
        if kind == runtime_events.NOTE and payload.get("agentFinal"):
            with self._lock:
                self._agent_text = [str(payload.get("agentFinal") or "")]
                self._delta_chars = 0
                self._delta_published = 0
            return
        if kind == runtime_events.NOTE and payload.get("agentDelta"):
            # 模型逐字输出：累积下来做收尾汇总，按步长节流成进度提示，
            # 否则一次任务的上百条增量会把运行时事件环（400 条）挤满。
            delta = str(payload.get("agentDelta") or "")
            with self._lock:
                self._agent_text.append(delta)
                if len(self._agent_text) > 400:
                    del self._agent_text[:100]
                self._delta_chars += len(delta)
                if self._delta_chars - self._delta_published < _DELTA_PUBLISH_STEP:
                    return
                self._delta_published = self._delta_chars
                chars = self._delta_chars
            kind = runtime_events.TASK_PROGRESS
            published = {"message": "执行引擎生成中…", "chars": chars}

        with self._lock:
            if kind == runtime_events.TOOL_STARTED:
                # "无进展"= 同一个动作反复重来（换着法重试不算），而不是"这轮失败了"
                sig = "%s|%s" % (published.get("tool"), str(published.get("detail") or "")[:160])
                if sig in self._seen_sigs:
                    self._stale += 1
                else:
                    self._seen_sigs.add(sig)
                    self._stale = 0
            elif kind == runtime_events.TOOL_FINISHED:
                self._rounds += 1
            elif kind == runtime_events.PLAN_UPDATED:
                self._stale = 0
                raw_plan = published.get("plan")
                plan = []
                for item in (raw_plan[:40] if isinstance(raw_plan, list) else []):
                    if not isinstance(item, dict):
                        continue
                    step = str(item.get("step") or "").strip()[:500]
                    status = str(item.get("status") or "pending")
                    status = "in_progress" if status in ("inProgress", "in_progress") else status
                    if step and status in ("pending", "in_progress", "completed"):
                        plan.append({"step": step, "status": status})
                self._task["plan"] = plan
                self._task["currentStep"] = next(
                    (item["step"] for item in plan if item["status"] == "in_progress"), "")
                self._task["planExplanation"] = str(published.get("explanation") or "")[:1500]
                self._task["planUpdatedAt"] = time.time()
                session_store.update_meta(handle.thread_id, {
                    "plan": copy.deepcopy(plan), "currentStep": self._task["currentStep"],
                    "planExplanation": self._task["planExplanation"],
                })
        runtime_events.publish(kind, thread_id=getattr(event, "thread_id", "") or "",
                               turn_id=getattr(event, "turn_id", "") or "", **published)

    def _finish(self, generation: int, status: str, reason: str = "") -> None:
        with self._lock:
            if generation != self._generation:
                return
            if self._stop_requested:
                status = "stopped"
                reason = self._stop_reason or "任务已停止"
            task = dict(self._task)
            result = {
                "status": status,
                "reason": str(reason or ""),
                "goal": str(task.get("goal") or ""),
                "taskId": str(task.get("taskId") or ""),
                "delivery": str(task.get("delivery") or "sink"),
                "sessionId": int(task.get("sessionId") or 0),
                "turn": int(task.get("turn") or 0),
                "workspace": str(task.get("workspace") or ""),
                "threadId": str(task.get("threadId") or ""),
                "turnId": str(task.get("turnId") or ""),
                "rounds": self._rounds,
                "declines": self._declines,
                "elapsedSeconds": round(time.time() - float(task.get("startedAt") or time.time()), 1),
                "finishedAt": time.time(),
                "summary": self._agent_summary(),
                "plan": copy.deepcopy(task.get("plan") or []),
                "currentStep": str(task.get("currentStep") or ""),
                "lastApproval": copy.deepcopy(task.get("lastApproval")),
            }
            self._last_result = result
            self._task["state"] = "idle"
            self._handle = None
        self._decline_pending("任务收尾")
        self._clear_pid()
        self._update_meta(result)
        kind = {
            "completed": runtime_events.TASK_COMPLETED,
            "failed": runtime_events.TASK_FAILED,
        }.get(status, runtime_events.TASK_INTERRUPTED)
        runtime_events.publish(kind, thread_id=result["threadId"], turn_id=result["turnId"], **result)
        self._log("执行引擎", "任务结束: status=%s 轮次=%s 拒绝=%s 耗时=%.1fs %s" % (
            status, result["rounds"], result["declines"], result["elapsedSeconds"], reason))
        self._dispatch_terminal(result)

    def _agent_summary(self) -> str:
        text = "".join(self._agent_text)
        return text[-_MAX_AGENT_TEXT:] if len(text) > _MAX_AGENT_TEXT else text

    def _backend_model(self) -> str:
        try:
            with self._lock:
                status = self._probe_status
            return str(getattr(status, "model", "") or "")
        except Exception:
            return ""

    @staticmethod
    def _backend_alive(backend) -> bool:
        try:
            client = getattr(backend, "_client", None)
            if client is None:
                return True
            return bool(client.alive())
        except Exception:
            return True

    # ── 持久线程元数据（阶段 3b）──────────────────────────
    def _write_meta(self, handle, task: dict) -> None:
        """任务一开始就落盘 threadId：进程被杀时，下次启动才知道有活没干完。"""
        thread_id = str(getattr(handle, "thread_id", "") or "")
        if not thread_id:
            return
        try:
            session_store.write_meta(thread_id, {
                "taskId": str(task.get("taskId") or ""),
                "lianxinSessionId": int(task.get("sessionId") or 0),
                "lianxinTurn": int(task.get("turn") or 0),
                "goal": str(task.get("goal") or ""),
                "cwd": str(task.get("cwd") or ""),
                "workspace": str(task.get("workspace") or ""),
                "sandbox": str(task.get("sandbox") or ""),
                "approvalMode": str(task.get("approvalMode") or ""),
                "status": "running",
                "startedAt": float(task.get("startedAt") or time.time()),
                "lastTurnId": str(getattr(handle, "turn_id", "") or ""),
                "interrupted": False,
                "cardSaved": False,
            })
        except Exception as exc:
            self._log("执行引擎", "写入线程元数据失败: %s" % exc)

    def _update_meta(self, result: dict) -> None:
        thread_id = str(result.get("threadId") or "")
        if not thread_id:
            return
        status = str(result.get("status") or "")
        try:
            session_store.update_meta(thread_id, {
                "taskId": str(result.get("taskId") or ""),
                "status": status,
                "plan": copy.deepcopy(result.get("plan") or []),
                "currentStep": str(result.get("currentStep") or ""),
                "lastApproval": copy.deepcopy(result.get("lastApproval")),
                "rounds": int(result.get("rounds") or 0),
                "elapsedSeconds": result.get("elapsedSeconds") or 0,
                "reason": str(result.get("reason") or ""),
                "lastTurnId": str(result.get("turnId") or ""),
                "summary": str(result.get("summary") or "")[:4000],
                "finishedAt": float(result.get("finishedAt") or time.time()),
                "interrupted": status in ("interrupted", "stopped", "failed"),
            })
        except Exception as exc:
            self._log("执行引擎", "更新线程元数据失败: %s" % exc)

    def recover_interrupted(self) -> dict:
        """启动时把上次没来得及收尾的任务补成卡片。

        - 退出时还在跑（status=running）：按「非预期中断」处理，给出「接着做」入口；
        - 已经收尾、但卡片没来得及落库：按真实终态补一张，避免结果凭空消失；
        - 已经落过卡片的不再重复播报（cardSaved）。
        """
        try:
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    return {"ok": True, "recovered": 0, "threads": []}
            metas = session_store.iter_metas()
        except Exception as exc:
            self._log("执行引擎", "扫描未完成任务失败: %s" % exc)
            return {"ok": False, "error": str(exc), "recovered": 0, "threads": []}

        now = time.time()

        def _num(value, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        pending = []
        for meta in metas:
            thread_id = str(meta.get("threadId") or "")
            if not thread_id:
                continue
            status = str(meta.get("status") or "")
            if status == "running":
                pending.append((meta, True, "interrupted",
                                "莲心上次被关闭时，这个任务还没跑完"))
            elif not meta.get("cardSaved") and status in (
                    "completed", "failed", "interrupted", "stopped"):
                if status in ("interrupted", "stopped"):
                    pending.append((meta, True, "interrupted",
                                    "上次关闭前这个任务没有正常收尾"))
                else:
                    pending.append((meta, False, status,
                                    str(meta.get("reason") or "上次关闭前没来得及把结果告诉你")))

        recovered = []
        for meta, resumable, target_status, reason in pending:
            thread_id = str(meta.get("threadId") or "")
            started = _num(meta.get("startedAt"), now)
            elapsed = _num(meta.get("elapsedSeconds"), 0.0) or max(0.0, now - started)
            result = {
                "status": target_status,
                "reason": reason,
                "goal": str(meta.get("goal") or ""),
                "taskId": str(meta.get("taskId") or "recover-%s" % hashlib.sha1(thread_id.encode("utf-8")).hexdigest()[:10]),
                "delivery": "sink",
                "sessionId": int(_num(meta.get("lianxinSessionId"))),
                "turn": int(_num(meta.get("lianxinTurn"))),
                "workspace": str(meta.get("workspace") or ""),
                "threadId": thread_id,
                "turnId": str(meta.get("lastTurnId") or ""),
                "rounds": int(_num(meta.get("rounds"))),
                "declines": 0,
                "elapsedSeconds": round(elapsed, 1),
                "finishedAt": _num(meta.get("finishedAt"), now),
                "summary": str(meta.get("summary") or ""),
                "lastApproval": copy.deepcopy(meta.get("lastApproval")),
                "recovered": bool(resumable),
                "skipNarration": True,
            }
            try:
                session_store.update_meta(thread_id, {
                    "taskId": result["taskId"],
                    "status": target_status,
                    "interrupted": bool(resumable),
                    "reason": reason,
                    "updatedAt": now,
                })
            except Exception:
                pass
            recovered.append(thread_id)
            self._dispatch_terminal(result)
        if recovered:
            self._log("执行引擎", "发现 %d 个上次没来得及收尾的任务，已补记卡片" % len(recovered))
        return {"ok": True, "recovered": len(recovered), "threads": recovered}

    def cleanup_sessions(self, max_age_days: float = 7.0, keep: int = 200) -> dict:
        """按「7 天 / 最近 200 条」清理过期执行记录（先到者为准）。"""
        try:
            removed = session_store.prune(max_age_days=max_age_days, keep=keep)
        except Exception as exc:
            self._log("执行引擎", "清理过期执行记录失败: %s" % exc)
            return {"ok": False, "error": str(exc), "removed": 0}
        if not removed:
            return {"ok": True, "removed": 0, "threads": []}

        def _delete():
            backend = self._ensure_backend()
            for thread_id in removed:
                try:
                    backend.delete_session(thread_id)
                except Exception as exc:
                    self._log("执行引擎", "删除过期线程失败 %s: %s" % (thread_id, exc))

        threading.Thread(target=_delete, name="lianxin-runtime-prune", daemon=True).start()
        self._log("执行引擎", "清理 %d 个过期执行记录" % len(removed))
        return {"ok": True, "removed": len(removed), "threads": removed}

    # ── 孤儿进程记录 ──────────────────────────────────────
    def _check_orphan_pid(self) -> None:
        try:
            if not _PID_FILE.is_file():
                return
            data = json.loads(_PID_FILE.read_text(encoding="utf-8") or "{}")
        except Exception:
            self._clear_pid()
            return
        try:
            pid = int(data.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            self._log("执行引擎", "上次执行引擎进程仍在运行 (pid=%s)，如无异常可忽略" % pid)
            return
        self._clear_pid()
        self._log("执行引擎", "已清理上次遗留的执行引擎记录")

    def _write_pid(self, handle) -> None:
        pid = 0
        try:
            pid = int(self._ensure_backend().pid() or 0)
        except Exception:
            pid = 0
        try:
            _PID_DIR.mkdir(parents=True, exist_ok=True)
            _PID_FILE.write_text(
                json.dumps({"pid": pid, "threadId": str(getattr(handle, "thread_id", "") or ""),
                            "startedAt": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            self._log("执行引擎", "写入运行记录失败: %s" % exc)

    @staticmethod
    def _clear_pid() -> None:
        try:
            if _PID_FILE.is_file():
                os.remove(str(_PID_FILE))
        except Exception:
            pass


_RUNTIME: Optional[TaskRuntime] = None
_RUNTIME_LOCK = threading.Lock()


def get_task_runtime() -> TaskRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            _RUNTIME = TaskRuntime()
            atexit.register(_RUNTIME.shutdown)
        return _RUNTIME
