"""
摄像头管理
封装 OpenCV VideoCapture，提供启动、停止、帧读取等功能。
异常安全：摄像头打开失败/运行中断不崩溃，返回错误信息。
"""

import time
from typing import Optional, Tuple

import cv2
import numpy as np

from ..config import (
    CAMERA_INDEX,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
    CAMERA_FALLBACK_WIDTH,
    CAMERA_FALLBACK_HEIGHT,
)


class CameraManager:
    """摄像头管理器。

    负责 OpenCV VideoCapture 的生命周期管理，
    不包含任何视觉识别逻辑。
    """

    def __init__(self, camera_index: int = CAMERA_INDEX):
        self._camera_index = camera_index
        self._cap: Optional[cv2.VideoCapture] = None
        self._running = False
        self._width = 0
        self._height = 0
        self._fps = 0.0
        self._last_frame_time = 0.0
        self._fps_smoothing = 0.0  # 平滑后的 FPS
        self._error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> float:
        return self._fps_smoothing

    @property
    def error(self) -> Optional[str]:
        return self._error

    def start(self) -> bool:
        """启动摄像头。

        Returns:
            True 表示启动成功，False 表示失败（错误信息在 error 属性中）。
        """
        if self._running:
            return True

        try:
            self._cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
        except Exception:
            self._cap = cv2.VideoCapture(self._camera_index)

        if not self._cap or not self._cap.isOpened():
            self._error = "未检测到可用摄像头，请检查设备是否连接。"
            self._running = False
            return False

        # 尝试设置目标分辨率和帧率
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        self._cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

        # 读取实际分辨率
        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self._cap.get(cv2.CAP_PROP_FPS)

        # 如果目标分辨率失败，尝试降级
        if actual_w < 320 or actual_h < 240:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_FALLBACK_WIDTH)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_FALLBACK_HEIGHT)
            actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = self._cap.get(cv2.CAP_PROP_FPS)

        if actual_w < 320 or actual_h < 240:
            self._error = "摄像头打开失败，请检查是否被其他程序占用。"
            self._cap.release()
            self._cap = None
            self._running = False
            return False

        self._width = actual_w
        self._height = actual_h
        self._fps = actual_fps if actual_fps > 0 else CAMERA_FPS
        self._fps_smoothing = self._fps
        self._last_frame_time = time.monotonic()
        self._running = True
        self._error = None
        return True

    def read_frame(self) -> Optional[np.ndarray]:
        """读取一帧图像。

        Returns:
            BGR 格式图像，失败返回 None。
        """
        if not self._running or self._cap is None:
            return None

        try:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                return None
        except Exception:
            return None

        # 更新 FPS 计算
        now = time.monotonic()
        dt = now - self._last_frame_time
        if dt > 0:
            instant_fps = 1.0 / dt
            # 指数平滑
            if self._fps_smoothing > 0:
                self._fps_smoothing = 0.9 * self._fps_smoothing + 0.1 * instant_fps
            else:
                self._fps_smoothing = instant_fps
        self._last_frame_time = now

        return frame

    def stop(self):
        """停止摄像头并释放资源。"""
        self._running = False
        try:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
        except Exception:
            pass
        self._width = 0
        self._height = 0
        self._fps = 0.0
        self._fps_smoothing = 0.0

    def __del__(self):
        self.stop()
