"""
ExpressionManager：莲心 Galgame 窗口的情绪识别与动画状态映射。
情绪关键词 → 桌宠动画状态（happy/sleep/think/idle 等）。
映射可在设置面板的“行为动作触发”页自定义。
"""
from utils.pet_behavior import (
    EMOTION_TRIGGER_KEYS,  # noqa: F401  (设置面板沿用该键顺序)
    emotion_for_text,
    state_for_emotion,
    state_for_event as resolve_state_for_event,
)


class ExpressionManager:
    """管理情绪识别与动画状态映射。"""

    def __init__(self, assets_dir):
        self._assets_dir = assets_dir

    def match(self, text: str) -> str:
        """从 AI 回复文本中匹配情绪关键词，返回情绪名称。"""
        return emotion_for_text(text)

    def state_for_event(self, event_key: str, fallback: str = "idle") -> str:
        """返回事件（thinking/speaking/emotion_*）对应的动画状态，读取设置面板配置。"""
        try:
            from utils.settings import get_settings
            triggers = get_settings().galgame_action_triggers
        except Exception:
            triggers = {}
        return resolve_state_for_event(event_key, triggers, fallback)

    def state_for(self, emotion: str) -> str:
        """返回情绪对应的动画状态（读取设置面板中的行为动作触发配置）。"""
        try:
            from utils.settings import get_settings
            triggers = get_settings().galgame_action_triggers
        except Exception:
            triggers = {}
        return state_for_emotion(emotion, triggers)
