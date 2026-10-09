"""莲心自习室 Web 前端与 Python 计时/数据层之间的桥接。"""

import json
from datetime import datetime

from pathlib import Path

from PyQt5.QtCore import QObject, QSettings, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QFileDialog

from .database import StudyDatabase
from .report_service import StudyReportService
from .timer import FocusTimer
from utils.sound import play_sound
from utils.resource_path import get_asset_path

try:
    from config import get_user_name
except Exception:  # pragma: no cover - 启动早期配置异常时使用回退称呼
    get_user_name = lambda: "雨心"


class StudyRoomBridge(QObject):
    """QWebChannel 暴露的稳定后端接口，前端不直接接触 SQLite。"""

    timer_tick = pyqtSignal(str)
    phase_changed = pyqtSignal(str)
    tasks_changed = pyqtSignal(str)
    statistics_changed = pyqtSignal(str)
    companion_message = pyqtSignal(str)
    focus_completed = pyqtSignal(str)
    clock_changed = pyqtSignal(str)
    minimize_requested = pyqtSignal()
    fullscreen_requested = pyqtSignal()
    focus_fullscreen_requested = pyqtSignal(bool)
    close_requested = pyqtSignal()

    def __init__(self, parent=None, db_path=None):
        super().__init__(parent)
        self.db = StudyDatabase(db_path)
        self.reports = StudyReportService(self.db)
        self.visit_id = self.db.open_visit()
        self.timer = FocusTimer(self)
        self.timer.tick.connect(self._on_tick)
        self.timer.phase_changed.connect(self._on_phase_changed)
        self.timer.completed.connect(self._on_completed)
        self._closed = False
        self._task_id = None
        self._settings = QSettings("Lianxin", "StudyRoom")
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(30_000)
        self._clock_timer.timeout.connect(self._emit_clock)
        self._clock_timer.start()
        self._stats_timer = QTimer(self)
        # 年度热力图和周趋势包含多次数据库聚合，只在用户打开成长记录时刷新。
        # 一分钟一次已经足够显示“今日打开时长”，并避免隐藏页面持续占用主线程。
        self._stats_timer.setInterval(60_000)
        self._stats_timer.timeout.connect(self._emit_statistics)

    @staticmethod
    def _json(payload):
        return json.dumps(payload, ensure_ascii=False)

    def _task_payload(self):
        return [
            {"id": task["id"], "title": task["title"], "completed": bool(task["completed"]),
             "estimate_minutes": task["estimate_minutes"], "break_minutes": task.get("break_minutes", 5),
             "repeat_enabled": bool(task.get("repeat_enabled", 0))}
            for task in self.db.tasks()
        ]

    def _stats_payload(self):
        data = self.db.stats_range("today")
        trend = self.db.week_trend()
        time_rewind = self.db.time_rewind()
        return {
            "focus_seconds": data["focus_seconds"],
            "room_seconds": data["room_seconds"],
            "visits": data["visits"],
            "completed": data["completed"],
            "completed_sessions": data.get("completed_sessions", data["completed"]),
            "interrupted_sessions": data.get("interrupted_sessions", 0),
            "completed_tasks": data.get("completed_tasks", 0),
            "streak": self.db.streak(),
            "period": "today",
            "previous_focus_seconds": data.get("previous_focus_seconds", 0),
            "previous_completed_sessions": data.get("previous_completed_sessions", 0),
            "previous_label": data.get("previous_label", "昨天"),
            "trend": [{"date": row["date"].isoformat(), "focus_seconds": row["focus_seconds"]} for row in trend],
            "time_rewind": time_rewind,
            "recent_focus": self.db.recent_focus_sessions(),
        }

    def _state_payload(self):
        self.timer.refresh()
        return {
            "tasks": self._task_payload(),
            "stats": self._stats_payload(),
            "timer": {
                "phase": self.timer.phase,
                "remaining": self.timer.remaining,
                "total": self.timer.total,
                "break_seconds": self.timer.break_seconds,
                "repeat_enabled": self.timer.repeat_enabled,
                "task_name": self.timer.task_name,
                "active": self.timer.active,
                "paused": self.timer.paused,
            },
            "user_name": get_user_name(),
            "settings": self._settings_payload(),
            "clock": self._clock_payload(),
            "space": self._space_payload(),
        }

    @staticmethod
    def _clock_payload():
        now = datetime.now()
        weekdays = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
        lunar = "农历日期不可用"
        try:
            from zhdate import ZhDate
            value = ZhDate.from_datetime(now)
            months = ("", "正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊")
            days = ("", "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
                    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
                    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十")
            prefix = "闰" if getattr(value, "is_leap", getattr(value, "leap_month", False)) else ""
            lunar = f"农历{prefix}{months[value.lunar_month]}月{days[value.lunar_day]}"
        except Exception:
            pass
        return {"time": now.strftime("%H:%M"), "date": f"{now.year}年{now.month}月{now.day}日",
                "weekday": weekdays[now.weekday()], "lunar": lunar}

    def _emit_clock(self):
        self.clock_changed.emit(self._json(self._clock_payload()))

    def _settings_payload(self):
        return {
            "focus_minutes": int(self._settings.value("focus_minutes", 25)),
            "break_minutes": int(self._settings.value("break_minutes", 5)),
            "auto_break": str(self._settings.value("auto_break", "true")).lower() in ("1", "true", "yes"),
            "auto_fullscreen": str(self._settings.value("auto_fullscreen", "true")).lower() in ("1", "true", "yes"),
            "show_completion": str(self._settings.value("show_completion", "true")).lower() in ("1", "true", "yes"),
            "animations": str(self._settings.value("animations", "true")).lower() in ("1", "true", "yes"),
        }

    def _wallpapers(self):
        directory = get_asset_path("自习室")
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        items = [{"id": "default", "name": "默认壁纸", "url": ""}]
        if directory.is_dir():
            for image in sorted(directory.iterdir(), key=lambda path: path.name.lower()):
                if image.is_file() and image.suffix.lower() in allowed:
                    items.append({"id": str(image.resolve()), "name": image.stem, "url": QUrl.fromLocalFile(str(image.resolve())).toString()})
        current = str(self._settings.value("space_wallpaper", "default"))
        if current not in {item["id"] for item in items} and Path(current).is_file():
            items.append({"id": current, "name": f"自定义：{Path(current).stem}", "url": QUrl.fromLocalFile(current).toString()})
        return items

    def _space_payload(self):
        return {
            "wallpapers": self._wallpapers(),
            "settings": {
                "wallpaper": str(self._settings.value("space_wallpaper", "default")),
                "wallpaper_opacity": float(self._settings.value("space_wallpaper_opacity", 0.42)),
                "content_mask_opacity": float(self._settings.value("space_content_mask_opacity", 0.82)),
                "fit": str(self._settings.value("space_wallpaper_fit", "cover")),
            },
            "notes": self.db.companion_notes(),
            "week_events": self.db.week_events(),
        }

    def _emit_tasks_and_stats(self):
        self.tasks_changed.emit(self._json(self._task_payload()))
        self._emit_statistics()

    def _emit_statistics(self):
        self.statistics_changed.emit(self._json(self._stats_payload()))

    @pyqtSlot(result=str)
    def get_space(self):
        return self._json(self._space_payload())

    @pyqtSlot(str, float, float, str, result=str)
    def save_space_settings(self, wallpaper, wallpaper_opacity, content_mask_opacity, fit):
        path = str(wallpaper or "default")
        if path != "default" and not Path(path).is_file():
            return self._json(self._space_payload())
        self._settings.setValue("space_wallpaper", path)
        self._settings.setValue("space_wallpaper_opacity", max(0.0, min(1.0, float(wallpaper_opacity))))
        self._settings.setValue("space_content_mask_opacity", max(0.35, min(1.0, float(content_mask_opacity))))
        self._settings.setValue("space_wallpaper_fit", "contain" if fit == "contain" else "cover")
        self._settings.sync()
        return self._json(self._space_payload())

    @pyqtSlot(result=str)
    def choose_custom_wallpaper(self):
        path, _ = QFileDialog.getOpenFileName(None, "选择自习室壁纸", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)")
        return str(Path(path).resolve()) if path else ""

    @pyqtSlot(int, str)
    def update_note(self, note_id, action):
        if self.db.update_note(int(note_id), str(action)):
            self.companion_message.emit("我收到你的回应啦。" if action == "like" else "这张纸条已经替你整理好了。")

    @pyqtSlot(result=str)
    def get_initial_state(self):
        return self._json(self._state_payload())

    @pyqtSlot(str, result=str)
    def get_statistics(self, period="today"):
        period = period if period in ("today", "week", "month", "year") else "today"
        data = self.db.stats_range(period)
        data["streak"] = self.db.streak()
        trend = self.db.week_trend()
        data["trend"] = [{"date": row["date"].isoformat(), "focus_seconds": row["focus_seconds"]} for row in trend]
        data["time_rewind"] = self.db.time_rewind()
        data["recent_focus"] = self.db.recent_focus_sessions()
        return self._json(data)

    @pyqtSlot(str, str, result=str)
    def get_report(self, period="week", anchor=""):
        """提供周报/月报的本地聚合结果，不触发模型调用。"""
        return self._json(self.reports.build(str(period), str(anchor or "")))

    @pyqtSlot(int, int, int)
    def start_focus(self, focus_minutes, break_minutes, task_id=-1):
        if self.timer.active:
            return
        task_id = int(task_id)
        task_name = ""
        task_estimate = None
        task_break = None
        task_repeat = False
        if task_id >= 0:
            for task in self.db.tasks(include_completed=False):
                if task["id"] == task_id:
                    task_name = task["title"]
                    task_estimate = task["estimate_minutes"]
                    task_break = task.get("break_minutes", 5)
                    task_repeat = bool(task.get("repeat_enabled", 0))
                    break
        self._task_id = task_id if task_name else None
        if task_estimate:
            focus_minutes = task_estimate
        if task_break is not None:
            break_minutes = task_break
        focus_minutes = max(1, min(180, int(focus_minutes)))
        break_minutes = max(0, min(60, int(break_minutes)))
        self.timer.start_focus(focus_minutes * 60, break_minutes * 60, task_name, task_repeat)
        self.companion_message.emit("先把注意力留给眼前这一件事。别急着演年度失踪，我在这儿盯着。")

    @pyqtSlot()
    def toggle_pause(self):
        self.timer.toggle_pause()

    @pyqtSlot()
    def stop_focus(self):
        if not self.timer.active:
            return
        phase = self.timer.phase
        started_at = self.timer.started_at
        elapsed = self.timer.stop()
        if phase == "focus" and elapsed > 0:
            self.db.add_focus_session(self._task_id, self.timer.task_name, started_at, elapsed, False)
        self._emit_tasks_and_stats()
        self.companion_message.emit("这一段先到这里。别急着给自己判失败，能回来继续才算本事。")

    @pyqtSlot(str, int, int, bool, result=int)
    def add_task(self, title, estimate_minutes=25, break_minutes=5, repeat_enabled=False):
        title = (title or "").strip()
        if not title:
            return 0
        task_id = self.db.add_task(title, max(1, min(600, int(estimate_minutes))),
                                   max(0, min(60, int(break_minutes))), bool(repeat_enabled))
        self._emit_tasks_and_stats()
        return task_id

    @pyqtSlot(int)
    def toggle_task(self, task_id):
        self.db.toggle_task(int(task_id))
        self._emit_tasks_and_stats()

    @pyqtSlot(int)
    def complete_task(self, task_id):
        if self.db.complete_task(int(task_id)):
            self._emit_tasks_and_stats()
            self.companion_message.emit("这件事收进完成清单了。看吧，你也不是只会把计划写得很好看。")

    @pyqtSlot(int)
    def delete_task(self, task_id):
        self.db.delete_task(int(task_id))
        self._emit_tasks_and_stats()

    @pyqtSlot(int, str, int, int, bool)
    def update_task(self, task_id, title, estimate_minutes=25, break_minutes=5, repeat_enabled=False):
        title = (title or "").strip()
        if not title:
            return
        self.db.update_task(
            int(task_id), title, max(1, min(600, int(estimate_minutes))),
            max(0, min(60, int(break_minutes))), bool(repeat_enabled),
        )
        self._emit_tasks_and_stats()
        self.companion_message.emit("任务配置更新了。下次开始前别再临时和时间讨价还价。")

    @pyqtSlot()
    def refresh_statistics(self):
        self.statistics_changed.emit(self._json(self._stats_payload()))

    @pyqtSlot(bool)
    def set_statistics_active(self, active):
        """仅在成长记录可见时保留周期统计刷新。"""
        active = bool(active)
        if active:
            if not self._stats_timer.isActive():
                self._stats_timer.start()
        else:
            self._stats_timer.stop()

    @pyqtSlot(int, int, bool, bool, bool, bool)
    def save_settings(self, focus_minutes, break_minutes, auto_break, auto_fullscreen,
                      show_completion, animations):
        self._settings.setValue("focus_minutes", max(1, min(180, int(focus_minutes))))
        self._settings.setValue("break_minutes", max(0, min(60, int(break_minutes))))
        self._settings.setValue("auto_break", bool(auto_break))
        self._settings.setValue("auto_fullscreen", bool(auto_fullscreen))
        self._settings.setValue("show_completion", bool(show_completion))
        self._settings.setValue("animations", bool(animations))
        self._settings.sync()
        self.companion_message.emit("自习室设置保存好了。之后就按这套节奏来，别半路赖账。")

    @pyqtSlot()
    def minimize_window(self):
        self.minimize_requested.emit()

    @pyqtSlot()
    def toggle_fullscreen(self):
        self.fullscreen_requested.emit()

    @pyqtSlot(bool)
    def set_focus_fullscreen(self, enabled):
        """进入/离开沉浸专注时请求窗口调整，宿主负责保留原窗口状态。"""
        self.focus_fullscreen_requested.emit(bool(enabled))

    @pyqtSlot()
    def close_window(self):
        self.close_requested.emit()

    def _on_tick(self, remaining, phase):
        self.timer_tick.emit(self._json({"remaining": remaining, "total": self.timer.total, "phase": phase,
                                         "task_name": self.timer.task_name,
                                         "repeat_enabled": self.timer.repeat_enabled,
                                         "active": self.timer.active,
                                         "paused": self.timer.paused}))

    def _on_phase_changed(self, phase):
        self.phase_changed.emit(phase)

    def _on_completed(self, phase, duration):
        if phase == "focus":
            self.db.add_focus_session(self._task_id, self.timer.task_name,
                                      self.timer.started_at, duration, True)
            self.db.create_companion_note(self.timer.task_name, duration)
            # 仅完整走完专注倒计时时播放；暂停、手动结束、休息结束均不触发。
            play_sound("FinishedClock.mp3")
            self._emit_tasks_and_stats()
            self.focus_completed.emit(self._json({
                "task_id": self._task_id,
                "task_name": self.timer.task_name,
                "duration": duration,
                "repeat_enabled": self.timer.repeat_enabled,
            }))
            self.companion_message.emit("居然真的专注完了。先喝口水，剩下的等你缓过来再决定。")
        else:
            self.companion_message.emit("休息时间到了。准备好就回来，前辈我还记着下一段呢。")

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._clock_timer.stop()
        self._stats_timer.stop()
        if self.timer.active:
            phase = self.timer.phase
            elapsed = self.timer.stop()
            if phase == "focus" and elapsed > 0:
                self.db.add_focus_session(self._task_id, self.timer.task_name,
                                          self.timer.started_at, elapsed, False)
        self.db.close_visit(self.visit_id)
