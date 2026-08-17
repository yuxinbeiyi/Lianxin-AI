"""
模拟莲心回复
第一阶段：硬编码映射，不调用任何 LLM。

未来接入莲心时，只需替换这个模块的实现，
上层 EventManager 和 UI 不用改。
"""

from typing import Optional

from ..events.gesture_event import (
    GestureEvent,
    GESTURE_OK,
    GESTURE_THUMBS_UP,
    GESTURE_WAVE,
)


# 手势 → 莲心回复 的映射表
GESTURE_REPLIES = {
    GESTURE_OK: "收到～",
    GESTURE_THUMBS_UP: "不客气～这是我力所能及的。",
    GESTURE_WAVE: "拜拜，一会儿见～",
}


class MockLianXin:
    """模拟莲心回复模块。

    接收手势事件，返回模拟回复文本。
    作为 EventManager 的事件处理器使用。
    """

    def __init__(self):
        self._last_reply: Optional[str] = None
        self._last_gesture: Optional[str] = None

    @property
    def last_reply(self) -> Optional[str]:
        return self._last_reply

    @property
    def last_gesture(self) -> Optional[str]:
        return self._last_gesture

    def get_reply(self, gesture: str) -> str:
        """根据手势类型获取莲心回复文本。"""
        return GESTURE_REPLIES.get(gesture, "（我不太明白这个手势呢...）")

    def handle_event(self, event: GestureEvent) -> str:
        """事件处理器接口：接收 GestureEvent，返回回复。

        可以直接作为 EventManager 的 handler 使用。
        """
        self._last_gesture = event.gesture
        reply = self.get_reply(event.gesture)
        self._last_reply = reply
        return reply

    def __call__(self, event: GestureEvent):
        """允许直接作为回调函数使用。"""
        self.handle_event(event)
