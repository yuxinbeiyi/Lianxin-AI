# utils/net_ease_cleanup.py
"""网易云后台播放器（mpv）清理与探测工具。

莲心的网易云播放链路由 neteasecli 以 detached 方式启动 mpv，mpv 不会随莲心
退出而自动结束。这里提供按特征精确清理 mpv 的函数，供 closeEvent / aboutToQuit
/ atexit 多处兜底调用；另外提供"莲心自己的 mpv 是否在运行"的探测与残留状态
文件清理，供音乐状态可信化（幽灵播放判定）与启动基线使用。

进程枚举默认走 psutil（纯 Python、毫秒级、不会创建控制台窗口）；只有 psutil
不可用时才回退到 PowerShell，且所有子进程都带 CREATE_NO_WINDOW + SW_HIDE。
历史问题：这里曾用裸 subprocess 每秒拉起一次 powershell 做探测，在图形界面
（无控制台父进程）下每次都会闪出一个黑窗口，导致桌面反复闪现 powershell 窗口。
"""

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - 缺依赖时回退 PowerShell
    psutil = None

# 通过可执行路径（netease-music-mcp-main）或命令行特征（neteasecli-mpv 的 IPC
# 管道）精确匹配莲心专用的 mpv，避免误杀用户自己打开的其他 mpv 播放器。
_MPV_EXE_MARKER = "netease-music-mcp-main"
_MPV_CMDLINE_MARKER = "neteasecli-mpv"
_MPV_PROCESS_NAMES = frozenset({"mpv.exe", "mpv.com", "mpv"})
_MPV_FILTER = (
    "($_.ExecutablePath -like '*netease-music-mcp-main*') "
    "-or ($_.CommandLine -like '*neteasecli-mpv*')"
)

# 探测结果缓存：进程枚举本身很快，缓存只是避免同一轮里被多个调用点重复枚举。
_PROBE_CACHE_SECONDS = 5.0
_probe_lock = threading.Lock()
_probe_cache = {"at": 0.0, "running": None, "ok": False}


def _no_window_kwargs() -> dict:
    """让子进程在无控制台父进程（pythonw / GUI）下也不弹出窗口。"""
    if os.name != "nt":
        return {}
    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    return kwargs


def _powershell_argv(script: str) -> list:
    return [
        "powershell", "-NoProfile", "-NonInteractive",
        "-WindowStyle", "Hidden", "-Command", script,
    ]


def _run_powershell(script: str, timeout: float = 10.0) -> str:
    completed = subprocess.run(
        _powershell_argv(script),
        timeout=timeout, capture_output=True, **_no_window_kwargs(),
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


def _is_lianxin_mpv(name: str, exe: str, cmdline: str) -> bool:
    """只认莲心自己的 mpv（marker 命中才杀），避免误伤用户自开的 mpv。"""
    if (name or "").lower() not in _MPV_PROCESS_NAMES:
        return False
    if _MPV_EXE_MARKER in (exe or "").lower():
        return True
    return _MPV_CMDLINE_MARKER in (cmdline or "").lower()


def _probe_mpv_pids_psutil() -> Optional[list]:
    """用 psutil 找出莲心专用 mpv 的 PID；psutil 不可用时返回 None。"""
    if psutil is None:
        return None
    pids = []
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            name = proc.info.get("name") or ""
            if name.lower() not in _MPV_PROCESS_NAMES:
                continue
            try:
                exe = proc.exe() or ""
            except Exception:
                exe = ""
            cmdline = ""
            if _MPV_EXE_MARKER not in exe.lower():
                try:
                    cmdline = " ".join(proc.cmdline() or [])
                except Exception:
                    cmdline = ""
            if _is_lianxin_mpv(name, exe, cmdline):
                pids.append(int(proc.info.get("pid") or 0))
    except Exception:
        return None
    return pids


def _probe_mpv_pids_powershell() -> Optional[list]:
    """psutil 不可用时的兜底探测（绝不弹窗）。"""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='mpv.exe' or Name='mpv.com'\" | "
        "Where-Object { " + _MPV_FILTER + " } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        completed = subprocess.run(
            _powershell_argv(ps),
            timeout=8, capture_output=True, **_no_window_kwargs(),
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


def _probe_mpv_pids() -> Optional[list]:
    """返回莲心专用 mpv 的 PID 列表；探测本身失败时返回 None（未知）。"""
    pids = _probe_mpv_pids_psutil()
    if pids is not None:
        return pids
    return _probe_mpv_pids_powershell()


def _kill_pids_psutil(pids) -> int:
    """用 psutil 终止给定 PID，返回成功处理的数量。"""
    if psutil is None:
        return 0
    killed = 0
    for pid in pids:
        try:
            psutil.Process(int(pid)).kill()
            killed += 1
        except Exception:
            pass
    return killed


def stop_netease_mpv():
    """停止莲心专用的网易云后台 mpv 播放器并清理残留状态文件。"""
    pids = _probe_mpv_pids()
    if pids and psutil is not None:
        _kill_pids_psutil(pids)
    elif pids is None or psutil is None:
        # 探测或终止能力不可用：回退到 PowerShell 一次性清理（不弹窗）。
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


def netease_mpv_pids() -> list:
    """兼容入口：返回 PID 列表（探测不可用时为空列表）。"""
    return list(_probe_mpv_pids() or [])


def netease_mpv_state(*, max_age: float = _PROBE_CACHE_SECONDS) -> Optional[bool]:
    """莲心自己的 mpv 是否在运行（带缓存，max_age 控制结果最多用多久）。

    True=在运行，False=确认没有，None=探测不了（进程枚举失败）。
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
