"""Transient execution notices, separate from conversation and memory."""

import threading
import time
from collections import deque


class ExecutionStatusBus:
    def __init__(self, capacity=128):
        self.enabled = False
        self._events = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._latest = 0

    def publish(self, message, *, code, source="chat", session_id=None,
                request_id="", state="failed", level="warning"):
        with self._lock:
            self._latest = max(self._latest + 1, int(time.time() * 1000))
            event = {
                "id": self._latest, "timestamp": int(time.time() * 1000),
                "message": str(message), "code": str(code), "source": source,
                "sessionId": session_id, "requestId": str(request_id),
                "state": state, "level": level,
            }
            self._events.append(event)
            return dict(event)

    def events(self, after=0, session_id=None):
        with self._lock:
            items = [dict(event) for event in self._events
                     if event["id"] > int(after)
                     and (session_id is None or event["sessionId"] in (None, session_id))]
            return {"items": items, "latest": self._latest}


_BUS = ExecutionStatusBus()


def get_execution_status_bus():
    return _BUS
