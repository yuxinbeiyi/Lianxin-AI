"""Lianxin bridge runtime log.

Tees the api_server console output to logs/debug.log, mirroring the legacy
main.py ``_TeeWriter`` behaviour so the bat launcher can show the same kind of
transparent backend activity (startup flow, memory heartbeat, music watcher,
MCP, watchdog, API requests).

On install, the previous session's debug.log is rotated to
``debug_<timestamp>.log`` (newest 10 kept) so every launch starts a clean log.

Usage::

    from utils import bridge_log
    bridge_log.install("debug.log")
    bridge_log.log("启动", "hello")
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_START_TIME = time.time()

_LOG_PATH: Path | None = None
_REAL_STDOUT = None
_REAL_STDERR = None
_LOG_STREAM = None
_WRITE_LOCK = threading.Lock()


class _TeeWriter:
    """Write to the real console stream and the debug log file at the same time."""

    def __init__(self, log_fp, real):
        self._log = log_fp
        self._real = real

    def write(self, data: str) -> int:
        # Locked: api_server is multi-threaded (one thread per HTTP request),
        # so a bare print() would interleave its content/newline writes.
        with _WRITE_LOCK:
            if self._real is not None:
                try:
                    self._real.write(data)
                    self._real.flush()
                except Exception:
                    pass
            if self._log is not None:
                try:
                    self._log.write(data)
                    self._log.flush()
                except Exception:
                    pass
        return len(data)

    def flush(self) -> None:
        with _WRITE_LOCK:
            if self._real is not None:
                try:
                    self._real.flush()
                except Exception:
                    pass
            if self._log is not None:
                try:
                    self._log.flush()
                except Exception:
                    pass

    def isatty(self) -> bool:
        return False


def rotate_log(target: Path, keep: int = 10) -> None:
    """Move the previous session's log aside and prune old backups.

    Reused by other log sinks (image-flow diagnostics, legacy UI subprocess
    logs) so every sink has the same bounded growth behaviour as debug.log.
    """
    if not target.exists():
        return
    try:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = target.with_name(f"{target.stem}_{stamp}{target.suffix}")
        target.replace(backup)
    except Exception:
        pass
    try:
        backups = sorted(
            target.parent.glob(f"{target.stem}_*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[keep:]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception:
        pass


# 兼容旧调用名
_rotate_log = rotate_log


def install(log_filename: str = "debug.log") -> Path:
    """Rotate the previous log, then tee stdout/stderr to console + logs/<file>."""
    global _LOG_PATH, _REAL_STDOUT, _REAL_STDERR, _LOG_STREAM
    log_dir = _PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / log_filename
    _rotate_log(target)
    _LOG_PATH = target
    _REAL_STDOUT = sys.stdout
    _REAL_STDERR = sys.stderr
    log_fp = None
    try:
        log_fp = open(target, "w", encoding="utf-8", buffering=1)
    except Exception:
        log_fp = None
    if log_fp is not None:
        _LOG_STREAM = log_fp
        sys.stdout = _TeeWriter(log_fp, _REAL_STDOUT)
        sys.stderr = _TeeWriter(log_fp, _REAL_STDERR)
    return _LOG_PATH


def log_path() -> Path:
    return _LOG_PATH or (_PROJECT_ROOT / "logs" / "debug.log")


def log(tag: str, message: str, *, console: bool = True) -> None:
    """Emit one line, optionally keeping verbose diagnostics out of the console."""
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] [{tag}] {message}\n"
    with _WRITE_LOCK:
        if console and _REAL_STDOUT is not None:
            try:
                _REAL_STDOUT.write(line)
                _REAL_STDOUT.flush()
            except Exception:
                pass
        if _LOG_STREAM is not None:
            try:
                _LOG_STREAM.write(line)
                _LOG_STREAM.flush()
            except Exception:
                pass
        elif console:
            try:
                sys.stdout.write(line)
            except Exception:
                pass


def uptime_seconds() -> float:
    return time.time() - _START_TIME
