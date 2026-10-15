# brain/mcp/mcp_client.py
"""外部 MCP 客户端 —— 通过 stdio 子进程连接社区 MCP 服务器

实现标准 MCP 协议 (JSON-RPC over stdio)。

参考：https://spec.modelcontextprotocol.io/

稳定性要点（2026-10 加固）：
- stdout 由常驻线程按行投递到队列，读取带超时，不会阻塞事件循环；
- 子进程死亡 / 响应超时 / 响应错位都会被识别为“连接失效”；
- 失效后自动重启子进程并重试一次；仍失败则返回可读错误，而不是静默的空结果。

Usage::

    from brain.mcp.mcp_client import ExternalMCPClient
    client = ExternalMCPClient(service_name, command, env)
    client.connect_sync()
"""

import asyncio
import json
import logging
import os
import queue
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("MCPClient")

# stdout 关闭哨兵：与“读取超时（None）”区分开
_EOF = object()


class ExternalMCPClient:
    """标准 MCP 客户端 — JSON-RPC over stdio

    启动外部 MCP 服务器子进程，通过 stdin/stdout 进行 JSON-RPC 通信。
    支持社区 MCP Server（如 @modelcontextprotocol/server-filesystem 等）。
    """

    INIT_TIMEOUT = 30.0
    CALL_TIMEOUT = 120.0

    def __init__(
        self,
        service_name: str,
        command: List[str],
        env: Dict[str, str] = None,
    ):
        self.service_name = service_name
        self.command = command
        self.env = env
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        # A stdio MCP server has one stdout stream. Keep each write/read pair
        # together so concurrent calls cannot consume one another's response.
        self._io_lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._restart_lock = threading.Lock()
        self._connected = False
        self._tools: List[Dict] = []
        self._stdout_queue: "queue.Queue[Any]" = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._last_error = ""
        self.icon = "🔌"
        self.description = f"外部 MCP 服务: {service_name}"

    # ── 连接 ──────────────────────────────────────────────────────────
    async def _connect(self) -> bool:
        if self._connected:
            return True
        # 非阻塞抢占：已有连接流程在跑时等它的结果，避免同一直连流程被启动两次
        # （也避免用 threading 锁 await 造成事件循环死锁）。
        if not self._connect_lock.acquire(blocking=False):
            deadline = time.time() + self.INIT_TIMEOUT
            while time.time() < deadline:
                await asyncio.sleep(0.1)
                if self._connected:
                    return True
                if not self._connect_lock.locked():
                    break
            return self._connected
        try:
            return await self._connect_locked()
        finally:
            self._connect_lock.release()

    async def _connect_locked(self) -> bool:
        """启动子进程并完成 MCP 初始化握手"""
        if not self.command:
            logger.warning(f"[MCP] 未配置启动命令: {self.service_name}")
            return False

        self._drain_stdout_queue()
        try:
            merged_env = {**os.environ, **(self.env or {})}
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            # 外部 MCP 服务（含其派生的分离进程如 mpv）随莲心退出被系统自动清理
            try:
                from utils.child_kill_job import assign_pid_to_kill_job
                assign_pid_to_kill_job(self._process.pid)
            except Exception:
                pass
            self._start_reader()

            response = await self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "lianxin-ai", "version": "1.0.0"},
                },
                timeout=self.INIT_TIMEOUT,
            )
            if response is None or "result" not in response:
                logger.error(
                    "[MCP] 初始化失败: %s → %s",
                    self.service_name,
                    self._last_error or "无有效响应",
                )
                self._cleanup_process()
                return False

            await self._notify("initialized", {})
            tools_response = await self._request(
                "tools/list", {}, timeout=self.INIT_TIMEOUT
            )
            self._tools = ((tools_response or {}).get("result") or {}).get("tools", [])
            self._connected = True
            self._last_error = ""
            logger.info(
                f"[MCP] 外部服务已连接: {self.service_name} "
                f"({len(self._tools)} 个工具)"
            )
            return True

        except Exception as e:
            logger.error(f"[MCP] 连接失败: {self.service_name} → {e}")
            self._last_error = str(e)
            self._cleanup_process()
            return False

    def _start_reader(self) -> None:
        """常驻读线程：把 stdout 按行投递到队列，避免在事件循环里阻塞读取。"""
        proc = self._process
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return

        def _pump():
            try:
                for line in stream:
                    self._stdout_queue.put(line)
            except Exception:
                pass
            finally:
                self._stdout_queue.put(_EOF)

        self._reader_thread = threading.Thread(
            target=_pump, name=f"mcp-reader-{self.service_name}", daemon=True
        )
        self._reader_thread.start()

    def _read_line(self, timeout: float):
        """返回一行 / None（超时）/ _EOF（stdout 关闭）。"""
        try:
            return self._stdout_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _drain_stdout_queue(self) -> None:
        while True:
            try:
                self._stdout_queue.get_nowait()
            except queue.Empty:
                return

    def _mark_dead(self, reason: str) -> None:
        """标记连接失效；下一次调用会自动重启子进程。"""
        if self._connected or self._process is not None:
            logger.warning("[MCP] %s 连接失效: %s", self.service_name, reason)
        self._last_error = reason
        self._connected = False

    # ── JSON-RPC ──────────────────────────────────────────────────────
    async def _request(self, method: str, params: dict, timeout: float = None) -> Optional[dict]:
        with self._io_lock:
            return await self._request_locked(method, params, timeout)

    async def _request_locked(self, method: str, params: dict, timeout: float = None) -> Optional[dict]:
        """发送请求并等待响应；传输层失败返回 None（调用方据此重启重试）。"""
        request_id = self._request_id + 1
        self._request_id = request_id
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        wait_seconds = float(timeout or self.CALL_TIMEOUT)

        proc = self._process
        if proc is None:
            self._mark_dead("子进程不存在")
            return None
        return_code = proc.poll()
        if return_code is not None:
            self._mark_dead(f"子进程已退出 (rc={return_code})")
            return None
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception as e:
            self._mark_dead(f"写入 stdin 失败: {e}")
            return None

        line = await asyncio.to_thread(self._read_line, wait_seconds)
        if line is None:
            # 丢弃可能迟到的响应，避免下一条请求读到错位的返回值
            self._drain_stdout_queue()
            self._mark_dead(f"{method} 等待响应超时（{wait_seconds:.0f}s）")
            return None
        if line is _EOF:
            self._mark_dead(f"stdout 已关闭（{method}）")
            return None
        try:
            response = json.loads(line)
        except Exception as e:
            self._mark_dead(f"响应不是合法 JSON: {e}")
            return None
        if response.get("id") != request_id:
            logger.error(
                "[MCP] RPC response id mismatch: method=%s expected=%s actual=%s",
                method, request_id, response.get("id"),
            )
            self._mark_dead("响应 id 错位")
            return None
        return response

    async def _notify(self, method: str, params: dict):
        try:
            notification = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            self._process.stdin.write(
                json.dumps(notification, ensure_ascii=False) + "\n"
            )
            self._process.stdin.flush()
        except Exception:
            pass

    # ── 工具调用 ──────────────────────────────────────────────────────
    def _error_payload(self, message: str) -> str:
        return json.dumps(
            {"status": "error", "message": message},
            ensure_ascii=False,
        )

    async def _call_once(self, tool_name: str, arguments: dict) -> Optional[dict]:
        return await self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=self.CALL_TIMEOUT,
        )

    async def _restart(self) -> bool:
        """重启子进程（进程死亡 / 超时后的自愈路径）。"""
        with self._restart_lock:
            self._connected = False
            self._drain_stdout_queue()
            self._cleanup_process()
            if not await self._connect():
                logger.error("[MCP] %s 自动重启失败: %s", self.service_name, self._last_error)
                return False
            logger.info("[MCP] %s 已自动重启", self.service_name)
            return True

    async def handle_call(self, tool_name: str, arguments: dict) -> str:
        if not self._connected:
            if not await self._connect():
                return self._error_payload(
                    f"外部MCP服务未连接: {self.service_name}"
                    + (f"（{self._last_error}）" if self._last_error else "")
                )

        response = await self._call_once(tool_name, arguments)
        if response is None:
            # 多半是子进程死亡/超时：重启一次再试，避免“一次失败就永久失效”
            logger.warning(
                "[MCP] %s.%s 调用失败（%s），重启子进程后重试",
                self.service_name, tool_name, self._last_error or "无响应",
            )
            if await self._restart():
                response = await self._call_once(tool_name, arguments)

        if response is None:
            return self._error_payload(
                f"调用 {self.service_name}.{tool_name} 失败："
                f"{self._last_error or '服务无响应'}"
            )
        if "error" in response:
            logger.error("[MCP] RPC returned error: %s", response["error"])
            return self._error_payload(
                f"{self.service_name}.{tool_name} 返回错误: {response['error']}"
            )
        return json.dumps(response.get("result", {}), ensure_ascii=False)

    # ── 同步入口 ──────────────────────────────────────────────────────
    def connect_sync(self) -> bool:
        """同步连接外部 MCP 服务（注册阶段使用）"""
        if self._connected:
            return True
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self._connect())
            # 已有事件循环，在新线程中运行
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(self._connect())).result(timeout=30)
        except Exception as e:
            logger.error(f"[MCP] connect_sync 异常: {self.service_name} → {e}")
            return False

    def get_tool_definitions_openai(self) -> list:
        """返回 OpenAI Function Calling 格式的工具定义"""
        openai_tools = []
        for tool in self._tools:
            name = f"mcp__{self.service_name}__{tool['name']}"
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema",
                        {"type": "object", "properties": {}}),
                }
            })
        return openai_tools

    def cleanup(self):
        self._connected = False
        self._cleanup_process()

    def _cleanup_process(self):
        proc = self._process
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass
            self._process = None
        self._reader_thread = None
        self._drain_stdout_queue()
