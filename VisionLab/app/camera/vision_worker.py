import time

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from .camera_manager import CameraManager
from ..features.gesture_feature import GestureFeature
from ..features.face_feature import FaceFeature
from ..features.companion_feature import CompanionFeature
from ..features.pose_feature import PoseFeature
from pathlib import Path
from ..storage.vision_database import VisionDatabase


class VisionWorker(QObject):
    frame_ready = pyqtSignal(object)
    status_ready = pyqtSignal(dict)
    event_ready = pyqtSignal(str)
    started = pyqtSignal(bool, str)
    stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.camera = CameraManager()
        self.enabled = {"face": False, "gesture": False, "companion": False}
        self.gesture = GestureFeature()
        self.face_device = "CPU"
        self.face = FaceFeature(self.face_device)
        self.companion = CompanionFeature()
        self.pose = PoseFeature()
        self.database = VisionDatabase(Path(__file__).resolve().parents[2] / "data" / "vision.db")
        self._stop = False

    @pyqtSlot()
    def run(self):
        if not self.camera.start():
            self.started.emit(False, self.camera.error)
            self.stopped.emit()
            return
        self.database.open()
        if self.enabled["gesture"] and not self.gesture.start():
            self.event_ready.emit(f"手势识别不可用：{self.gesture.error}")
            self.enabled["gesture"] = False
        if self.enabled["face"] and not self.face.start():
            self.event_ready.emit(f"人脸识别不可用：{self.face.error}")
            self.enabled["face"] = False
        elif self.enabled["face"]:
            self.event_ready.emit(f"人脸推理设备：{self.face.provider or 'CPU'}")
        if self.enabled["companion"] and not self.pose.start():
            self.event_ready.emit(f"姿态检测不可用：{self.pose.error}")
        self._stop = False
        self.started.emit(True, "")
        while not self._stop:
            started = time.monotonic()
            frame = self.camera.read()
            if frame is None:
                continue
            gesture_status = {"gesture": "NONE", "gesture_confidence": 0.0,
                              "hands": 0, "gesture_state": "READY"}
            face_status = {"face": "未启用", "face_count": 0,
                           "face_confidence": 0.0}
            companion_state = "未启用"
            work_duration = 0.0
            if self.enabled["companion"]:
                frame, pose_status = self.pose.process(frame)
                present = pose_status["pose_present"] if self.pose.initialized else face_status["face_count"] > 0
                identity = face_status.get("identity", "UNKNOWN") if present else "UNKNOWN"
                companion_state, events, work_duration = self.companion.update(
                    1 if present else 0, identity)
                for event in events:
                    self.event_ready.emit(event)
                    self.database.record_event(event)
                    if event in ("USER_ENTER", "USER_RETURN"):
                        self.database.add_presence_time(0, session_started=True)
                    elif event == "USER_LEAVE":
                        self.database.add_presence_time(work_duration, left_at=None)
            if self.enabled["face"]:
                frame, face_status = self.face.process(frame)
            if self.enabled["gesture"]:
                frame, gesture_status = self.gesture.process(frame)
                # 手势事件触发：检查 should_trigger 标志
                if gesture_status.get("should_trigger", False) and gesture_status["gesture"] != "NONE":
                    event = f"GESTURE_{gesture_status['gesture']}"
                    self.event_ready.emit(event)
                    self.database.record_event(event)
            self.frame_ready.emit(frame)
            status = {
                "camera": f"{self.camera.width}x{self.camera.height}",
                "fps": round(self.camera.fps, 1),
                "face": "未启用" if not self.enabled["face"] else "待接入",
                "gesture": "未启用" if not self.enabled["gesture"] else gesture_status["gesture"],
                "gesture_confidence": gesture_status["gesture_confidence"],
                "hands": gesture_status["hands"],
                "gesture_state": gesture_status["gesture_state"],
                "face": face_status["face"],
                "face_count": face_status["face_count"],
                "face_confidence": face_status["face_confidence"],
                "companion": companion_state,
                "work_duration": round(work_duration),
                "pose_confidence": pose_status["pose_confidence"] if self.enabled["companion"] else 0.0,
            }
            summary = self.database.today_summary()
            status["today_presence"] = round(summary["total_seconds"])
            status["today_sessions"] = summary["session_count"]
            self.status_ready.emit(status)
            remaining = 1 / 30 - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
        self.camera.stop()
        self.gesture.stop()
        self.face.stop()
        self.companion.reset()
        self.database.close()
        self.pose.stop()
        self.stopped.emit()

    @pyqtSlot()
    def stop(self):
        self._stop = True

    @pyqtSlot(int)
    def set_gesture_cooldown(self, seconds: int):
        """设置手势冷却时间"""
        if hasattr(self.gesture, 'state') and self.gesture.state:
            self.gesture.state.cooldown = float(seconds)
            print(f"[VisionWorker] 手势冷却时间已设置为 {seconds} 秒")

    @pyqtSlot(str, bool)
    def set_feature_enabled(self, name, enabled):
        if name not in self.enabled:
            return

        self.enabled[name] = enabled

        if not self.camera.running:
            return

        # 启用功能
        if enabled:
            if name == "gesture":
                if not self.gesture.start():
                    self.event_ready.emit(f"手势识别启动失败：{self.gesture.error}")
                else:
                    self.event_ready.emit("✅ 手势识别已启用")
            elif name == "face":
                if not self.face.start():
                    self.event_ready.emit(f"人脸识别启动失败：{self.face.error}")
                else:
                    self.event_ready.emit(f"✅ 人脸识别已启用（{self.face.provider or 'CPU'}）")
            elif name == "companion":
                if not self.pose.start():
                    self.event_ready.emit(f"姿态检测启动失败：{self.pose.error}")
                else:
                    self.event_ready.emit("✅ 陪伴检测已启用")
        # 停用功能
        else:
            if name == "gesture":
                self.gesture.stop()
                self.event_ready.emit("⏸️ 手势识别已停用")
            elif name == "face":
                self.face.stop()
                self.event_ready.emit("⏸️ 人脸识别已停用")
            elif name == "companion":
                self.companion.reset()
                self.pose.stop()
                self.event_ready.emit("⏸️ 陪伴检测已停用")

    @pyqtSlot(str)
    def set_face_device(self, device):
        self.face_device = device.upper()
        self.face.set_device(self.face_device)
        if self.enabled["face"] and self.camera.running:
            if self.face.start():
                self.event_ready.emit(f"人脸推理设备：{self.face.provider or 'CPUExecutionProvider'}")
            else:
                self.event_ready.emit(f"人脸设备切换失败：{self.face.error}")

    @pyqtSlot(str)
    def begin_face_enrollment(self, name):
        if not self.enabled["face"]:
            self.enabled["face"] = self.face.start()
        if self.enabled["face"] and self.face.begin_enrollment(name):
            self.event_ready.emit(f"开始录入本人：{name}")
        else:
            self.event_ready.emit(f"无法开始录入本人：{self.face.error or '请先启动并启用人脸识别'}")
