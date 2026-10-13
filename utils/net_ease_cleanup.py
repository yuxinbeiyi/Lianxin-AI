# utils/net_ease_cleanup.py
"""网易云后台播放器（mpv）清理与探测工具。

莲心的网易云播放链路由 neteasecli 以 detached 方式启动 mpv，mpv 不会随莲心
退出而自动结束。这里提供按特征精确清理 mpv 的函数，供 closeEvent / aboutToQuit
/ atexit 多处兜底调用；另外提供"莲心自己的 mpv 是否在运行"的探测与残留状态
文件清理，供音乐状态可信化（幽灵播放判定）与启动基线使用。
"""

import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

# 通过可执行路径（netease-music-mcp-main）或命令行特征（neteasecli-mpv 的 IPC
# 管道）精确匹配莲心专用的 mpv，避免误杀用户自己打开的其他 mpv 播放器。
_MPV_FILTER = (
    "($_.ExecutablePath -like '*netease-music-mcp-main*') "
    "-or ($_.CommandLine -like '*neteasecli-mpv*')"
)

_PROBE_CACHE_SECONDS = 2.5
_probe_lock = threading.Lock()
_probe_cache = {"at": 0.0, "running": None, "ok": False}


def _run_powershell(script: str, timeout: float = 10.0) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout, capture_output=True,
    )
    return (completed.stdout or b"").decode("utf-8", "replace")


def clear_netease_state_files() -> list:
    """删除残留的 .listening-state.json，避免重启后被误判为"仍在播放"。"""
    removed = []
    try:
        from utils.paths import get_netease_state_file_candidates
        candidates = get_netease_state_file_candidates()
    except Exception:
        candidates = [
            Path(__file__).resolve().parent.parent
            / "参考项目" / "netease-music-mcp-main" / ".listening-state.json"
        ]
    for path in candidates:
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except Exception as exc:
            print(f"[退出] 清理网易云状态文件失败: {exc}", flush=True)
    return removed


def stop_netease_mpv():
    """停止莲心专用的网易云后台 mpv 播放器并清理残留状态文件。"""
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='mpv.exe' or Name='mpv.com'\" | "
            "Where-Object { " + _MPV_FILTER + " } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        _run_powershell(ps, timeout=10)
    except Exception as exc:
        print(f"[退出] 停止网易云 mpv 失败: {exc}", flush=True)
    _invalidate_probe_cache()
    clear_netease_state_files()


def _probe_mpv_pids() -> Optional[list]:
    """返回莲心专用 mpv 的 PID 列表；探测本身失败时返回 None（未知）。"""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='mpv.exe' or Name='mpv.com'\" | "
        "Where-Object { " + _MPV_FILTER + " } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=8, capture_output=True,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    output = (completed.stdout or b"").decode("utf-8", "replace")
    pids = []
    for line in output.splitlines():
        value = line.strip()
        if value.isdigit():
            pids.append(int(value))
    return pids


def netease_mpv_pids() -> list:
    """兼容入口：返回 PID 列表（探测不可用时为空列表）。"""
    return list(_probe_mpv_pids() or [])


def netease_mpv_state(*, max_age: float = _PROBE_CACHE_SECONDS) -> Optional[bool]:
    """莲心自己的 mpv 是否在运行（带缓存，max_age 控制结果最多用多久）。

    True=在运行，False=确认没有，None=探测不了（PowerShell 异常或超时）。
    三态很重要：探测不了时不能当成"没有在播"，否则会误判成幽灵播放。
    判"不在运行"时建议传较小的 max_age：刚开播时 mpv 可能才拉起来，
    用太久以前的 False 会冤枉真实的播放。
    """
    now = time.monotonic()
    if max_age > 0:
        with _probe_lock:
            if _probe_cache["ok"] and now - _probe_cache["at"] < max_age:
                return _probe_cache["running"]
    pids = _probe_mpv_pids()
    running = None if pids is None else bool(pids)
    with _probe_lock:
        _probe_cache.update({"at": now, "running": running, "ok": True})
    return running


def netease_mpv_running(*, max_age: float = _PROBE_CACHE_SECONDS) -> bool:
    """布尔化版本：探测不可用时按"未运行"处理（启动基线据此保守处理）。"""
    return bool(netease_mpv_state(max_age=max_age))


def _invalidate_probe_cache() -> None:
    with _probe_lock:
        _probe_cache.update({"at": 0.0, "running": None, "ok": False})
