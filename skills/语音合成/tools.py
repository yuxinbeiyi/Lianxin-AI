"""
语音合成技能工具 — GPT-SoVITS + Edge-TTS 回退。

提供 2 个 AI 可调用工具：
  - set_voice_mood: 设置默认情绪音色
  - list_voice_styles: 列出可用风格

桌面端语音由 voice/speaker.py 自动朗读，无需 AI 显式调用。
"""

import importlib
import os
import tempfile
import threading
import time
import logging
import re

logger = logging.getLogger("voice_tools")

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "set_voice_mood",
            "description": (
                "设置当前会话的默认语音情绪/音色风格。设置后后续的语音合成都会使用此风格，"
                "除非当前会话内再次修改。可选：auto（自动匹配）、casual（日常温柔）、"
                "tsundere（傲娇强势）、romantic（深情）、long（长句稳定）、angry（生气愤怒）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "enum": ["auto", "casual", "tsundere", "romantic", "long", "angry"],
                        "description": "要设置的默认语音情绪风格"
                    }
                },
                "required": ["mood"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_voice_styles",
            "description": "列出所有可用的语音合成音色/情绪风格及其描述。当用户问'有哪些语音风格''可以用什么语气说话'时调用。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


# ── 每会话状态（模块级变量，重启后重置为 auto） ─────────
_session_mood = "auto"


# ── 工具实现 ──────────────────────────────────────────────

def _set_voice_mood(mood: str) -> str:
    """设置会话默认情绪。"""
    global _session_mood
    valid = ["auto", "casual", "tsundere", "romantic", "long", "angry"]
    if mood not in valid:
        return f"无效的语音风格：{mood}。可选：{'、'.join(valid)}"
    _session_mood = mood
    label = {
        "auto": "自动匹配",
        "casual": "日常温柔",
        "tsundere": "傲娇",
        "romantic": "深情",
        "long": "长句稳定",
        "angry": "生气",
    }.get(mood, mood)
    return f"语音风格已设为：{label}（{mood}）"


def _list_voice_styles() -> str:
    """列出可用语音风格。"""
    try:
        from brain.tts_engine import TtsEngine
        styles = TtsEngine.list_ref_styles()
    except Exception:
        styles = []

    if not styles:
        return "当前没有可用的语音风格（使用默认 Edge-TTS 标准音色）。如需定制声线，请配置 GPT-SoVITS 并添加参考音频。"

    lines = ["可用的语音合成风格："]
    for s in styles:
        lang_status = ""
        if s.get("file_count", 0) > 0:
            lang_status = f"（{s['file_count']}个参考音频）"
        lines.append(f"- {s['label']}（{s['mood']}）{lang_status}")
        if s.get("description"):
            lines.append(f"  └ {s['description']}")

    # 附加引擎状态
    try:
        from brain.tts_engine import TtsEngine, resolve_tts_mode, MODE_GPT_ONLY
        from config import get_tts_config
        engine = TtsEngine()
        configured = resolve_tts_mode(get_tts_config().get("engine"))
        lines.append("")
        if configured == MODE_GPT_ONLY:
            if engine.gpt_sovits_available:
                lines.append("当前引擎：GPT-SoVITS（声音克隆已启用，失败不会回退 Edge-TTS）")
            else:
                lines.append("当前引擎：GPT-SoVITS（未配置或不可用，语音会合成失败）")
                lines.append("提示：在「声音设置」里配置 GPT-SoVITS 路径，或改选 Edge-TTS")
        else:
            lines.append("当前引擎：Edge-TTS（标准云端发音，失败不会切换声线）")
            lines.append("提示：如需声音克隆，请在「声音设置」里改选 GPT-SoVITS")
    except Exception:
        pass

    return "\n".join(lines)


def stop_voice_playback():
    """停止当前正在播放的语音。供 main_window 在用户发新消息时调用。"""
    try:
        import pygame
        if pygame.mixer.get_init():
            pygame.mixer.stop()
    except Exception:
        pass


TOOL_EXECUTORS = {
    "set_voice_mood": lambda inp: _set_voice_mood(inp["mood"]),
    "list_voice_styles": lambda inp: _list_voice_styles(),
}
