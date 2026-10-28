"""新版界面的视觉感知常驻运行时。

把 VisionLab 的采集/推理引擎放进 api_server 进程，供 React 面板通过
``/api/vision/*`` 控制；事件通过回调交给 ``brain.vision_reactions``。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VISIONLAB_ROOT = PROJECT_ROOT / "VisionLab"
CONFIG_DIR = Path.home() / ".lianxin"
FACE_CONFIG_PATH = CONFIG_DIR / "vision_config.json"
GESTURE_CONFIG_PATH = CONFIG_DIR / "gesture_config.json"

REACTION_EVENTS = {
    "USER_ENTER", "USER_LEAVE", "LONG_WORK", "STRANGER_PERSISTING",
    "GESTURE_OK", "GESTURE_THUMBS_UP", "GESTURE_WAVE",
}

_JPEG_QUALITY = 70
_MAX_EVENTS = 200
_runtime = None
_runtime_lock = threading.Lock()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_vision_settings() -> dict:
    """读取旧面板留下的配置，保证升级后开关和冷却不丢。"""
    face_config = _read_json(FACE_CONFIG_PATH)
    gesture_config = _read_json(GESTURE_CONFIG_PATH)
    features = face_config.get("features") or {}
    return {
        "device": str(face_config.get("face_device") or "CPU").upper(),
        "cooldown": max(3, min(30, int(gesture_config.get("cooldown_seconds") or 7))),
        "features": {
            "face": bool(features.get("face", False)),
            "gesture": bool(features.get("gesture", False)),
            "companion": bool(features.get("companion", False)),
        },
    }


def _save_vision_settings(settings: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        face_config = _read_json(FACE_CONFIG_PATH)
        face_config["face_device"] = settings["device"]
        face_config["features"] = dict(settings["features"])
        FACE_CONFIG_PATH.write_text(json.dumps(face_config, ensure_ascii=False, indent=2), encoding="utf-8")
        gesture_config = _read_json(GESTURE_CONFIG_PATH)
        gesture_config["cooldown_seconds"] = int(settings["cooldown"])
        GESTURE_CONFIG_PATH.write_text(json.dumps(gesture_config, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[视觉运行时] 保存配置失败: {exc}", flush=True)


class VisionRuntime:
    def __init__(self, on_event=None):
        self.on_event = on_event
        self._lock = threading.RLock()
        self._worker = None
        self._thread = None
        self._phase = "idle"  # idle | loading | running | error
        self._error = ""
        self._status = {}
        self._frame = None
        self._jpeg = b""
        self._frame_at = 0.0
        self._events = deque(maxlen=_MAX_EVENTS)
        self._event_seq = 0
        self._stop_requested = False
        self._tracking = None
        self._tracking_active = False
        self._tracking_simulated = False
        self._tracking_message = ""
        self._tracking_status = ""
        self._tracking_jpeg = b""
        self._tracking_frame_at = 0.0
        self._tracking_stop = threading.Event()
        self._settings = load_vision_settings()

    # ── 查询 ──────────────────────────────────────────────

    def state(self) -> dict:
        with self._lock:
            settings = {
                "features": dict(self._settings["features"]),
                "device": self._settings["device"],
                "cooldown": self._settings["cooldown"],
            }
            status = dict(self._status)
            phase = self._phase
            error = self._error
            frame_at = self._frame_at
            tracking = {
                "active": self._tracking_active,
                "simulated": self._tracking_simulated,
                "message": self._tracking_message,
                "status": self._tracking_status,
                "frameAt": self._tracking_frame_at,
            }
        return {
            "phase": phase,
            "running": phase == "running",
            "loading": phase == "loading",
            "error": error,
            **settings,
            "camera": status.get("camera", ""),
            "fps": status.get("fps", 0.0),
            "provider": status.get("provider", ""),
            "face": status.get("face", "未启用"),
            "faceCount": status.get("face_count", 0),
            "gesture": status.get("gesture", "未启用"),
            "gestureConfidence": status.get("gesture_confidence", 0.0),
            "companion": status.get("companion", "未启用"),
            "videoDuration": int(status.get("video_duration", 0) or 0),
            "todayPresence": int(status.get("today_presence", 0) or 0),
            "frameAt": frame_at,
            "tracking": tracking,
        }

    def events_after(self, after: int = 0) -> dict:
        with self._lock:
            items = [event for event in self._events if int(event["id"]) > int(after)]
        return {"items": items, "latest": items[-1]["id"] if items else int(after)}

    def latest_frame(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def latest_jpeg(self) -> bytes:
        with self._lock:
            return self._jpeg

    def is_active(self) -> bool:
        with self._lock:
            return self._phase in ("loading", "running")

    # ── 控制 ──────────────────────────────────────────────

    def start(self) -> dict:
        with self._lock:
            if self._phase in ("loading", "running"):
                return self.state()
            self._phase = "loading"
            self._error = ""
            self._status = {}
            self._stop_requested = False
        threading.Thread(target=self._load_and_run, name="vision-loader", daemon=True).start()
        return self.state()

    def stop(self) -> dict:
        with self._lock:
            self._stop_requested = True
            worker = self._worker
        if worker is not None:
            worker.stop()
        return self.state()

    def set_feature(self, name: str, enabled: bool) -> dict:
        if name not in ("face", "gesture", "companion"):
            raise ValueError("未知的视觉功能")
        with self._lock:
            self._settings["features"][name] = bool(enabled)
            self._persist()
            worker = self._worker
        if worker is not None:
            worker.submit_command("set_feature_enabled", name, bool(enabled))
        return self.state()

    def set_device(self, device: str) -> dict:
        device = str(device or "CPU").upper()
        if device not in ("CPU", "GPU"):
            raise ValueError("人脸推理设备只能是 CPU 或 GPU")
        with self._lock:
            self._settings["device"] = device
            self._persist()
            worker = self._worker
        if worker is not None:
            worker.submit_command("set_face_device", device)
        return self.state()

    def set_cooldown(self, seconds: int) -> dict:
        seconds = max(3, min(30, int(seconds)))
        with self._lock:
            self._settings["cooldown"] = seconds
            self._persist()
            worker = self._worker
        if worker is not None:
            worker.submit_command("set_gesture_cooldown", seconds)
        return self.state()

    def enroll(self, name: str) -> dict:
        name = str(name or "").strip()
        if not name:
            raise ValueError("称呼不能为空")
        with self._lock:
            worker = self._worker
            if worker is None:
                raise RuntimeError("请先启动视觉感知")
            # 与旧面板一致：录入前自动打开人脸识别，界面开关同步。
            self._settings["features"]["face"] = True
            self._persist()
        worker.submit_command("set_feature_enabled", "face", True)
        worker.submit_command("begin_face_enrollment", name)
        return {"ok": True, "name": name}

    # ── 肩载人脸追踪 ──────────────────────────────────────

    def start_tracking(self, mode: str) -> dict:
        mode = str(mode or "").strip().lower()
        if mode in ("off", "stop"):
            return self.stop_tracking()
        if mode not in ("esp32", "simulate"):
            raise ValueError("追踪模式只能是 esp32 / simulate / off")
        with self._lock:
            previous = self._tracking
            same_mode = (
                previous is not None
                and self._tracking_active
                and self._tracking_simulated == (mode == "simulate")
            )
        if same_mode:
            return self.state()
        if previous is not None:
            old_worker = getattr(previous, "worker", None)
            if old_worker is not None and old_worker.isRunning():
                previous.request_stop()
                deadline = time.time() + 3
                while old_worker.isRunning() and time.time() < deadline:
                    time.sleep(0.1)
                if old_worker.isRunning():
                    with self._lock:
                        self._tracking_message = "上一段追踪正在停止，请稍后重试"
                    return self.state()
        from PyQt5.QtCore import Qt

        from brain.face_tracking import FaceTrackingController

        controller = FaceTrackingController()
        controller.state_changed.connect(self._on_tracking_state, Qt.DirectConnection)
        controller.debug_message.connect(self._on_tracking_debug, Qt.DirectConnection)
        with self._lock:
            self._tracking = controller
            self._tracking_active = True
            self._tracking_simulated = mode == "simulate"
            self._tracking_message = "正在启动…"
            self._tracking_status = ""
            self._tracking_jpeg = b""
            self._tracking_frame_at = 0.0
            self._tracking_stop.clear()
        threading.Thread(target=self._tracking_loop, name="vision-tracking", daemon=True).start()
        controller.request_start(
            simulated=mode == "simulate",
            device=self._settings["device"],
            headless=True,
        )
        return self.state()

    def stop_tracking(self) -> dict:
        with self._lock:
            controller = self._tracking
            self._tracking_stop.set()
            self._tracking_active = False
            self._tracking_message = "已停止"
            self._tracking_jpeg = b""
            self._tracking_frame_at = 0.0
        if controller is not None:
            controller.request_stop()
        return self.state()

    def tracking_jpeg(self) -> bytes:
        with self._lock:
            return self._tracking_jpeg

    def _tracking_loop(self) -> None:
        while not self._tracking_stop.is_set():
            with self._lock:
                controller = self._tracking
            worker = getattr(controller, "worker", None)
            if worker is None:
                time.sleep(0.1)
                continue
            try:
                frame, result, status = worker.latest_preview()
                if frame is not None:
                    self._draw_tracking_overlay(frame, result)
                    ok, encoded = cv2.imencode(
                        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY],
                    )
                    with self._lock:
                        if ok:
                            self._tracking_jpeg = encoded.tobytes()
                        self._tracking_status = status
                        self._tracking_frame_at = time.time()
            except Exception as exc:
                print(f"[视觉运行时] 追踪预览失败: {exc}", flush=True)
            time.sleep(0.06)

    @staticmethod
    def _draw_tracking_overlay(frame, result) -> None:
        height, width = frame.shape[:2]
        center = (width // 2, height // 2)
        cv2.drawMarker(frame, center, (255, 255, 255), cv2.MARKER_CROSS, 24, 2)
        box = (result or {}).get("user_box")
        if box is not None:
            x1, y1, x2, y2 = [int(value) for value in box]
            target = ((x1 + x2) // 2, (y1 + y2) // 2)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 220, 100), 2)
            cv2.circle(frame, target, 5, (0, 0, 255), -1)
            cv2.circle(frame, center, 5, (0, 0, 255), -1)
            cv2.arrowedLine(frame, center, target, (0, 220, 255), 2, tipLength=0.15)
        overlay = (result or {}).get("overlay_status")
        if overlay:
            cv2.putText(frame, overlay, (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        0.65, (0, 255, 180), 2, cv2.LINE_AA)

    def _on_tracking_state(self, active, simulated, message) -> None:
        with self._lock:
            self._tracking_active = bool(active)
            self._tracking_simulated = bool(simulated)
            self._tracking_message = str(message or "")
            if not active:
                self._tracking_stop.set()
                self._tracking_jpeg = b""
                self._tracking_frame_at = 0.0
            elif message:
                self._tracking_message = str(message)

    def _on_tracking_debug(self, message) -> None:
        with self._lock:
            self._tracking_status = str(message or self._tracking_status)

    # ── 内部 ──────────────────────────────────────────────

    def _persist(self) -> None:
        _save_vision_settings(self._settings)

    def _load_and_run(self) -> None:
        try:
            if str(VISIONLAB_ROOT) not in sys.path:
                sys.path.insert(0, str(VISIONLAB_ROOT))
            from app.camera.vision_worker import VisionWorker
            from PyQt5.QtCore import Qt

            worker = VisionWorker()
            with self._lock:
                settings = {
                    "features": dict(self._settings["features"]),
                    "device": self._settings["device"],
                    "cooldown": self._settings["cooldown"],
                }
            worker.enabled = dict(settings["features"])
            worker.face_device = settings["device"]
            worker.face.set_device(settings["device"])
            worker.set_gesture_cooldown(settings["cooldown"])
            # api_server 没有运行 Qt 事件循环，排队的跨线程信号会丢失，
            # 这里显式直连，让帧/状态/事件在采集线程里同步回调。
            worker.frame_ready.connect(self._on_frame, Qt.DirectConnection)
            worker.status_ready.connect(self._on_status, Qt.DirectConnection)
            worker.event_ready.connect(self._on_event, Qt.DirectConnection)
            worker.started.connect(self._on_started, Qt.DirectConnection)
            worker.stopped.connect(self._on_stopped, Qt.DirectConnection)
            thread = threading.Thread(target=worker.run, name="vision-loop", daemon=True)
            with self._lock:
                if self._stop_requested:
                    self._phase = "idle"
                    self._worker = None
                    self._thread = None
                    return
                self._worker = worker
                self._thread = thread
            thread.start()
        except Exception as exc:
            message = f"{exc}\n{traceback.format_exc()}"
            print(f"[视觉运行时] 加载 VisionWorker 失败: {message}", flush=True)
            with self._lock:
                self._phase = "error"
                self._error = f"视觉模块加载失败：{exc}"
            self._append_log(self._error)

    def _on_frame(self, frame) -> None:
        try:
            frame = frame.copy()
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY])
            with self._lock:
                if ok:
                    self._jpeg = encoded.tobytes()
                self._frame = frame
                self._frame_at = time.time()
        except Exception as exc:
            print(f"[视觉运行时] 帧缓存失败: {exc}", flush=True)

    def _on_status(self, status) -> None:
        with self._lock:
            self._status = dict(status or {})

    def _on_started(self, success, message) -> None:
        with self._lock:
            if success:
                self._phase = "running"
                self._error = ""
            else:
                self._phase = "error"
                self._error = str(message or "视觉感知启动失败")
        if not success:
            self._append_log(self._error)

    def _on_stopped(self) -> None:
        with self._lock:
            if self._phase != "error":
                self._phase = "idle"
            self._worker = None
            self._thread = None
            self._status = {}
            self._frame = None
            self._jpeg = b""
            self._frame_at = 0.0

    def _on_event(self, event) -> None:
        event = str(event or "").strip()
        if not event:
            return
        self._append_log(event)
        name = event.split("|", 1)[0]
        if name not in REACTION_EVENTS or self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception as exc:
            print(f"[视觉运行时] 事件反应失败: {exc}", flush=True)

    def _append_log(self, message: str) -> None:
        with self._lock:
            self._event_seq += 1
            self._events.append({
                "id": self._event_seq,
                "type": message.split("|", 1)[0],
                "message": message,
                "timestamp": time.time(),
            })


def get_vision_runtime(on_event=None) -> VisionRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = VisionRuntime(on_event=on_event)
        elif on_event is not None:
            _runtime.on_event = on_event
        return _runtime


def peek_vision_runtime():
    """只读取已存在的运行时，避免观察工具意外创建实例。"""
    with _runtime_lock:
        return _runtime
