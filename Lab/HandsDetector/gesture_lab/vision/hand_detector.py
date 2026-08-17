"""
手部关键点检测器
封装 MediaPipe Hand Landmarker，输入图像输出 21 个关键点及左右手信息。
"""

import time
from pathlib import Path
from typing import Optional

import cv2
import mediapipe as mp
import numpy as np

from ..config import (
    MODEL_PATH,
    MAX_NUM_HANDS,
    MIN_HAND_DETECTION_CONFIDENCE,
    MIN_HAND_PRESENCE_CONFIDENCE,
    MIN_TRACKING_CONFIDENCE,
)


def _resolve_model_path(src_path: Path) -> str:
    """确保模型文件路径可用。

    MediaPipe C++ 底层在 Windows 下对非 ASCII 路径支持差（errno=-1）。
    如果路径包含非 ASCII 字符，就复制到用户 TEMP 目录下的纯英文路径。
    """
    import shutil
    import tempfile

    path_str = str(src_path).replace("\\", "/")

    # 检查路径是否全是 ASCII
    try:
        path_str.encode("ascii")
        is_ascii = True
    except UnicodeEncodeError:
        is_ascii = False

    if is_ascii and src_path.exists():
        return path_str

    # 路径含中文 → 复制到纯英文临时目录
    if not src_path.exists():
        return path_str  # 路径不存在也直接返回，后面会报错

    tmp_dir = Path(tempfile.gettempdir()) / "gesture_lab_models"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / src_path.name

    # 只在文件不同时复制（用大小简单判断）
    if not tmp_path.exists() or tmp_path.stat().st_size != src_path.stat().st_size:
        shutil.copy2(src_path, tmp_path)

    return str(tmp_path).replace("\\", "/")


BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
HandLandmarkerResult = mp.tasks.vision.HandLandmarkerResult
VisionRunningMode = mp.tasks.vision.RunningMode


class HandDetector:
    """手部关键点检测器。

    使用 MediaPipe Hand Landmarker LIVE_STREAM 模式，异步接收检测结果。
    检测结果缓存在 latest_result 中，供上层读取。
    """

    def __init__(self, model_path: Optional[Path] = None):
        # MediaPipe 的 C++ 底层在 Windows 下对中文/非 ASCII 路径支持不好，
        # 会报 errno=-1。_resolve_model_path 会把模型复制到纯英文临时目录。
        src_path = Path(model_path or MODEL_PATH).resolve()
        self._model_path = _resolve_model_path(src_path)
        self._landmarker: Optional[HandLandmarker] = None
        self.latest_result: Optional[HandLandmarkerResult] = None
        self.latest_timestamp_ms: int = 0
        self._frame_count = 0
        self._initialized = False
        self._init_error: Optional[str] = None

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    def initialize(self) -> bool:
        """初始化 MediaPipe Hand Landmarker。

        返回 True 表示初始化成功，失败时返回 False，错误信息在 init_error 中。
        """
        try:
            if not Path(self._model_path).exists():
                self._init_error = f"模型文件不存在: {self._model_path}"
                return False

            options = HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=self._model_path),
                running_mode=VisionRunningMode.LIVE_STREAM,
                num_hands=MAX_NUM_HANDS,
                min_hand_detection_confidence=MIN_HAND_DETECTION_CONFIDENCE,
                min_hand_presence_confidence=MIN_HAND_PRESENCE_CONFIDENCE,
                min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
                result_callback=self._on_result,
            )
            self._landmarker = HandLandmarker.create_from_options(options)
            self._initialized = True
            self._init_error = None
            return True
        except Exception as exc:
            self._init_error = f"手部检测模块初始化失败: {exc}"
            self._initialized = False
            return False

    def _on_result(self, result: HandLandmarkerResult,
                   output_image: mp.Image, timestamp_ms: int):
        """LIVE_STREAM 模式的结果回调。"""
        self.latest_result = result
        self.latest_timestamp_ms = timestamp_ms

    def detect(self, frame_bgr: np.ndarray) -> bool:
        """送入一帧图像进行检测。

        Args:
            frame_bgr: OpenCV BGR 格式图像

        Returns:
            True 表示成功送入检测，False 表示未初始化或失败。
        """
        if not self._initialized or self._landmarker is None:
            return False
        try:
            self._frame_count += 1
            # BGR → RGB
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            timestamp_ms = int(time.monotonic() * 1000)
            self._landmarker.detect_async(mp_image, timestamp_ms)
            return True
        except Exception:
            return False

    def get_hands_count(self) -> int:
        """当前检测到的手数量。"""
        if self.latest_result is None:
            return 0
        return len(self.latest_result.hand_landmarks or [])

    def get_landmarks(self, hand_index: int = 0) -> Optional[list]:
        """获取指定手的 21 个关键点（归一化坐标）。

        返回 NormalizedLandmark 列表，每个元素有 x, y, z 属性（0~1 归一化）。
        """
        if self.latest_result is None:
            return None
        hands = self.latest_result.hand_landmarks or []
        if hand_index >= len(hands):
            return None
        return hands[hand_index]

    def get_handedness(self, hand_index: int = 0) -> str:
        """获取指定手的左右手（"Left" / "Right"）。"""
        if self.latest_result is None:
            return ""
        handedness_list = self.latest_result.handedness or []
        if hand_index >= len(handedness_list):
            return ""
        categories = handedness_list[hand_index]
        if categories:
            return categories[0].category_name
        return ""

    def close(self):
        """释放资源。"""
        try:
            if self._landmarker is not None:
                self._landmarker.close()
                self._landmarker = None
        except Exception:
            pass
        self._initialized = False
        self.latest_result = None
