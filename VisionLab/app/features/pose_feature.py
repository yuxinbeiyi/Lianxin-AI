"""Optional MediaPipe Pose Landmarker adapter for workstation presence."""

from pathlib import Path

import cv2

try:
    import mediapipe as mp
    _IMPORT_ERROR = ""
except Exception as exc:
    mp = None
    _IMPORT_ERROR = str(exc)


_VISIONLAB_ROOT = Path(__file__).resolve().parents[2]
_MODEL_CANDIDATES = (
    _VISIONLAB_ROOT / "models" / "pose" / "pose_landmarker_full.task",
)
MODEL_PATH = next((path for path in _MODEL_CANDIDATES if path.exists()),
                  _MODEL_CANDIDATES[0])


class PoseFeature:
    def __init__(self):
        self.landmarker = None
        self.error = ""
        self.initialized = False

    def start(self):
        if self.initialized:
            return True
        if mp is None:
            self.error = _IMPORT_ERROR
            return False
        if not MODEL_PATH.exists():
            self.error = f"姿态模型不存在：{MODEL_PATH}"
            return False
        try:
            options = mp.tasks.vision.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                num_poses=1,
                min_pose_detection_confidence=0.5,
                min_pose_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self.landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)
            self.initialized = True
            self.error = ""
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def process(self, frame):
        if not self.initialized:
            return frame, {"pose_present": False, "pose_confidence": 0.0}
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self.landmarker.detect(image)
            poses = result.pose_landmarks or []
            if not poses:
                return frame, {"pose_present": False, "pose_confidence": 0.0}
            landmarks = poses[0]
            visible = [float(point.visibility or 0.0) for point in landmarks]
            confidence = max(visible, default=0.0)
            return frame, {"pose_present": confidence >= 0.35,
                           "pose_confidence": round(confidence, 2)}
        except Exception as exc:
            self.error = str(exc)
            return frame, {"pose_present": False, "pose_confidence": 0.0}

    def stop(self):
        if self.landmarker is not None:
            self.landmarker.close()
        self.landmarker = None
        self.initialized = False
