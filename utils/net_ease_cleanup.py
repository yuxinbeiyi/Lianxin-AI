# utils/net_ease_cleanup.py
"""网易云后台播放器（mpv）清理工具。

莲心的网易云播放链路由 neteasecli 以 detached 方式启动 mpv，mpv 不会随莲心
退出而自动结束。这里提供按特征精确清理 mpv 的函数，供 closeEvent / aboutToQuit
/ atexit 多处兜底调用。
"""

import subprocess
from pathlib import Path


def stop_netease_mpv():
    """停止莲心专用的网易云后台 mpv 播放器并清理残留状态文件。

    通过可执行路径（netease-music-mcp-main）或命令行特征（neteasecli-mpv
    的 IPC 管道）精确匹配，避免误杀用户自己打开的其他 mpv 播放器。
    """
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='mpv.exe' or Name='mpv.com'\" | "
            "Where-Object { ($_.ExecutablePath -like '*netease-music-mcp-main*') "
            "-or ($_.CommandLine -like '*neteasecli-mpv*') } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=10, capture_output=True,
        )
    except Exception as exc:
        print(f"[退出] 停止网易云 mpv 失败: {exc}", flush=True)
    # 清理 MusicWatcher 轮询的残留状态文件，避免重启后误判仍在播放
    try:
        state_file = (
            Path(__file__).resolve().parent.parent
            / "参考项目" / "netease-music-mcp-main" / ".listening-state.json"
        )
        if state_file.exists():
            state_file.unlink()
    except Exception as exc:
        print(f"[退出] 清理网易云状态文件失败: {exc}", flush=True)