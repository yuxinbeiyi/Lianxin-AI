"""统一音乐服务门面。

播放器协议仍由 LianxinBridge 负责兼容现有 API；该门面为新版 API、工具和
旧版 UI 提供稳定的音乐领域入口，避免工具依赖 Qt 窗口是否启动。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable
from utils.music_stats import MusicStats


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class MusicPlaybackTracker:
    """把轮询到的播放状态转换成"真实播放时长"，落盘为 music.playback 事件。

    - 只有 active（playing 且未 paused）才累加；
    - 用单调时钟按 tick 间隔累加，不用 progress/startedAt 判断"是否在播"（避免 seek 与暂停污染）；
    - 单次会话最多计到曲长：避免播放器卡在 active 时无限累加（旧实现只依赖分片，超限值会存活）；
    - 停滞护栏：当播放器给出可信 position 后，若 position 长时间不推进则暂停累加；
      播放器不提供 position（如 8765 的 status.position 恒为 0）时自动退回墙上时钟口径；
    - 同一首歌的连续播放记为一个会话（session_id = 会话开始的毫秒时间戳，跨进程唯一）；
    - 每 flush_seconds 落一个快照分片，崩溃最多丢一个分片。
    """

    def __init__(self, stats: MusicStats | None = None, flush_seconds: float = 60.0, clock=None,
                 stall_grace_seconds: float = 30.0):
        self._stats = stats or MusicStats()
        self._flush_seconds = max(5.0, float(flush_seconds))
        self._stall_grace = max(5.0, float(stall_grace_seconds))
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._session: dict | None = None

    @staticmethod
    def identity(state: dict) -> tuple[str, str, str, str]:
        state = state or {}
        current = state.get("currentTrack") if isinstance(state.get("currentTrack"), dict) else {}
        source = str(state.get("source") or "netease")
        track_id = str(state.get("id") or state.get("songId") or current.get("id") or "").strip()
        name = str(state.get("name") or state.get("title") or current.get("title") or "").strip()
        artist = str(state.get("artist") or current.get("artist") or "").strip()
        if not track_id:
            track_id = name
        if source.startswith("netease"):
            source = "netease"
        return source, track_id, name, artist

    @staticmethod
    def _is_active(state: dict) -> bool:
        if bool(state.get("active")):
            return True
        return bool(state.get("playing")) and not bool(state.get("paused"))

    def tick(self, state: dict | None) -> None:
        state = state or {}
        now = self._clock()
        with self._lock:
            source, track_id, name, artist = self.identity(state)
            current = state.get("currentTrack") if isinstance(state.get("currentTrack"), dict) else {}
            album = str(state.get("album") or current.get("album") or "")
            cover = str(state.get("coverUrl") or "")
            duration = _as_float(state.get("duration"))
            active = self._is_active(state)

            session = self._session
            if not track_id:
                if session is not None:
                    session["playing"] = False
                    session["last_tick"] = now
                return

            if session is None or session["track_id"] != track_id or session["source"] != source:
                if session is not None:
                    self._finalize(session, now)
                self._session = {
                    "source": source, "track_id": track_id, "name": name, "artist": artist,
                    "album": album, "cover_url": cover, "duration": duration,
                    "seconds": 0.0, "session_id": str(int(time.time() * 1000)),
                    "segment": 0, "playing": active, "last_tick": now, "last_flush": now,
                    "last_position": None, "progress_at": now,
                    "stall_armed": False, "stalled": False, "cap_flushed": False,
                }
                return

            session["playing"] = active
            if name:
                session["name"] = name
            if artist:
                session["artist"] = artist
            if album:
                session["album"] = album
            if cover:
                session["cover_url"] = cover
            if duration:
                session["duration"] = max(session["duration"], duration)

            # 停滞护栏：仅在拿到可信 position 后才生效，避免对"没有 position 的播放器"误判。
            position = _as_float(state.get("position"))
            known_duration = session["duration"]
            if known_duration > 0 and 0.0 < position <= known_duration + 1.0:
                session["stall_armed"] = True
                if position != session["last_position"]:
                    session["last_position"] = position
                    session["progress_at"] = now
            session["stalled"] = bool(session["stall_armed"]) and (
                now - session["progress_at"]) > self._stall_grace

            if active and not session["stalled"]:
                delta = max(0.0, now - session["last_tick"])
                if known_duration > 0:
                    # 一次连续播放不应超过曲长；真实重复循环需播放器刷新 position
                    delta = min(delta, max(0.0, known_duration - session["seconds"]))
                session["seconds"] += delta
            session["last_tick"] = now

            capped = known_duration > 0 and session["seconds"] >= known_duration - 1e-6
            if active and not session["stalled"] and (now - session["last_flush"]) >= self._flush_seconds:
                # 已达曲长上限：写最后一次快照后就不再写（同会话聚合取最大值，重复写只堆垃圾行）。
                if capped and session.get("cap_flushed"):
                    session["last_flush"] = now
                else:
                    self._flush(session)
                    session["cap_flushed"] = capped

    def _emit(self, session: dict, *, advance_segment: bool, completed: bool = False) -> None:
        seconds = session["seconds"]
        if session["duration"]:
            seconds = min(seconds, session["duration"])
        if seconds <= 0:
            if advance_segment:
                session["segment"] += 1
            return
        self._stats.record_play(
            source=session["source"], track_id=session["track_id"], name=session["name"],
            artist=session["artist"], album=session["album"], cover_url=session["cover_url"],
            seconds=seconds, session_id=session["session_id"], segment=session["segment"],
            duration=session["duration"], completed=completed,
        )
        if advance_segment:
            session["segment"] += 1

    def _flush(self, session: dict) -> None:
        self._emit(session, advance_segment=True)
        session["last_flush"] = self._clock()

    def _finalize(self, session: dict, now: float | None = None) -> None:
        if now is not None and session.get("playing") and not session.get("stalled"):
            session["seconds"] += max(0.0, now - session["last_tick"])
            session["playing"] = False
            session["last_tick"] = now
        self._emit(session, advance_segment=False)
        self._session = None

    def current_seconds(self, state: dict | None = None) -> float | None:
        with self._lock:
            session = self._session
            if session is None:
                return 0.0
            if state is not None:
                source, track_id, _name, _artist = self.identity(state)
                if track_id and (session["track_id"] != track_id or session["source"] != source):
                    return 0.0
            seconds = session["seconds"]
            if session["playing"] and not session.get("stalled"):
                seconds += max(0.0, self._clock() - session["last_tick"])
            if session["duration"] > 0:
                seconds = min(seconds, session["duration"] + 1.0)
            return seconds

    def current_session_id(self, state: dict | None = None) -> str | None:
        with self._lock:
            session = self._session
            if session is None:
                return None
            if state is not None:
                source, track_id, _name, _artist = self.identity(state)
                if track_id and (session["track_id"] != track_id or session["source"] != source):
                    return None
            return session["session_id"]

    def stop(self) -> None:
        with self._lock:
            session = self._session
            if session is not None:
                self._finalize(session, self._clock())


class MusicService:
    def __init__(self, bridge: Any):
        self._bridge = bridge
        self._stats = MusicStats()
        self._local_backend = None
        self.playback_tracker = MusicPlaybackTracker(self._stats)

    @property
    def stats_store(self) -> MusicStats:
        return self._stats

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
        state.setdefault("updatedAt", time.time())
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
        return self._stats.statistics()

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
