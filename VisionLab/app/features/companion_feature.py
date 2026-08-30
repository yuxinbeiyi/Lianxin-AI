"""Presence state machine built on top of face detection results."""

import time


class CompanionFeature:
    ABSENCE_SECONDS = 30.0
    PRESENCE_CONFIRM_SECONDS = 1.0
    STRANGER_ALERT_SECONDS = 15.0
    LONG_WORK_SECONDS = 60 * 60

    def __init__(self):
        self.state = "NO_PERSON"
        self._present_since = None
        self._absent_since = None
        self._work_started = None
        self._stranger_since = None
        self._long_work_sent = False
        # 失陪时长跟踪：离开期间持续累计，USER_RETURN 时读取
        self._pending_absence = 0.0
        self.last_absence_seconds = 0.0

    def reset(self):
        self.state = "NO_PERSON"
        self._present_since = None
        self._absent_since = None
        self._work_started = None
        self._stranger_since = None
        self._long_work_sent = False
        self._pending_absence = 0.0
        self.last_absence_seconds = 0.0

    def update(self, face_count, identity="UNKNOWN", now=None):
        if now is None:
            now = time.monotonic()
        events = []
        if identity == "STRANGER" and face_count > 0:
            if self._stranger_since is None:
                self._stranger_since = now
            if now - self._stranger_since >= self.STRANGER_ALERT_SECONDS:
                events.append("STRANGER_PERSISTING")
                self._stranger_since = now
        else:
            self._stranger_since = None

        if identity == "USER" and face_count > 0:
            self._absent_since = None
            if self._present_since is None:
                self._present_since = now
            present_for = now - self._present_since
            if self.state == "NO_PERSON" and present_for >= self.PRESENCE_CONFIRM_SECONDS:
                self.state = "PERSON_PRESENT"
                self.last_absence_seconds = 0.0
                events.append("USER_ENTER")
            elif self.state == "AWAY" and present_for >= self.PRESENCE_CONFIRM_SECONDS:
                self.state = "PERSON_PRESENT"
                self.last_absence_seconds = self._pending_absence
                events.append("USER_RETURN")
        else:
            self._present_since = None
            if self._absent_since is None:
                self._absent_since = now
            self._pending_absence = max(0.0, now - self._absent_since)
            if self.state == "PERSON_PRESENT" and now - self._absent_since >= self.ABSENCE_SECONDS:
                self.state = "AWAY"
                events.append("USER_LEAVE")
        duration = 0.0
        if self._work_started is not None and self.state == "PERSON_PRESENT":
            duration = max(0.0, now - self._work_started)
            if duration >= self.LONG_WORK_SECONDS and not self._long_work_sent:
                events.append("LONG_WORK")
                self._long_work_sent = True
        if "USER_ENTER" in events or "USER_RETURN" in events:
            self._work_started = now
        if "USER_LEAVE" in events:
            duration = max(0.0, now - (self._work_started or now))
            self._work_started = None
            self._long_work_sent = False
        return self.state, events, duration

    def current_duration(self, now=None):
        """返回当前本人观测会话时长，不改变状态。"""
        if now is None:
            now = time.monotonic()
        if self._work_started is None or self.state != "PERSON_PRESENT":
            return 0.0
        return max(0.0, now - self._work_started)

    def flush_session(self, now=None):
        """结束当前观测会话并返回尚未落库的时长。"""
        duration = self.current_duration(now)
        self.reset()
        return duration
