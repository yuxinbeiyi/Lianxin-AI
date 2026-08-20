"""
VisionPanel：莲心视觉感知面板（独立浮动窗口）
集成 VisionLab 的视觉识别能力，提供人脸识别、手势识别、陪伴检测功能。
支持多人识别、视觉事件触发情感响应、与主界面的深度联动。
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTextEdit, QGroupBox, QGridLayout, QWidget, QInputDialog, QMessageBox,
    QComboBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage, QPixmap
import sys
from pathlib import Path


class VisionPanel(QDialog):
    """莲心视觉感知面板"""

    # 视觉事件信号（发送给主界面）
    user_entered = pyqtSignal()
    user_returned = pyqtSignal()
    user_left = pyqtSignal()
    long_work = pyqtSignal()
    stranger_detected = pyqtSignal()
    gesture_ok = pyqtSignal()

    # 获取当前帧信号（供工具调用）
    frame_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("👁️ 莲心视觉感知")
        self.resize(1000, 700)
        self.setMinimumSize(900, 650)

        # 设置窗口标志：独立窗口，可最小化，可关闭
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowCloseButtonHint
        )

        self._thread = None
        self._worker = None
        self._current_frame = None  # 缓存当前帧，供"看看你面前的是谁"使用

        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        """构建UI布局"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        # 标题
        title = QLabel("👁️ 莲心视觉感知")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # 主体区域：摄像头 + 侧边栏
        main_row = QHBoxLayout()
        main_row.setSpacing(16)

        # 摄像头画面
        self.video_label = QLabel("摄像头尚未启动\n\n点击下方「启动」按钮开始视觉感知")
        self.video_label.setObjectName("videoLabel")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(560, 420)
        main_row.addWidget(self.video_label, 3)

        # 侧边栏
        sidebar = QWidget()
        sidebar.setFixedWidth(280)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setSpacing(16)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)

        # 功能开关区
        feature_group = QGroupBox("功能开关")
        feature_group.setObjectName("featureGroup")
        feature_layout = QVBoxLayout(feature_group)
        feature_layout.setSpacing(8)

        # 人脸推理设备选择
        device_row = QHBoxLayout()
        device_label = QLabel("人脸推理:")
        device_label.setObjectName("deviceLabel")
        self.face_device_combo = QComboBox()
        self.face_device_combo.addItems(["CPU", "GPU"])
        self.face_device_combo.setObjectName("deviceCombo")
        device_row.addWidget(device_label)
        device_row.addWidget(self.face_device_combo, 1)
        feature_layout.addLayout(device_row)

        self.check_face = QCheckBox("人脸识别")
        self.check_gesture = QCheckBox("手势识别")
        self.check_companion = QCheckBox("陪伴检测")

        # 连接热插拔信号
        self.check_face.stateChanged.connect(lambda state: self._toggle_feature("face", state == 2))
        self.check_gesture.stateChanged.connect(lambda state: self._toggle_feature("gesture", state == 2))
        self.check_companion.stateChanged.connect(lambda state: self._toggle_feature("companion", state == 2))

        feature_layout.addWidget(self.check_face)
        feature_layout.addWidget(self.check_gesture)
        feature_layout.addWidget(self.check_companion)
        sidebar_layout.addWidget(feature_group)

        # 状态区
        status_group = QGroupBox("当前状态")
        status_group.setObjectName("statusGroup")
        status_layout = QGridLayout(status_group)
        status_layout.setVerticalSpacing(8)
        status_layout.setHorizontalSpacing(12)

        self.status_labels = {}
        for row, (key, label_text) in enumerate([
            ("camera", "摄像头"),
            ("fps", "帧率"),
            ("face", "人脸"),
            ("gesture", "手势"),
            ("companion", "工位"),
            ("duration", "工作时长"),
        ]):
            label = QLabel(label_text + ":")
            label.setObjectName("statusLabel")
            value = QLabel("-")
            value.setObjectName("statusValue")
            self.status_labels[key] = value

            status_layout.addWidget(label, row, 0, Qt.AlignLeft)
            status_layout.addWidget(value, row, 1, Qt.AlignLeft)

        sidebar_layout.addWidget(status_group)
        sidebar_layout.addStretch()

        main_row.addWidget(sidebar, 1)
        layout.addLayout(main_row)

        # 日志区
        log_label = QLabel("实时事件日志")
        log_label.setObjectName("logLabel")
        layout.addWidget(log_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(140)
        self.log_text.setPlaceholderText(
            "视觉事件会显示在这里...\n"
            "例如：用户进入、离开、手势识别、陌生人警报等"
        )
        layout.addWidget(self.log_text)

        # 按钮区
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.btn_start = QPushButton("启动")
        self.btn_stop = QPushButton("停止")
        self.btn_enroll = QPushButton("录入本人")
        self.btn_add_friend = QPushButton("录入朋友")
        self.btn_clear = QPushButton("清空日志")

        self.btn_stop.setEnabled(False)

        self.btn_start.clicked.connect(self._start_vision)
        self.btn_stop.clicked.connect(self._stop_vision)
        self.btn_enroll.clicked.connect(self._enroll_self)
        self.btn_add_friend.clicked.connect(self._enroll_friend)
        self.btn_clear.clicked.connect(self.log_text.clear)

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addWidget(self.btn_enroll)
        btn_layout.addWidget(self.btn_add_friend)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_clear)

        layout.addLayout(btn_layout)

    def _apply_style(self):
        """应用莲心主题样式"""
        self.setStyleSheet("""
            QDialog {
                background-color: #0F1419;
                color: #E9EDF2;
            }

            #titleLabel {
                font-size: 20px;
                font-weight: bold;
                color: #FFFFFF;
                padding: 8px;
            }

            #logLabel {
                font-size: 13px;
                font-weight: 600;
                color: #B8C9E0;
                padding: 4px 0px;
            }

            QGroupBox {
                font-size: 14px;
                font-weight: 600;
                color: #DCEFE8;
                border: 1px solid #2D3748;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 8px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0px 8px;
                color: #75B8A8;
            }

            QCheckBox {
                font-size: 14px;
                color: #DCEFE8;
                padding: 10px 4px;
                spacing: 8px;
            }

            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }

            #deviceLabel {
                font-size: 13px;
                color: #B8C9E0;
            }

            #deviceCombo {
                font-size: 13px;
                background-color: #1A2330;
                color: #E9EDF2;
                border: 1px solid #2D3748;
                border-radius: 6px;
                padding: 6px 10px;
            }

            #statusLabel {
                font-size: 13px;
                font-weight: 600;
                color: #B8C9E0;
                padding: 6px 0px;
            }

            #statusValue {
                font-size: 13px;
                color: #E9EDF2;
                padding: 6px 0px;
            }

            QTextEdit {
                font-size: 11px;
                font-family: "Microsoft YaHei UI", "Consolas";
                background-color: #1A2330;
                border: 1px solid #2D3748;
                border-radius: 8px;
                padding: 8px;
                color: #CBD5E1;
                line-height: 1.6;
            }

            QPushButton {
                font-size: 14px;
                font-family: "Microsoft YaHei UI";
                background-color: #2A5148;
                color: #DCEFE8;
                border: 1px solid #416B63;
                border-radius: 10px;
                padding: 10px 20px;
                min-height: 40px;
                min-width: 100px;
            }

            QPushButton:hover {
                background-color: #347767;
                border-color: #75B8A8;
            }

            QPushButton:pressed {
                background-color: #1E3D35;
            }

            QPushButton:disabled {
                background-color: #1C2D29;
                color: #78918A;
                border: 1px solid #30463F;
            }

            #videoLabel {
                background-color: #0D1117;
                border: 1px solid #2D3748;
                border-radius: 10px;
                color: #6B7A90;
                font-size: 14px;
            }
        """)

    def _start_vision(self):
        """启动视觉识别"""
        if self._thread is not None:
            return

        # 动态导入 VisionWorker（避免循环导入）
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "VisionLab"))
            from app.camera.vision_worker import VisionWorker
        except Exception as e:
            self._append_log(f"❌ 加载 VisionWorker 失败：{e}")
            QMessageBox.critical(self, "加载失败", f"无法加载视觉模块：{e}")
            return

        self._thread = QThread(self)
        self._worker = VisionWorker()

        # 设置设备
        device = self.face_device_combo.currentText()
        self._worker.face_device = device
        self._worker.face.set_device(device)

        # 设置启用的功能
        self._worker.enabled = {
            "face": self.check_face.isChecked(),
            "gesture": self.check_gesture.isChecked(),
            "companion": self.check_companion.isChecked(),
        }

        self._worker.moveToThread(self._thread)

        # 连接信号
        self._thread.started.connect(self._worker.run)
        self._worker.frame_ready.connect(self._on_frame_ready)
        self._worker.status_ready.connect(self._on_status_ready)
        self._worker.event_ready.connect(self._on_event_ready)
        self._worker.started.connect(self._on_started)
        self._worker.stopped.connect(self._on_stopped)
        self._worker.stopped.connect(self._thread.quit)
        self._thread.finished.connect(self._clear_thread)

        # 启动线程
        self._thread.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def _stop_vision(self):
        """停止视觉识别"""
        if self._worker is not None:
            self._worker.stop()

    def _toggle_feature(self, feature_name: str, enabled: bool):
        """热插拔功能开关"""
        if self._worker is None:
            return

        self._worker.set_feature_enabled(feature_name, enabled)
        status = "启用" if enabled else "停用"
        feature_names = {"face": "人脸识别", "gesture": "手势识别", "companion": "陪伴检测"}
        self._append_log(f"{'✅' if enabled else '⏸️'} {feature_names.get(feature_name, feature_name)}{status}")

    def _enroll_self(self):
        """录入本人"""
        if self._worker is None:
            self._append_log("❌ 请先启动视觉感知")
            return

        name, ok = QInputDialog.getText(self, "录入本人", "请输入你的称呼（例如：雨心）：")
        if not ok or not name.strip():
            return

        # 确保人脸识别已启用
        if not self.check_face.isChecked():
            self.check_face.setChecked(True)
            self._worker.set_feature_enabled("face", True)

        self._worker.begin_face_enrollment(name.strip())
        self._append_log(f"📸 开始录入本人：{name.strip()}（请正对摄像头，保持15帧）")

    def _enroll_friend(self):
        """录入朋友（多人识别）"""
        if self._worker is None:
            self._append_log("❌ 请先启动视觉感知")
            return

        name, ok = QInputDialog.getText(self, "录入朋友", "请输入朋友的名字（例如：小明）：")
        if not ok or not name.strip():
            return

        # 确保人脸识别已启用
        if not self.check_face.isChecked():
            self.check_face.setChecked(True)
            self._worker.set_feature_enabled("face", True)

        # TODO: 实现多人识别录入（当前 VisionWorker 只支持单人）
        self._append_log(f"📸 开始录入朋友：{name.strip()}（多人识别功能开发中）")
        QMessageBox.information(self, "功能开发中", "多人识别功能正在开发中，敬请期待！")

    @pyqtSlot(object)
    def _on_frame_ready(self, frame):
        """接收并显示视频帧"""
        self._current_frame = frame  # 缓存当前帧

        # 转换为 QPixmap 显示
        rgb = frame[:, :, ::-1].copy()
        h, w = rgb.shape[:2]
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(
            self.video_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.video_label.setPixmap(pixmap)

    @pyqtSlot(dict)
    def _on_status_ready(self, status):
        """更新状态显示"""
        # 摄像头
        if "camera" in status:
            self.status_labels["camera"].setText(status["camera"])

        # FPS
        if "fps" in status:
            self.status_labels["fps"].setText(f"{status['fps']}")

        # 人脸
        if "face" in status:
            self.status_labels["face"].setText(status["face"])

        # 手势
        if "gesture" in status:
            gesture = status["gesture"]
            if gesture != "NONE":
                self.status_labels["gesture"].setText(f"{gesture} ({status.get('gesture_confidence', 0):.0%})")
            else:
                self.status_labels["gesture"].setText("就绪")

        # 陪伴
        if "companion" in status:
            self.status_labels["companion"].setText(status["companion"])

        # 工作时长
        if "work_duration" in status:
            duration = int(status["work_duration"])
            self.status_labels["duration"].setText(f"{duration // 60:02d}:{duration % 60:02d}")

    @pyqtSlot(str)
    def _on_event_ready(self, event):
        """处理视觉事件"""
        self._append_log(f"[事件] {event}")

        # 触发对应的信号，发送给主界面
        if event == "USER_ENTER":
            self.user_entered.emit()
        elif event == "USER_RETURN":
            self.user_returned.emit()
        elif event == "USER_LEAVE":
            self.user_left.emit()
        elif event == "LONG_WORK":
            self.long_work.emit()
        elif event == "STRANGER_PERSISTING":
            self.stranger_detected.emit()
        elif "OK" in event:
            self.gesture_ok.emit()

    @pyqtSlot(bool, str)
    def _on_started(self, success, message):
        """启动结果"""
        if success:
            self._append_log("✅ 视觉感知已启动")
        else:
            self._append_log(f"❌ 启动失败：{message}")
            QMessageBox.warning(self, "启动失败", message)
            self._stop_vision()

    @pyqtSlot()
    def _on_stopped(self):
        """停止完成"""
        self._append_log("⏸️ 视觉感知已停止")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.video_label.clear()
        self.video_label.setText("摄像头已停止\n\n点击「启动」按钮重新开始")

    def _clear_thread(self):
        """清理线程"""
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    def _append_log(self, message):
        """添加日志"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def get_current_frame(self):
        """获取当前帧（供"看看你面前的是谁"工具调用）"""
        return self._current_frame

    def closeEvent(self, event):
        """关闭窗口时停止识别"""
        self._stop_vision()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        event.accept()
