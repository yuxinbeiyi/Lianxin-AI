"""Optional InsightFace adapter for the first face-detection stage."""

import json
import os
import site
import time
from pathlib import Path

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
EMBEDDING_PATH = DATA_DIR / "user_embedding.npy"
PROFILE_PATH = DATA_DIR / "user_profile.json"
INSIGHTFACE_ROOT = PROJECT_ROOT / "models" / "face"


class FaceFeature:
    INFERENCE_INTERVAL = 0.15  # About 6-7 face inferences per second.

    def __init__(self, device="CPU"):
        self.analysis = None
        self.error = ""
        self.initialized = False
        self.user_name = ""
        self.user_embedding = None
        self.enrolling = False
        self.enroll_name = ""
        self.enroll_embeddings = []
        self._load_profile()
        self.device = device.upper()
        self.provider = ""
        self._last_inference = 0.0
        self._last_status = {"face": "未检测到人脸", "face_count": 0,
                             "face_confidence": 0.0, "identity": "UNKNOWN"}
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
            if self.device == "GPU":
                self._prepare_cuda_dlls()
            from insightface.app import FaceAnalysis
            INSIGHTFACE_ROOT.mkdir(parents=True, exist_ok=True)
            requested = ["CPUExecutionProvider"]
            ctx_id = -1
            if self.device == "GPU":
                try:
                    import onnxruntime as ort
                    if "CUDAExecutionProvider" in ort.get_available_providers():
                        requested = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                        ctx_id = 0
                    else:
                        self.error = "onnxruntime-gpu 不可用，已回退 CPU"
                except Exception:
                    self.error = "无法检测 CUDA Provider，已回退 CPU"
            # InsightFace resolves models as ``root/models/<name>``.  The
            # reorganized lab stores the bundle at ``models/face/buffalo_l``.
            self.analysis = FaceAnalysis(name="face/buffalo_l",
                                          root=str(PROJECT_ROOT),
                                          providers=requested,
                                          allowed_modules=['detection', 'recognition'])
            self.analysis.prepare(ctx_id=ctx_id, det_size=(320, 320))
            models = self.analysis.models.values() if isinstance(self.analysis.models, dict) else self.analysis.models
            first_model = next(iter(models), None)
            self.provider = (first_model.session.get_providers()[0]
                             if first_model is not None else requested[-1])

            # 调试：输出已加载的模型
            if isinstance(self.analysis.models, dict):
                loaded = ', '.join(self.analysis.models.keys())
                print(f"[FaceFeature] 已加载模型: {loaded}")

            self.initialized = True
            self.error = ""
            return True
        except Exception as exc:
            self.error = str(exc)
            self.analysis = None
            self.initialized = False
            if self.device == "GPU":
                self.device = "CPU"
                self.error = f"GPU 初始化失败，已回退 CPU：{exc}"
                return self.start()
            return False

    def _prepare_cuda_dlls(self):
        roots = []
        for base in site.getsitepackages():
            roots.append(os.path.join(base, "Lib", "site-packages"))
            roots.append(base)
        names = ("cuda_runtime", "cudnn", "cublas", "cuda_nvrtc",
                 "cuda_cupti", "cufft")
        paths = []
        for root in roots:
            for name in names:
                path = os.path.join(root, "nvidia", name, "bin")
                if os.path.isdir(path) and path not in paths:
                    paths.append(path)
        handles = []
        for path in paths:
            try:
                handles.append(os.add_dll_directory(path))
            except (FileNotFoundError, OSError):
                pass
        self._cuda_dll_handles = handles
        if paths:
            os.environ["PATH"] = ";".join(paths) + ";" + os.environ.get("PATH", "")

    def set_device(self, device):
        device = device.upper()
        if device != self.device and self.initialized:
            self.stop()
        self.device = device

    def process(self, frame):
        if not self.initialized or self.analysis is None:
            return frame, {"face": "未初始化", "face_count": 0,
                            "face_confidence": 0.0, "identity": "UNKNOWN"}
        now = time.monotonic()

        # 动态调整推理间隔：检测到人脸时快（0.15s，~6.7 FPS），无人时慢（0.33s，~3 FPS）
        interval = 0.15 if self._last_status.get("face_count", 0) > 0 else 0.33

        if now - self._last_inference < interval:
            self._draw_boxes(frame)
            return frame, dict(self._last_status)
        try:
            self._last_inference = now
            faces = self.analysis.get(frame)
            count = len(faces)
            confidence = max((float(face.det_score) for face in faces), default=0.0)
            self._last_boxes = []
            for face in faces:
                is_user = (self.user_embedding is not None and
                           self._similarity(face.embedding) >= 0.45)
                label = self.user_name if is_user else "陌生人"
                box = np.asarray(face.bbox, dtype=np.int32).tolist()
                self._last_boxes.append((box, label, is_user))
            identity = "UNKNOWN"
            if self.enrolling:
                if faces:
                    self.enroll_embeddings.append(np.asarray(faces[0].embedding, dtype=np.float32))
                state = f"录入中 {len(self.enroll_embeddings)}/15"
                if len(self.enroll_embeddings) >= 15:
                    self._finish_enrollment()
                    state = f"已录入 {self.user_name}"
                    identity = "USER"
            elif count == 0:
                state = "未检测到人脸"
            elif self.user_embedding is None:
                state = "检测到人脸（未录入本人）"
                identity = "STRANGER"
            else:
                similarity = self._similarity(faces[0].embedding)
                state = (f"本人：{self.user_name}" if similarity >= 0.45
                         else "陌生人")
                identity = "USER" if similarity >= 0.45 else "STRANGER"
            self._last_status = {"face": state, "face_count": count,
                                 "face_confidence": round(confidence, 2),
                                 "identity": identity}
            self._draw_boxes(frame)
            return frame, dict(self._last_status)
        except Exception as exc:
            self.error = str(exc)
            self._last_status = {"face": "检测异常", "face_count": 0,
                                 "face_confidence": 0.0, "identity": "UNKNOWN"}
            return frame, dict(self._last_status)

    def _draw_boxes(self, frame):
        labels = []
        for box, label, is_user in self._last_boxes:
            x1, y1, x2, y2 = box
            color = (80, 220, 100) if is_user else (80, 220, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            baseline = max(22, y1)
            labels.append((x1, baseline, label, color))
        if not labels:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        draw = ImageDraw.Draw(image)
        font_path = next((path for path in (
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
        ) if __import__("os").path.exists(path)), None)
        font = ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default()
        for x1, baseline, label, color in labels:
            text_color = (20, 25, 30)
            left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
            width = max(70, right - left + 12)
            draw.rectangle((x1, baseline - 24, x1 + width, baseline), fill=color[::-1])
            draw.text((x1 + 6, baseline - 22), label, font=font, fill=text_color)
        frame[:] = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)

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
