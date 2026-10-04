"""Adapter contract for the legacy pygame/local music player.

The adapter deliberately knows nothing about Qt. MainWindow can register a
small callback object here while the service and tools use one stable shape.
"""

from __future__ import annotations

from typing import Any, Callable


class LocalMusicBackend:
    def __init__(
        self,
        state_provider: Callable[[], dict],
        control_handler: Callable[[str, dict], dict],
    ) -> None:
        self._state_provider = state_provider
        self._control_handler = control_handler

    def state(self) -> dict:
        return dict(self._state_provider() or {})

    def control(self, action: str, payload: dict | None = None) -> dict:
        return dict(self._control_handler(action, payload or {}) or {})


class CallbackMusicBackend:
    """Convenience backend for callers that already expose callbacks."""

    def __init__(self, callbacks: Any) -> None:
        self._callbacks = callbacks

    def state(self) -> dict:
        return dict(self._callbacks.state() or {})

    def control(self, action: str, payload: dict | None = None) -> dict:
        return dict(self._callbacks.control(action, payload or {}) or {})
