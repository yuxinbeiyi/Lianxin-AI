"""
手势状态机
负责连续帧确认、去抖、冷却判断。

状态流转：
  NO_GESTURE → CANDIDATE → CONFIRMED → TRIGGERED → COOLDOWN → READY → NO_GESTURE

注意：GestureState 只负责"确认手势"和"判断能否触发事件"，
不负责分发事件（EventManager 负责）。
"""

import time
from dataclasses import dataclass
from typing import Optional

from ..config import GESTURE_CONFIRM_FRAMES, GESTURE_COOLDOWN
from ..events.gesture_event import GESTURE_NONE


# 状态常量
STATE_READY = "READY"           # 就绪，等待手势
STATE_CANDIDATE = "CANDIDATE"   # 候选帧，等待连续确认
STATE_CONFIRMED = "CONFIRMED"   # 已确认，准备触发
STATE_TRIGGERED = "TRIGGERED"   # 刚触发事件
STATE_COOLDOWN = "COOLDOWN"     # 冷却中
STATE_WAIT_RELEASE = "WAIT_RELEASE"  # 冷却后等待用户松手


@dataclass
class GestureStateInfo:
    """当前手势状态快照（供 UI 显示）。"""
    state: str = STATE_READY
    gesture: str = GESTURE_NONE
    confidence: float = 0.0
    cooldown_remaining: float = 0.0  # 剩余冷却时间（秒）
    candidate_frames: int = 0        # 已连续确认的帧数


class GestureState:
    """手势状态机。

    用法：
        gs = GestureState()
        gs.update(gesture, confidence)  # 每帧调用
        if gs.should_trigger():         # 这一帧是否应该触发事件
            event = gs.get_event_info()
    """

    def __init__(self,
                 confirm_frames: int = GESTURE_CONFIRM_FRAMES,
                 cooldown: float = GESTURE_COOLDOWN):
        self.confirm_frames = confirm_frames
        self.cooldown = cooldown

        self._state = STATE_READY
        self._gesture = GESTURE_NONE
        self._confidence = 0.0
        self._candidate_gesture = GESTURE_NONE
        self._candidate_frames = 0
        self._trigger_time: float = 0.0
        self._trigger_gesture = GESTURE_NONE
        self._should_trigger = False    # 当前帧是否触发了事件

    @property
    def state(self) -> str:
        return self._state

    @property
    def gesture(self) -> str:
        return self._gesture

    @property
    def confidence(self) -> float:
        return self._confidence

    def should_trigger(self) -> bool:
        """当前帧是否触发了新的手势事件。

        只能读取一次，读完自动清零。
        """
        result = self._should_trigger
        self._should_trigger = False
        return result

    def reset(self):
        self._state = STATE_READY
        self._gesture = GESTURE_NONE
        self._confidence = 0.0
        self._candidate_gesture = GESTURE_NONE
        self._candidate_frames = 0
        self._trigger_time = 0.0
        self._trigger_gesture = GESTURE_NONE
        self._should_trigger = False

    def get_info(self) -> GestureStateInfo:
        """获取当前状态快照（供 UI 显示）。"""
        remaining = 0.0
        if self._state == STATE_COOLDOWN:
            remaining = max(0.0, self.cooldown - (time.monotonic() - self._trigger_time))

        return GestureStateInfo(
            state=self._state,
            gesture=self._gesture,
            confidence=self._confidence,
            cooldown_remaining=remaining,
            candidate_frames=self._candidate_frames,
        )

    def update(self, gesture: str, confidence: float):
        """每帧更新一次。

        Args:
            gesture: 当前帧识别到的手势类型
            confidence: 置信度 0~1
        """
        self._confidence = confidence
        now = time.monotonic()

        # ── 冷却状态 ──
        if self._state == STATE_COOLDOWN:
            if now - self._trigger_time >= self.cooldown:
                self._state = STATE_WAIT_RELEASE
                self._gesture = self._trigger_gesture
                self._candidate_gesture = GESTURE_NONE
                self._candidate_frames = 0
            else:
                # 仍在冷却中，不处理新手势
                return

        if self._state == STATE_WAIT_RELEASE:
            if gesture == GESTURE_NONE or confidence < 0.3:
                self._state = STATE_READY
                self._gesture = GESTURE_NONE
                self._trigger_gesture = GESTURE_NONE
            return

        # ── 就绪/候选/已确认状态 ──
        if gesture == GESTURE_NONE or confidence < 0.3:
            # 没有检测到有效手势，重置候选
            self._gesture = GESTURE_NONE
            self._candidate_gesture = GESTURE_NONE
            self._candidate_frames = 0
            if self._state == STATE_CANDIDATE:
                self._state = STATE_READY
            return

        # 有有效手势输入
        if gesture == self._candidate_gesture:
            # 与候选手势相同，累计帧数
            self._candidate_frames += 1
            self._gesture = gesture

            if self._candidate_frames >= self.confirm_frames and self._state != STATE_TRIGGERED:
                # 连续帧确认完成
                self._state = STATE_TRIGGERED
                self._should_trigger = True
                self._trigger_time = now
                self._trigger_gesture = gesture
        else:
            # 新手势，重置候选计数
            self._candidate_gesture = gesture
            self._candidate_frames = 1
            self._gesture = gesture
            self._state = STATE_CANDIDATE

        # 触发后立即进入冷却
        if self._state == STATE_TRIGGERED:
            self._state = STATE_COOLDOWN
