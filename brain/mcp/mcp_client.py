# brain/mcp/mcp_client.py
"""外部 MCP 客户端 — 通过 stdio 子进程连接社区 MCP 服务器

实现标准 MCP 协议 (JSON-RPC over stdio)。
参考：https://spec.modelcontextprotocol.io/
"""

import asyncio
import json
import logging
import os
import subprocess
import threading
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("MCPClient")


class ExternalMCPClient:
    """标准 MCP 客户端 — JSON-RPC over stdio

    启动外部 MCP 服务器子进程，通过 stdin/stdout 进行 JSON-RPC 通信。
    支持社区 MCP Server（如 @modelcontextprotocol/server-filesystem 等）。
    """

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
        self._connected = False
        self._tools: List[Dict] = []
        self.icon = "🔌"
        self.description = f"外部 MCP 服务: {service_name}"

    async def _connect(self) -> bool:
        if self._connected:
            return True
        with self._connect_lock:
            if self._connected:
                return True
            return await self._connect_locked()

    async def _connect_locked(self) -> bool:
        """启动子进程并完成 MCP 初始化握手"""
        if self._connected:
            return True
        if not self.command:
            logger.warning(f"[MCP] 未配置启动命令: {self.service_name}")
            return False

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

            init_result = await self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "lianxin-ai", "version": "1.0.0"},
            })

            if init_result:
                await self._notify("initialized", {})
                tools_result = await self._request("tools/list", {})
                self._tools = tools_result.get("tools", [])
                self._connected = True
                logger.info(
                    f"[MCP] 外部服务已连接: {self.service_name} "
                    f"({len(self._tools)} 个工具)"
                )
                return True

        except Exception as e:
            logger.error(f"[MCP] 连接失败: {self.service_name} → {e}")
            self._cleanup_process()

        return False

    async def _request(self, method: str, params: dict) -> dict:
        with self._io_lock:
            return await self._request_locked(method, params)

    async def _request_locked(self, method: str, params: dict) -> dict:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        try:
            self._process.stdin.write(
                json.dumps(payload, ensure_ascii=False) + "\n"
            )
            self._process.stdin.flush()
            line = self._process.stdout.readline()
            if not line:
                return {}
            response = json.loads(line)
            if response.get("id") != self._request_id:
                logger.error(
                    "[MCP] RPC response id mismatch: method=%s expected=%s actual=%s",
                    method, self._request_id, response.get("id"),
                )
                return {}
            if "error" in response:
                logger.error("[MCP] RPC returned error: %s", response["error"])
                return {}
            return response.get("result", {})
        except Exception as e:
            logger.error(f"[MCP] RPC 失败: {method} → {e}")
            return {}

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

    async def handle_call(self, tool_name: str, arguments: dict) -> str:
        if not self._connected:
            connected = await self._connect()
            if not connected:
                return json.dumps(
                    {"status": "error", "message": f"外部MCP服务未连接: {self.service_name}"},
                    ensure_ascii=False,
                )

        result = await self._request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return json.dumps(result, ensure_ascii=False)
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
        if self._process:
            try:
                self._process.stdin.close()
                self._process.stdout.close()
                try:
                    self._process.stderr.close()
                except Exception:
                    pass
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
