"""
ProactiveChatScheduler：主动聊天调度器
负责管理每小时权重、发送频率，并判断当前是否应触发主动消息。

配置持久化路径：用户目录/.lianxin/proactive_settings.json
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from utils.paths import get_user_data_dir

_SETTINGS_PATH = get_user_data_dir() / "proactive_settings.json"

# 默认每小时权重（0~10），下标为小时数 0~23
_DEFAULT_WEIGHTS = [
    0, 0, 0, 0, 0, 0, 0, 0,   # 00~07 深夜/凌晨 不发
    4, 6, 6, 6,                # 08~11 上午（提高权重）
    5, 5, 5,                   # 12~14 午休
    6, 6, 6,                   # 15~17 下午
    5, 5,                      # 18~19 傍晚
    8, 8, 8,                   # 20~22 晚上（高权重）
    4,                         # 23    夜晚
]

# 基础触发概率（每次 5 分钟轮询）
_BASE_RATE = 0.08   # 从 0.05 提高到 0.08，增强主动感

# 两次主动消息的最小间隔（分钟）
DEFAULT_MIN_INTERVAL_MINUTES = 25

# 用户发消息后推迟主动聊天的默认时间（分钟）
DEFAULT_USER_ACTIVITY_DEFER_MINUTES = 15


class ProactiveChatScheduler:
    """
    每次 GUI 定时器触发（5 分钟）时调用 should_fire()。
    返回 True 表示本次应生成一条主动消息。
    """

    def __init__(self):
        self._settings: dict = {}
        self._load_settings()

        # 调度状态：成功时间跨重启保留，用户活跃推迟只在本次运行有效。
        try:
            self._last_fire_time = datetime.fromisoformat(
                self._settings.get("_last_global_success", "")
            )
        except (TypeError, ValueError):
            self._last_fire_time = None
        self._defer_until: datetime | None = None          # 因用户活跃而推迟到的时间
        # 新建的桌面会话如果始终没有用户消息，需要在等待期后主动破冰。
        # 该状态只属于当前运行，不持久化，避免重启后误判历史会话。
        self._empty_session_started_at: datetime | None = None
        self._empty_session_waiting = False

    # ── 持久化 ────────────────────────────────────────────────

    def _load_settings(self):
        defaults = self._default_settings()
        try:
            if _SETTINGS_PATH.exists():
                data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
                self._settings = data
                # 兼容旧版本：enabled → desktop_enabled
                if "enabled" in self._settings and "desktop_enabled" not in self._settings:
                    self._settings["desktop_enabled"] = self._settings.pop("enabled")
                # 新字段只补缺省值，不覆盖用户已有配置。
                for key, value in defaults.items():
                    if key not in self._settings:
                        self._settings[key] = value.copy() if isinstance(value, dict) else value
                return
        except Exception:
            pass
        self._settings = defaults

    def _default_settings(self) -> dict:
        return {
            "desktop_enabled": False,
            "music_feedback_enabled": True,
            "weights": list(_DEFAULT_WEIGHTS),           # 24 个整数 0~10
            "frequency": 3,                               # 1~15
            "min_interval_minutes": DEFAULT_MIN_INTERVAL_MINUTES,
            "user_defer_minutes": DEFAULT_USER_ACTIVITY_DEFER_MINUTES,
            "qq_enabled": False,
            "memory_link_enabled": True,
            "memory_evaluation_interval_minutes": 30,
            "memory_max_candidates": 8,
            # 统一行为调度：达到触发条件后只选择并执行一种行为
            "normal_enabled": True,
            "behavior_weights": {
                "normal": 30,
                "observe": 25,
                "bilibili": 20,
                "slack": 25,
            },
            "behavior_cooldowns": {
                "normal": 30,
                "observe": 60,
                "bilibili": 180,
                "slack": 45,
            },
            "avoid_behavior_repeat": True,
            "fallback_on_failure": True,
            "_behavior_last_success": {},
            "_behavior_history": [],
            "_last_global_success": "",
            # 观察设置
            "observe_enabled": False,
            "screenshot_prob": 30,
            "camera_prob": 15,
            "camera_index": 0,
            "camera_wait": 15,
            "observe_send_to_qq": False,
            # B站冲浪配置
            "bilibili_enabled": False,
            "bilibili_probability": 40,
            "bilibili_max_results": 5,
            "bilibili_sort": "totalrank",
            "bilibili_tag_cooldown_hours": 48,
            # 摸鱼设置
            "slack_enabled": False,
            "slack_idle_minutes": 20,

            "slack_supplement_diary": True,
            "slack_review_old_diary": True,
            "slack_search_old_topic": True,
            "slack_remind_todo": True,
            "slack_random_question": True,
            "slack_weather_chitchat": True,
        
            "slack_read_local_files": True,
            "slack_browser_history": True,
            "slack_check_cpu_disk": True,
            "slack_check_recycle_bin": True,
            "slack_remind_rest": True,
            "slack_remind_water": True,
            "slack_anniversary_remind": True,
            "slack_next_song": True,
            "_slack_last_diary_supplement_count": 0,
            "_slack_last_diary_supplement_date": "",
        }

    def reload_settings(self):
        """从 JSON 文件重新加载设置（用于外部修改后同步内存状态）。"""
        self._load_settings()

    def save_settings(self):
        """将当前设置写入 JSON 文件。"""
        try:
            _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SETTINGS_PATH.write_text(
                json.dumps(self._settings, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[ProactiveChat] 保存设置失败: {e}")

    # ── 属性访问 ──────────────────────────────────────────────

    @property
    def desktop_enabled(self) -> bool:
        return self._settings.get("desktop_enabled", False)

    @desktop_enabled.setter
    def desktop_enabled(self, val: bool):
        self._settings["desktop_enabled"] = val

    @property
    def music_feedback_enabled(self) -> bool:
        return self._settings.get("music_feedback_enabled", True)

    @music_feedback_enabled.setter
    def music_feedback_enabled(self, val: bool):
        self._settings["music_feedback_enabled"] = val

    @property
    def weights(self) -> list[int]:
        return self._settings.get("weights", list(_DEFAULT_WEIGHTS))

    @weights.setter
    def weights(self, val: list[int]):
        self._settings["weights"] = val

    @property
    def frequency(self) -> int:
        return self._settings.get("frequency", 3)

    @frequency.setter
    def frequency(self, val: int):
        self._settings["frequency"] = max(1, min(50, val))

    @property
    def min_interval_minutes(self) -> int:
        return self._settings.get("min_interval_minutes", DEFAULT_MIN_INTERVAL_MINUTES)

    @min_interval_minutes.setter
    def min_interval_minutes(self, val: int):
        self._settings["min_interval_minutes"] = max(10, min(120, val))

    @property
    def user_defer_minutes(self) -> int:
        return self._settings.get("user_defer_minutes", DEFAULT_USER_ACTIVITY_DEFER_MINUTES)

    @user_defer_minutes.setter
    def user_defer_minutes(self, val: int):
        self._settings["user_defer_minutes"] = max(5, min(60, val))

    @property
    def qq_enabled(self) -> bool:
        return self._settings.get("qq_enabled", False)

    @qq_enabled.setter
    def qq_enabled(self, val: bool):
        self._settings["qq_enabled"] = val

    @property
    def memory_link_enabled(self) -> bool:
        return bool(self._settings.get("memory_link_enabled", True))

    @memory_link_enabled.setter
    def memory_link_enabled(self, value: bool):
        self._settings["memory_link_enabled"] = bool(value)

    @property
    def memory_evaluation_interval_minutes(self) -> int:
        return max(10, min(1440, int(self._settings.get("memory_evaluation_interval_minutes", 30))))

    @memory_evaluation_interval_minutes.setter
    def memory_evaluation_interval_minutes(self, value: int):
        self._settings["memory_evaluation_interval_minutes"] = max(10, min(1440, int(value)))

    @property
    def memory_max_candidates(self) -> int:
        return max(1, min(20, int(self._settings.get("memory_max_candidates", 8))))

    @memory_max_candidates.setter
    def memory_max_candidates(self, value: int):
        self._settings["memory_max_candidates"] = max(1, min(20, int(value)))

    @property
    def normal_enabled(self) -> bool:
        return self._settings.get("normal_enabled", True)

    @normal_enabled.setter
    def normal_enabled(self, val: bool):
        self._settings["normal_enabled"] = bool(val)

    @property
    def behavior_weights(self) -> dict[str, int]:
        defaults = self._default_settings()["behavior_weights"]
        stored = self._settings.get("behavior_weights", {})
        return {name: max(0, min(100, int(stored.get(name, value))))
                for name, value in defaults.items()}

    @behavior_weights.setter
    def behavior_weights(self, val: dict[str, int]):
        current = self.behavior_weights
        for name in current:
            if name in val:
                current[name] = max(0, min(100, int(val[name])))
        self._settings["behavior_weights"] = current

    @property
    def behavior_cooldowns(self) -> dict[str, int]:
        defaults = self._default_settings()["behavior_cooldowns"]
        stored = self._settings.get("behavior_cooldowns", {})
        return {name: max(0, min(1440, int(stored.get(name, value))))
                for name, value in defaults.items()}

    @behavior_cooldowns.setter
    def behavior_cooldowns(self, val: dict[str, int]):
        current = self.behavior_cooldowns
        for name in current:
            if name in val:
                current[name] = max(0, min(1440, int(val[name])))
        self._settings["behavior_cooldowns"] = current

    @property
    def avoid_behavior_repeat(self) -> bool:
        return self._settings.get("avoid_behavior_repeat", True)

    @avoid_behavior_repeat.setter
    def avoid_behavior_repeat(self, val: bool):
        self._settings["avoid_behavior_repeat"] = bool(val)

    @property
    def fallback_on_failure(self) -> bool:
        return self._settings.get("fallback_on_failure", True)

    @fallback_on_failure.setter
    def fallback_on_failure(self, val: bool):
        self._settings["fallback_on_failure"] = bool(val)

    def is_behavior_ready(self, behavior: str, now: datetime | None = None) -> bool:
        """检查单类行为冷却；没有成功执行记录时立即可选。"""
        stamp = self._settings.get("_behavior_last_success", {}).get(behavior, "")
        if not stamp:
            return True
        try:
            last = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            return True
        cooldown = self.behavior_cooldowns.get(behavior, 0)
        return ((now or datetime.now()) - last).total_seconds() >= cooldown * 60

    def choose_behavior(self, candidates: list[str]) -> str:
        """从已经通过可用性检查的行为中按权重选择一种。"""
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            return ""
        weights = self.behavior_weights
        effective = [float(weights.get(name, 0)) for name in candidates]
        history = self._settings.get("_behavior_history", [])
        if self.avoid_behavior_repeat and history:
            last = history[-1]
            if last in candidates and len(candidates) > 1:
                effective[candidates.index(last)] *= 0.15
        if sum(effective) <= 0:
            return ""
        return random.choices(candidates, weights=effective, k=1)[0]

    def record_behavior_success(self, behavior: str):
        """只有界面实际收到非空消息后才记录全局和单行为冷却。"""
        now = datetime.now()
        self._empty_session_waiting = False
        self._empty_session_started_at = None
        self._last_fire_time = now
        self._settings["_last_global_success"] = now.isoformat(timespec="seconds")
        last_success = self._settings.setdefault("_behavior_last_success", {})
        last_success[behavior] = now.isoformat(timespec="seconds")
        history = self._settings.setdefault("_behavior_history", [])
        history.append(behavior)
        del history[:-12]
        self.save_settings()

    # ── 观察设置 ────────────────────────────────────────────────

    @property
    def observe_enabled(self) -> bool:
        return self._settings.get("observe_enabled", False)

    @observe_enabled.setter
    def observe_enabled(self, val: bool):
        self._settings["observe_enabled"] = val

    @property
    def screenshot_prob(self) -> int:
        """截图触发概率 0~100（每小时）"""
        return self._settings.get("screenshot_prob", 30)

    @screenshot_prob.setter
    def screenshot_prob(self, val: int):
        self._settings["screenshot_prob"] = max(0, min(100, val))

    @property
    def camera_prob(self) -> int:
        """摄像头触发概率 0~100（每小时）"""
        return self._settings.get("camera_prob", 15)

    @camera_prob.setter
    def camera_prob(self, val: int):
        self._settings["camera_prob"] = max(0, min(100, val))

    @property
    def camera_index(self) -> int:
        return self._settings.get("camera_index", 0)

    @camera_index.setter
    def camera_index(self, val: int):
        self._settings["camera_index"] = max(0, val)

    @property
    def camera_wait(self) -> int:
        """摄像头打开后等待秒数再抓拍"""
        return self._settings.get("camera_wait", 15)

    @camera_wait.setter
    def camera_wait(self, val: int):
        self._settings["camera_wait"] = max(3, min(30, val))

    @property
    def observe_send_to_qq(self) -> bool:
        """观察消息是否同时发送到 QQ"""
        return self._settings.get("observe_send_to_qq", False)

    @observe_send_to_qq.setter
    def observe_send_to_qq(self, val: bool):
        self._settings["observe_send_to_qq"] = val

    # ── B站冲浪设置 ────────────────────────────────────────────

    @property
    def bilibili_enabled(self) -> bool:
        return self._settings.get("bilibili_enabled", False)

    @bilibili_enabled.setter
    def bilibili_enabled(self, val: bool):
        self._settings["bilibili_enabled"] = val

    @property
    def bilibili_probability(self) -> int:
        return self._settings.get("bilibili_probability", 40)

    @bilibili_probability.setter
    def bilibili_probability(self, val: int):
        self._settings["bilibili_probability"] = max(0, min(100, val))

    @property
    def bilibili_max_results(self) -> int:
        return self._settings.get("bilibili_max_results", 5)

    @bilibili_max_results.setter
    def bilibili_max_results(self, val: int):
        self._settings["bilibili_max_results"] = max(1, min(20, val))

    @property
    def bilibili_sort(self) -> str:
        return self._settings.get("bilibili_sort", "totalrank")

    @bilibili_sort.setter
    def bilibili_sort(self, val: str):
        self._settings["bilibili_sort"] = val

    @property
    def bilibili_tag_cooldown_hours(self) -> int:
        return self._settings.get("bilibili_tag_cooldown_hours", 48)

    @bilibili_tag_cooldown_hours.setter
    def bilibili_tag_cooldown_hours(self, val: int):
        self._settings["bilibili_tag_cooldown_hours"] = max(1, min(168, val))

    def should_surf_bilibili(self) -> bool:
        if not self.bilibili_enabled:
            return False
        if self.bilibili_probability <= 0:
            return False
        return random.randint(1, 100) <= self.bilibili_probability

    # ── 摸鱼设置 ────────────────────────────────────────────

    @property
    def slack_enabled(self) -> bool:
        return self._settings.get("slack_enabled", False)

    @slack_enabled.setter
    def slack_enabled(self, val: bool):
        self._settings["slack_enabled"] = val

    @property
    def slack_idle_minutes(self) -> int:
        return max(0, min(240, int(self._settings.get("slack_idle_minutes", 20))))

    @slack_idle_minutes.setter
    def slack_idle_minutes(self, val: int):
        self._settings["slack_idle_minutes"] = max(0, min(240, int(val)))

    @property
    def slack_supplement_diary(self) -> bool:
        return self._settings.get("slack_supplement_diary", True)

    @slack_supplement_diary.setter
    def slack_supplement_diary(self, val: bool):
        self._settings["slack_supplement_diary"] = val

    @property
    def slack_review_old_diary(self) -> bool:
        return self._settings.get("slack_review_old_diary", True)

    @slack_review_old_diary.setter
    def slack_review_old_diary(self, val: bool):
        self._settings["slack_review_old_diary"] = val

    @property
    def slack_search_old_topic(self) -> bool:
        return self._settings.get("slack_search_old_topic", True)

    @slack_search_old_topic.setter
    def slack_search_old_topic(self, val: bool):
        self._settings["slack_search_old_topic"] = val

    @property
    def slack_remind_todo(self) -> bool:
        return self._settings.get("slack_remind_todo", True)

    @slack_remind_todo.setter
    def slack_remind_todo(self, val: bool):
        self._settings["slack_remind_todo"] = val

    @property
    def slack_random_question(self) -> bool:
        return self._settings.get("slack_random_question", True)

    @slack_random_question.setter
    def slack_random_question(self, val: bool):
        self._settings["slack_random_question"] = val

    @property
    def slack_weather_chitchat(self) -> bool:
        return self._settings.get("slack_weather_chitchat", True)

    @slack_weather_chitchat.setter
    def slack_weather_chitchat(self, val: bool):
        self._settings["slack_weather_chitchat"] = val

    @property
    def slack_read_local_files(self) -> bool:
        return self._settings.get("slack_read_local_files", True)

    @slack_read_local_files.setter
    def slack_read_local_files(self, val: bool):
        self._settings["slack_read_local_files"] = val

    @property
    def slack_browser_history(self) -> bool:
        return self._settings.get("slack_browser_history", True)

    @slack_browser_history.setter
    def slack_browser_history(self, val: bool):
        self._settings["slack_browser_history"] = val

    @property
    def slack_check_cpu_disk(self) -> bool:
        return self._settings.get("slack_check_cpu_disk", True)

    @slack_check_cpu_disk.setter
    def slack_check_cpu_disk(self, val: bool):
        self._settings["slack_check_cpu_disk"] = val

    @property
    def slack_check_recycle_bin(self) -> bool:
        return self._settings.get("slack_check_recycle_bin", True)

    @slack_check_recycle_bin.setter
    def slack_check_recycle_bin(self, val: bool):
        self._settings["slack_check_recycle_bin"] = val

    @property
    def slack_remind_rest(self) -> bool:
        return self._settings.get("slack_remind_rest", True)

    @slack_remind_rest.setter
    def slack_remind_rest(self, val: bool):
        self._settings["slack_remind_rest"] = val

    @property
    def slack_remind_water(self) -> bool:
        return self._settings.get("slack_remind_water", True)

    @slack_remind_water.setter
    def slack_remind_water(self, val: bool):
        self._settings["slack_remind_water"] = val

    @property
    def slack_anniversary_remind(self) -> bool:
        return self._settings.get("slack_anniversary_remind", True)

    @slack_anniversary_remind.setter
    def slack_anniversary_remind(self, val: bool):
        self._settings["slack_anniversary_remind"] = val

    @property
    def slack_next_song(self) -> bool:
        return self._settings.get("slack_next_song", True)

    @slack_next_song.setter
    def slack_next_song(self, val: bool):
        self._settings["slack_next_song"] = val

    def get_enabled_slack_actions(self) -> list[str]:
        """返回当前启用的摸鱼动作列表"""
        actions = []
        if self.slack_supplement_diary:
            actions.append("supplement_diary")
        if self.slack_review_old_diary:
            actions.append("review_old_diary")
        if self.slack_search_old_topic:
            actions.append("search_old_topic")
        if self.slack_remind_todo:
            actions.append("remind_todo")
        if self.slack_random_question:
            actions.append("random_question")
        if self.slack_weather_chitchat:
            actions.append("weather_chitchat")
        if self.slack_read_local_files:
            actions.append("read_local_files")
        if self.slack_browser_history:
            actions.append("browser_history")
        if self.slack_check_cpu_disk:
            actions.append("check_cpu_disk")
        if self.slack_check_recycle_bin:
            actions.append("check_recycle_bin")
        if self.slack_remind_rest:
            actions.append("remind_rest")
        if self.slack_remind_water:
            actions.append("remind_water")
        if self.slack_anniversary_remind:
            actions.append("anniversary_remind")
        if self.slack_next_song:
            actions.append("next_song")
        return actions

    def can_supplement_diary_today(self) -> bool:
        """今天是否还能补充日记（最多2次）"""
        today = datetime.now().strftime("%Y-%m-%d")
        last_date = self._settings.get("_slack_last_diary_supplement_date", "")
        if last_date != today:
            self._settings["_slack_last_diary_supplement_count"] = 0
            self._settings["_slack_last_diary_supplement_date"] = today
        return self._settings.get("_slack_last_diary_supplement_count", 0) < 2

    def record_diary_supplement(self):
        """记录一次日记补充"""
        today = datetime.now().strftime("%Y-%m-%d")
        self._settings["_slack_last_diary_supplement_date"] = today
        self._settings["_slack_last_diary_supplement_count"] = self._settings.get("_slack_last_diary_supplement_count", 0) + 1

    # ── 观察运行时状态 ────────────────────────────────────────────

    def get_last_observation(self) -> str:
        """返回上次观察描述（短期记忆），空字符串表示无。"""
        return self._settings.get("_last_observation", "")

    def set_last_observation(self, desc: str):
        self._settings["_last_observation"] = desc

    # ── 观察触发判断 ──────────────────────────────────────────────

    def should_observe(self) -> str:
        """按来源权重选择截图或摄像头；观察行为本身由统一调度器选择。"""
        if not self.observe_enabled:
            return ""
        if not self.desktop_enabled:
            return ""
        modes = []
        weights = []
        if self.screenshot_prob > 0:
            modes.append("screenshot")
            weights.append(self.screenshot_prob)
        if self.camera_prob > 0:
            modes.append("camera")
            weights.append(self.camera_prob)
        return random.choices(modes, weights=weights, k=1)[0] if modes else ""

    # ── 调度逻辑 ──────────────────────────────────────────────

    def notify_session_started(self):
        """新建空白桌面会话时调用，启动首次主动破冰计时。"""
        self._empty_session_started_at = datetime.now()
        self._empty_session_waiting = True
        # 新会话不应继承上一会话尚未结束的“用户活跃推迟”。
        self._defer_until = None

    def notify_user_active(self):
        """用户发出消息时调用，将主动聊天推迟一段时间。"""
        self._empty_session_waiting = False
        self._empty_session_started_at = None
        self._defer_until = datetime.now() + timedelta(minutes=self.user_defer_minutes)

    def notify_fired(self):
        """主动消息已成功发送时调用，记录时间。"""
        self._last_fire_time = datetime.now()

    def should_fire(self) -> bool:
        """
        5 分钟轮询时调用。
        返回 True 表示本次应生成并发送一条主动消息。
        """
        if not self.desktop_enabled and not self.qq_enabled:
            return False

        now = datetime.now()

        # 因用户活跃而推迟
        if self._defer_until and now < self._defer_until:
            return False

        # 最小间隔
        if self._last_fire_time:
            elapsed = (now - self._last_fire_time).total_seconds() / 60
            if elapsed < self.min_interval_minutes:
                return False

        # 当前小时权重
        hour = now.hour
        weight = self.weights[hour] if hour < len(self.weights) else 0
        if weight <= 0:
            return False

        # 空白会话不能永远依赖随机抽签。等待与“用户发言后推迟”相同的时长后，
        # 在允许时段保证尝试一次；若生成失败，下一轮仍可重试，成功后再清除。
        empty_started = getattr(self, "_empty_session_started_at", None)
        if (self.desktop_enabled
                and getattr(self, "_empty_session_waiting", False)
                and empty_started is not None
                and now >= empty_started + timedelta(minutes=self.user_defer_minutes)):
            return True

        # 增强版概率公式：P = (weight/10) × (frequency/15) × BASE_RATE × 1.5
        prob = (weight / 10.0) * (self.frequency / 30.0) * _BASE_RATE * 1.5
        prob = min(prob, 0.3)
        return random.random() < prob

    def can_deliver_memory_cue(self) -> bool:
        """Apply user-activity, cooldown and quiet-hour policy without randomness."""
        if not self.memory_link_enabled or (not self.desktop_enabled and not self.qq_enabled):
            return False
        now = datetime.now()
        if self._defer_until and now < self._defer_until:
            return False
        if self._last_fire_time and (now-self._last_fire_time).total_seconds()/60 < self.min_interval_minutes:
            return False
        return bool(self.weights[now.hour] if now.hour < len(self.weights) else 0)

    def can_deliver_emotional_motive(self) -> bool:
        """Apply delivery policy to a v3 emotional contact motive without randomness."""
        if not self.normal_enabled or (not self.desktop_enabled and not self.qq_enabled):
            return False
        now = datetime.now()
        if self._defer_until and now < self._defer_until:
            return False
        if self._last_fire_time:
            elapsed = (now - self._last_fire_time).total_seconds() / 60
            if elapsed < self.min_interval_minutes:
                return False
        if not self.is_behavior_ready("normal", now):
            return False
        return bool(self.weights[now.hour] if now.hour < len(self.weights) else 0)

    def should_slack_fire(self) -> bool:
        """
        使用与主动聊天相同的 24h 权重 + 概率公式判断是否触发摸鱼。
        5 分钟轮询时调用。
        """
        if not self.slack_enabled:
            return False
        actions = self.get_enabled_slack_actions()
        if not actions:
            return False

        now = datetime.now()
        # 因用户活跃而推迟
        if self._defer_until and now < self._defer_until:
            return False

        # 最小间隔
        if self._last_fire_time:
            elapsed = (now - self._last_fire_time).total_seconds() / 60
            if elapsed < self.min_interval_minutes:
                return False

        # 当前小时权重
        hour = now.hour
        weight = self.weights[hour] if hour < len(self.weights) else 0
        if weight <= 0:
            return False

        # 摸鱼概率 = 主动聊天概率 × 0.9（保持一致）
        prob = (weight / 10.0) * (self.frequency / 30.0) * _BASE_RATE * 0.9
        prob = min(prob, 0.2)
        return random.random() < prob
    def should_slack(self) -> str:
        """
        判断是否应该触发摸鱼。
        返回摸鱼动作名称，空字符串表示不触发。
        """
        if not self.slack_enabled:
            return ""
        actions = self.get_enabled_slack_actions()
        if not actions:
            return ""
        # 补充日记有每日上限
        if "supplement_diary" in actions and not self.can_supplement_diary_today():
            actions.remove("supplement_diary")
        if not actions:
            return ""
        return random.choice(actions)

    def debug_fire(self) -> bool:
        """
        调试按钮：忽略所有限制，直接触发一次。
        """
        if not self.desktop_enabled and not self.qq_enabled:
            return False
        return True
