"""
AccompanyStats：莲心陪伴统计模块
记录累计使用时长，支持跨会话累加
"""

import json
import threading
import time
from datetime import datetime, date
from pathlib import Path
from utils.paths import get_user_data_dir   # 新增导入


class AccompanyStats:
    """陪伴统计管理器"""

    def __init__(self):
        # 使用用户数据目录
        self._data_dir = get_user_data_dir()
        self._stats_file = self._data_dir / "accompany_stats.json"
        self._ensure_data_dir()
        self._load()
        self._ensure_visual_stats()
        self._visual_lock = threading.RLock()
        self._video_session_started = None
        self._voice_call_session_started = None
        self._session_start_time = None  # 本次启动时间

    def _ensure_data_dir(self):
        """确保用户数据目录存在"""
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _load(self):
        """从文件加载统计数据"""
        if self._stats_file.exists():
            try:
                with open(self._stats_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._total_seconds = data.get("total_seconds", 0)
                    self._session_count = data.get("session_count", 0)
                    self._first_meet_date = data.get("first_meet_date", "")
                    self._avatar_interactions = data.get("avatar_interactions", {}) or {}
                    self._avatar_interactions.setdefault("events", [])
            except (json.JSONDecodeError, IOError):
                self._total_seconds = 0
                self._session_count = 0
                self._first_meet_date = ""
                self._avatar_interactions = {"events": []}
        else:
            self._total_seconds = 0
            self._session_count = 0
            self._first_meet_date = ""
            self._avatar_interactions = {"events": []}

    @staticmethod
    def _default_visual_stats():
        return {"video_seconds": 0.0, "voice_call_seconds": 0.0,
                "gesture_interaction_count": 0,
                "gesture_counts": {"wave": 0, "thumbs_up": 0, "ok": 0},
                "vision_sessions": 0, "voice_call_sessions": 0,
                "last_vision_at": "", "last_voice_call_at": "", "last_gesture_at": ""}

    def _ensure_visual_stats(self, data=None):
        current = self._default_visual_stats()
        current.update((data or {}).get("visual_stats", {}) or {})
        current["gesture_counts"] = {
            **self._default_visual_stats()["gesture_counts"],
            **(current.get("gesture_counts", {}) or {}),
        }
        self._visual_stats = current

    def _start_visual_session(self, kind):
        with self._visual_lock:
            attr = f"_{kind}_session_started"
            if getattr(self, attr) is not None:
                return False
            setattr(self, attr, time.monotonic())
            key = "vision_sessions" if kind == "video" else "voice_call_sessions"
            self._visual_stats[key] = int(self._visual_stats.get(key, 0)) + 1
            self._save()
            return True

    def _end_visual_session(self, kind):
        with self._visual_lock:
            attr = f"_{kind}_session_started"
            started = getattr(self, attr)
            if started is None:
                return 0
            elapsed = max(0.0, time.monotonic() - started)
            setattr(self, attr, None)
            key = "video_seconds" if kind == "video" else "voice_call_seconds"
            self._visual_stats[key] = float(self._visual_stats.get(key, 0.0)) + elapsed
            stamp = "last_vision_at" if kind == "video" else "last_voice_call_at"
            self._visual_stats[stamp] = datetime.now().isoformat(timespec="seconds")
            self._save()
            return int(elapsed)

    def start_video_session(self):
        return self._start_visual_session("video")

    def end_video_session(self):
        return self._end_visual_session("video")

    def start_voice_call_session(self):
        return self._start_visual_session("voice_call")

    def end_voice_call_session(self):
        return self._end_visual_session("voice_call")

    def record_gesture_interaction(self, kind, reply_source="llm"):
        kind = {"GESTURE_WAVE": "wave", "GESTURE_THUMBS_UP": "thumbs_up", "GESTURE_OK": "ok"}.get(kind, kind)
        if kind not in ("wave", "thumbs_up", "ok"):
            return False
        with self._visual_lock:
            self._visual_stats["gesture_interaction_count"] += 1
            counts = self._visual_stats["gesture_counts"]
            counts[kind] = int(counts.get(kind, 0)) + 1
            self._visual_stats["last_gesture_at"] = datetime.now().isoformat(timespec="seconds")
            self._save()
            return True

    def get_visual_stats(self):
        with self._visual_lock:
            result = dict(self._visual_stats)
            result["gesture_counts"] = dict(self._visual_stats["gesture_counts"])
            for kind, started in (("video", self._video_session_started), ("voice_call", self._voice_call_session_started)):
                if started is not None:
                    key = "video_seconds" if kind == "video" else "voice_call_seconds"
                    result[key] += max(0.0, time.monotonic() - started)
            return result

    def reset_visual_stats(self):
        with self._visual_lock:
            self._visual_stats = self._default_visual_stats()
            self._video_session_started = None
            self._voice_call_session_started = None
            self._save()

    def reload(self):
        """重新从文件加载数据（用于设置保存后立即更新）"""
        self._load()
        print(f"[陪伴统计] 已重新加载数据，first_meet_date={self._first_meet_date}")

    def _save(self):
        """保存统计数据到文件"""
        data = {
            "total_seconds": self._total_seconds,
            "session_count": self._session_count,
            "first_meet_date": self._first_meet_date,
            "avatar_interactions": self._avatar_interactions,
            "visual_stats": self._visual_stats,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(self._stats_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # 以下方法保持不变（start_session, end_session, has_first_meet_date 等）
    # 注意：确保 _save() 和 _load() 已正确使用新路径，其他方法无需改动

    def start_session(self):
        """程序启动时调用，记录本次会话开始时间"""
        self._session_start_time = datetime.now()
        self._session_count += 1
        self._save()

    def end_session(self):
        """程序关闭时调用，计算本次使用时长并累加"""
        if self._session_start_time is not None:
            elapsed = (datetime.now() - self._session_start_time).total_seconds()
            if elapsed > 0:
                self._total_seconds += elapsed
                self._save()
        self._session_start_time = None

    # ── 初识日期管理 ─────────────────────────────────────────

    def has_first_meet_date(self) -> bool:
        return bool(self._first_meet_date)

    def get_first_meet_date(self) -> str:
        return self._first_meet_date

    def set_first_meet_date(self, date_str: str):
        self._first_meet_date = date_str
        self._save()

    def record_avatar_interaction(self, interaction_type="user_tap", reaction_type="neutral"):
        """记录头像互动；这是陪伴统计，不写入长期记忆。"""
        data = self._avatar_interactions
        data["interaction_count"] = int(data.get("interaction_count", 0)) + 1
        data["user_tap_count"] = int(data.get("user_tap_count", 0)) + (1 if interaction_type == "user_tap" else 0)
        data["assistant_counter_tap_count"] = int(data.get("assistant_counter_tap_count", 0)) + (1 if interaction_type == "counter_tap" else 0)
        data["assistant_counter_headpat_count"] = int(data.get("assistant_counter_headpat_count", 0)) + (1 if interaction_type == "counter_headpat" else 0)
        data["last_interaction_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        types = data.setdefault("reaction_types", {})
        types[reaction_type] = int(types.get(reaction_type, 0)) + 1
        events = data.setdefault("events", [])
        events.append({
            "at": datetime.now().isoformat(timespec="seconds"),
            "type": interaction_type,
            "reaction": reaction_type,
        })
        del events[:-100]
        self._save()

    def clear_avatar_events(self):
        """只清空头像互动明细，保留陪伴时长、会话次数和初识日期。"""
        data = self._avatar_interactions
        for key in (
            "interaction_count", "user_tap_count", "assistant_counter_tap_count",
            "counter_tap_count", "assistant_counter_headpat_count", "counter_headpat_count",
            "assistant_tap_user_count", "user_self_tap_count", "user_headpat_count",
            "assistant_headpat_user_count", "sound_count", "llm_success_count",
            "fallback_count", "streak_max",
        ):
            data.pop(key, None)
        data["events"] = []
        data.pop("last_interaction_at", None)
        self._save()

    def record_avatar_detail(self, interaction_type, *, actor="user", target="assistant",
                             source="user", reaction="neutral", sound=False,
                             llm=False, fallback=False, streak=0, context=None):
        """记录可回溯的头像互动明细；不写入长期记忆。"""
        self.record_avatar_interaction(interaction_type, reaction)
        data = self._avatar_interactions
        counter_map = {
            "counter_tap": "counter_tap_count",
            "counter_headpat": "counter_headpat_count",
            "assistant_tap_user": "assistant_tap_user_count",
            "user_self_tap": "user_self_tap_count",
            "user_headpat": "user_headpat_count",
            "assistant_headpat_user": "assistant_headpat_user_count",
        }
        key = counter_map.get(interaction_type)
        if key:
            data[key] = int(data.get(key, 0)) + 1
        if sound:
            data["sound_count"] = int(data.get("sound_count", 0)) + 1
        if llm:
            data["llm_success_count"] = int(data.get("llm_success_count", 0)) + 1
        if fallback:
            data["fallback_count"] = int(data.get("fallback_count", 0)) + 1
        data["streak_max"] = max(int(data.get("streak_max", 0)), int(streak or 0))
        if data.get("events"):
            event = data["events"][-1]
            event.update({
                "actor": actor, "target": target, "source": source,
                "sound": bool(sound), "llm": bool(llm), "fallback": bool(fallback),
                "streak": int(streak or 0), "context": context or {},
            })
            self._save()

    def record_avatar_outcome(self, *, llm=False, fallback=False):
        """补写最近一次互动的生成结果。"""
        data = self._avatar_interactions
        if llm:
            data["llm_success_count"] = int(data.get("llm_success_count", 0)) + 1
        if fallback:
            data["fallback_count"] = int(data.get("fallback_count", 0)) + 1
        if data.get("events"):
            data["events"][-1].update({"llm": bool(llm), "fallback": bool(fallback)})
        self._save()

    def get_avatar_interactions(self) -> dict:
        return dict(self._avatar_interactions)

    def get_avatar_interaction_summary(self) -> dict:
        events = self._avatar_interactions.get("events", [])
        today = datetime.now().date().isoformat()
        week_start = date.today().toordinal() - date.today().weekday()
        today_count = 0
        week_count = 0
        for event in events:
            stamp = str(event.get("at", ""))
            if stamp[:10] == today:
                today_count += 1
            try:
                if datetime.fromisoformat(stamp).date().toordinal() >= week_start:
                    week_count += 1
            except ValueError:
                pass
        return {
            "total": int(self._avatar_interactions.get("interaction_count", 0)),
            "user_taps": int(self._avatar_interactions.get("user_tap_count", 0)),
            "counter": int(self._avatar_interactions.get("assistant_counter_tap_count", 0)),
            "counter_taps": max(
                int(self._avatar_interactions.get("counter_tap_count", 0)),
                int(self._avatar_interactions.get("assistant_counter_tap_count", 0)),
            ),
            "counter_headpats": max(
                int(self._avatar_interactions.get("counter_headpat_count", 0)),
                int(self._avatar_interactions.get("assistant_counter_headpat_count", 0)),
            ),
            "assistant_taps": int(self._avatar_interactions.get("assistant_tap_user_count", 0)),
            "self_taps": int(self._avatar_interactions.get("user_self_tap_count", 0)),
            "user_headpats": int(self._avatar_interactions.get("user_headpat_count", 0)),
            "assistant_headpats": int(self._avatar_interactions.get("assistant_headpat_user_count", 0)),
            "sound_count": int(self._avatar_interactions.get("sound_count", 0)),
            "llm_success": int(self._avatar_interactions.get("llm_success_count", 0)),
            "fallback": int(self._avatar_interactions.get("fallback_count", 0)),
            "streak_max": int(self._avatar_interactions.get("streak_max", 0)),
            "today": today_count,
            "week": week_count,
            "last_interaction_at": self._avatar_interactions.get("last_interaction_at", ""),
            "events": list(events),
        }

    def get_total_days_since_first_meet(self) -> int:
        if not self._first_meet_date:
            return 0
        try:
            first_date = datetime.strptime(self._first_meet_date, "%Y-%m-%d").date()
            today = date.today()
            return (today - first_date).days + 1
        except ValueError:
            return 0

    # ── 统计数据获取 ─────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "total_seconds": self._total_seconds,
            "session_count": self._session_count
        }

    def get_current_total_seconds(self) -> int:
        current_seconds = self._total_seconds
        if self._session_start_time is not None:
            elapsed = (datetime.now() - self._session_start_time).total_seconds()
            current_seconds += max(0, elapsed)
        return int(current_seconds)

    def get_current_session_seconds(self) -> int:
        """Return the active-session duration without changing persisted totals."""
        if self._session_start_time is None:
            return 0
        return max(0, int((datetime.now() - self._session_start_time).total_seconds()))

    def get_current_formatted_duration(self) -> str:
        seconds = self.get_current_total_seconds()
        days = seconds // 86400
        seconds %= 86400
        hours = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0 or days > 0:
            parts.append(f"{hours}小时")
        if minutes > 0 or hours > 0 or days > 0:
            parts.append(f"{minutes}分钟")
        parts.append(f"{seconds}秒")
        return "".join(parts)

    def get_formatted_duration(self) -> str:
        seconds = int(self._total_seconds)
        days = seconds // 86400
        seconds %= 86400
        hours = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0 or days > 0:
            parts.append(f"{hours}小时")
        if minutes > 0 or hours > 0 or days > 0:
            parts.append(f"{minutes}分钟")
        parts.append(f"{seconds}秒")
        return "".join(parts)

    def reset(self) -> str:
        self._total_seconds = 0
        self._session_count = 0
        self._first_meet_date = ""
        self._avatar_interactions = {"events": []}
        self._session_start_time = datetime.now()
        self._save()
        return "陪伴统计数据已重置"
