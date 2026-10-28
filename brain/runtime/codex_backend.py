"""Codex 执行后端（阶段 1：只读接入）。

协议要点全部来自本机实测（codex-cli 0.146.0-alpha.9.2）：
- 链路：initialize -> thread/start -> turn/start -> 通知流 -> 反向审批 -> turn/completed
- 反向请求与服务端通知共用同一 stdout JSONL 通道，反向请求必须原样回执 id
- 沙箱档位取值 read-only / workspace-write / danger-full-access
- 审批策略取值 never / on-request / untrusted
- 命令审批回执 {"decision": "accept"|"acceptForSession"|"decline"|"cancel"}

阶段 1 约束：
- 沙箱固定 read-only，工作目录限定在专用沙箱目录
- **所有审批一律拒绝**（只观察不落地）
- thread 用 ephemeral=false 持久化，重启后可 thread/resume 接着做
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from brain.runtime import events as runtime_events
from brain.runtime.backend import (
    APPROVAL_ON_REQUEST,
    BackendStatus,
    ControlResult,
    DECISION_DECLINE,
    SANDBOX_READ_ONLY,
    SessionHandle,
    TaskSpec,
)

_APPROVAL_REQUEST_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "execCommandApproval",
    "applyPatchApproval",
}

_LEGACY_APPROVAL_METHODS = {"execCommandApproval", "applyPatchApproval"}

# Codex 的 item.type -> 工具信息栏里显示的工具名
_ITEM_TOOL_NAMES = {
    "commandExecution": "codex_exec",
    "fileChange": "codex_write",
    "mcpToolCall": "codex_mcp",
    "webSearch": "codex_web_search",
    "imageGeneration": "codex_image",
}

_SKIP_ITEM_TYPES = {"userMessage", "agentMessage", "reasoning"}

# 内部哨兵：反向请求先不回执，等调用方通过 approve() 决定
_NO_REPLY = object()


class CodexRpcError(RuntimeError):
    def __init__(self, method: str, error: dict) -> None:
        self.method = method
        self.code = error.get("code")
        super().__init__("%s: %s" % (method, _short(error.get("message") or error)))


class CodexOutcomeUnknown(TimeoutError):
    pass


def _checked_result(method: str, response: dict) -> dict:
    if response.get("error") is not None:
        raise CodexRpcError(method, response["error"])
    result = response.get("result")
    if not isinstance(result, dict):
        raise CodexOutcomeUnknown("%s 未返回可确认的结果，不自动重试" % method)
    return result


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(name or "")).lower()


def _short(value, limit: int = 200) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _version_from_user_agent(user_agent: str) -> str:
    """从 `codex_vscode/0.146.0-alpha.9.2 (Windows ...)` 里取出版本号。"""
    match = re.search(r"/\s*([0-9][0-9A-Za-z.\-+]*)", str(user_agent or ""))
    return match.group(1) if match else ""


def codex_missing_hint() -> str:
    """找不到 codex 时给用户看的可操作提示（不要暴露 WinError 87）。"""
    return (
        "未找到 codex 可执行文件。任选其一即可修好："
        "① 把 codex.exe 的绝对路径填进 config.json 的 runtime.codex_bin；"
        "② 设置环境变量 LIANXIN_CODEX_BIN 指向 codex.exe；"
        "③ 安装 VS Code 的 Codex 扩展（已安装会被自动发现，无需配置）。"
    )


def _vscode_codex_candidates() -> list:
    """VS Code / Cursor 扩展自带的 codex CLI。

    桌面双击启动的进程 PATH 里没有扩展目录，只能按安装位置直接找；
    `shutil.which` 在那种环境下一定扑空，随后 Popen("") 会抛 WinError 87。
    """
    home = Path.home()
    found: list = []
    exe_name = "codex.exe" if os.name == "nt" else "codex"
    for ext_root in (".vscode", ".vscode-insiders", ".cursor"):
        root = home / ext_root / "extensions"
        if not root.is_dir():
            continue
        for package in root.glob("openai.chatgpt-*"):
            for layout in ("windows-x86_64", "darwin-arm64", "darwin-x86_64", "linux-x86_64"):
                candidate = package / "bin" / layout / exe_name
                if candidate.is_file():
                    found.append(candidate)
    return found


def _codex_candidate_sort_key(candidate: Path):
    """按 codex-package.json 里的版本号排序（目录名排序不可靠：26.1007 < 26.727）。"""
    version = ""
    try:
        meta = json.loads(
            (candidate.parent / "codex-package.json").read_text(encoding="utf-8")
        )
        version = str(meta.get("version") or "")
    except Exception:
        version = ""
    numbers = tuple(int(part) for part in re.findall(r"\d+", version)) if version else ()
    try:
        mtime = candidate.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (numbers, mtime)


def _known_codex_paths() -> list:
    """常见安装位置（不依赖 PATH）。"""
    home = Path.home()
    paths = _vscode_codex_candidates()
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")):
        if base:
            paths.append(Path(base) / "Programs" / "codex" / "codex.exe")
    paths.append(home / ".codex" / "bin" / "codex.exe")
    return paths


def default_codex_bin() -> str:
    """按 环境变量 -> 配置 -> vendor -> 常见安装位置 -> PATH 顺序寻找 codex。"""
    override = str(os.environ.get("LIANXIN_CODEX_BIN") or "").strip()
    if override and Path(override).is_file():
        return override

    try:
        from config import get_runtime_config

        configured = str(get_runtime_config().get("codex_bin") or "").strip()
    except Exception:
        configured = ""
    if configured and os.path.exists(configured):
        return configured

    vendor = Path(__file__).resolve().parents[2] / "vendor" / "codex"
    if vendor.is_dir():
        for child in sorted(vendor.iterdir(), reverse=True):
            if child.is_dir():
                for exe_name in ("codex.exe", "codex"):
                    candidate = child / exe_name
                    if candidate.is_file():
                        return str(candidate)

    discovered = sorted(_vscode_codex_candidates(), key=_codex_candidate_sort_key, reverse=True)
    if discovered:
        return str(discovered[0])

    for candidate in _known_codex_paths():
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue

    return shutil.which("codex") or ""


def default_workspace() -> str:
    """阶段 1 的专用沙箱目录：仓库之外，避免任何误伤。"""
    try:
        from config import get_runtime_config

        configured = str(get_runtime_config().get("workspace") or "").strip()
    except Exception:
        configured = ""
    if configured:
        return configured
    return str(Path.home() / ".lianxin" / "runtime" / "workspace")


class CodexAppServerClient:
    """最小 JSON-RPC over stdio 客户端。"""

    def __init__(self, exe: str, on_notification: Optional[Callable] = None,
                 on_server_request: Optional[Callable] = None, log: Optional[Callable] = None) -> None:
        self.exe = exe
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._log = log or (lambda *_a, **_k: None)
        self.proc: Optional[subprocess.Popen] = None
        self.stderr_lines: list = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._waiters: dict = {}
        self._results: dict = {}
        self._alive = False

    # ── 生命周期 ──────────────────────────────────────────────
    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if not str(self.exe or "").strip():
            # 空字符串会走到 CreateProcess("") 并抛 WinError 87（参数错误），
            # 在这里直接给出可读原因，别让用户看到系统错误码。
            raise RuntimeError(codex_missing_hint())
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [self.exe, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self._alive = True
        threading.Thread(target=self._pump_stdout, daemon=True, name="codex-stdout").start()
        threading.Thread(target=self._pump_stderr, daemon=True, name="codex-stderr").start()

    def alive(self) -> bool:
        return bool(self.proc is not None and self.proc.poll() is None)

    def pid(self) -> int:
        """codex app-server 的 PID；0 表示未启动。"""
        proc = self.proc
        try:
            return int(proc.pid) if proc is not None and proc.poll() is None else 0
        except Exception:
            return 0

    def terminate(self) -> None:
        self._alive = False
        self._fail_pending()
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        children = []
        try:
            import psutil

            if proc.poll() is None:
                children = psutil.Process(proc.pid).children(recursive=True)
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        if children:
            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass
            try:
                _, survivors = psutil.wait_procs(children, timeout=2)
                for child in survivors:
                    try:
                        child.kill()
                    except Exception:
                        pass
                psutil.wait_procs(survivors, timeout=2)
            except Exception:
                pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    def _fail_pending(self) -> None:
        with self._lock:
            for mid, waiter in self._waiters.items():
                self._results.setdefault(mid, {"transportClosed": True})
                waiter.set()

    # ── IO 泵 ────────────────────────────────────────────────
    def _pump_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if not self._alive:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                self._log("codex-raw", line[:200])
                continue
            if "method" in msg and "id" in msg:
                self._handle_server_request(msg)
            elif "method" in msg:
                self._dispatch_notification(msg)
            elif "id" in msg:
                mid = msg["id"]
                with self._lock:
                    waiter = self._waiters.get(mid)
                    if waiter is not None:
                        self._results[mid] = msg
                        waiter.set()
        self._alive = False
        self._fail_pending()

    def _pump_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            if not self._alive:
                break
            self.stderr_lines.append(line.rstrip())
            if len(self.stderr_lines) > 400:
                del self.stderr_lines[:100]

    def _dispatch_notification(self, msg: dict) -> None:
        try:
            if self._on_notification is not None:
                self._on_notification(msg)
        except Exception as exc:
            self._log("codex-notify-error", str(exc))

    def _handle_server_request(self, msg: dict) -> None:
        handler = self._on_server_request
        result = None
        error = None
        if handler is not None:
            try:
                result, error = handler(msg)
            except Exception as exc:
                self._log("codex-request-error", str(exc))
        if result is _NO_REPLY:
            return
        if error is not None:
            self.reply(msg.get("id"), error=error)
        else:
            self.reply(msg.get("id"), result=result if result is not None else {})

    # ── 写入 ─────────────────────────────────────────────────
    def _write(self, obj: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("codex app-server 未启动")
        with self._write_lock:
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()

    def notify(self, method: str, params=None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params=None, timeout: float = 90) -> dict:
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            waiter = threading.Event()
            self._waiters[mid] = waiter
        try:
            self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
            if not waiter.wait(timeout):
                raise CodexOutcomeUnknown("等待 %s 响应超时(%ss)，结果不确定，不自动重试" % (method, timeout))
            with self._lock:
                response = self._results.pop(mid, {})
            if response.get("transportClosed"):
                raise CodexOutcomeUnknown("%s 连接中断，结果不确定，不自动重试" % method)
            _checked_result(method, response)
            return response
        finally:
            with self._lock:
                self._waiters.pop(mid, None)
                self._results.pop(mid, None)

    def reply(self, req_id, result=None, error=None) -> bool:
        msg = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        try:
            self._write(msg)
            return True
        except Exception:
            return False


class CodexExecutionBackend:
    """把 codex app-server 包装成莲心的执行后端（阶段 1 只读）。"""

    name = "codex"

    def __init__(self, exe: str = "", pinned_version: str = "", log: Optional[Callable] = None) -> None:
        self._log = log or (lambda *_a, **_k: None)
        self._exe = exe or default_codex_bin()
        self._pinned_version = str(pinned_version or "").strip()
        self._client: Optional[CodexAppServerClient] = None
        self._status: Optional[BackendStatus] = None
        self._queue: "queue.Queue" = queue.Queue()
        self._item_tools: dict = {}
        self._item_changes: dict = {}
        self._pending_approvals: dict = {}
        self._approval_decider: Optional[Callable] = None
        self._session_id: Optional[int] = None
        self._turn: int = 0
        self._init_lock = threading.Lock()
        self._initialized = False
        self._user_agent = ""
        self._version = ""
        self._control_lock = threading.RLock()
        self._scope_lock = threading.RLock()
        self._generation = 0
        self._client_generation = 0
        self._scope_thread = ""
        self._scope_turn = ""
        self._active_turn = ""
        self._starting_turn = False
        self._finished_turns: set = set()
        self._turn_ready = threading.Event()

    # ── 探测 ─────────────────────────────────────────────────
    def probe(self, force: bool = False) -> BackendStatus:
        if self._status is not None and not force and self._status.available:
            return self._status
        if not self._exe or not os.path.exists(self._exe):
            self._status = BackendStatus(available=False, reason=codex_missing_hint())
            return self._status
        try:
            client, init_error = self._ensure_initialized()
            if init_error:
                self._status = BackendStatus(available=False, version=self._version,
                                             reason=init_error)
                return self._status
            user_agent = self._user_agent
            version = self._version

            model = provider = ""
            base_url = ""
            try:
                conf = client.request("config/read", {}, timeout=30)
                cfg = ((conf.get("result") or {}).get("config") or {})
                model = str(cfg.get("model") or "")
                provider = str(cfg.get("model_provider") or "")
                base_url = str(cfg.get("model_provider_base_url") or "")
            except Exception as exc:
                self._log("codex-config-read", str(exc))

            reachable, reason = self._check_model_channel(provider)
            if not reachable:
                self._status = BackendStatus(
                    available=False, version=version, model=model, provider=provider, reason=reason
                )
                return self._status

            self._status = BackendStatus(
                available=True, version=version, model=model, provider=provider,
                reason="ok", extras={"userAgent": user_agent, "baseUrl": base_url},
            )
            return self._status
        except Exception as exc:
            self._status = BackendStatus(available=False, reason="探测异常：%s" % _short(exc))
            return self._status

    def _ensure_initialized(self):
        """确保 app-server 完成 initialize 握手。probe 与 start 共用同一份。"""
        client = self._ensure_client()
        with self._init_lock:
            if self._initialized:
                return client, ""
            init = client.request(
                "initialize",
                {"clientInfo": {"name": "lianxin-runtime", "version": "0.1.0"},
                 "capabilities": {"experimentalApi": True}},
                timeout=60,
            )
            if init.get("error"):
                return client, "initialize 失败：%s" % _short(init["error"])
            user_agent = str((init.get("result") or {}).get("userAgent") or "")
            version = _version_from_user_agent(user_agent)
            if self._pinned_version and version and not version.startswith(self._pinned_version):
                return client, "版本不符：需要 %s，实际 %s" % (self._pinned_version, version)
            client.notify("initialized")
            self._initialized = True
            self._user_agent = user_agent
            self._version = version
            self._log("codex-init", "已握手 %s" % user_agent)
            return client, ""

    def _check_model_channel(self, provider: str):
        """自定义 provider 时校验模型通道是否在线（避免静默不可用）。"""
        if provider and provider != "custom":
            return True, "ok"
        import socket

        try:
            from config import get_runtime_config

            endpoint = str(get_runtime_config().get("model_channel_endpoint") or "127.0.0.1:15721")
        except Exception:
            endpoint = "127.0.0.1:15721"
        host, _, port = endpoint.partition(":")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                if sock.connect_ex((host or "127.0.0.1", int(port or 0))) == 0:
                    return True, "ok"
        except Exception:
            pass
        return False, "模型通道不可达（%s），执行引擎暂不可用" % endpoint

    def _ensure_client(self) -> CodexAppServerClient:
        if self._client is not None and self._client.alive():
            return self._client
        if self._client is not None:
            self._client.terminate()
        self._client_generation += 1
        client_generation = self._client_generation
        client = CodexAppServerClient(
            self._exe,
            on_notification=lambda msg: self._on_notification(msg, client_generation),
            on_server_request=lambda msg: self._on_server_request(msg, client_generation),
            log=self._log,
        )
        client.start()
        self._client = client
        with self._init_lock:
            self._initialized = False
        return client

    # ── 任务 ─────────────────────────────────────────────────
    def set_approval_decider(self, decider: Callable) -> None:
        """decider(handle, method, params) -> 'accept' | 'decline' | ..."""
        self._approval_decider = decider

    def set_context(self, session_id: Optional[int], turn: int) -> None:
        self._session_id = session_id
        self._turn = turn

    def pid(self) -> int:
        """??????? PID?0 ????????"""
        return self._client.pid() if self._client is not None else 0

    # ── 会话维护（thread 持久化）──────────────────────────────
    def _cli(self, args, timeout: float = 60.0):
        """跑一次 codex 子命令；返回 (ok, 输出)。不开窗口，失败交给调用方。"""
        if not self._exe or not os.path.exists(self._exe):
            return False, codex_missing_hint()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.run(
                [self._exe, *[str(arg) for arg in args]],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            return False, "codex %s 超时" % str(args[0] if args else "")
        except Exception as exc:
            return False, str(exc)
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return proc.returncode == 0, output

    def unarchive(self, thread_id: str) -> bool:
        """app-server 退出时 codex 会自动归档线程，resume 前必须先解档。"""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return False
        ok, _output = self._cli(["unarchive", thread_id], timeout=60)
        # 没归档过也会返回非零；真正的可用性由后面的 thread/resume 判定，这里不刷日志。
        return ok

    def delete_session(self, thread_id: str) -> bool:
        """彻底删掉一个 codex 线程（清理过期执行记录时用）。"""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return False
        # 非交互终端必须显式 --force，否则 codex 会拒绝确认删除。
        ok, output = self._cli(["delete", "--force", thread_id], timeout=60)
        if not ok:
            self._log("codex-delete", "%s: %s" % (thread_id, output[:300]))
        return ok

    def start(self, spec: TaskSpec) -> SessionHandle:
        if spec.goal_contract is not None:
            raise RuntimeError("普通执行后端不支持目标契约；请使用原生目标适配层")
        with self._control_lock:
            with self._scope_lock:
                self._generation = spec.generation or self._generation + 1
                self._scope_thread = self._scope_turn = self._active_turn = ""
                self._starting_turn = False
                self._finished_turns.clear()
                self._turn_ready.clear()
                self._item_tools.clear()
                self._item_changes.clear()
            for request_id, pending in list(self._pending_approvals.items()):
                if self._client is not None:
                    self._client.reply(request_id, self._decision_payload(pending["method"], DECISION_DECLINE))
            self._pending_approvals.clear()
            self.events(0)
            try:
                return self._start(spec)
            except Exception:
                self.shutdown()
                raise

    def _start(self, spec: TaskSpec) -> SessionHandle:
        self.set_context(spec.session_id, spec.turn)
        if not self._exe or not os.path.exists(self._exe):
            raise RuntimeError(codex_missing_hint())
        client, init_error = self._ensure_initialized()
        if init_error:
            raise RuntimeError("执行引擎未就绪：%s" % init_error)
        resume_id = str(getattr(spec, "resume_thread_id", "") or "").strip()
        config = dict(spec.config or {})
        config["features.goals"] = False
        if resume_id:
            # 接着上次的线程跑：先解档（app-server 退出会自动归档），再恢复上下文。
            self.unarchive(resume_id)
            resumed = client.request(
                "thread/resume",
                {"threadId": resume_id, "excludeTurns": False, "cwd": spec.cwd,
                 "sandbox": spec.sandbox, "approvalPolicy": spec.approval_policy,
                 "config": config},
                timeout=120,
            )
            if resumed.get("error"):
                raise RuntimeError("thread/resume 失败：%s" % _short(resumed["error"]))
            result = _checked_result("thread/resume", resumed)
            thread_id = str((result.get("thread") or {}).get("id") or resume_id)
            if not thread_id:
                raise RuntimeError("thread/resume 未返回 threadId")
            # 上个进程可能是被硬杀的，rollout 里还留着「进行中」的 turn。
            # 先收掉它，否则紧接着的 turn/start 会撞上 "turn already in progress"。
            for old_turn in (result.get("thread") or {}).get("turns") or []:
                old_id = str(old_turn.get("id") or "")
                if old_turn.get("status") == "inProgress" and old_id:
                    _checked_result("turn/interrupt", client.request(
                        "turn/interrupt", {"threadId": thread_id, "turnId": old_id}, timeout=30))
                    self._wait_turn_idle(client, thread_id, old_id)
                if old_id:
                    self._finished_turns.add(old_id)
        else:
            started = client.request(
                "thread/start",
                {
                    "cwd": spec.cwd,
                    "sandbox": spec.sandbox or SANDBOX_READ_ONLY,
                    "approvalPolicy": spec.approval_policy or APPROVAL_ON_REQUEST,
                    "ephemeral": False,
                    "config": config,
                },
                timeout=120,
            )
            if started.get("error"):
                raise RuntimeError("thread/start 失败：%s" % _short(started["error"]))
            result = _checked_result("thread/start", started)
            thread_id = str((result.get("thread") or {}).get("id") or "")
            if not thread_id:
                raise RuntimeError("thread/start 未返回 threadId")

        with self._scope_lock:
            self._scope_thread = thread_id
            self._starting_turn = True
        turn = client.request(
            "turn/start",
            {"threadId": thread_id, "input": [{"type": "text", "text": spec.goal}]},
            timeout=120,
        )
        if turn.get("error"):
            raise RuntimeError("turn/start 失败：%s" % _short(turn["error"]))
        turn_result = _checked_result("turn/start", turn)
        turn_id = str((turn_result.get("turn") or {}).get("id") or "")
        if not turn_id:
            raise CodexOutcomeUnknown("turn/start 未返回 turnId，不自动重试")
        with self._scope_lock:
            if self._scope_turn and self._scope_turn != turn_id:
                raise CodexOutcomeUnknown("turn/start 响应与通知的 turnId 不一致")
            self._scope_turn = turn_id
            self._starting_turn = False

        handle = SessionHandle(thread_id=thread_id, turn_id=turn_id, backend=self.name,
                               generation=self._generation)
        runtime_events.publish(
            runtime_events.TASK_PROGRESS, thread_id=thread_id, turn_id=turn_id,
            message="会话已建立", model=result.get("model"), sandbox=spec.sandbox,
        )
        return handle

    @staticmethod
    def _wait_turn_idle(client, thread_id: str, turn_id: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = _checked_result("thread/read", client.request(
                "thread/read", {"threadId": thread_id, "includeTurns": True}, timeout=10))
            turns = (result.get("thread") or {}).get("turns") or []
            if not any(turn.get("id") == turn_id and turn.get("status") == "inProgress" for turn in turns):
                return
            time.sleep(0.1)
        raise CodexOutcomeUnknown("旧 turn 尚未确认停止，不启动新 turn")

    def _control(self, method: str, handle: SessionHandle, text: str = "") -> ControlResult:
        with self._control_lock:
            with self._scope_lock:
                matching = (handle.generation == self._generation and handle.thread_id == self._scope_thread
                            and handle.turn_id == self._scope_turn)
                waiting = (matching and self._client is not None and not self._active_turn
                           and handle.turn_id not in self._finished_turns)
            if waiting:
                self._turn_ready.wait(5)
            with self._scope_lock:
                if (handle.generation != self._generation or handle.thread_id != self._scope_thread
                        or handle.turn_id != self._scope_turn):
                    return ControlResult("stale", handle.thread_id, handle.turn_id, "任务或 turn 已切换")
                client = self._client
                active = bool(self._active_turn)
                finished = handle.turn_id in self._finished_turns
            if not active or client is None:
                if not finished and client is not None:
                    if method == "turn/interrupt":
                        self.shutdown()
                        return ControlResult("uncertain", handle.thread_id, handle.turn_id,
                                             "turn 启动尚未确认，已关闭本次后端以取消排队工作")
                    return ControlResult("not_ready", handle.thread_id, handle.turn_id,
                                         "turn 启动尚未确认，未发送补充请求")
                return ControlResult("not_running", handle.thread_id, handle.turn_id, "当前 turn 已结束或连接不可用")
            params = {"threadId": handle.thread_id}
            if method == "turn/steer":
                if not text.strip():
                    return ControlResult("rejected", handle.thread_id, handle.turn_id, "补充内容为空")
                params.update(expectedTurnId=handle.turn_id, input=[{"type": "text", "text": text}])
            else:
                params["turnId"] = handle.turn_id
            try:
                result = _checked_result(method, client.request(method, params, timeout=30))
                if method == "turn/steer" and result.get("turnId") != handle.turn_id:
                    raise CodexOutcomeUnknown("steer 未确认目标 turn，不自动重试")
                return ControlResult("accepted", handle.thread_id, handle.turn_id, "控制请求已确认")
            except CodexRpcError as exc:
                return ControlResult("rejected", handle.thread_id, handle.turn_id, str(exc))
            except Exception as exc:
                if method == "turn/interrupt":
                    self.shutdown()
                return ControlResult("uncertain", handle.thread_id, handle.turn_id, str(exc))

    def steer(self, handle: SessionHandle, text: str) -> ControlResult:
        return self._control("turn/steer", handle, text)

    def interrupt(self, handle: SessionHandle) -> ControlResult:
        result = self._control("turn/interrupt", handle)
        if result.status == "rejected":
            with self._control_lock:
                if handle.generation == self._generation:
                    self.shutdown()
        return result

    def approve(self, handle: SessionHandle, request_id, decision: str) -> bool:
        client = self._client
        if client is None:
            return False
        with self._scope_lock:
            if (handle.generation != self._generation or handle.thread_id != self._scope_thread
                    or handle.turn_id != self._scope_turn or not self._active_turn):
                return False
            pending = self._pending_approvals.pop(request_id, None)
            if pending is None:
                return False
            return client.reply(request_id, self._decision_payload(pending.get("method", ""), decision)) is True

    @staticmethod
    def _decision_payload(method: str, decision: str) -> dict:
        if method in _LEGACY_APPROVAL_METHODS:
            return {"decision": "approved" if decision in ("accept", "acceptForSession") else "denied"}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}}
        return {"decision": decision}

    _APPROVAL_FILE_METHODS = ("item/fileChange/requestApproval", "applyPatchApproval")

    def changes_for(self, item_id, method: str = "", limit: int = 20000) -> list:
        """取某个 fileChange 条目的改动预览（路径 / 类型 / diff / 增删行数）。"""
        if method and method not in self._APPROVAL_FILE_METHODS:
            return []
        info = self._item_changes.get(str(item_id or "")) or {}
        files = []
        for change in (info.get("changes") or [])[:20]:
            if not isinstance(change, dict):
                continue
            kind = change.get("kind")
            kind_type = str(kind.get("type") or "") if isinstance(kind, dict) else str(kind or "")
            diff = str(change.get("diff") or "")
            files.append({
                "path": str(change.get("path") or ""),
                "movePath": str(kind.get("move_path") or "") if isinstance(kind, dict) else "",
                "kind": kind_type or "update",
                "diff": diff[:limit],
                "truncated": len(diff) > limit,
                "diffBytes": len(diff.encode("utf-8", "replace")),
                "additions": sum(1 for line in diff.splitlines()
                                 if line.startswith("+") and not line.startswith("+++")),
                "deletions": sum(1 for line in diff.splitlines()
                                 if line.startswith("-") and not line.startswith("---")),
            })
        return files

    def pending_approvals(self) -> list:
        """仍在等待回执的审批请求（供状态查询与异常收口）。"""
        return [{"requestId": rid, "method": info.get("method", "")}
                for rid, info in list(self._pending_approvals.items())]

    def events(self, timeout: float = 0.5) -> list:
        """增量取出运行时事件（最多阻塞 timeout 秒拿第一条）。"""
        items: list = []
        try:
            items.append(self._queue.get(timeout=timeout))
        except queue.Empty:
            return items
        while True:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items

    def close(self, handle: SessionHandle) -> None:
        self.interrupt(handle)

    def shutdown(self) -> None:
        with self._scope_lock:
            self._client_generation += 1
            self._scope_thread = self._scope_turn = self._active_turn = ""
            self._starting_turn = False
            self._turn_ready.set()
        if self._client is not None:
            self._client.terminate()
            self._client = None
        with self._init_lock:
            self._initialized = False
        self._status = None
        self._item_tools.clear()
        self._item_changes.clear()
        self._pending_approvals.clear()

    # ── 反向请求（审批 / 时间） ──────────────────────────────
    def _on_server_request(self, msg: dict, client_generation=None):
        with self._scope_lock:
            if client_generation is not None and client_generation != self._client_generation:
                return None, {"code": -32600, "message": "stale execution connection"}
            return self._on_scoped_server_request(msg)

    def _on_scoped_server_request(self, msg: dict):
        method = str(msg.get("method") or "")
        params = msg.get("params") or {}
        req_id = msg.get("id")

        if method == "currentTime/read":
            return {"currentTimeAt": int(time.time())}, None

        if method in _APPROVAL_REQUEST_METHODS:
            if not self._accept_scope(params):
                return self._decision_payload(method, DECISION_DECLINE), None
            # 审批卡片统一由 TaskRuntime 发（带 diff 与计数），这里只登记原始请求
            self._pending_approvals[req_id] = {"method": method, "params": params}
            if self._approval_decider is None:
                self._pending_approvals.pop(req_id, None)
                return self._decision_payload(method, DECISION_DECLINE), None
            try:
                decision = self._approval_decider(
                    SessionHandle(self._scope_thread, self._scope_turn, generation=self._generation),
                    method, params, req_id)
            except Exception as exc:
                self._log("codex-approval-decider", str(exc))
                decision = DECISION_DECLINE
            if decision is None:
                # 挂起：等调用方 approve() 后再回执（先预览再确认）
                return _NO_REPLY, None
            self._pending_approvals.pop(req_id, None)
            return self._decision_payload(method, decision), None

        if method == "item/tool/requestUserInput":
            return {"answers": {}}, None

        return None, {"code": -32601, "message": "lianxin-runtime: unsupported method %s" % method}

    # ── 通知 -> 事件 ─────────────────────────────────────────
    def _accept_scope(self, params: dict, method: str = "") -> bool:
        thread_id = str(params.get("threadId") or "")
        turn_id = str(params.get("turnId") or (params.get("turn") or {}).get("id") or "")
        with self._scope_lock:
            if not self._scope_thread or thread_id != self._scope_thread:
                return False
            if turn_id:
                if self._scope_turn and turn_id != self._scope_turn:
                    return False
                if not self._scope_turn:
                    if not self._starting_turn or turn_id in self._finished_turns:
                        return False
                    self._scope_turn = turn_id
                if turn_id in self._finished_turns:
                    return False
                if method == "turn/started":
                    self._active_turn = turn_id
                    self._turn_ready.set()
                elif method == "turn/completed":
                    self._finished_turns.add(turn_id)
                    self._active_turn = ""
                    self._turn_ready.set()
            elif method not in ("error", "warning", "guardianWarning"):
                return False
            return True

    def _on_notification(self, msg: dict, client_generation=None) -> None:
        with self._scope_lock:
            if client_generation is not None and client_generation != self._client_generation:
                return
            self._on_scoped_notification(msg)

    def _on_scoped_notification(self, msg: dict) -> None:
        method = str(msg.get("method") or "")
        params = msg.get("params") or {}
        if not self._accept_scope(params, method):
            return
        thread_id = str(params.get("threadId") or "")

        if method == "item/started":
            item = params.get("item") or {}
            item_type = str(item.get("type") or "")
            if item_type == "fileChange":
                self._item_changes[str(item.get("id") or "")] = {"changes": item.get("changes") or []}
            if item_type in _SKIP_ITEM_TYPES:
                return
            tool_name = _ITEM_TOOL_NAMES.get(item_type) or ("codex_" + _snake(item_type))
            self._item_tools[str(item.get("id") or "")] = {
                "name": tool_name, "at": time.time(), "summary": _short(item.get("command") or item.get("path"), 200),
            }
            runtime_events.tool_started(tool_name, {"type": item_type, "detail": self._item_tools[str(item.get("id") or "")]["summary"]},
                                        session_id=self._session_id, turn=self._turn)
            self._emit(runtime_events.TOOL_STARTED, thread_id, params.get("turnId"),
                       tool=tool_name, itemType=item_type,
                       detail=self._item_tools[str(item.get("id") or "")]["summary"])
            return

        if method == "item/completed":
            item = params.get("item") or {}
            item_type = str(item.get("type") or "")
            if item_type == "agentMessage":
                text = str(item.get("text") or "")
                if text:
                    self._emit(runtime_events.NOTE, thread_id, params.get("turnId"), agentFinal=text)
                return
            if item_type in _SKIP_ITEM_TYPES:
                return
            item_id = str(item.get("id") or "")
            self._item_changes.pop(item_id, None)
            info = self._item_tools.pop(item_id, None) or {}
            tool_name = info.get("name") or (_ITEM_TOOL_NAMES.get(item_type) or ("codex_" + _snake(item_type)))
            elapsed = (time.time() - float(info.get("at") or time.time())) * 1000.0
            exit_code = item.get("exitCode")
            is_error = bool(item.get("status") in ("failed", "declined")) or (exit_code not in (None, 0))
            preview = _short(item.get("aggregatedOutput") or item.get("stdout") or item.get("path") or exit_code)
            runtime_events.tool_finished(tool_name, preview, is_error=is_error, elapsed_ms=elapsed,
                                         session_id=self._session_id, turn=self._turn)
            self._emit(runtime_events.TOOL_FINISHED, thread_id, params.get("turnId"),
                       tool=tool_name, itemType=item_type, ok=not is_error, elapsedMs=round(elapsed, 1))
            return

        if method == "turn/started":
            self._emit(runtime_events.TASK_PROGRESS, thread_id, params.get("turnId") or params.get("turn", {}).get("id"),
                       message="turn 已开始")
            return

        if method == "turn/plan/updated":
            self._emit(runtime_events.PLAN_UPDATED, thread_id, params.get("turnId"),
                       plan=params.get("plan"), explanation=params.get("explanation"))
            return

        if method == "thread/tokenUsage/updated":
            self._emit(runtime_events.TOKEN_USAGE, thread_id, params.get("turnId"),
                       usage=params.get("tokenUsage") or params.get("usage") or {})
            return

        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if delta:
                self._emit(runtime_events.NOTE, thread_id, params.get("turnId"), agentDelta=delta[:400])
            return

        if method in ("error", "warning", "guardianWarning"):
            self._emit(runtime_events.NOTE, thread_id, "", level=method, message=_short(params.get("message"), 300))
            return

        if method == "turn/completed":
            turn = params.get("turn") or {}
            error = turn.get("error")
            if isinstance(error, dict):
                error = error.get("message") or _short(error, 200)
            self._emit(runtime_events.TURN_FINISHED, thread_id, turn.get("id"),
                       finalStatus=str(turn.get("status") or ""), error=_short(error, 200))

    def _emit(self, kind: str, thread_id: str, turn_id, **payload) -> None:
        try:
            self._queue.put(RuntimeEventLite(kind, thread_id or "", turn_id or "", payload,
                                             generation=self._generation))
        except Exception:
            pass


class RuntimeEventLite:
    """轻量事件包装，避免在热路径上构造 dataclass 的开销。"""

    __slots__ = ("kind", "thread_id", "turn_id", "payload", "ts", "generation")

    def __init__(self, kind: str, thread_id: str, turn_id: str, payload: dict, generation: int = 0) -> None:
        self.kind = kind
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.payload = payload
        self.ts = time.time()
        self.generation = generation

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ts": self.ts,
            "threadId": self.thread_id,
            "turnId": self.turn_id,
            "generation": self.generation,
            "payload": self.payload,
        }
