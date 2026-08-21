from PyQt5.QtCore import Qt, QThread, pyqtSlot
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
    QInputDialog, QComboBox, QSplitter,
)

from ..camera.vision_worker import VisionWorker


class VisionLabWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("莲心视觉感知实验室")
        self.resize(1200, 720)
        self.setMinimumSize(1120, 720)
        self.thread = None
        self.worker = None
        self.checks = {}
        self.status = {}
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("莲心视觉感知实验室")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.video = QLabel("摄像头尚未启动")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setMinimumSize(560, 420)
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video.setObjectName("video")
        video_panel = QWidget()
        video_layout = QVBoxLayout(video_panel)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.addWidget(self.video)
        video_panel.setMinimumWidth(620)
        splitter.addWidget(log_panel)
        splitter.addWidget(video_panel)
        log_title = QLabel("实时事件日志")
        log_title.setObjectName("logTitle")
        log_layout.addWidget(log_title)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setMaximumHeight(190)
        self.log.setPlaceholderText("启动、功能切换、模型状态和识别事件会显示在这里")
        log_layout.addWidget(self.log, 1)
        log_panel.setMinimumWidth(280)

        side_widget = QWidget()
        side_widget.setMinimumWidth(360)
        side = QVBoxLayout(side_widget)
        feature_frame = QFrame()
        feature_layout = QVBoxLayout(feature_frame)
        feature_layout.addWidget(QLabel("功能开关"))
        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("人脸推理"))
        self.face_device = QComboBox()
        self.face_device.addItems(["CPU", "GPU"])
        self.face_device.currentTextChanged.connect(self._on_face_device_changed)
        device_row.addWidget(self.face_device)
        feature_layout.addLayout(device_row)
        for key, label in (("face", "人脸识别"), ("gesture", "手势识别"), ("companion", "陪伴检测")):
            check = QCheckBox(label)
            check.stateChanged.connect(lambda state, name=key: self._toggle(name, state))
            self.checks[key] = check
            feature_layout.addWidget(check)
        side.addWidget(feature_frame)

        status_frame = QFrame()
        status_layout = QGridLayout(status_frame)
        status_layout.addWidget(QLabel("当前状态"), 0, 0, 1, 2)
        for row, (key, label) in enumerate((("camera", "摄像头"), ("fps", "FPS"), ("hands", "双手"), ("gesture", "手势"), ("gesture_confidence", "手势置信度"), ("gesture_state", "手势状态"), ("face", "人脸"), ("face_count", "人脸数量"), ("face_confidence", "人脸置信度"), ("companion", "观测"), ("pose_confidence", "姿态置信度"), ("video_duration", "视频时间"), ("today_presence", "今日陪伴总时长"), ("today_sessions", "今日陪伴次数")), 1):
            value = QLabel("-")
            value.setObjectName("statusValue")
            self.status[key] = value
            key_label = QLabel(label)
            key_label.setObjectName("statusLabel")
            status_layout.addWidget(key_label, row, 0)
            status_layout.addWidget(value, row, 1)
        for key, label in (("model_gesture", "Model gesture"),
                           ("model_confidence", "Model confidence")):
            row_index = status_layout.rowCount()
            key_label = QLabel(label)
            key_label.setObjectName("statusLabel")
            value = QLabel("-")
            value.setObjectName("statusValue")
            status_layout.addWidget(key_label, row_index, 0)
            status_layout.addWidget(value, row_index, 1)
            self.status[key] = value
        side.addWidget(status_frame)
        side.addStretch()
        splitter.addWidget(side_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([300, 700, 380])
        layout.addWidget(splitter, 1)

        log_title = QLabel("实时事件日志")
        log_title.setObjectName("logTitle")
        buttons = QHBoxLayout()
        self.start_button = QPushButton("启动摄像头")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        clear = QPushButton("清空日志")
        clear.clicked.connect(self.log.clear)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        enroll = QPushButton("录入本人")
        enroll.clicked.connect(self._enroll_face)
        buttons.addWidget(enroll)
        buttons.addStretch()
        buttons.addWidget(clear)
        layout.addLayout(buttons)

    def _enroll_face(self):
        name, ok = QInputDialog.getText(self, "录入本人", "请输入用户称呼：")
        if not ok or not name.strip():
            return
        if self.worker is None:
            self._append("请先启动视觉实验室")
            return
        self.checks["face"].setChecked(True)
        self.worker.begin_face_enrollment(name)

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #10151d; color: #dbe4ee; }
            #title { font-size: 20px; font-weight: bold; padding: 4px; }
            #video, QFrame, QTextEdit { background: #19212d; border: 1px solid #334155; border-radius: 6px; }
            QFrame { padding: 8px; }
            #logTitle { color: #94a3b8; font-weight: bold; padding-top: 4px; }
            #statusLabel { color: #cbd5e1; font-size: 12px; }
            #statusValue { color: #f8fafc; font-size: 13px; font-weight: 600; }
            QFrame QLabel { color: #e2e8f0; }
            QCheckBox { padding: 8px 2px; }
            QPushButton { padding: 8px 22px; background: #334155; border: 0; border-radius: 4px; }
            QPushButton:hover { background: #475569; }
        """)

    def _toggle(self, name, state):
        enabled = bool(state)
        self._append(f"{'启用' if enabled else '关闭'}功能：{name}")
        if self.worker is not None:
            self.worker.set_feature_enabled(name, enabled)

    def _on_face_device_changed(self, device):
        self._append(f"选择人脸推理设备：{device}")
        if self.worker is not None:
            self.worker.set_face_device(device)

    def start(self):
        if self.thread is not None:
            return
        self.thread = QThread(self)
        self.worker = VisionWorker()
        self.worker.face_device = self.face_device.currentText()
        self.worker.face.set_device(self.worker.face_device)
        for name, check in self.checks.items():
            self.worker.enabled[name] = check.isChecked()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.frame_ready.connect(self._show_frame)
        self.worker.status_ready.connect(self._update_status)
        self.worker.event_ready.connect(self._append)
        self.worker.started.connect(self._started)
        self.worker.stopped.connect(self._stopped)
        self.worker.stopped.connect(self.thread.quit)
        self.thread.finished.connect(self._clear_thread)
        self.thread.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def stop(self):
        if self.worker is not None:
            self.worker.stop()

    @pyqtSlot(bool, str)
    def _started(self, ok, message):
        if ok:
            self._append("视觉实验室已启动，当前为第一阶段基础预览")
        else:
            QMessageBox.warning(self, "启动失败", message)
            self.stop()

    @pyqtSlot()
    def _stopped(self):
        self._append("视觉实验室已停止")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _clear_thread(self):
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None

    @pyqtSlot(object)
    def _show_frame(self, frame):
        rgb = frame[:, :, ::-1].copy()
        h, w = rgb.shape[:2]
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        self.video.setPixmap(QPixmap.fromImage(image).scaled(self.video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    @pyqtSlot(dict)
    def _update_status(self, data):
        for key, value in data.items():
            if key in self.status:
                if key == "video_duration":
                    value = f"{int(value) // 60:02d}:{int(value) % 60:02d}"
                elif key == "today_presence":
                    value = f"{int(value) // 3600:02d}:{int(value) % 3600 // 60:02d}"
                self.status[key].setText(str(value))

    def _append(self, message):
        self.log.append(message)

    def closeEvent(self, event):
        self.stop()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(3000)
        event.accept()
