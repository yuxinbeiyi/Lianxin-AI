"""Memory monitoring and crash leave-behind helpers.

莲心可能因原生级内存耗尽/原生 DLL 崩溃而「静默闪退」：此时 Python 层的
excepthook 与 faulthandler 都来不及写日志（debug.log/crash.log/fault.log
全部没有 traceback）。本模块提供：

- 后台监控线程：周期采样进程 RSS 与系统可用内存，超过阈值时打 WARN；
- 危急（内存即将耗尽）时用 faulthandler 把全线程堆栈转储到 fault.log，
  为下次闪退留下可定位的线索；
- log_memory_snapshot()：供关键操作（写日记、torch 初始化等）前后采样，
  便于确认是否在某个操作附近发生 OOM。

仅依赖 psutil；若未安装则回退到 Windows ctypes 读取系统内存，并跳过 RSS。
"""

from __future__ import annotations

import os
import sys
import time
import threading
import logging
from typing import Dict, Optional

try:
    import psutil
    _HAS_PSUTIL = True
except Exception:  # pragma: no cover - psutil 缺失时回退
    _HAS_PSUTIL = False

_logger = logging.getLogger("MemoryGuard")

# 阈值（MB）
WARN_RSS_MB = 4096           # 进程 RSS 超过此值 → WARN
CRITICAL_RSS_MB = 6144       # 进程 RSS 超过此值 → 危急，转储全线程堆栈
WARN_AVAILABLE_MB = 1024     # 系统可用内存低于此值 → WARN
CRITICAL_AVAILABLE_MB = 512  # 系统可用内存低于此值 → 危急，转储全线程堆栈

_HEARTBEAT_INTERVAL = 60.0   # 常规心跳日志间隔（秒）
_SAMPLE_INTERVAL = 10.0      # 采样间隔（秒）
_STALL_DUMP_INTERVAL = 30.0  # 危急转储去重间隔（秒）

_start_lock = threading.Lock()
_monitor_started = False
_last_critical_dump = 0.0


def _fault_log_path() -> str:
    """fault.log 绝对路径（与 main.py 保持一致）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "logs", "fault.log")


def _memory_status_ctypes() -> Optional[Dict[str, float]]:
    """Windows ctypes 回退：仅能取到系统可用内存，RSS 为 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        if not ok:
            return None
        total = float(stat.ullTotalPhys) / (1024 * 1024)
        available = float(stat.ullAvailPhys) / (1024 * 1024)
        percent = 100.0 * (total - available) / total if total > 0 else 0.0
        return {"rss_mb": None, "available_mb": available, "percent": percent}
    except Exception:
        return None


def get_memory_status() -> Optional[Dict[str, float]]:
    """返回 {rss_mb, available_mb, percent}；失败返回 None。"""
    if _HAS_PSUTIL:
        try:
            vm = psutil.virtual_memory()
            proc = psutil.Process()
            rss = proc.memory_info().rss / (1024 * 1024)
            available = vm.available / (1024 * 1024)
            return {
                "rss_mb": rss,
                "available_mb": available,
                "percent": vm.percent,
            }
        except Exception:
            pass
    if sys.platform == "win32":
        return _memory_status_ctypes()
    return None


def format_status(status: Optional[Dict[str, float]]) -> str:
    """把内存状态格式化为一行可读文本。"""
    if not status:
        return "内存状态未知"
    rss = status.get("rss_mb")
    rss_text = f"{rss:.0f}MB" if rss is not None else "n/a"
    return (
        f"RSS={rss_text} 可用={status.get('available_mb', 0):.0f}MB "
        f"占用={status.get('percent', 0):.0f}%"
    )


def dump_all_threads(reason: str) -> None:
    """危急时把全部线程的 Python 堆栈追加写入 fault.log（崩溃留痕）。"""
    global _last_critical_dump
    now = time.monotonic()
    if now - _last_critical_dump < _STALL_DUMP_INTERVAL:
        return
    _last_critical_dump = now
    try:
        import faulthandler
        path = _fault_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"[MemoryGuard] {reason} 时间={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            faulthandler.dump_traceback(file=f, all_threads=True)
        print(f"[内存] 危急：{reason}，已转储全线程堆栈到 {path}", flush=True)
    except Exception as exc:  # pragma: no cover - 转储失败不应影响主流程
        print(f"[内存] 堆栈转储失败: {exc}", flush=True)


def log_memory_snapshot(tag: str = "") -> Optional[Dict[str, float]]:
    """打印一次内存快照到 debug.log；返回状态 dict。"""
    status = get_memory_status()
    suffix = f" [{tag}]" if tag else ""
    print(f"[内存]{suffix} {format_status(status)}", flush=True)
    return status


def _monitor_loop() -> None:
    """后台监控线程主体：心跳 + 阈值告警 + 危急转储。"""
    global _monitor_started
    last_heartbeat = 0.0
    while True:
        try:
            status = get_memory_status()
            if status:
                now = time.monotonic()
                # 常规心跳：周期性确认进程还活着、内存走势正常
                if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
                    last_heartbeat = now
                    print(f"[内存] 心跳 {format_status(status)}", flush=True)

                rss = status.get("rss_mb")
                available = status.get("available_mb", 0.0)
                critical = (
                    (rss is not None and rss > CRITICAL_RSS_MB)
                    or available < CRITICAL_AVAILABLE_MB
                )
                if critical:
                    dump_all_threads(f"内存危急 {format_status(status)}")
                elif (
                    (rss is not None and rss > WARN_RSS_MB)
                    or available < WARN_AVAILABLE_MB
                ):
                    print(f"[内存] WARN {format_status(status)}", flush=True)
        except Exception:
            pass  # 监控线程永不因自身异常退出
        time.sleep(_SAMPLE_INTERVAL)


def start_memory_monitor() -> bool:
    """启动后台内存监控（幂等）。返回是否本次启动。"""
    global _monitor_started
    with _start_lock:
        if _monitor_started:
            return False
        _monitor_started = True
    thread = threading.Thread(
        target=_monitor_loop, name="memory-guard", daemon=True
    )
    thread.start()
    print("[内存] 后台内存监控已启动", flush=True)
    return True