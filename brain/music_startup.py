# brain/music_startup.py
"""音乐启动基线：让莲心每次重启时都安静、自然地开始。

历史问题：莲心被强杀/崩溃时，neteasecli 写下的 .listening-state.json 会残留
active=true；同时后台 mpv 可能仍在播放。于是重启后音乐空间显示"正在播放"，
MusicWatcher 也会对这首"幽灵歌"发表评论。

这里做三件事（只在启动时做一次）：
1) 删除残留的状态文件，让"重启后从没有在播开始"；
2) 重置幽灵播放判定（brain.music_state）；
3) 只有在"莲心自己的 mpv 确实在运行、且确实在播一首可信的歌"时，才发一次暂停，
   把播放器置于暂停基线。探测不到就完全不动它——绝不在启动时反而把歌播起来。
"""

import json
import logging
import urllib.request

logger = logging.getLogger("MusicStartup")

NETEASE_BASE_URL = "http://127.0.0.1:8765"


def _fetch_status(timeout: float = 0.8):
    try:
        with urllib.request.urlopen(NETEASE_BASE_URL + "/api/status", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _credible_playing(status) -> bool:
    """是否"确实在播一首可信的歌"（用于决定值不值得发暂停）。

    8765 在 mpv 未运行时仍会谎报 playing=true，只有曲长 + 播放位置都可信时才
    认为它在真的播放；否则一律不动（避免 /api/pause 的 toggle 语义反而开播）。
    """
    if not isinstance(status, dict):
        return False
    st = status.get("status") if isinstance(status.get("status"), dict) else {}
    playback = status.get("playback") if isinstance(status.get("playback"), dict) else {}
    if not st.get("playing") or st.get("paused"):
        return False
    try:
        duration = float(st.get("duration") or 0.0) or float(playback.get("durationMs") or 0.0) / 1000.0
        position = float(st.get("position") or 0.0)
    except (TypeError, ValueError):
        return False
    if duration <= 0.0:
        return False
    return position < max(0.0, duration - 0.5)


def _post_pause(timeout: float = 3.0) -> bool:
    try:
        request = urllib.request.Request(
            NETEASE_BASE_URL + "/api/pause",
            data=b"{}",
            headers={"Content-Type": "application/json", "X-Lianxin-Bridge": "1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            resp.read()
        return True
    except Exception as exc:
        logger.info("[MusicStartup] 启动暂停请求失败: %s", exc)
        return False


def establish_music_baseline() -> dict:
    """建立"每次重启都从暂停/停止开始"的启动基线。"""
    result = {"cleared": [], "paused": False, "reason": ""}
    try:
        from brain.music_state import reset_ghost_guard
        reset_ghost_guard()
    except Exception as exc:
        logger.info("[MusicStartup] 重置幽灵播放判定失败: %s", exc)
    try:
        from utils.net_ease_cleanup import clear_netease_state_files
        result["cleared"] = clear_netease_state_files()
    except Exception as exc:
        logger.info("[MusicStartup] 清理残留状态文件失败: %s", exc)
    try:
        from utils.net_ease_cleanup import netease_mpv_running
        mpv_running = bool(netease_mpv_running())
    except Exception:
        mpv_running = False
    if not mpv_running:
        result["reason"] = "后台 mpv 未运行，保持静止基线"
        logger.info("[MusicStartup] %s", result["reason"])
        return result
    if not _credible_playing(_fetch_status()):
        result["reason"] = "播放状态不可信，不发送暂停（避免误触发开播）"
        logger.info("[MusicStartup] %s", result["reason"])
        return result
    result["paused"] = _post_pause()
    result["reason"] = "已把播放器置于暂停基线" if result["paused"] else "暂停请求失败"
    logger.info("[MusicStartup] %s", result["reason"])
    return result
