"""Small process-local registry for observable subsystem runtime state.

This module deliberately has no GUI, database, network, or worker imports.
Owners of a subsystem publish snapshots at lifecycle boundaries; diagnostic
readers can then inspect them without starting that subsystem as a side effect.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import threading


_lock = threading.RLock()
_states: dict[str, dict] = {}


def set_status(component: str, **fields) -> None:
    """Replace a component snapshot with a timestamped shallow payload."""
    name = str(component or "").strip()
    if not name:
        return
    payload = dict(fields)
    payload.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
    with _lock:
        _states[name] = payload


def update_status(component: str, **fields) -> None:
    """Merge fields into an existing snapshot, refreshing its timestamp."""
    name = str(component or "").strip()
    if not name:
        return
    with _lock:
        payload = dict(_states.get(name, {}))
        payload.update(fields)
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _states[name] = payload


def clear_status(component: str) -> None:
    with _lock:
        _states.pop(str(component or "").strip(), None)


def get_status(component: str) -> dict | None:
    with _lock:
        payload = _states.get(str(component or "").strip())
        return deepcopy(payload) if payload is not None else None


def snapshot() -> dict[str, dict]:
    with _lock:
        return deepcopy(_states)
