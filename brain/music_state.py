"""统一网易云播放状态映射（单一实现）。

`api_server`（新版 REST 桥 / 音乐工具）与 `gui/music_box/net_ease_bridge`
（QWebEngine 音乐空间）共用同一套 8765 → 统一 state 映射，避免字段漂移。

统一约定：
- 位置字段同时提供 `progress`（新版）与兼容别名 `position`（Web 播放器）；
- 模式字段同时提供 `mode` 与兼容别名 `loop_mode`（list/random）；
- 音量：`volume` 为 0~100（与 8765 一致），`volumeRatio` 为 0~1（兼容 Web 播放器前端）；
- 队列同时提供 `queueAvailable`（新版）与 `has_playlist`（Web 播放器）；
- 封面解析策略由调用方注入（HTTP 代理 / 本地缓存），其余字段完全一致。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import urllib.request
except Exception:  # pragma: no cover
    urllib = None

DEFAULT_BASE_URL = "http://127.0.0.1:8765"

try:
    # 与 brain/music_lyrics 共用同一份占位歌词规则，避免前后端两套判定漂移。
    from brain.music_lyrics import PLACEHOLDER_LYRICS
except ImportError:  # pragma: no cover - 兼容以 brain/ 为根的直接导入
    from music_lyrics import PLACEHOLDER_LYRICS


def _as_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_lyrics(raw: Any) -> list:
    lyrics = []
    if isinstance(raw, list):
        for line in raw:
            if not isinstance(line, dict):
                continue
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            lyrics.append({"time": _as_float(line.get("time")), "text": text})
    lyrics.sort(key=lambda item: item["time"])
    return lyrics


def track_cover_url(track: Any) -> str:
    """Extract a cover URL from the different shapes returned by NetEase APIs."""
    if not isinstance(track, dict):
        return ""
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    return str(
        track.get("coverUrl")
        or track.get("cover_url")
        or track.get("picUrl")
        or track.get("albumPicUrl")
        or track.get("coverImgUrl")
        or album.get("picUrl")
        or album.get("coverUrl")
        or ""
    )


def _volume_pair(raw: Any) -> tuple:
    """Return (0~100 integer-ish, 0~1 ratio) from a 0~100 or 0~1 input."""
    value = _as_float(raw, 80.0)
    ratio = value / 100.0 if value > 1.0 else value
    ratio = max(0.0, min(1.0, ratio))
    return round(ratio * 100.0, 2), ratio


def build_state(
    status_payload: Any,
    queue_payload: Any,
    *,
    cover_resolver: Optional[Callable[[str], str]] = None,
    playlist_cover_resolver: Optional[Callable[[str], str]] = None,
    extras: Optional[dict] = None,
) -> dict:
    """把 8765 的 /api/status + /api/queue 映射成统一 state。"""
    status_payload = status_payload if isinstance(status_payload, dict) else {}
    queue_payload = queue_payload if isinstance(queue_payload, dict) else {}
    st = status_payload.get("status")
    playback = status_payload.get("playback")
    listening = status_payload.get("listening")
    st = st if isinstance(st, dict) else {}
    playback = playback if isinstance(playback, dict) else {}
    listening = listening if isinstance(listening, dict) else {}

    lyrics = parse_lyrics(listening.get("lyrics"))
    style = str(listening.get("style") or "")
    instrumental = not any(line["text"] not in PLACEHOLDER_LYRICS for line in lyrics)

    queue = queue_payload.get("queue") if isinstance(queue_payload.get("queue"), list) else []
    index = _as_int(queue_payload.get("index", -1), -1)
    mode = str(queue_payload.get("mode") or "sequence")

    playing = bool(st.get("playing"))
    paused = bool(st.get("paused"))
    active = playing and not paused
    position = _as_float(st.get("position"))
    duration = _as_float(st.get("duration"))
    # 8765 的 status.duration 有时返回毫秒（与 playback.durationMs 同源）。若按秒处理，
    # 曲长会被放大 1000 倍，从而让"单次播放不超过曲长"的上限失效（曾出现 144s 的歌累计到 1172s）。
    duration_ms = _as_float(playback.get("durationMs"))
    if duration > 21600.0 and duration % 1000.0 == 0:
        duration = duration / 1000.0
    if duration_ms > 0:
        duration = duration_ms / 1000.0
    if duration > 0 and position > duration + 1.0:
        position = position / 1000.0
    volume, volume_ratio = _volume_pair(st.get("volume"))

    current_id = str(playback.get("id") or playback.get("songId") or "")
    if not (0 <= index < len(queue)):
        index = next(
            (i for i, item in enumerate(queue)
             if isinstance(item, dict) and str(item.get("id") or "") == current_id),
            -1,
        )

    playlist_items = []
    for i, track in enumerate(queue):
        track = track if isinstance(track, dict) else {}
        item = {
            "id": track.get("id") or "",
            "title": track.get("name") or track.get("title") or "未知曲目",
            "artist": track.get("artist") or "",
            "duration": (
                _as_float(track.get("durationMs")) / 1000.0
                if track.get("durationMs") else _as_float(track.get("duration"))
            ),
            "index": i,
            "favorite": False,
        }
        if playlist_cover_resolver is not None:
            item["coverUrl"] = playlist_cover_resolver(track_cover_url(track)) or ""
        playlist_items.append(item)

    cover_raw = track_cover_url(playback)
    if not cover_raw and 0 <= index < len(queue):
        cover_raw = track_cover_url(queue[index])
    cover = cover_resolver(cover_raw) if cover_resolver else cover_raw
    if not cover:
        cover = ""

    title = playback.get("name") or playback.get("title") or ""
    current_track = {
        "id": current_id,
        "title": title,
        "artist": playback.get("artist") or "",
        "album": playback.get("album") or "",
        "coverUrl": cover,
        "duration": duration,
    }

    state = {
        "serviceOnline": True,
        "loggedIn": status_payload.get("loggedIn"),
        "stateVersion": status_payload.get("stateVersion", 0),
        "updatedAt": time.time(),
        "source": "netease-8765",
        "active": active,
        "playing": playing,
        "paused": paused,
        "name": title,
        "title": title,
        "artist": playback.get("artist") or "",
        "album": playback.get("album") or "",
        "coverUrl": cover,
        "progress": position,
        "position": position,
        "duration": duration,
        "volume": volume,
        "volumeRatio": volume_ratio,
        "mode": mode,
        "loop_mode": "random" if mode == "shuffle" else "list",
        "id": current_id,
        "songId": current_id,
        "startedAt": playback.get("startedAt") or "",
        "currentTrack": current_track,
        "current_index": index,
        "playlist": playlist_items,
        "queueAvailable": bool(playlist_items),
        "has_playlist": bool(queue),
        "favorite": False,
        "error": "",
        "lyrics": lyrics,
        "style": style,
        "instrumental": instrumental,
        "space_background": "",
        "wallpaper": "",
        "space_settings": None,
    }
    for key, value in (extras or {}).items():
        if value is not None:
            state[key] = value
    return state


def offline_state(reason: str = "播放器服务离线", *, source: str = "netease-8765") -> dict:
    """离线 / 未连接时的统一 state。"""
    state = {
        "serviceOnline": False,
        "loggedIn": None,
        "stateVersion": 0,
        "updatedAt": time.time(),
        "source": source,
        "active": False,
        "playing": False,
        "paused": False,
        "name": "",
        "title": "",
        "artist": "",
        "album": "",
        "coverUrl": "",
        "progress": 0.0,
        "position": 0.0,
        "duration": 0.0,
        "volume": 80.0,
        "volumeRatio": 0.8,
        "mode": "sequence",
        "loop_mode": "list",
        "id": "",
        "songId": "",
        "startedAt": "",
        "currentTrack": {"id": "", "title": "", "artist": "", "album": "", "coverUrl": "", "duration": 0.0},
        "current_index": -1,
        "playlist": [],
        "queueAvailable": False,
        "has_playlist": False,
        "favorite": False,
        "error": reason,
        "lyrics": [],
        "style": "",
        "instrumental": True,
        "space_background": "",
        "wallpaper": "",
        "space_settings": None,
    }
    return state


def legacy_payloads(raw: Any) -> tuple:
    """把旧 .listening-state.json 形状适配成 (status, queue) 以便复用 build_state。"""
    raw = raw if isinstance(raw, dict) else {}
    # 旧状态文件可能只有 durationMs（毫秒）。先归一到秒，否则 build_state 会把毫秒当秒，
    # 再把秒当毫秒重复 x1000，导致 duration 被放大。>6 小时且整千的 duration 视为毫秒。
    duration = _as_float(raw.get("duration"))
    if duration <= 0:
        duration = _as_float(raw.get("durationMs")) / 1000.0
    if duration > 21600.0 and duration % 1000.0 == 0:
        duration = duration / 1000.0
    status = {
        "loggedIn": raw.get("loggedIn"),
        "stateVersion": raw.get("stateVersion", 0),
        "status": {
            "playing": bool(raw.get("playing")) and not bool(raw.get("paused")),
            "paused": bool(raw.get("paused")),
            "position": raw.get("position") if raw.get("position") is not None else raw.get("progress", 0),
            "duration": duration,
            "volume": raw.get("volume", 80),
        },
        "playback": {
            "id": raw.get("id") or raw.get("songId") or "",
            "name": raw.get("name") or raw.get("title") or "",
            "artist": raw.get("artist") or "",
            "album": raw.get("album") or "",
            "coverUrl": raw.get("coverUrl") or "",
            "durationMs": duration * 1000.0,
        },
        "listening": {
            "lyrics": raw.get("lyrics") or raw.get("firstLyrics") or [],
            "style": raw.get("style") or "",
        },
    }
    queue = {
        "queue": raw.get("playlist") if isinstance(raw.get("playlist"), list) else [],
        "index": raw.get("current_index", raw.get("index", -1)),
        "mode": raw.get("mode") or "sequence",
    }
    return status, queue


class NetEaseStateProvider:
    """抓取 8765 状态并缓存；可选后台刷新线程，避免在调用线程里做阻塞 IO。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 1.2,
        poll_interval: float = 1.0,
        headers: Any = None,
    ):
        self._base = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = (0.4, max(0.2, float(timeout)))
        self._interval = max(0.3, float(poll_interval))
        self._headers = headers
        self._lock = threading.RLock()
        self._status: Optional[dict] = None
        self._queue: Optional[dict] = None
        self._ok = False
        self._at = 0.0
        self._session = requests.Session() if requests is not None else None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _request_headers(self) -> dict:
        try:
            headers = self._headers() if callable(self._headers) else self._headers
        except Exception:
            headers = None
        return dict(headers or {})

    def _get_json(self, path: str):
        url = self._base + path
        headers = self._request_headers()
        if self._session is not None:
            try:
                resp = self._session.get(url, headers=headers, timeout=self._timeout)
                if resp.ok:
                    return resp.json()
            except Exception:
                return None
            return None
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=self._timeout[1]) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def refresh(self) -> bool:
        status = self._get_json("/api/status")
        queue = self._get_json("/api/queue")
        ok = isinstance(status, dict)
        with self._lock:
            if ok:
                self._status = status
                self._queue = queue if isinstance(queue, dict) else {}
            self._ok = ok
            self._at = time.monotonic()
        return ok

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                pass
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="netease-state", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=1.5)
            except Exception:
                pass

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> tuple:
        """返回 (status, queue, ok)；无缓存时同步抓取一次。"""
        with self._lock:
            if self._at > 0:
                return self._status, self._queue, self._ok
        self.refresh()
        with self._lock:
            return self._status, self._queue, self._ok

    def build(self, *, cover_resolver=None, playlist_cover_resolver=None, extras=None):
        """在线时返回统一 state，离线时返回 None。"""
        status, queue, ok = self.snapshot()
        if not ok:
            return None
        return build_state(
            status, queue,
            cover_resolver=cover_resolver,
            playlist_cover_resolver=playlist_cover_resolver,
            extras=extras,
        )
