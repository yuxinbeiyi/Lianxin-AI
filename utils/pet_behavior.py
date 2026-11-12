"""Qt-free pet behaviour definitions shared by the PyQt galgame windows and the new native pet windows."""

from __future__ import annotations

import re

# 动画状态 → 帧间隔（毫秒），与桌宠保持一致
STATE_INTERVALS: dict[str, int] = {
    "idle": 220, "sit": 300, "sleep": 420, "happy": 180,
    "study": 260, "think": 260, "walk_left": 120, "walk_right": 120,
}

# 可在设置面板里选择的动作状态（不含左右移动，移动由菜单单独控制）
SELECTABLE_STATES: tuple[tuple[str, str], ...] = (
    ("idle", "待机"), ("sit", "坐下"), ("sleep", "睡眠"),
    ("happy", "开心"), ("study", "学习"), ("think", "思考"),
)

# 情绪名称 → 设置里的触发键（行为动作触发配置）
EMOTION_TRIGGER_KEYS: dict[str, str] = {
    "开心": "emotion_happy", "生气": "emotion_angry", "伤心": "emotion_sad",
    "惊讶": "emotion_surprised", "疑惑": "emotion_confused", "害羞": "emotion_shy",
    "撒娇": "emotion_coquettish", "疲惫": "emotion_tired", "默认": "emotion_default",
}

# 行为动作触发：事件 → 动画状态
DEFAULT_TRIGGERS: dict[str, str] = {
    "thinking": "think", "speaking": "happy",
    "emotion_happy": "happy", "emotion_angry": "think", "emotion_sad": "sit",
    "emotion_surprised": "happy", "emotion_confused": "think", "emotion_shy": "sit",
    "emotion_coquettish": "happy", "emotion_tired": "sleep", "emotion_default": "idle",
}

EMOTION_LABELS: tuple[str, ...] = tuple(EMOTION_TRIGGER_KEYS)

# 触发事件（设置键 → 显示名），不含情绪（情绪单独列出）
GENERAL_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("thinking", "思考中"),
    ("speaking", "说话中"),
)

# 情绪触发（复用表达管理器的触发键顺序）
EMOTION_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("emotion_happy", "开心"),
    ("emotion_angry", "生气"),
    ("emotion_sad", "伤心"),
    ("emotion_surprised", "惊讶"),
    ("emotion_confused", "疑惑"),
    ("emotion_shy", "害羞"),
    ("emotion_coquettish", "撒娇"),
    ("emotion_tired", "疲惫"),
    ("emotion_default", "默认"),
)

TRIGGER_ITEMS: tuple[tuple[str, str], ...] = GENERAL_TRIGGERS + EMOTION_TRIGGERS

# 情绪正则匹配模式（优先级从高到低）
_EMOTION_PATTERNS: list[tuple[str, str]] = [
    ("开心", r"(开心|高兴|快乐|愉快|喜悦|哈哈哈|嘿嘿|嘻嘻|好开心)"),
    ("生气", r"(生气|愤怒|不满|不爽|气死|烦死了|火大)"),
    ("伤心", r"(伤心|难过|哭泣|悲伤|泪|哭哭|呜呜|好难过)"),
    ("惊讶", r"(惊讶|吃惊|震惊|意外|真的吗|不会吧|天哪|哇)"),
    ("疑惑", r"(疑惑|困惑|不解|奇怪|嗯？|啥？|为什么)"),
    ("害羞", r"(害羞|不好意思|脸红|羞羞|难为情)"),
    ("撒娇", r"(撒娇|嘛~|人家|讨厌|不要嘛)"),
    ("疲惫", r"(疲惫|累|好累|困|想睡|没精神)"),
]


def valid_state(state: str, fallback: str = "idle") -> str:
    return state if state in STATE_INTERVALS else fallback


def emotion_for_text(text: str) -> str:
    """Match the AI reply text against the original emotion patterns."""
    for emotion, pattern in _EMOTION_PATTERNS:
        if re.search(pattern, str(text or "")):
            return emotion
    return "默认"


def state_for_event(event_key: str, triggers: dict | None = None, fallback: str = "idle") -> str:
    """Resolve an event key through saved triggers; invalid saved values fall back."""
    state = (triggers or {}).get(event_key, "")
    return valid_state(state, valid_state(fallback, "idle"))


def state_for_emotion(emotion: str, triggers: dict | None = None) -> str:
    key = EMOTION_TRIGGER_KEYS.get(emotion, "emotion_default")
    fallback = {
        "emotion_happy": "happy", "emotion_angry": "think", "emotion_sad": "sit",
        "emotion_surprised": "happy", "emotion_confused": "think", "emotion_shy": "sit",
        "emotion_coquettish": "happy", "emotion_tired": "sleep", "emotion_default": "idle",
    }.get(key, "idle")
    return state_for_event(key, triggers, fallback)
