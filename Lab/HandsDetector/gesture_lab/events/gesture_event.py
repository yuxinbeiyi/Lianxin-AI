"""
手势事件数据类
独立于视觉检测和 UI，作为各模块之间的通信协议。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# 手势类型常量
GESTURE_NONE = "NONE"
GESTURE_OK = "OK"
GESTURE_THUMBS_UP = "THUMBS_UP"
GESTURE_WAVE = "WAVE"

# 所有已知手势
ALL_GESTURES = (GESTURE_NONE, GESTURE_OK, GESTURE_THUMBS_UP, GESTURE_WAVE)

# 手势中文名映射
GESTURE_NAMES = {
    GESTURE_NONE: "无",
    GESTURE_OK: "OK",
    GESTURE_THUMBS_UP: "竖大拇指",
    GESTURE_WAVE: "挥手",
}


@dataclass
class GestureEvent:
    """手势确认事件。

    只有经过连续帧确认 + 冷却检查后，才会生成此事件并分发。
    """
    gesture: str                    # 手势类型：OK / THUMBS_UP / WAVE
    confidence: float = 0.0         # 置信度 0.0 ~ 1.0
    hand_count: int = 0             # 检测到的手的数量
    timestamp: Optional[datetime] = None  # 事件时间

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    @property
    def name(self) -> str:
        """中文手势名。"""
        return GESTURE_NAMES.get(self.gesture, self.gesture)

    @property
    def time_str(self) -> str:
        """格式化时间戳 HH:MM:SS。"""
        return self.timestamp.strftime("%H:%M:%S")
