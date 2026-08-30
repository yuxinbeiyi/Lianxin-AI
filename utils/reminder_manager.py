"""
reminder_manager.py - 智能提醒管理模块
支持单次、每天、每周、每月提醒
"""

import json
import uuid
from datetime import datetime, time
from pathlib import Path
from typing import List, Dict, Optional
from utils.paths import get_user_data_dir
from datetime import datetime, time, timedelta

_REMINDER_FILE = get_user_data_dir() / "reminders.json"


class ReminderManager:
    def __init__(self):
        self._reminders: List[Dict] = []
        self._load()

    def _load(self):
        if _REMINDER_FILE.exists():
            try:
                with open(_REMINDER_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._reminders = data.get("reminders", [])
            except Exception:
                self._reminders = []
        else:
            self._reminders = []

    def _save(self):
        _REMINDER_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_REMINDER_FILE, "w", encoding="utf-8") as f:
            json.dump({"reminders": self._reminders}, f, ensure_ascii=False, indent=2)

    def add(self, name: str, rule: str, time_str: str,
            interval: int = 1, weekdays: List[int] = None,
            day_of_month: int = None, advance_minutes: int = 0,
            smart_reply: bool = False) -> Dict:
        reminder = {
            "id": str(uuid.uuid4()),
            "name": name,
            "rule": rule,
            "time": time_str,
            "interval": interval,
            "weekdays": weekdays or [],
            "day_of_month": day_of_month if rule == "monthly" else None,
            "advance_minutes": int(advance_minutes),
            "enabled": True,
            "last_triggered_date": "",
            "smart_reply": smart_reply   # 新增
        }
        self._reminders.append(reminder)
        self._save()
        return reminder

    def delete(self, rid: str):
        self._reminders = [r for r in self._reminders if r["id"] != rid]
        self._save()

    def update(self, rid: str, **kwargs):
        for r in self._reminders:
            if r["id"] == rid:
                r.update(kwargs)
                self._save()
                return True
        return False

    def get_all(self) -> List[Dict]:
        return self._reminders.copy()

    def get_due_reminders(self, now: Optional[datetime] = None) -> List[Dict]:
        """
        获取所有到期的提醒（未被标记为今天已触发）
        支持 advance_minutes 提前提醒
        """
        if now is None:
            now = datetime.now()
        today_date = now.strftime("%Y-%m-%d")
        due = []

        for r in self._reminders:
            if not r["enabled"]:
                continue

            # 跳过今天已经触发过的
            if r.get("last_triggered_date") == today_date:
                continue

            # 解析提醒时间（时:分）
            try:
                remind_time = datetime.strptime(r["time"], "%H:%M").time()
            except ValueError:
                print(f"[Reminder] 时间格式错误: {r['time']}")
                continue

            # 计算实际提醒时刻（考虑 advance_minutes 提前）
            advance = r.get("advance_minutes", 0)
            # 将提醒时间转为当天的 datetime 对象
            base_dt = datetime.combine(now.date(), remind_time)
            # 减去提前分钟数得到实际触发时刻
            trigger_dt = base_dt - timedelta(minutes=advance)

            # 若提前后时间跨天（例如 00:10 提前 20 分钟 → 前一天 23:50）
            # 则只考虑同一天的触发，跨天的不触发（避免提前一天报警）
            if trigger_dt.date() != now.date():
                # 如果跨天，说明提前量过大，忽略本次检查（不触发）
                continue

            # 比较当前时间是否 >= 触发时刻（使用整数秒比较，避免微秒问题）
            now_sec = now.hour * 3600 + now.minute * 60 + now.second
            trigger_sec = trigger_dt.hour * 3600 + trigger_dt.minute * 60 + trigger_dt.second

            if now_sec < trigger_sec:
                continue  # 还未到触发时间

            # 检查重复规则是否应在今天触发
            if not self._should_trigger_today(r, now):
                continue

            # 对于 once 类型，如果提醒时间已是过去（且从未触发过），
            # 可以选择顺延到第二天（避免刚添加就错误触发）
            # 这里增加保护：如果 reminder 刚添加（id 很新）且时间已过，且从未触发过，则顺延
            # 但为了简单，先保持原逻辑，仅添加日志
            due.append(r)

        return due

    def mark_triggered(self, rid: str):
        """标记某提醒已触发（今日不再触发）"""
        for r in self._reminders:
            if r["id"] == rid:
                r["last_triggered_date"] = datetime.now().strftime("%Y-%m-%d")
                self._save()
                break

    def _should_trigger_today(self, reminder: Dict, now: datetime) -> bool:
        rule = reminder["rule"]
        if rule == "once":
            # 单次提醒：如果从未触发过（last_triggered_date 为空），则触发
            return not reminder.get("last_triggered_date")
        elif rule == "daily":
            return True
        elif rule == "weekly":
            weekdays = reminder.get("weekdays", [])
            if not weekdays:
                return False
            # now.weekday(): 0=周一, 6=周日
            return now.weekday() in weekdays
        elif rule == "monthly":
            day = reminder.get("day_of_month")
            if day is None:
                return False
            return now.day == day
        else:
            return False

    def enable(self, rid: str, enabled: bool = True):
        for r in self._reminders:
            if r["id"] == rid:
                r["enabled"] = enabled
                self._save()
                return True
        return False


_SHARED: Optional["ReminderManager"] = None


def get_reminder_manager() -> "ReminderManager":
    """进程内共享实例（GUI 面板、DutyScheduler、LLM 工具共用同一份数据）。"""
    global _SHARED
    if _SHARED is None:
        _SHARED = ReminderManager()
    return _SHARED