"""莲心虚拟世界的 aiohttp 调试服务。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
from contextlib import nullcontext
from pathlib import Path

from aiohttp import WSMsgType, web

from brain.physical.models import Marker, Obstacle, Point
from brain.physical.host import PhysicalRuntimeHost, get_physical_runtime_host
from brain.physical.runtime import PhysicalRuntime


ASSETS_DIR = Path(__file__).resolve().parents[2] / "gui" / "physical_sim"
SERVICE_KEY = web.AppKey("physical_sim_service", object)


class PhysicalSimService:
    def __init__(self, runtime: PhysicalRuntime | None = None, *, tick_seconds: float = 0.02,
                 snapshot_seconds: float = 0.02, host: PhysicalRuntimeHost | None = None):
        if runtime is not None and host is not None:
            raise ValueError("runtime 与 host 不能同时指定")
        self._host = host
        self.runtime = host.runtime if host is not None else (runtime or PhysicalRuntime())
        self.tick_seconds = tick_seconds
        self.snapshot_seconds = snapshot_seconds
        self.clients: set[web.WebSocketResponse] = set()
        self._runner_task: asyncio.Task | None = None
        self._snapshot_task: asyncio.Task | None = None

    def create_app(self) -> web.Application:
        app = web.Application()
        app[SERVICE_KEY] = self
        app.router.add_get("/", self.index)
        app.router.add_get("/healthz", self.healthz)
        app.router.add_get("/ws", self.websocket)
        app.router.add_get("/debug/report", self.debug_report)
        app.router.add_static("/assets/", ASSETS_DIR, show_index=False)
        app.on_startup.append(self.start)
        app.on_cleanup.append(self.stop)
        return app

    async def start(self, _app: web.Application) -> None:
        if self._host is not None:
            self._host.start()
            # 宿主线程负责 50Hz 物理推进，aiohttp 线程只负责持续发布快照。
            self._snapshot_task = asyncio.create_task(
                self._snapshot_loop(), name="physical-snapshot-loop"
            )
        else:
            self._runner_task = asyncio.create_task(self._run_loop(), name="physical-sim-loop")

    async def stop(self, _app: web.Application) -> None:
        if self._runner_task is not None:
            self._runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None
        if self._snapshot_task is not None:
            self._snapshot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._snapshot_task
            self._snapshot_task = None
        for client in list(self.clients):
            await client.close()
        self.clients.clear()

    async def index(self, _request: web.Request) -> web.FileResponse:
        return web.FileResponse(ASSETS_DIR / "index.html")

    async def healthz(self, _request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "service": "physical-sim",
            "revision": self.runtime.world.revision,
        })

    async def debug_report(self, _request: web.Request) -> web.Response:
        report = self.snapshot()
        report["service"] = {
            "tick_seconds": self.tick_seconds,
            "snapshot_seconds": self.snapshot_seconds,
            "connected_clients": len(self.clients),
            "shared_runtime": self._host is not None,
        }
        return web.json_response(report, headers={
            "Content-Disposition": "attachment; filename=physical-debug-report.json",
        })

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(heartbeat=20)
        await socket.prepare(request)
        self.clients.add(socket)
        await socket.send_json(self.snapshot())
        try:
            async for message in socket:
                if message.type == WSMsgType.TEXT:
                    await self._handle_message(socket, message.data)
                elif message.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(socket)
        return socket

    async def _run_loop(self) -> None:
        elapsed = 0.0
        while True:
            self.runtime.tick(self.tick_seconds)
            elapsed += self.tick_seconds
            if elapsed >= self.snapshot_seconds:
                elapsed = 0.0
                await self.broadcast_snapshot()
            await asyncio.sleep(self.tick_seconds)

    async def _snapshot_loop(self) -> None:
        """只广播快照，不触碰宿主运行时，避免重复推进物理时钟。"""
        while True:
            await asyncio.sleep(self.snapshot_seconds)
            await self.broadcast_snapshot()

    async def _handle_message(self, socket: web.WebSocketResponse, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象")
            response = self.handle_command(payload)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            response = {"type": "error", "message": str(exc)}
        await socket.send_json(response)
        await self.broadcast_snapshot()

    def handle_command(self, payload: dict) -> dict:
        with self._guard():
            command_type = str(payload.get("type", ""))
            world = self.runtime.world
            if command_type == "place_marker":
                if self.runtime.find_pending_task("navigate_to_marker") is not None:
                    raise ValueError("导航执行中，不能改写活动标记；请等待到达或先取消任务")
                point = self._snap_to_grid(Point(self._number(payload, "x"), self._number(payload, "y")))
                world.add_marker(Marker("marker_001", point))
                return self._ack("食物已放置")
            if command_type == "add_obstacle":
                obstacle = Obstacle(
                    x=self._number(payload, "x"), y=self._number(payload, "y"),
                    width=self._number(payload, "w"), height=self._number(payload, "h"),
                )
                world.add_obstacle(obstacle)
                return self._ack("障碍物已添加")
            if command_type == "clear_obstacles":
                world.clear_obstacles()
                return self._ack("障碍物已清除")
            if command_type == "remove_obstacle":
                point = Point(self._number(payload, "x"), self._number(payload, "y"))
                return self._ack("障碍物已删除" if world.remove_obstacle_at(point) else "该位置没有障碍物")
            if command_type == "start_debug_navigation":
                if not world.active_marker_id:
                    raise ValueError("请先放置活动标记")
                pending_navigation = self.runtime.find_pending_task("navigate_to_marker")
                if pending_navigation is not None:
                    return self._ack("导航任务已在执行中", task_id=pending_navigation.id)
                active = world.active_task
                if active is not None and not active.status.is_terminal:
                    raise ValueError(f"当前任务 {active.id} 尚未结束，请先取消或等待完成")
                task = self.runtime.submit_navigation(world.active_marker_id)
                return self._ack("导航任务已提交", task_id=task.id)
            if command_type == "manual_move":
                direction = str(payload.get("direction", ""))
                return self._ack("蛇已移动一格" if self.runtime.manual_move(direction) else "该方向被障碍物或蛇身阻挡")
            if command_type == "cancel_task":
                return self._ack("任务已取消" if self.runtime.cancel_active_task() else "当前没有可取消任务")
            if command_type == "emergency_stop":
                return self._ack("急停已执行" if self.runtime.emergency_stop() else "当前没有运行任务")
            if command_type == "reset":
                self.runtime.reset()
                return self._ack("世界已重置")
            raise ValueError(f"未知请求类型：{command_type}")

    async def broadcast_snapshot(self) -> None:
        if not self.clients:
            return
        payload = self.snapshot()
        stale = []
        for client in self.clients:
            try:
                await client.send_json(payload)
            except ConnectionError:
                stale.append(client)
        for client in stale:
            self.clients.discard(client)

    def snapshot(self) -> dict:
        with self._guard():
            world = self.runtime.world
            task = world.active_task
            return {
            "type": "world_snapshot",
            "revision": world.revision,
            "world": {"width": world.width, "height": world.height},
            "snake": {
                "body": [[point.x, point.y] for point in world.snake.body],
                "direction": world.snake.direction,
                "speed": round(world.snake.speed, 3),
            },
            "markers": [
                {"id": marker.id, "x": marker.position.x, "y": marker.position.y,
                 "active": marker.id == world.active_marker_id}
                for marker in world.markers.values()
            ],
            "obstacles": [
                {"x": obstacle.x, "y": obstacle.y, "w": obstacle.width, "h": obstacle.height}
                for obstacle in world.obstacles
            ],
            "task": None if task is None else {
                "id": task.id, "kind": task.kind, "status": task.status.value,
                "error": task.error, "path_index": task.path_index, "path_length": len(task.path),
            },
            "path": [] if task is None else [[point.x, point.y] for point in task.path],
                "events": self.runtime.event_log[-30:],
            }

    def _guard(self):
        return self._host.lock if self._host is not None else nullcontext()

    @staticmethod
    def _snap_to_grid(point: Point, *, cell_size: int = 20) -> Point:
        """将鼠标坐标对齐至网格中心，确保食物可被蛇头精确到达。"""
        return Point(
            int(point.x // cell_size) * cell_size + cell_size / 2,
            int(point.y // cell_size) * cell_size + cell_size / 2,
        )

    @staticmethod
    def _number(payload: dict, key: str) -> float:
        value = payload[key]
        if not isinstance(value, (int, float)):
            raise ValueError(f"{key} 必须是数字")
        return float(value)

    @staticmethod
    def _ack(message: str, *, task_id: str = "") -> dict:
        return {"type": "ack", "message": message, "task_id": task_id}


def create_app(runtime: PhysicalRuntime | None = None) -> web.Application:
    if runtime is not None:
        return PhysicalSimService(runtime).create_app()
    return PhysicalSimService(host=get_physical_runtime_host()).create_app()


class PhysicalSimServer:
    """在莲心主进程内运行 Web 调试服务，避免产生第二份世界状态。"""
    def __init__(self, *, host: str = "127.0.0.1", port: int = 8766):
        self.host = host
        self.port = port
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: Exception | None = None

    def start(self, timeout: float = 3.0) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._ready_event.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, name="physical-sim-web", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout)
        return self.error is None and self._ready_event.is_set()

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(create_app())
        try:
            loop.run_until_complete(runner.setup())
            loop.run_until_complete(web.TCPSite(runner, self.host, self.port).start())
            self._ready_event.set()
            loop.run_until_complete(self._wait_for_shutdown())
        except Exception as exc:
            self.error = exc
            self._ready_event.set()
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()

    async def _wait_for_shutdown(self) -> None:
        """保持 aiohttp 事件循环运行，直到主程序请求停止。"""
        while not self._stop_event.is_set():
            await asyncio.sleep(0.05)


_server: PhysicalSimServer | None = None
_server_lock = threading.Lock()


def start_physical_sim_server() -> PhysicalSimServer:
    global _server
    with _server_lock:
        if _server is None:
            _server = PhysicalSimServer()
        _server.start()
        return _server


def stop_physical_sim_server() -> None:
    global _server
    with _server_lock:
        if _server is not None:
            _server.shutdown()
            _server = None


def main() -> None:
    host = os.getenv("LIANXIN_PHYSICAL_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("LIANXIN_PHYSICAL_PORT", "8766"))
    except ValueError:
        port = 8766
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
