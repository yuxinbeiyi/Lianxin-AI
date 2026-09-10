"""
ExpressionManager：莲心 Galgame 窗口的情绪识别与动画状态映射。
情绪关键词 → 桌宠动画状态（happy/sleep/think/idle 等）。
映射可在设置面板的“行为动作触发”页自定义。
"""
import re

from .sprite_animation import STATE_INTERVALS

# 情绪名称 → 设置里的触发键（行为动作触发配置）
EMOTION_TRIGGER_KEYS: dict[str, str] = {
    "开心": "emotion_happy",
    "生气": "emotion_angry",
    "伤心": "emotion_sad",
    "惊讶": "emotion_surprised",
    "疑惑": "emotion_confused",
    "害羞": "emotion_shy",
    "撒娇": "emotion_coquettish",
    "疲惫": "emotion_tired",
    "默认": "emotion_default",
}

# 情绪正则匹配模式（优先级从高到低）
_EMOTION_PATTERNS: list[tuple[str, str]] = [
    ("开心",   r"(开心|高兴|快乐|愉快|喜悦|哈哈哈|嘿嘿|嘻嘻|好开心)"),
    ("生气",   r"(生气|愤怒|不满|不爽|气死|烦死了|火大)"),
    ("伤心",   r"(伤心|难过|哭泣|悲伤|泪|哭哭|呜呜|好难过)"),
    ("惊讶",   r"(惊讶|吃惊|震惊|意外|真的吗|不会吧|天哪|哇)"),
    ("疑惑",   r"(疑惑|困惑|不解|奇怪|嗯？|啥？|为什么)"),
    ("害羞",   r"(害羞|不好意思|脸红|羞羞|难为情)"),
    ("撒娇",   r"(撒娇|嘛~|人家|讨厌|不要嘛)"),
    ("疲惫",   r"(疲惫|累|好累|困|想睡|没精神)"),
    ("默认",   ""),  # 兜底
]


def _valid_state(state: str, fallback: str = "idle") -> str:
    return state if state in STATE_INTERVALS else fallback


class ExpressionManager:
    """管理情绪识别与动画状态映射。"""

    def __init__(self, assets_dir):
        self._assets_dir = assets_dir

    def match(self, text: str) -> str:
        """从 AI 回复文本中匹配情绪关键词，返回情绪名称。"""
        for emotion, pattern in _EMOTION_PATTERNS:
            if not pattern:
                continue
            if re.search(pattern, text):
                return emotion
        return "默认"

    def state_for_event(self, event_key: str, fallback: str = "idle") -> str:
        """返回事件（thinking/speaking/emotion_*）对应的动画状态，读取设置面板配置。"""
        try:
            from utils.settings import get_settings
            triggers = get_settings().galgame_action_triggers
            state = (triggers or {}).get(event_key, "")
        except Exception:
            state = ""
        if not state:
            state = fallback
        return _valid_state(state, fallback)

    def state_for(self, emotion: str) -> str:
        """返回情绪对应的动画状态（读取设置面板中的行为动作触发配置）。"""
        key = EMOTION_TRIGGER_KEYS.get(emotion, "emotion_default")
        fallback = {
            "emotion_happy": "happy",
            "emotion_angry": "think",
            "emotion_sad": "sit",
            "emotion_surprised": "happy",
            "emotion_confused": "think",
            "emotion_shy": "sit",
            "emotion_coquettish": "happy",
            "emotion_tired": "sleep",
            "emotion_default": "idle",
        }.get(key, "idle")
        return self.state_for_event(key, fallback)
