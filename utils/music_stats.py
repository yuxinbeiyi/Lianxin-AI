"""
utils/music_stats.py - 音乐陪伴统计

事实来源：统一事件库 interaction_events.db 中的 music.playback / music.feedback 事件。
music_stats.json 退化为兼容缓存（旧读取方仍可读），随时可由事件重建。

写入约定（分片快照）：
- 同一首歌的一次连续播放为一个"播放会话"（session_id）。
- 每累计若干秒落一个"快照"事件，metadata.seconds 是该会话截至当前的累计秒数。
- 聚合时按 (source, track_id, session_id) 取最大 seconds，因此崩溃丢分片或重复写入
  都不会重复计数。
- event_key = music:playback:<source>:<track_id>:<session_id>:<segment>

迁移：旧 music_stats.json 只有"本地文件路径"维度的历史数据，首次运行会
迁移为 source="local" 的 music.playback 事件（幂等，仅迁移一次）。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.paths import get_user_data_dir

_MUSIC_STATS_FILE = get_user_data_dir() / "music_stats.json"

MUSIC_FEATURE = "music"
EVENT_PLAYBACK = "music.playback"
EVENT_FEEDBACK = "music.feedback"

# 旧安装目录拼写（莲心AI / Lianxin-AI）会把同一首歌拆成两条历史，
# 归一化到当前存在的资源路径，避免"播放次数 / 最常听"被放大。
_ASSET_MUSIC_DIR = Path(__file__).resolve().parent.parent / "assets" / "music"


def canonical_local_track_id(track_id: str, cache: dict | None = None) -> str:
    """把 source=local 的历史路径归一到当前存在的文件路径。

    仅当记录路径已不存在、而同名文件存在于项目 assets/music 时才改写；
    其余情况原样返回（不做 basename 全局合并，避免误并不同目录的同名文件）。
    """
    raw = str(track_id or "")
    if not raw:
        return raw
    if cache is not None:
        hit = cache.get(raw)
        if hit is not None:
            return hit
    resolved = raw
    try:
        path = Path(raw)
        if not path.exists() and path.name:
            candidate = _ASSET_MUSIC_DIR / path.name
            if candidate.exists():
                resolved = str(candidate)
    except (OSError, ValueError):
        resolved = raw
    if cache is not None:
        cache[raw] = resolved
    return resolved


def _stable_hash(value: str) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]


def _duration_seconds(value: Any) -> float:
    """把可能是毫秒的历史曲长归一为秒。

    仅当数值大得"不可能是秒"（>6 小时）且为整千时才按毫秒处理，
    避免误伤 4000s（66 分钟）这类合法长曲。
    """
    secs = _to_float(value)
    if secs > 21600.0 and secs % 1000.0 == 0:
        return secs / 1000.0
    return secs


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def feedback_event_key(source: str, track_id: str, session_id: str) -> str:
    return "music:feedback:{}:{}:{}".format(source, track_id, session_id)


def _parse_iso(value: Any) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


class MusicStats:
    """听歌统计。对外保持旧 API（data / get_total_hours / get_most_played_song）。"""

    _file_lock = threading.RLock()
    _REFRESH_TTL = 5.0

    def __init__(self, stats_file: Path | str | None = None, event_store: Any = None):
        self._stats_file = Path(stats_file or _MUSIC_STATS_FILE)
        self._events = event_store
        self._track_resolve_cache: dict = {}
        self._lock = threading.RLock()
        self._data: dict = {"total_seconds": 0, "songs": {}, "tracks": [], "daily": {}}
        self._refreshed_at = 0.0
        self._dirty = True
        try:
            self._migrate_legacy()
        except Exception:
            pass
        self._refresh(force=True)

    # ---------- event store ----------
    def _store(self):
        if self._events is None:
            from brain.interaction_events import InteractionEventStore
            self._events = InteractionEventStore()
        return self._events

    # ---------- legacy json ----------
    def _read_legacy_json(self) -> dict:
        try:
            if self._stats_file.exists():
                return json.loads(self._stats_file.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return {}

    def _migrate_legacy(self) -> None:
        legacy = self._read_legacy_json()
        songs = legacy.get("songs") if isinstance(legacy, dict) else None
        if not isinstance(songs, dict) or not songs:
            return
        store = self._store()
        if store.list_events(feature=MUSIC_FEATURE, limit=1):
            return
        for key, item in songs.items():
            if not isinstance(item, dict):
                continue
            seconds = _to_float(item.get("seconds"))
            if seconds <= 0:
                continue
            track_id = str(key)
            name = str(item.get("name") or Path(key).stem)
            store.record(
                feature=MUSIC_FEATURE,
                event_type=EVENT_PLAYBACK,
                source_id=track_id,
                content=name,
                occurred_at=item.get("last_played") or None,
                metadata={
                    "source": "local",
                    "track_id": track_id,
                    "name": name,
                    "seconds": seconds,
                    "session_id": "legacy",
                    "segment": 0,
                    "migrated": True,
                },
                importance="normal",
                searchable=False,
                event_key=f"music:playback:legacy:{_stable_hash(track_id)}",
            )

    # ---------- write ----------
    def _write_playback(self, *, source, track_id, name, seconds, session_id, segment,
                        duration=0.0, completed=False, artist="", album="",
                        cover_url="", occurred_at=None) -> None:
        store = self._store()
        payload = {
            "source": str(source or "local"),
            "track_id": str(track_id),
            "name": name or "",
            "artist": artist or "",
            "album": album or "",
            "cover_url": cover_url or "",
            "seconds": round(max(0.0, _to_float(seconds)), 2),
            "duration": round(max(0.0, _to_float(duration)), 2),
            "session_id": str(session_id),
            "segment": int(segment),
            "completed": bool(completed),
        }
        store.record(
            feature=MUSIC_FEATURE,
            event_type=EVENT_PLAYBACK,
            source_id=str(track_id),
            content=name or "",
            summary="{}:{} {}s".format(payload["source"], payload["track_id"], payload["seconds"]),
            occurred_at=occurred_at,
            metadata=payload,
            importance="normal",
            searchable=False,
            event_key="music:playback:{}:{}:{}:{}".format(
                payload["source"], payload["track_id"], payload["session_id"], payload["segment"]),
        )
        with self._lock:
            self._dirty = True

    def record_play(self, *, source, track_id, name="", seconds=0.0, session_id="",
                    segment=0, duration=0.0, completed=False, artist="", album="",
                    cover_url="") -> None:
        """登记/快照一次播放会话。segment 递增；聚合只取同一会话的最大秒数。"""
        if not track_id:
            return
        sid = str(session_id) if session_id else str(int(time.time() * 1000))
        self._write_playback(
            source=source, track_id=track_id, name=name, seconds=seconds,
            session_id=sid, segment=segment, duration=duration, completed=completed,
            artist=artist, album=album, cover_url=cover_url,
        )
        self._refresh(force=True)

    def record_feedback(self, *, source, track_id, name="", content="", session_id="",
                        message_id=None, status="ready", session=None) -> None:
        """记录一条听歌反馈，并与曲目关联（幂等：同一会话只记一次）。"""
        if not track_id:
            return
        store = self._store()
        sid = str(session_id) if session_id else str(int(time.time() * 1000))
        meta = {
            "source": str(source or "netease"),
            "track_id": str(track_id),
            "name": name or "",
            "session_id": sid,
            "generation": sid,
            "message_id": message_id,
            "status": status,
            "session": session,
        }
        store.record(
            feature=MUSIC_FEATURE,
            event_type=EVENT_FEEDBACK,
            source_id=str(track_id),
            content=str(content or ""),
            summary="feedback:{}".format(name or track_id),
            metadata=meta,
            importance="normal",
            searchable=True,
            event_key=feedback_event_key(meta["source"], track_id, sid),
        )
        with self._lock:
            self._dirty = True
        self._refresh(force=True)

    def has_feedback(self, *, source: str, track_id: str, session_id: str) -> bool:
        """同一播放会话是否已反馈过（幂等）。"""
        if not track_id or not session_id:
            return False
        try:
            return bool(self._store().event_exists(
                feedback_event_key(source or "netease", track_id, session_id)))
        except Exception:
            return False

    def last_feedback_time(self, *, source: str = "", track_id: str = "") -> float | None:
        """返回该曲最近一次反馈的 Unix 时间戳（用于按曲冷却），无则 None。"""
        if not track_id:
            return None
        try:
            rows = self._store().list_events(
                feature=MUSIC_FEATURE, event_type=EVENT_FEEDBACK,
                source_id=str(track_id), limit=500)
        except Exception:
            return None
        prefix = str(source).split("-", 1)[0]
        latest = None
        for row in rows:
            try:
                md = json.loads(row.get("metadata_json") or "{}")
            except (TypeError, ValueError):
                md = {}
            if not isinstance(md, dict):
                md = {}
            row_source = str(md.get("source") or "").split("-", 1)[0]
            if prefix and row_source and row_source != prefix:
                continue
            ts = _parse_iso(row.get("occurred_at"))
            if ts is not None and (latest is None or ts > latest):
                latest = ts
        return latest

    def update_song(self, file_path: str, duration_seconds: int) -> None:
        """兼容旧本地播放器：把一次播放登记为 source="local" 的会话事件。"""
        seconds = _to_float(duration_seconds)
        if seconds <= 0:
            return
        path_str = str(file_path)
        self.record_play(
            source="local", track_id=path_str, name=Path(file_path).stem,
            seconds=seconds, session_id="local-{}".format(int(time.time() * 1000)),
        )

    # ---------- aggregate ----------
    def _aggregate(self):
        """返回 None 表示事件库不可用，调用方回退到旧 JSON。"""
        try:
            store = self._store()
            rows = store.list_events(feature=MUSIC_FEATURE, event_type=EVENT_PLAYBACK, limit=50000)
            feedback_rows = store.list_events(feature=MUSIC_FEATURE, event_type=EVENT_FEEDBACK, limit=50000)
        except Exception:
            return None

        sessions: dict = {}
        for row in rows:
            try:
                md = json.loads(row.get("metadata_json") or "{}")
            except (TypeError, ValueError):
                md = {}
            if not isinstance(md, dict):
                md = {}
            source = str(md.get("source") or "local")
            # 会话身份必须用"原始 track_id"：迁移历史的 session_id 固定为 "legacy"，
            # 若在此提前归一化，同曲两种路径拼写的分片会互相覆盖（丢时长）。
            track_id = str(md.get("track_id") or row.get("source_id") or "")
            session_id = str(md.get("session_id") or row.get("id") or "")
            if not track_id:
                continue
            seconds = _to_float(md.get("seconds"))
            occurred = row.get("occurred_at") or ""
            day = row.get("local_date") or (occurred[:10] if occurred else "")
            key = (source, track_id, session_id)
            current = sessions.get(key)
            if current is None:
                sessions[key] = {
                    "source": source, "track_id": track_id, "session_id": session_id,
                    "name": md.get("name") or "", "artist": md.get("artist") or "",
                    "album": md.get("album") or "", "cover_url": md.get("cover_url") or "",
                    "seconds": max(0.0, seconds), "duration": _to_float(md.get("duration")),
                    "completed": bool(md.get("completed")),
                    "first_at": occurred, "last_at": occurred, "day": day,
                }
            else:
                if seconds > current["seconds"]:
                    current["seconds"] = max(0.0, seconds)
                    for field in ("name", "artist", "album", "cover_url"):
                        if md.get(field):
                            current[field] = md[field]
                    if md.get("duration"):
                        current["duration"] = _to_float(md.get("duration"))
                    current["completed"] = bool(md.get("completed"))
                if occurred and (not current["first_at"] or occurred < current["first_at"]):
                    current["first_at"] = occurred
                if occurred and occurred > current["last_at"]:
                    current["last_at"] = occurred
                    current["day"] = day or current["day"]

        feedback_by_track: dict = {}
        for row in feedback_rows:
            try:
                md = json.loads(row.get("metadata_json") or "{}")
            except (TypeError, ValueError):
                md = {}
            if not isinstance(md, dict):
                md = {}
            fb_source = str(md.get("source") or "netease")
            fb_track = str(md.get("track_id") or row.get("source_id") or "")
            if fb_source == "local":
                fb_track = canonical_local_track_id(fb_track, self._track_resolve_cache)
            tk = "{}:{}".format(fb_source, fb_track)
            feedback_by_track[tk] = feedback_by_track.get(tk, 0) + 1

        tracks: dict = {}
        daily: dict = {}
        total = 0.0
        for session in sessions.values():
            secs = max(0.0, session["seconds"])
            # 读取侧同样按曲长封顶：分片取最大值会让"当时曲长未知"的超限值存活下来。
            limit = _duration_seconds(session["duration"])
            if limit > 0:
                secs = min(secs, limit)
            if secs <= 0:
                continue
            total += secs
            display_id = session["track_id"]
            if session["source"] == "local":
                display_id = canonical_local_track_id(display_id, self._track_resolve_cache)
            tk = "{}:{}".format(session["source"], display_id)
            entry = tracks.get(tk)
            if entry is None:
                entry = tracks[tk] = {
                    "source": session["source"], "track_id": display_id,
                    "name": session["name"], "artist": session["artist"],
                    "album": session["album"], "cover_url": session["cover_url"],
                    "seconds": 0.0, "play_count": 0,
                    "first_played": session["first_at"], "last_played": session["last_at"],
                    "feedback_count": feedback_by_track.get(tk, 0),
                }
            entry["seconds"] += secs
            entry["play_count"] += 1
            if session["last_at"] and (not entry["last_played"] or session["last_at"] > entry["last_played"]):
                entry["last_played"] = session["last_at"]
            for field in ("name", "artist", "album", "cover_url"):
                if not entry[field] and session[field]:
                    entry[field] = session[field]
            if session["day"]:
                daily[session["day"]] = daily.get(session["day"], 0.0) + secs

        ordered = sorted(tracks.values(), key=lambda item: item["seconds"], reverse=True)
        return {"total_seconds": total, "tracks": ordered, "daily": daily}

    def _legacy_view(self, agg: dict) -> dict:
        return {
            "total_seconds": int(round(agg["total_seconds"])),
            "songs": {
                item["track_id"]: {
                    "name": item["name"],
                    "seconds": int(round(item["seconds"])),
                    "last_played": item.get("last_played") or "",
                }
                for item in agg["tracks"]
            },
        }

    def _refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and not self._dirty and (now - self._refreshed_at) < self._REFRESH_TTL:
                return
            need_write = force or self._dirty
            agg = self._aggregate()
            if agg is None:
                legacy = self._read_legacy_json()
                self._data = {
                    "total_seconds": int(legacy.get("total_seconds", 0) or 0),
                    "songs": legacy.get("songs") or {},
                    "tracks": [], "daily": {},
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            else:
                view = self._legacy_view(agg)
                self._data = {
                    "total_seconds": view["total_seconds"],
                    "songs": view["songs"],
                    "tracks": agg["tracks"],
                    "daily": agg["daily"],
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
            self._dirty = False
            self._refreshed_at = now
            if need_write:
                self._write_cache()

    def _write_cache(self) -> None:
        try:
            payload = {
                "total_seconds": int(self._data.get("total_seconds", 0) or 0),
                "songs": self._data.get("songs") or {},
                "updated_at": self._data.get("updated_at", ""),
            }
            self._stats_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._stats_file.with_suffix(self._stats_file.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._stats_file)
        except Exception:
            pass

    # ---------- read (legacy API) ----------
    @property
    def data(self) -> dict:
        self._refresh()
        return self._data

    def save(self) -> None:
        with self._lock:
            self._write_cache()

    def get_total_hours(self) -> float:
        return float(self.data.get("total_seconds", 0) or 0) / 3600.0

    def get_most_played_song(self):
        songs = self.data.get("songs") or {}
        if not songs:
            return None, 0
        best = max(songs.values(), key=lambda item: item.get("seconds", 0))
        return best.get("name"), int(best.get("seconds", 0) or 0)

    # ---------- read (extended) ----------
    def statistics(self) -> dict:
        self._refresh()
        data = self._data or {}
        tracks = list(data.get("tracks") or [])
        total = float(data.get("total_seconds", 0) or 0)
        most = tracks[0] if tracks else None
        daily = data.get("daily") or {}
        today = datetime.now().strftime("%Y-%m-%d")
        week_start = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        today_seconds = sum(v for k, v in daily.items() if k == today)
        week_seconds = sum(v for k, v in daily.items() if k >= week_start)
        recent = sorted(tracks, key=lambda item: item.get("last_played") or "", reverse=True)[:10]
        return {
            "total_seconds": int(round(total)),
            "total_hours": total / 3600.0,
            "today_seconds": int(round(today_seconds)),
            "week_seconds": int(round(week_seconds)),
            "play_count": int(sum(int(item.get("play_count", 0)) for item in tracks)),
            "feedback_count": int(sum(int(item.get("feedback_count", 0)) for item in tracks)),
            "most_played": (
                {"name": most["name"], "seconds": int(round(most["seconds"]))} if most else None
            ),
            "tracks": [
                {
                    "source": item["source"], "track_id": item["track_id"],
                    "name": item["name"], "artist": item["artist"],
                    "seconds": int(round(item["seconds"])),
                    "play_count": int(item["play_count"]),
                    "last_played": item.get("last_played") or "",
                }
                for item in tracks[:50]
            ],
            "recent": [
                {
                    "source": item["source"], "track_id": item["track_id"],
                    "name": item["name"], "artist": item["artist"],
                    "last_played": item.get("last_played") or "",
                }
                for item in recent
            ],
        }
