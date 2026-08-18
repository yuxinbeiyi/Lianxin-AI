"""Optional InsightFace adapter for the first face-detection stage."""

import json
import time
from pathlib import Path

import numpy as np


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
EMBEDDING_PATH = DATA_DIR / "user_embedding.npy"
PROFILE_PATH = DATA_DIR / "user_profile.json"
INSIGHTFACE_ROOT = Path(__file__).resolve().parents[1] / "models" / "insightface"


class FaceFeature:
    INFERENCE_INTERVAL = 0.15  # About 6-7 face inferences per second.

    def __init__(self):
        self.analysis = None
        self.error = ""
        self.initialized = False
        self.user_name = ""
        self.user_embedding = None
        self.enrolling = False
        self.enroll_name = ""
        self.enroll_embeddings = []
        self._load_profile()
        self._last_inference = 0.0
        self._last_status = {"face": "未检测到人脸", "face_count": 0,
                             "face_confidence": 0.0}
        self._last_boxes = []

    def _load_profile(self):
        try:
            if EMBEDDING_PATH.exists() and PROFILE_PATH.exists():
                self.user_embedding = np.load(EMBEDDING_PATH)
                self.user_name = json.loads(PROFILE_PATH.read_text(encoding="utf-8")).get("name", "")
        except Exception:
            self.user_embedding = None
            self.user_name = ""

    def start(self):
        if self.initialized:
            return True
        try:
            from insightface.app import FaceAnalysis
            INSIGHTFACE_ROOT.mkdir(parents=True, exist_ok=True)
            self.analysis = FaceAnalysis(name="buffalo_l",
                                          root=str(INSIGHTFACE_ROOT),
                                          providers=["CPUExecutionProvider"])
            self.analysis.prepare(ctx_id=-1, det_size=(640, 640))
            self.initialized = True
            self.error = ""
            return True
        except Exception as exc:
            self.error = str(exc)
            self.analysis = None
            self.initialized = False
            return False

    def process(self, frame):
        if not self.initialized or self.analysis is None:
            return frame, {"face": "未初始化", "face_count": 0,
                            "face_confidence": 0.0}
        now = time.monotonic()
        if now - self._last_inference < self.INFERENCE_INTERVAL:
            self._draw_boxes(frame)
            return frame, dict(self._last_status)
        try:
            self._last_inference = now
            faces = self.analysis.get(frame)
            count = len(faces)
            confidence = max((float(face.det_score) for face in faces), default=0.0)
            self._last_boxes = [np.asarray(face.bbox, dtype=np.int32).tolist()
                                for face in faces]
            if self.enrolling:
                if faces:
                    self.enroll_embeddings.append(np.asarray(faces[0].embedding, dtype=np.float32))
                state = f"录入中 {len(self.enroll_embeddings)}/15"
                if len(self.enroll_embeddings) >= 15:
                    self._finish_enrollment()
                    state = f"已录入 {self.user_name}"
            elif count == 0:
                state = "未检测到人脸"
            elif self.user_embedding is None:
                state = "检测到人脸（未录入本人）"
            else:
                similarity = self._similarity(faces[0].embedding)
                state = (f"本人：{self.user_name}" if similarity >= 0.45
                         else "陌生人")
            self._last_status = {"face": state, "face_count": count,
                                 "face_confidence": round(confidence, 2)}
            self._draw_boxes(frame)
            return frame, dict(self._last_status)
        except Exception as exc:
            self.error = str(exc)
            self._last_status = {"face": "检测异常", "face_count": 0,
                                 "face_confidence": 0.0}
            return frame, dict(self._last_status)

    def _draw_boxes(self, frame):
        cv2 = __import__("cv2")
        for x1, y1, x2, y2 in self._last_boxes:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 220, 255), 2)

    def stop(self):
        self.analysis = None
        self.initialized = False

    def begin_enrollment(self, name):
        if not self.initialized or not name.strip():
            return False
        self.enrolling = True
        self.enroll_name = name.strip()
        self.enroll_embeddings = []
        return True

    def _finish_enrollment(self):
        embedding = np.mean(np.stack(self.enroll_embeddings), axis=0)
        embedding = embedding / max(np.linalg.norm(embedding), 1e-8)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        np.save(EMBEDDING_PATH, embedding)
        PROFILE_PATH.write_text(json.dumps({"name": self.enroll_name}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.user_embedding = embedding
        self.user_name = self.enroll_name
        self.enrolling = False

    def _similarity(self, embedding):
        current = np.asarray(embedding, dtype=np.float32)
        current = current / max(np.linalg.norm(current), 1e-8)
        saved = self.user_embedding / max(np.linalg.norm(self.user_embedding), 1e-8)
        return float(np.dot(current, saved))
