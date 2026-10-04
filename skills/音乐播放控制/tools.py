"""
音乐播放控制技能 — 自定义工具
莲心音乐盒的播放控制与信息查询
"""

import brain.tools as _brain_tools

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "control_music",
            "description": "控制莲心音乐盒的播放状态、切换歌曲、循环模式、音量等。当用户要求播放/暂停音乐、下一首/上一首、切换循环模式、增大/减小音量时，调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play", "pause", "next", "prev", "loop", "volume_up", "volume_down"],
                        "description": "要执行的操作：play（播放音乐），pause（暂停），next（下一首），prev（上一首），loop（切换循环模式），volume_up（增加音量），volume_down（减小音量）"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_music_playlist",
            "description": "获取当前音乐播放列表中的所有歌曲名称（按顺序）。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_music_status",
            "description": "获取当前音乐播放状态：是否在播放，当前歌曲名，当前播放进度（秒）和总时长。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_music_stats",
            "description": "获取音乐陪伴统计：累计听歌总时长（小时），以及播放次数最多的歌曲名和累计秒数。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


def _control_music(action: str) -> str:
    service = _get_service()
    if service is not None:
        try:
            mapped = {"prev": "previous", "loop": "mode"}.get(action, action)
            payload = {"mode": "shuffle"} if action == "loop" else {}
            result = service.control(mapped, payload)
            return f"音乐操作已完成：{action}。当前状态：{result.get('name') or result.get('title') or '未知歌曲'}"
        except Exception as exc:
            return f"音乐操作失败：{exc}"
    cb = _brain_tools._music_control_callback
    if cb:
        return cb(action)
    return "未连接到音乐播放器，无法控制。"


def _get_music_playlist() -> str:
    service = _get_service()
    if service is not None:
        try:
            return service.playlist_text()
        except Exception as exc:
            return f"读取网易云播放队列失败：{exc}"
    cb = _brain_tools._music_info_callback
    if cb:
        return cb("playlist")
    return "音乐播放器未就绪。"


def _get_music_status() -> str:
    service = _get_service()
    if service is not None:
        try:
            return service.status_text()
        except Exception as exc:
            return f"读取网易云播放状态失败：{exc}"
    cb = _brain_tools._music_info_callback
    if cb:
        return cb("status")
    return "音乐播放器未就绪。"


def _get_music_stats() -> str:
    service = _get_service()
    if service is not None:
        try:
            return service.stats_text()
        except Exception as exc:
            return f"读取网易云音乐统计失败：{exc}"
    cb = _brain_tools._music_info_callback
    if cb:
        return cb("stats")
    return "音乐统计未就绪。"


def _get_service():
    try:
        from brain.music_service import get_music_service
        return get_music_service()
    except Exception:
        return None


TOOL_EXECUTORS = {
    "control_music":     lambda inp: _control_music(inp["action"]),
    "get_music_playlist": lambda inp: _get_music_playlist(),
    "get_music_status":  lambda inp: _get_music_status(),
    "get_music_stats":   lambda inp: _get_music_stats(),
}
