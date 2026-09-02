"""ESP32-CAM remote face tracking session.

The worker owns one long-lived WebSocket connection. Frames are processed in
the worker thread and only the rendered preview crosses into the Qt UI.
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PyQt5.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout, QSizePolicy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FACE_FEATURE_PATH = PROJECT_ROOT / "VisionLab"


@dataclass(frozen=True)
class FaceTrackingConfig:
    """初版现场校准参数，硬件测试时只需调整这里。"""

    pan_min: int = 20
    pan_max: int = 150
    tilt_min: int = 20
    tilt_max: int = 130
    pan_direction: int = 1
    tilt_direction: int = 1
    dead_zone_x: float = 0.08
    dead_zone_y: float = 0.08
    pan_gain: float = 42.0
    tilt_gain: float = 36.0
    max_pan_step: int = 8
    max_tilt_step: int = 7
    command_interval: float = 0.10
    prediction_seconds: float = 0.12


class GimbalController:
    """纯控制逻辑：不依赖 Qt、WebSocket 或摄像头，便于离线测试。"""

    def __init__(self, config: FaceTrackingConfig | None = None):
        self.config = config or FaceTrackingConfig()
        self.pan = 90
        self.tilt = 45
        self.last_command_at = 0.0

    def update(self, error, width: int, height: int, now: float) -> tuple[int, int] | None:
        if now - self.last_command_at < self.config.command_interval:
            return None
        nx = float(error[0]) / max(width, 1)
        ny = float(error[1]) / max(height, 1)
        cfg = self.config
        step_pan = 0 if abs(nx) < cfg.dead_zone_x else int(
            np.clip(nx * cfg.pan_gain * cfg.pan_direction,
                    -cfg.max_pan_step, cfg.max_pan_step)
        )
        step_tilt = 0 if abs(ny) < cfg.dead_zone_y else int(
            np.clip(ny * cfg.tilt_gain * cfg.tilt_direction,
                    -cfg.max_tilt_step, cfg.max_tilt_step)
        )
        if not step_pan and not step_tilt:
            return None
        self.pan = int(np.clip(self.pan + step_pan, cfg.pan_min, cfg.pan_max))
        self.tilt = int(np.clip(self.tilt + step_tilt, cfg.tilt_min, cfg.tilt_max))
        self.last_command_at = now
        return self.pan, self.tilt

    def reset(self):
        self.pan, self.tilt = 90, 45
        self.last_command_at = 0.0


class FrameCaptureThread(QThread):
    """采集线程：只产生最新视频帧，不执行人脸推理。"""

    frame_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, simulated=False, parent=None):
        super().__init__(parent)
        self.simulated = simulated
        self._stop_requested = False
        self._commands = queue.Queue()
        self._bridge = None
        self.error_message = ""
        self.ready_event = threading.Event()

    def stop(self):
        self._stop_requested = True

    def send_command(self, command):
        if not self.simulated:
            self._commands.put(command)

    def run(self):
        try:
            if self.simulated:
                if str(FACE_FEATURE_PATH) not in sys.path:
                    sys.path.insert(0, str(FACE_FEATURE_PATH))
                from app.camera.camera_manager import CameraManager
                self._camera = CameraManager(index=0, width=1280, height=720, fps=30)
                if not self._camera.start():
                    self.error_message = self._camera.error
                    self.failed.emit(f"本机模拟摄像头启动失败：{self.error_message}")
                    return
                self.ready_event.set()
                while not self._stop_requested:
                    frame = self._camera.read()
                    if frame is not None:
                        self.frame_ready.emit(frame)
                self._camera.stop()
                return

            from brain.hardware_bridge import HardwareBridge
            self._bridge = HardwareBridge()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._remote_loop(loop))
            loop.close()
        except Exception as exc:
            self.error_message = str(exc)
            self.failed.emit(f"视频采集异常：{exc}")
        finally:
            self.ready_event.set()

    async def _remote_loop(self, loop):
        if not await self._bridge.connect():
            self.error_message = "ESP32-CAM 不在线"
            self.failed.emit("肩载设备连接失败：ESP32-CAM 不在线")
            return
        ack = await self._bridge._send_cmd("track_start", timeout_sec=10)
        if not ack or "track" not in str(ack):
            self.error_message = "ESP32-CAM 未能启动视频推流"
            self.failed.emit(self.error_message)
            return
        self.ready_event.set()
        # Keep one recv task alive. Cancelling recv() with wait_for() while a
        # fragmented JPEG is being assembled can corrupt websockets' frame
        # queue and raise "cannot reset() while queue isn't empty".
        recv_task = asyncio.create_task(self._bridge.ws.recv())
        try:
            while not self._stop_requested and self._bridge.ws:
                while True:
                    try:
                        command = self._commands.get_nowait()
                    except queue.Empty:
                        break
                    await self._bridge.send_cmd_tracking(command)
                done, _ = await asyncio.wait({recv_task}, timeout=0.01)
                if not done:
                    continue
                payload = recv_task.result()
                recv_task = asyncio.create_task(self._bridge.ws.recv())
                if isinstance(payload, bytes):
                    frame = cv2.imdecode(
                        np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if frame is not None:
                        self.frame_ready.emit(frame)
        finally:
            if self._bridge and self._bridge.ws:
                await self._bridge.send_cmd_tracking("track_stop")
                await self._bridge.send_cmd_tracking("servo 90 45")
                ws = self._bridge.ws
                await ws.close()
                try:
                    await recv_task
                except BaseException:
                    pass
                await self._bridge.disconnect()


class FaceTrackingWorker(QThread):
    raw_frame_ready = pyqtSignal(object)
    result_ready = pyqtSignal(object)
    frame_ready = pyqtSignal(object)
    status_ready = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished_reason = pyqtSignal(str)
    command_debug = pyqtSignal(str)

    def __init__(self, device="CPU", simulated=False, parent=None):
        super().__init__(parent)
        self.device = device
        self.simulated = simulated
        self._stop_requested = False
        self._bridge = None
        self._loop = None
        self._controller = GimbalController()
        self._pan = self._controller.pan
        self._tilt = self._controller.tilt
        self._last_face = None
        self._velocity = np.zeros(2, dtype=np.float32)
        self._confirmed = 0
        self._lost_since = None
        self._last_frame_time = time.monotonic()
        self._frame_count = 0
        self._fps_started = self._last_frame_time
        self._capture = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._latest_seq = 0
        self._processed_seq = 0
        self._latest_result = None
        self._latest_status = "正在启动..."
        self._result_lock = threading.Lock()

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if str(FACE_FEATURE_PATH) not in sys.path:
                sys.path.insert(0, str(FACE_FEATURE_PATH))
            from app.features.face_feature import FaceFeature

            face = FaceFeature(self.device)
            if not face.start():
                self.failed.emit(f"人脸模型启动失败：{face.error}")
                return

            self._capture = FrameCaptureThread(simulated=self.simulated)
            self._capture.frame_ready.connect(
                self._on_capture_frame, Qt.DirectConnection
            )
            self._capture.failed.connect(self.failed, Qt.DirectConnection)
            self._capture.start()
            deadline = time.monotonic() + 12
            while not self._capture.ready_event.wait(0.05):
                if time.monotonic() >= deadline:
                    self.failed.emit("视频采集启动超时")
                    self._stop_requested = True
                    break
            if self._stop_requested or not self._capture.isRunning():
                return

            provider = getattr(face, "provider", "CPU") or "CPU"
            from brain.runtime_status import set_status
            set_status("face_tracking", running=True, health="正常", provider=provider,
                       status="追踪中", last_activity_summary="人脸追踪已启动")
            self.status_ready.emit(
                ("本机模拟追踪已启动" if self.simulated else "追踪已启动")
                + f"（{provider}），等待本人出现"
            )
            while not self._stop_requested:
                frame, seq = self._get_latest_frame()
                if frame is None or seq == self._processed_seq:
                    time.sleep(0.003)
                    continue
                self._processed_seq = seq
                rendered, state, result = self._process_frame(face, frame.copy())
                # The UI polls the latest result. Emitting one Qt event per
                # processed frame can outpace the render timer and retain old
                # NumPy images in the main-thread event queue.
                with self._result_lock:
                    self._latest_result = result
                    self._latest_status = state
        except Exception as exc:
            self.failed.emit(f"人脸追踪异常：{exc}")
        finally:
            try:
                from brain.runtime_status import update_status
                update_status("face_tracking", running=False, health="正常", status="已停止",
                              last_activity_summary="人脸追踪已停止")
            except Exception:
                pass
            if self._capture is not None:
                self._capture.stop()
                self._capture.wait(4000)
            self.finished_reason.emit(
                "本机模拟追踪已停止" if self.simulated else "人脸追踪已停止，云台已回中"
            )

    def _on_capture_frame(self, frame):
        with self._frame_lock:
            self._latest_frame = frame
            self._latest_seq += 1

    def _get_latest_frame(self):
        with self._frame_lock:
            if self._latest_frame is None:
                return None, self._latest_seq
            return self._latest_frame, self._latest_seq

    def latest_preview(self):
        """Return a snapshot of the newest frame and recognition result."""
        with self._frame_lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()
        with self._result_lock:
            result = dict(self._latest_result or {})
            status = self._latest_status
        return frame, result, status

    def _process_frame(self, face, frame):
        height, width = frame.shape[:2]
        center = np.array([width / 2.0, height / 2.0], dtype=np.float32)
        _, face_status = face.process(frame)

        user_box = None
        for box, _label, is_user in getattr(face, "_last_boxes", []):
            if is_user:
                user_box = np.asarray(box, dtype=np.float32)
                break

        now = time.monotonic()
        self._frame_count += 1
        if user_box is not None:
            target = np.array([(user_box[0] + user_box[2]) / 2,
                               (user_box[1] + user_box[3]) / 2], dtype=np.float32)
            if self._last_face is not None:
                dt = max(0.01, now - self._last_frame_time)
                raw_velocity = (target - self._last_face) / dt
                self._velocity = self._velocity * 0.65 + raw_velocity * 0.35
            self._last_face = target
            self._last_frame_time = now
            self._confirmed += 1
            self._lost_since = None
            if self._confirmed >= 3:
                # A small, bounded prediction compensates network and servo latency.
                predicted = target + np.clip(
                    self._velocity * self._controller.config.prediction_seconds,
                    -width * 0.12, width * 0.12,
                )
                self._apply_control(predicted - center, width, height, now)
            fps = self._frame_count / max(now - self._fps_started, 0.001)
            status = f"本人追踪中  Pan={self._pan} Tilt={self._tilt}  {fps:.1f} FPS"
            overlay_status = f"USER TRACKING  Pan={self._pan} Tilt={self._tilt}  {fps:.1f} FPS"
        else:
            self._confirmed = 0
            self._last_face = None
            self._velocity *= 0.5
            if self._lost_since is None:
                self._lost_since = now
            lost = now - self._lost_since
            status = "短暂丢失，保持云台" if lost < 1.5 else "未检测到本人，已停止修正"
            overlay_status = "TARGET LOST - HOLD" if lost < 1.5 else "NO USER - CONTROL STOPPED"

        cv2.drawMarker(frame, (int(center[0]), int(center[1])), (255, 255, 255),
                       cv2.MARKER_CROSS, 24, 2)
        if user_box is not None:
            target_int = tuple(np.round((user_box[:2] + user_box[2:]) / 2).astype(int))
            cv2.circle(frame, target_int, 5, (0, 255, 0), -1)
            cv2.arrowedLine(frame, (int(center[0]), int(center[1])), target_int,
                            (0, 220, 255), 2, tipLength=0.15)
        cv2.putText(frame, overlay_status, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                    (0, 255, 180), 2, cv2.LINE_AA)
        result = {
            "user_box": user_box.tolist() if user_box is not None else None,
            "width": width,
            "height": height,
            "status": status,
            "overlay_status": overlay_status,
        }
        return frame, status, result

    def _apply_control(self, error, width, height, now):
        if self.simulated and self._frame_count % 5 == 0:
            self.command_debug.emit(
                f"模拟误差 error=({float(error[0]) / max(width, 1):+.2f},"
                f"{float(error[1]) / max(height, 1):+.2f})"
            )
        target = self._controller.update(error, width, height, now)
        if target is None:
            return
        self._pan, self._tilt = target
        if self.simulated:
            self.command_debug.emit(
                f"模拟舵机 servo {self._pan} {self._tilt}"
            )
            return
        if self._capture is not None:
            self._capture.send_command(f"servo {self._pan} {self._tilt}")


class FaceTrackingWindow(QDialog):
    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self._worker = worker
        self.setWindowTitle("莲心 · 肩载人脸追踪")
        self.resize(760, 580)
        self._video = QLabel("等待 ESP32-CAM 视频...")
        self._video.setAlignment(Qt.AlignCenter)
        # Do not let QLabel's pixmap size hint resize the parent window.
        self._video.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._video.setMinimumSize(320, 240)
        self._last_pixmap = QPixmap()
        self._latest_frame = None
        self._latest_result = None
        self._status = QLabel("正在启动...")
        layout = QVBoxLayout(self)
        layout.addWidget(self._video, 1)
        layout.addWidget(self._status)
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(33)
        self._render_timer.timeout.connect(self._render_video)
        self._render_timer.start()

    def closeEvent(self, event):
        # Closing the preview must also stop the remote stream and worker.
        if hasattr(self, "_worker"):
            self._worker.stop()
        self._render_timer.stop()
        event.accept()

    def _render_video(self):
        frame, result, status = self._worker.latest_preview()
        if frame is None:
            return
        self._status.setText(status)
        height, width = frame.shape[:2]
        center = (width // 2, height // 2)
        cv2.drawMarker(frame, center, (255, 255, 255), cv2.MARKER_CROSS, 24, 2)
        box = result.get("user_box")
        if box is not None:
            x1, y1, x2, y2 = [int(v) for v in box]
            target = ((x1 + x2) // 2, (y1 + y2) // 2)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 220, 100), 2)
            cv2.circle(frame, target, 5, (0, 0, 255), -1)
            cv2.circle(frame, center, 5, (0, 0, 255), -1)
            cv2.arrowedLine(frame, center, target, (0, 220, 255), 2, tipLength=0.15)
        overlay = result.get("overlay_status")
        if overlay:
            cv2.putText(frame, overlay, (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        0.65, (0, 255, 180), 2, cv2.LINE_AA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self._last_pixmap = QPixmap.fromImage(image)
        self._resize_video()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_video()

    def _resize_video(self):
        if not self._last_pixmap.isNull():
            self._video.setPixmap(self._last_pixmap.scaled(
                self._video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


class FaceTrackingController(QObject):
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    state_changed = pyqtSignal(bool, bool, str)
    debug_message = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.worker = None
        self.window = None
        self.start_requested.connect(self._start)
        self.stop_requested.connect(self._stop)

    def request_start(self, simulated=False, device="CPU"):
        if self.worker is not None and self.worker.isRunning():
            return False
        self._requested_simulated = simulated
        self._requested_device = device
        self.start_requested.emit()
        return True

    def request_stop(self):
        self.stop_requested.emit()
        return True

    @pyqtSlot()
    def _start(self):
        if self.worker is not None and self.worker.isRunning():
            return
        self.worker = FaceTrackingWorker(
            device=getattr(self, "_requested_device", "CPU"),
            simulated=getattr(self, "_requested_simulated", False),
        )
        self.window = FaceTrackingWindow(self.worker)
        self.worker.command_debug.connect(self._on_command_debug)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished_reason.connect(self._on_finished)
        self.worker.finished.connect(self._on_thread_finished)
        self.window.show()
        self.worker.start()
        self.state_changed.emit(True, self.worker.simulated, "人脸追踪窗口已启动")

    @pyqtSlot()
    def _stop(self):
        if self.worker is not None:
            self.worker.stop()

    def _on_failed(self, message):
        self.state_changed.emit(False, False, message)

    def _on_finished(self, message):
        self.state_changed.emit(False, False, message)

    def _on_command_debug(self, message):
        self.debug_message.emit(message)

    def _on_thread_finished(self):
        if self.window:
            self.window.close()
            self.window.deleteLater()
        if self.worker:
            self.worker.deleteLater()
        self.window = None
        self.worker = None


_controller = None


def get_face_tracking_controller():
    global _controller
    app = QApplication.instance()
    if app is None:
        return None
    if _controller is None:
        _controller = FaceTrackingController()
        _controller.moveToThread(app.thread())
    return _controller
