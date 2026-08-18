"""Camera and CPU vision worker kept outside the Qt UI thread."""

import threading
import time

import cv2
import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from ..config import CAMERA_FPS
from ..events.gesture_event import GestureEvent
from ..vision.gesture_classifier import GestureClassifier
from ..vision.gesture_state import GestureState
from .camera_manager import CameraManager
from ..vision.hand_detector import HandDetector
from ..config import DIGIT_MODEL_PATH, DIGIT_LABELS_PATH
from ..vision.tflite_keypoint_classifier import TFLiteKeypointClassifier


class RecognitionWorker(QObject):
    frame_ready = pyqtSignal(object)
    status_ready = pyqtSignal(dict)
    gesture_event = pyqtSignal(object)
    started = pyqtSignal(bool, str)
    stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._camera = CameraManager()
        self._detector = HandDetector()
        self._classifier = GestureClassifier()
        self._state = GestureState()
        self._digit_classifier = TFLiteKeypointClassifier(
            DIGIT_MODEL_PATH, DIGIT_LABELS_PATH)
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

    @pyqtSlot()
    def run(self):
        if not self._detector.initialized and not self._detector.initialize():
            self.started.emit(False, self._detector.init_error or "手部检测模块初始化失败")
            self.stopped.emit()
            return
        if not self._camera.start():
            self.started.emit(False, self._camera.error or "摄像头启动失败")
            self._detector.close()
            self.stopped.emit()
            return

        self._stop_event.clear()
        self._classifier.reset()
        self._state.reset()
        self.started.emit(True, "")
        interval = 1.0 / max(1, CAMERA_FPS)

        while not self._stop_event.is_set():
            started_at = time.monotonic()
            frame = self._camera.read_frame()
            if frame is None:
                continue

            # Selfie-style preview: keep recognition coordinates aligned with
            # the mirrored image shown to the user.
            frame = cv2.flip(frame, 1)

            if not self._pause_event.is_set():
                self._detector.detect(frame)
                landmarks = self._detector.get_landmarks(0)
                hand_count = self._detector.get_hands_count()
                gesture, confidence = self._classifier.update(landmarks)
                model_gesture, model_confidence = self._digit_classifier.predict(landmarks)
                self._state.update(gesture, confidence)
                info = self._state.get_info()
                if self._state.should_trigger():
                    self.gesture_event.emit(GestureEvent(
                        gesture=info.gesture,
                        confidence=info.confidence,
                        hand_count=hand_count,
                    ))
                frame = self._draw_landmarks(frame, gesture, confidence)
            else:
                info = self._state.get_info()
                hand_count = self._detector.get_hands_count()

            self.frame_ready.emit(frame)
            info = self._state.get_info()
            self.status_ready.emit({
                "camera": f"{self._camera.width}×{self._camera.height}",
                "fps": round(self._camera.fps, 1),
                "hands": hand_count,
                "gesture": info.gesture,
                "confidence": round(info.confidence, 2),
                "event_state": info.state,
                "cooldown": round(info.cooldown_remaining, 2),
                "model_gesture": model_gesture if model_confidence >= 0.6 else "NONE",
                "model_confidence": round(model_confidence, 2),
            })
            remaining = interval - (time.monotonic() - started_at)
            if remaining > 0:
                self._stop_event.wait(remaining)

        self._camera.stop()
        self._detector.close()
        self.stopped.emit()

    @pyqtSlot()
    def stop(self):
        self._stop_event.set()

    @pyqtSlot(bool)
    def set_paused(self, paused: bool):
        if paused:
            self._pause_event.set()
        else:
            self._pause_event.clear()
            self._classifier.reset()
            self._state.reset()

    def _draw_landmarks(self, frame: np.ndarray, gesture: str,
                        confidence: float) -> np.ndarray:
        hands_count = self._detector.get_hands_count()
        h, w = frame.shape[:2]
        connections = [
            (0, 1), (1, 2), (2, 3), (3, 4),
            (0, 5), (5, 6), (6, 7), (7, 8),
            (5, 9), (9, 10), (10, 11), (11, 12),
            (9, 13), (13, 14), (14, 15), (15, 16),
            (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
        ]
        for hand_idx in range(hands_count):
            landmarks = self._detector.get_landmarks(hand_idx)
            if landmarks is None:
                continue
            points = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
            for start, end in connections:
                cv2.line(frame, points[start], points[end], (0, 255, 0), 2)
            for x, y in points:
                cv2.circle(frame, (x, y), 4, (255, 255, 0), -1)
        if gesture != "NONE":
            cv2.putText(frame, f"Gesture: {gesture}  {confidence * 100:.0f}%",
                        (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        return frame
