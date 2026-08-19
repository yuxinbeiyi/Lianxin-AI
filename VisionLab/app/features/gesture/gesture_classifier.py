"""
手势分类器
输入 21 个手部关键点，输出手势类型 + 置信度。

分为两类：
- 静态手势：OK、THUMBS_UP（单帧几何判断）
- 动态手势：WAVE（多帧轨迹分析）
"""

import math
import time
from collections import deque
from typing import Optional

from .config import (
    OK_DISTANCE_RATIO,
    OK_FINGER_EXTEND_RATIO,
    THUMBS_UP_RATIO,
    THUMBS_FINGER_CURL_RATIO,
    WAVE_WINDOW_SECONDS,
    WAVE_MIN_SWINGS,
    WAVE_MIN_DISPLACEMENT_RATIO,
    WAVE_AREA_STABILITY,
)
from .gesture_event import GESTURE_NONE, GESTURE_OK, GESTURE_THUMBS_UP, GESTURE_WAVE


# MediaPipe 手部 21 点索引
# 0: 腕部
# 1-4: 拇指 (CMC, MCP, IP, TIP)
# 5-8: 食指 (MCP, PIP, DIP, TIP)
# 9-12: 中指 (MCP, PIP, DIP, TIP)
# 13-16: 无名指 (MCP, PIP, DIP, TIP)
# 17-20: 小指 (MCP, PIP, DIP, TIP)

WRIST = 0
THUMB_TIP = 4
INDEX_FINGER_TIP = 8
INDEX_FINGER_PIP = 6
MIDDLE_FINGER_TIP = 12
MIDDLE_FINGER_PIP = 10
RING_FINGER_TIP = 16
RING_FINGER_PIP = 14
PINKY_TIP = 20
PINKY_PIP = 18
INDEX_MCP = 5
PINKY_MCP = 17


def _dist(p1, p2) -> float:
    """两个关键点的欧氏距离（归一化坐标空间）。"""
    dx = p1.x - p2.x
    dy = p1.y - p2.y
    return math.sqrt(dx * dx + dy * dy)


def _palm_width(landmarks) -> float:
    """手掌宽度：食指 MCP 到小指 MCP 的距离。"""
    return _dist(landmarks[INDEX_MCP], landmarks[PINKY_MCP])


def _palm_height(landmarks) -> float:
    """手掌高度：手腕到中指 MCP 的距离。"""
    return _dist(landmarks[WRIST], landmarks[MIDDLE_FINGER_TIP - 2])  # MIDDLE MCP = 9


def _palm_center(landmarks) -> tuple[float, float]:
    """手掌中心坐标（手腕和中指 MCP 的中点近似）。"""
    cx = (landmarks[WRIST].x + landmarks[MIDDLE_FINGER_TIP - 2].x) / 2
    cy = (landmarks[WRIST].y + landmarks[MIDDLE_FINGER_TIP - 2].y) / 2
    return cx, cy


def _palm_area_approx(landmarks) -> float:
    """手掌面积近似（宽 × 高）。"""
    return _palm_width(landmarks) * _palm_height(landmarks)


class StaticGestureClassifier:
    """静态手势分类器（单帧判断）。

    输入 21 个关键点，输出 (gesture, confidence)。
    """

    def classify(self, landmarks) -> tuple[str, float]:
        """分类单个手势。

        Args:
            landmarks: 21 个 NormalizedLandmark 列表

        Returns:
            (gesture_type, confidence)
        """
        if landmarks is None or len(landmarks) < 21:
            return GESTURE_NONE, 0.0

        # 依次检测各静态手势，取置信度最高的
        results = []

        ok_gesture, ok_conf = self._detect_ok(landmarks)
        if ok_gesture:
            results.append((GESTURE_OK, ok_conf))

        tu_gesture, tu_conf = self._detect_thumbs_up(landmarks)
        if tu_gesture:
            results.append((GESTURE_THUMBS_UP, tu_conf))

        if not results:
            return GESTURE_NONE, 0.0

        # 取置信度最高的
        results.sort(key=lambda x: x[1], reverse=True)
        return results[0]

    def _detect_ok(self, landmarks) -> tuple[bool, float]:
        """检测 OK 手势。

        特征：拇指指尖与食指指尖距离近，形成圆圈；
             中指、无名指、小指伸展。
        """
        thumb_tip = landmarks[THUMB_TIP]
        index_tip = landmarks[INDEX_FINGER_TIP]
        palm_w = _palm_width(landmarks)
        if palm_w < 0.01:
            return False, 0.0

        # 拇指食指距离 / 掌宽
        tip_dist = _dist(thumb_tip, index_tip)
        dist_ratio = tip_dist / palm_w

        if dist_ratio > OK_DISTANCE_RATIO:
            return False, 0.0

        # 检查其余三指是否伸展（指尖在 PIP 关节上方 = y 更小）
        extended = 0
        total = 0
        for tip_idx, pip_idx in [
            (MIDDLE_FINGER_TIP, MIDDLE_FINGER_PIP),
            (RING_FINGER_TIP, RING_FINGER_PIP),
            (PINKY_TIP, PINKY_PIP),
        ]:
            total += 1
            # 指尖 y < PIP y 表示伸展（图像坐标 y 向下增长）
            if landmarks[tip_idx].y < landmarks[pip_idx].y * OK_FINGER_EXTEND_RATIO:
                extended += 1

        if extended < 2:  # 至少两根手指伸展
            return False, 0.0

        # 置信度：距离越近 + 伸展手指越多，置信度越高
        dist_conf = max(0.0, 1.0 - dist_ratio / OK_DISTANCE_RATIO)
        extend_conf = extended / total
        confidence = 0.6 * dist_conf + 0.4 * extend_conf
        return True, max(0.3, min(1.0, confidence))

    def _detect_thumbs_up(self, landmarks) -> tuple[bool, float]:
        """检测竖大拇指手势。

        特征：拇指明显向上伸展，其余四指弯曲。
        """
        palm_h = _palm_height(landmarks)
        if palm_h < 0.01:
            return False, 0.0

        wrist = landmarks[WRIST]
        thumb_tip = landmarks[THUMB_TIP]
        middle_mcp = landmarks[MIDDLE_FINGER_TIP - 2]

        # 拇指长度：拇指尖到手腕的距离
        thumb_len = _dist(thumb_tip, wrist)
        thumb_ratio = thumb_len / palm_h

        if thumb_ratio < THUMBS_UP_RATIO:
            return False, 0.0

        # 拇指方向：拇指尖应该在手腕上方（y 更小），且相对手掌中心偏左或偏右
        if thumb_tip.y >= wrist.y:
            return False, 0.0

        # 检查其余四指是否弯曲（指尖到掌心的距离 < 手指长度 * 比例）
        cx, cy = _palm_center(landmarks)
        curled = 0
        total = 0
        for tip_idx, pip_idx in [
            (INDEX_FINGER_TIP, INDEX_FINGER_PIP),
            (MIDDLE_FINGER_TIP, MIDDLE_FINGER_PIP),
            (RING_FINGER_TIP, RING_FINGER_PIP),
            (PINKY_TIP, PINKY_PIP),
        ]:
            total += 1
            tip = landmarks[tip_idx]
            pip = landmarks[pip_idx]
            # 指尖到掌心距离
            tip_to_palm = math.hypot(tip.x - cx, tip.y - cy)
            # 手指长度近似：PIP 到指尖距离 × 2
            finger_len = _dist(tip, pip) * 2
            if finger_len < 0.005:
                continue
            if tip_to_palm / finger_len < THUMBS_FINGER_CURL_RATIO:
                curled += 1

        if curled < 2:  # 至少两根手指弯曲
            return False, 0.0

        # 置信度：拇指伸展比 + 弯曲手指比例
        thumb_conf = min(1.0, (thumb_ratio - THUMBS_UP_RATIO) / 0.3 + 0.5)
        curl_conf = curled / total
        confidence = 0.5 * thumb_conf + 0.5 * curl_conf
        return True, max(0.3, min(1.0, confidence))


class WaveDetector:
    """挥手（动态手势）检测器。

    跟踪手掌中心在时间窗口内的水平运动轨迹，
    检测到多次往返运动时判定为挥手。
    """

    def __init__(self, window_seconds: float = WAVE_WINDOW_SECONDS):
        self.window_seconds = window_seconds
        # 轨迹：[(timestamp, palm_center_x, palm_center_y, palm_area), ...]
        self._trajectory: deque = deque()
        self._last_gesture = GESTURE_NONE
        self._last_seen = 0.0

    def update(self, landmarks) -> tuple[str, float]:
        """更新一帧数据，返回 (gesture, confidence)。"""
        now = time.monotonic()

        if landmarks is None or len(landmarks) < 21:
            self._last_gesture = GESTURE_NONE
            if self._last_seen and now - self._last_seen > 0.25:
                self._trajectory.clear()
            return GESTURE_NONE, 0.0

        if self._last_seen and now - self._last_seen > 0.25:
            self._trajectory.clear()
        self._last_seen = now

        cx, cy = _palm_center(landmarks)
        area = _palm_area_approx(landmarks)
        palm_w = _palm_width(landmarks)

        # 加入轨迹
        self._trajectory.append((now, cx, cy, area, palm_w))

        # 清理过期数据
        while self._trajectory and now - self._trajectory[0][0] > self.window_seconds:
            self._trajectory.popleft()

        if len(self._trajectory) < 10:  # 数据点太少
            self._last_gesture = GESTURE_NONE
            return GESTURE_NONE, 0.0

        gesture, conf = self._detect_wave()
        self._last_gesture = gesture
        return gesture, conf

    def _detect_wave(self) -> tuple[str, float]:
        """从轨迹中检测挥手。"""
        if len(self._trajectory) < 10:
            return GESTURE_NONE, 0.0

        xs = [p[1] for p in self._trajectory]
        areas = [p[3] for p in self._trajectory]
        palm_ws = [p[4] for p in self._trajectory]
        avg_palm_w = sum(palm_ws) / len(palm_ws)
        if avg_palm_w < 0.005:
            return GESTURE_NONE, 0.0

        # 检查手掌面积稳定性（排除手走近/走远的情况）
        avg_area = sum(areas) / len(areas)
        if avg_area < 0.0001:
            return GESTURE_NONE, 0.0
        area_variance = max(areas) / avg_area - 1.0
        if area_variance > WAVE_AREA_STABILITY:
            return GESTURE_NONE, 0.0

        # 计算水平方向的往返次数
        min_disp = WAVE_MIN_DISPLACEMENT_RATIO * avg_palm_w
        swings = 0
        direction = 0  # 1 = 向右, -1 = 向左, 0 = 未定
        last_extreme_x = xs[0]
        last_extreme_idx = 0

        for i in range(1, len(xs)):
            dx = xs[i] - last_extreme_x
            if abs(dx) < min_disp:
                continue

            current_dir = 1 if dx > 0 else -1
            if direction == 0:
                direction = current_dir
                last_extreme_x = xs[i]
                last_extreme_idx = i
            elif current_dir != direction:
                # 方向变化 = 一次摆动
                swings += 1
                direction = current_dir
                last_extreme_x = xs[i]
                last_extreme_idx = i
            else:
                # 同方向，更新极值
                last_extreme_x = xs[i]
                last_extreme_idx = i

        if swings < WAVE_MIN_SWINGS:
            return GESTURE_NONE, 0.0

        # 置信度：摆动次数越多越可信
        conf = min(1.0, 0.4 + 0.15 * swings)
        # 面积越稳定，置信度越高
        area_stability = max(0.0, 1.0 - area_variance / WAVE_AREA_STABILITY)
        conf = 0.7 * conf + 0.3 * area_stability

        return GESTURE_WAVE, max(0.3, min(1.0, conf))

    def reset(self):
        """重置轨迹。"""
        self._trajectory.clear()
        self._last_gesture = GESTURE_NONE
        self._last_seen = 0.0


class GestureClassifier:
    """综合手势分类器（静态 + 动态）。

    每一帧调用 update(landmarks)，返回当前识别到的手势和置信度。
    动态手势优先（挥手一旦确认优先级最高），否则返回静态手势。
    """

    def __init__(self):
        self._static = StaticGestureClassifier()
        self._wave = WaveDetector()
        self.current_gesture = GESTURE_NONE
        self.current_confidence = 0.0

    def update(self, landmarks) -> tuple[str, float]:
        """更新一帧，返回 (gesture, confidence)。

        动态手势和静态手势同时检测，挥手优先。
        """
        # 动态手势检测
        wave_gesture, wave_conf = self._wave.update(landmarks)

        # 静态手势检测
        static_gesture, static_conf = self._static.classify(landmarks)

        # 挥手优先（置信度足够时）
        if wave_gesture == GESTURE_WAVE and wave_conf >= 0.5:
            self.current_gesture = GESTURE_WAVE
            self.current_confidence = wave_conf
            return GESTURE_WAVE, wave_conf

        # 否则返回静态手势
        self.current_gesture = static_gesture
        self.current_confidence = static_conf
        return static_gesture, static_conf

    def reset(self):
        """重置所有状态。"""
        self._wave.reset()
        self.current_gesture = GESTURE_NONE
        self.current_confidence = 0.0
