"""Adapter around the already-tested HandsDetector pipeline."""

import cv2

try:
    from .gesture.gesture_event import GESTURE_NONE
    from .gesture.gesture_classifier import GestureClassifier
    from .gesture.gesture_state import GestureState
    from .gesture.hand_detector import HandDetector
    _IMPORT_ERROR = ""
except Exception as exc:  # Optional feature dependency.
    GESTURE_NONE = "NONE"
    HandDetector = GestureClassifier = GestureState = None
    _IMPORT_ERROR = str(exc)


class GestureFeature:
    def __init__(self):
        self.detector = HandDetector() if HandDetector else None
        self.classifier = GestureClassifier() if GestureClassifier else None
        self.state = GestureState() if GestureState else None
        self.error = _IMPORT_ERROR
        self.initialized = False

    def start(self):
        if self.initialized:
            return True
        if self.detector is None:
            return False
        self.initialized = self.detector.initialize()
        if not self.initialized:
            self.error = self.detector.init_error or "手势识别初始化失败"
        else:
            self.classifier.reset()
            self.state.reset()
        return self.initialized

    def process(self, frame):
        if not self.initialized:
            return frame, self._empty_status()
        self.detector.detect(frame)
        landmarks = self.detector.get_landmarks(0)
        hands = self.detector.get_hands_count()
        gesture, confidence = self.classifier.update(landmarks)
        self.state.update(gesture, confidence)
        info = self.state.get_info()
        self._draw(frame, gesture, confidence)
        return frame, {
            "gesture": gesture,
            "gesture_confidence": round(confidence, 2),
            "hands": hands,
            "gesture_state": info.state,
        }

    def stop(self):
        self.detector.close()
        self.initialized = False

    @staticmethod
    def _empty_status():
        return {"gesture": GESTURE_NONE, "gesture_confidence": 0.0,
                "hands": 0, "gesture_state": "READY"}

    def _draw(self, frame, gesture, confidence):
        connections = [
            (0, 1), (1, 2), (2, 3), (3, 4),
            (0, 5), (5, 6), (6, 7), (7, 8),
            (5, 9), (9, 10), (10, 11), (11, 12),
            (9, 13), (13, 14), (14, 15), (15, 16),
            (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
        ]
        for hand_index in range(self.detector.get_hands_count()):
            landmarks = self.detector.get_landmarks(hand_index)
            if landmarks is None:
                continue
            h, w = frame.shape[:2]
            points = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
            for start, end in connections:
                cv2.line(frame, points[start], points[end], (0, 220, 120), 2)
            for point in points:
                cv2.circle(frame, point, 4, (255, 240, 80), -1)
        if gesture != GESTURE_NONE:
            cv2.putText(frame, f"Gesture: {gesture} {confidence * 100:.0f}%",
                        (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (80, 255, 120), 2)
