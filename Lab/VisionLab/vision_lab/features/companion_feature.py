"""Presence state machine built on top of face detection results."""

import time


class CompanionFeature:
    ABSENCE_SECONDS = 30.0
    PRESENCE_CONFIRM_SECONDS = 1.0

    def __init__(self):
        self.state = "NO_PERSON"
        self._present_since = None
        self._absent_since = None
        self._work_started = None

    def reset(self):
        self.state = "NO_PERSON"
        self._present_since = None
        self._absent_since = None
        self._work_started = None

    def update(self, face_count, now=None):
        if now is None:
            now = time.monotonic()
        events = []
        if face_count > 0:
            self._absent_since = None
            if self._present_since is None:
                self._present_since = now
            present_for = now - self._present_since
            if self.state == "NO_PERSON" and present_for >= self.PRESENCE_CONFIRM_SECONDS:
                self.state = "PERSON_PRESENT"
                events.append("USER_ENTER")
            elif self.state == "AWAY" and present_for >= self.PRESENCE_CONFIRM_SECONDS:
                self.state = "PERSON_PRESENT"
                events.append("USER_RETURN")
        else:
            self._present_since = None
            if self._absent_since is None:
                self._absent_since = now
            if self.state == "PERSON_PRESENT" and now - self._absent_since >= self.ABSENCE_SECONDS:
                self.state = "AWAY"
                events.append("USER_LEAVE")
        duration = 0.0
        if self._work_started is not None and self.state == "PERSON_PRESENT":
            duration = max(0.0, now - self._work_started)
        if "USER_ENTER" in events or "USER_RETURN" in events:
            self._work_started = now
        if "USER_LEAVE" in events:
            duration = max(0.0, now - (self._work_started or now))
            self._work_started = None
        return self.state, events, duration
