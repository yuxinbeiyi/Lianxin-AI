"""统一音乐服务门面。

播放器协议仍由 LianxinBridge 负责兼容现有 API；该门面为新版 API、工具和
旧版 UI 提供稳定的音乐领域入口，避免工具依赖 Qt 窗口是否启动。
"""

from __future__ import annotations

from typing import Any, Callable
from utils.music_stats import MusicStats


class MusicService:
    def __init__(self, bridge: Any):
        self._bridge = bridge
        self._stats = MusicStats()
        self._local_backend = None

    def register_local_backend(self, backend: Any) -> None:
        """Register the legacy/local player without coupling this service to Qt."""
        self._local_backend = backend

    def available_sources(self) -> list[str]:
        sources = ["netease"]
        if self._local_backend is not None:
            sources.append("local")
        return sources

    def local_state(self) -> dict:
        if self._local_backend is None:
            return {"source": "local", "serviceOnline": False, "error": "local backend unavailable"}
        state = dict(self._local_backend.state() or {})
        state.setdefault("active", bool(state.get("playing")))
        state.setdefault("paused", False)
        state.setdefault("progress", state.get("position", 0))
        state.setdefault("queueAvailable", bool(state.get("playlist")))
        state.setdefault("serviceOnline", True)
        state.setdefault("error", "")
        state.setdefault("updatedAt", __import__("time").time())
        state["source"] = "local"
        return state

    def local_control(self, action: str, payload: dict | None = None) -> dict:
        if self._local_backend is None:
            raise RuntimeError("local backend unavailable")
        return dict(self._local_backend.control(action, payload or {}) or {}, source="local")

    def state(self) -> dict:
        return self._bridge.music_state()

    def state_for(self, source: str = "netease") -> dict:
        return self.local_state() if source == "local" else self.state()

    def control(self, action: str, payload: dict | None = None) -> dict:
        return self._bridge.music_control(action, payload or {})

    def control_for(self, source: str, action: str, payload: dict | None = None) -> dict:
        return self.local_control(action, payload) if source == "local" else self.control(action, payload)

    def ensure_online(self) -> dict:
        return self._bridge.music_ensure()

    def playlist(self) -> list[dict]:
        state = self.state()
        return list(state.get("playlist") or [])

    def status_text(self) -> str:
        state = self.state()
        if not state.get("source") and not state.get("active"):
            return "网易云播放器未连接。"
        status = "播放中" if state.get("active") else ("已暂停" if state.get("paused") else "已停止")
        title = state.get("name") or state.get("title") or "未知歌曲"
        artist = state.get("artist") or "未知歌手"
        progress = float(state.get("progress") or 0)
        duration = float(state.get("duration") or 0)
        return f"状态：{status}\n当前歌曲：{title}\n歌手：{artist}\n进度：{int(progress)} / {int(duration)} 秒"

    def playlist_text(self) -> str:
        items = self.playlist()
        if not items:
            return "当前网易云播放队列为空。"
        lines = []
        for index, item in enumerate(items, 1):
            title = item.get("title") or item.get("name") or "未知歌曲"
            artist = item.get("artist") or "未知歌手"
            lines.append(f"{index}. {title} - {artist}")
        return "当前网易云播放队列：\n" + "\n".join(lines)


    def stats(self) -> dict:
        song_name, seconds = self._stats.get_most_played_song()
        return {
            "total_seconds": int(self._stats.data.get("total_seconds", 0)),
            "total_hours": self._stats.get_total_hours(),
            "most_played": {"name": song_name, "seconds": int(seconds or 0)} if song_name else None,
        }

    def stats_text(self) -> str:
        data = self.stats()
        most = data.get("most_played")
        if most:
            return f"累计听歌 {data['total_hours']:.1f} 小时。\n最常听的歌曲：{most['name']}，共 {most['seconds'] // 60} 分钟。"
        return f"累计听歌 {data['total_hours']:.1f} 小时。还没有积累出最常听的歌曲。"


def get_music_service():
    """在 API 服务已启动时取得单例；独立工具进程则返回 None。"""
    try:
        from api_server import bridge
        return getattr(bridge, "music_service", None)
    except Exception:
        return None
