"""自习室专注计时器。"""

import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal


class FocusTimer(QObject):
    tick = pyqtSignal(int, str)
    phase_changed = pyqtSignal(str)
    completed = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self.phase = "idle"
        self.remaining = 0
        self.total = 0
        self.break_seconds = 0
        self.focus_seconds = 0
        self.repeat_enabled = False
        self.started_at = ""
        self.task_name = ""
        self.elapsed_before_pause = 0
        self._last_tick = 0.0
        self._paused = False

    @property
    def active(self):
        return self.phase in ("focus", "break")

    @property
    def paused(self):
        return self.active and self._paused

    def refresh(self):
        """Reconcile elapsed time when the host Qt event loop is not ticking."""
        if self.active and not self.paused:
            self._on_tick()

    def start_focus(self, seconds: int, break_seconds: int, task_name: str = "", repeat_enabled: bool = False):
        self.phase = "focus"
        self.remaining = max(60, int(seconds))
        self.total = self.remaining
        self.focus_seconds = self.total
        self.break_seconds = max(0, int(break_seconds))
        self.repeat_enabled = bool(repeat_enabled)
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.task_name = task_name
        self.elapsed_before_pause = 0
        self._paused = False
        self._last_tick = time.monotonic()
        self.phase_changed.emit(self.phase)
        self.tick.emit(self.remaining, self.phase)
        self._timer.start()

    def toggle_pause(self):
        if not self.active:
            return
        if self._timer.isActive():
            self._timer.stop()
            self._paused = True
        else:
            self._last_tick = time.monotonic()
            self._timer.start()
            self._paused = False
        self.phase_changed.emit("paused" if self.paused else self.phase)

    def stop(self):
        if not self.active:
            return 0
        elapsed = max(0, self.total - self.remaining)
        self._timer.stop()
        self._paused = False
        self.phase = "idle"
        self.remaining = 0
        self.phase_changed.emit(self.phase)
        return elapsed

    def _on_tick(self):
        now = time.monotonic()
        delta = max(1, int(now - self._last_tick))
        self._last_tick = now
        self.remaining = max(0, self.remaining - delta)
        self.tick.emit(self.remaining, self.phase)
        if self.remaining > 0:
            return
        if self.phase == "focus":
            elapsed = self.total
            self.completed.emit("focus", elapsed)
            if self.break_seconds > 0:
                self.phase = "break"
                self.remaining = self.break_seconds
                self.total = self.break_seconds
                self.phase_changed.emit(self.phase)
                self.tick.emit(self.remaining, self.phase)
            else:
                if self.repeat_enabled:
                    self.remaining = self.focus_seconds
                    self.total = self.focus_seconds
                    self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._last_tick = time.monotonic()
                    self.phase_changed.emit(self.phase)
                    self.tick.emit(self.remaining, self.phase)
                    self._timer.start()
                else:
                    self._timer.stop()
                    self.phase = "idle"
                    self.phase_changed.emit(self.phase)
        else:
            self._timer.stop()
            self.completed.emit("break", 0)
            if self.repeat_enabled:
                self.phase = "focus"
                self.remaining = self.focus_seconds
                self.total = self.focus_seconds
                self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
                self._last_tick = time.monotonic()
                self.phase_changed.emit(self.phase)
                self.tick.emit(self.remaining, self.phase)
                self._timer.start()
            else:
                self.phase = "idle"
                self.phase_changed.emit(self.phase)
