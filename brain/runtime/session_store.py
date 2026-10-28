"""执行引擎持久线程的元数据（阶段 3b）。

codex 的 thread 现在用 ``ephemeral=false`` 落盘；这里保存的是莲心侧的映射：
``threadId -> 会话 / 目标 / 状态``。重启时靠它补记「非预期中断」，
用户点「接着做」时再拿 threadId 去 ``thread/resume``。

刻意不建数据库表：元数据只是执行引擎的附属状态，和聊天记录的生命周期不同。
"""

from __future__ import annotations

import json
import os
import time
import threading
from pathlib import Path
from typing import Optional

_RUNTIME_DIR = Path(__file__).resolve().parents[2] / "data" / "codex_runtime"
_SESSION_DIR = _RUNTIME_DIR / "sessions"
_META_LOCK = threading.RLock()


def _session_dir() -> Path:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


def _meta_path(thread_id: str) -> Optional[Path]:
    tid = str(thread_id or "").strip()
    if not tid:
        return None
    # threadId 是 UUID；仍然拒绝路径分隔符，避免任何拼接越界。
    if any(token in tid for token in ("/", "\\", "..", ":", "\x00")):
        return None
    return _session_dir() / ("%s.meta.json" % tid)


def write_meta(thread_id: str, data: dict) -> bool:
    with _META_LOCK:
        return _write_meta(thread_id, data)


def _write_meta(thread_id: str, data: dict) -> bool:
    """原子写入一条元数据；thread_id 为空或非法时返回 False。"""
    path = _meta_path(thread_id)
    if path is None:
        return False
    payload = dict(data or {})
    now = time.time()
    payload["threadId"] = str(thread_id).strip()
    payload.setdefault("createdAt", now)
    payload["updatedAt"] = now
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(tmp), str(path))
        return True
    except Exception:
        try:
            if tmp.exists():
                os.remove(str(tmp))
        except Exception:
            pass
        return False


def read_meta(thread_id: str) -> dict:
    path = _meta_path(thread_id)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def update_meta(thread_id: str, patch: dict, *, expected_task_id: Optional[str] = None) -> bool:
    with _META_LOCK:
        current = read_meta(thread_id)
        if not current:
            return False
        if expected_task_id is not None and current.get("taskId") != expected_task_id:
            return False
        current.update(dict(patch or {}))
        return write_meta(thread_id, current)


def iter_metas() -> list:
    """按最近活动时间倒序返回全部元数据。"""
    items = []
    for path in _session_dir().glob("*.meta.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if not data.get("threadId"):
            data["threadId"] = path.name[: -len(".meta.json")]
        items.append(data)
    items.sort(key=_stamp, reverse=True)
    return items


def _stamp(meta: dict) -> float:
    try:
        return float(meta.get("updatedAt") or meta.get("startedAt") or 0)
    except (TypeError, ValueError):
        return 0.0


def running_metas() -> list:
    """上次进程退出时仍处于 running 的线程。"""
    return [meta for meta in iter_metas() if str(meta.get("status") or "") == "running"]


def latest_resumable() -> dict:
    """最近一个可以「接着做」的任务（被中断/被停止且还没重跑）。"""
    for meta in iter_metas():
        if str(meta.get("status") or "") in ("interrupted", "stopped"):
            return dict(meta)
    return {}


def prune(max_age_days: float = 7.0, keep: int = 200) -> list:
    """删除「超过保留条数」或「超过保留天数」的终态记录，返回被删的 threadId。"""
    keep = max(1, int(keep or 1))
    cutoff = time.time() - max(0.0, float(max_age_days or 0)) * 86400
    removed = []
    for index, meta in enumerate(iter_metas()):
        if str(meta.get("status") or "") == "running":
            continue
        if index < keep and _stamp(meta) >= cutoff:
            continue
        path = _meta_path(str(meta.get("threadId") or ""))
        if path is None or not path.is_file():
            continue
        try:
            os.remove(str(path))
        except OSError:
            continue
        removed.append(str(meta.get("threadId") or ""))
    return removed
