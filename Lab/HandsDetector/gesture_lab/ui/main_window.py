"""
手势识别实验室 · 主窗口
深色工程实验室风格，左侧视频+状态，右侧事件日志，底部控制按钮。
"""

import time
from typing import Optional

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage, QPixmap, QFont, QColor
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QFrame, QTextEdit, QSplitter, QSizePolicy,
    QMessageBox,
)

from ..config import (
    WINDOW_TITLE, WINDOW_WIDTH, WINDOW_HEIGHT,
    VIDEO_WIDTH_RATIO, MAX_LOG_COUNT,
)
from ..camera.camera_manager import CameraManager
from ..camera.recognition_worker import RecognitionWorker
from ..vision.hand_detector import HandDetector
from ..vision.gesture_classifier import GestureClassifier
from ..vision.gesture_state import GestureState, STATE_READY, STATE_COOLDOWN, STATE_CANDIDATE, STATE_TRIGGERED, STATE_WAIT_RELEASE
from ..events.event_manager import EventManager
from ..events.gesture_event import GestureEvent, GESTURE_NAMES, GESTURE_NONE
from ..mock.mock_lianxin import MockLianXin


# 状态显示颜色
STATE_COLORS = {
    STATE_READY: "#4ade80",       # 绿
    STATE_CANDIDATE: "#facc15",   # 黄
    STATE_TRIGGERED: "#f97316",   # 橙
    STATE_COOLDOWN: "#94a3b8",    # 灰
}


class GestureLabWindow(QMainWindow):
    """莲心视觉感知实验室 · Gesture Lab 主窗口。"""

    # 内部信号（用于从识别线程安全地更新 UI）
    _frame_ready = pyqtSignal(object)   # numpy ndarray
    _status_update = pyqtSignal(dict)   # 状态字典
    _gesture_event = pyqtSignal(object)  # GestureEvent

    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(900, 600)

        # ── 核心模块 ──
        self._camera = CameraManager()
        self._detector = HandDetector()
        self._classifier = GestureClassifier()
        self._gesture_state = GestureState()
        self._event_mgr = EventManager()
        self._mock = MockLianXin()
        self._worker_thread = None
        self._worker = None

        # 注册事件处理器
        self._event_mgr.add_handler("mock_lianxin", self._mock)
        self._event_mgr.add_handler("ui_log", self._on_event_log)

        # ── 视频/识别定时器（UI 线程中运行） ──
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._paused = False

        # ── 构建 UI ──
        self._build_ui()
        self._apply_styles()

        # 连接内部信号
        self._gesture_event.connect(self._on_gesture_event_signal)

        # 初始化状态显示
        self._update_status_panel({
            "camera": "未启动",
            "fps": 0.0,
            "hands": 0,
            "gesture": "NONE",
            "confidence": 0.0,
            "event_state": "READY",
            "cooldown": 0.0,
            "model_gesture": "NONE",
            "model_confidence": 0.0,
        })

    # ─────────────────────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # 标题
        title = QLabel("莲心视觉感知实验室 · Gesture Lab")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)

        # 主体分割器（左视频 + 右日志）
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)

        # ── 左侧：视频 + 状态 ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # 视频显示区
        self._video_label = QLabel()
        self._video_label.setObjectName("videoLabel")
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setMinimumSize(480, 360)
        self._video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._video_label.setText("摄像头未启动")
        left_layout.addWidget(self._video_label, stretch=1)

        # 状态面板
        status_frame = QFrame()
        status_frame.setObjectName("statusFrame")
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(4)

        status_title = QLabel("识别状态")
        status_title.setObjectName("statusTitle")
        status_layout.addWidget(status_title)

        # 状态行
        self._status_labels = {}
        rows = [
            ("camera", "摄像头"),
            ("fps", "FPS"),
            ("hands", "手部数量"),
            ("gesture", "当前手势"),
            ("confidence", "置信度"),
            ("event_state", "事件状态"),
            ("cooldown", "冷却剩余"),
        ]
        for key, label in rows:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label)
            lbl.setObjectName("statusKey")
            lbl.setMinimumWidth(70)
            val = QLabel("-")
            val.setObjectName(f"statusVal_{key}")
            row.addWidget(lbl)
            row.addWidget(val, stretch=1)
            status_layout.addLayout(row)
            self._status_labels[key] = val

        for key, label in (("model_gesture", "Model gesture"),
                           ("model_confidence", "Model confidence")):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label)
            lbl.setObjectName("statusKey")
            lbl.setMinimumWidth(70)
            val = QLabel("-")
            val.setObjectName(f"statusVal_{key}")
            row.addWidget(lbl)
            row.addWidget(val, stretch=1)
            status_layout.addLayout(row)
            self._status_labels[key] = val

        left_layout.addWidget(status_frame)

        splitter.addWidget(left_panel)

        # ── 右侧：事件日志 ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        log_title = QLabel("实时事件日志")
        log_title.setObjectName("logTitle")
        right_layout.addWidget(log_title)

        self._log_view = QTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 9))
        right_layout.addWidget(self._log_view, stretch=1)

        splitter.addWidget(right_panel)

        # 设置分割器初始比例
        splitter.setStretchFactor(0, int(VIDEO_WIDTH_RATIO * 10))
        splitter.setStretchFactor(1, int((1 - VIDEO_WIDTH_RATIO) * 10))
        main_layout.addWidget(splitter, stretch=1)

        # ── 底部控制按钮 ──
        btn_frame = QFrame()
        btn_frame.setObjectName("btnFrame")
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(12, 8, 12, 8)
        btn_layout.setSpacing(8)

        self._btn_start = QPushButton("开启摄像头")
        self._btn_start.setObjectName("btnPrimary")
        self._btn_start.clicked.connect(self._on_start_clicked)

        self._btn_stop = QPushButton("关闭摄像头")
        self._btn_stop.setObjectName("btnSecondary")
        self._btn_stop.clicked.connect(self._on_stop_clicked)
        self._btn_stop.setEnabled(False)

        self._btn_pause = QPushButton("暂停识别")
        self._btn_pause.setObjectName("btnSecondary")
        self._btn_pause.clicked.connect(self._on_pause_clicked)
        self._btn_pause.setEnabled(False)

        self._btn_clear = QPushButton("清空日志")
        self._btn_clear.setObjectName("btnSecondary")
        self._btn_clear.clicked.connect(self._on_clear_clicked)

        btn_layout.addWidget(self._btn_start)
        btn_layout.addWidget(self._btn_stop)
        btn_layout.addWidget(self._btn_pause)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self._btn_clear)

        main_layout.addWidget(btn_frame)

    def _apply_styles(self):
        """应用深色工程实验室风格样式。"""
        self.setStyleSheet("""
            QMainWindow, #central {
                background-color: #0f1419;
            }
            #title {
                font-size: 18px;
                font-weight: bold;
                color: #e2e8f0;
                padding: 4px 0;
                letter-spacing: 1px;
            }
            #videoLabel {
                background-color: #1a1f2e;
                border: 1px solid #2d3748;
                border-radius: 6px;
                color: #64748b;
                font-size: 14px;
            }
            #statusFrame {
                background-color: #1a1f2e;
                border: 1px solid #2d3748;
                border-radius: 6px;
            }
            #statusTitle, #logTitle {
                font-size: 13px;
                font-weight: bold;
                color: #94a3b8;
                padding-bottom: 4px;
                border-bottom: 1px solid #2d3748;
                margin-bottom: 4px;
            }
            #statusKey {
                color: #64748b;
                font-size: 12px;
            }
            QLabel[class="statusValue"] {
                color: #e2e8f0;
                font-size: 12px;
            }
            #logView {
                background-color: #1a1f2e;
                border: 1px solid #2d3748;
                border-radius: 6px;
                color: #cbd5e1;
                padding: 8px;
            }
            #btnFrame {
                background-color: #1a1f2e;
                border: 1px solid #2d3748;
                border-radius: 6px;
            }
            QPushButton {
                padding: 8px 20px;
                border-radius: 4px;
                font-size: 13px;
                border: none;
            }
            QPushButton#btnPrimary {
                background-color: #3b82f6;
                color: white;
            }
            QPushButton#btnPrimary:hover {
                background-color: #2563eb;
            }
            QPushButton#btnPrimary:disabled {
                background-color: #475569;
                color: #94a3b8;
            }
            QPushButton#btnSecondary {
                background-color: #334155;
                color: #e2e8f0;
            }
            QPushButton#btnSecondary:hover {
                background-color: #475569;
            }
            QPushButton#btnSecondary:disabled {
                background-color: #1e293b;
                color: #64748b;
            }
            QSplitter::handle {
                background-color: #2d3748;
            }
        """)

    # ─────────────────────────────────────────────────────
    # 按钮事件
    # ─────────────────────────────────────────────────────

    def _on_start_clicked(self):
        if self._worker_thread is not None:
            return
        from PyQt5.QtCore import QThread
        self._worker_thread = QThread(self)
        self._worker = RecognitionWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.started.connect(self._on_worker_started)
        self._worker.frame_ready.connect(self._show_frame)
        self._worker.status_ready.connect(self._update_status_panel)
        self._worker.gesture_event.connect(self._on_gesture_event_signal)
        self._worker.stopped.connect(self._on_worker_stopped)
        self._worker.stopped.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.finished.connect(self._clear_worker)
        self._worker_thread.start()
        self._paused = False
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_pause.setEnabled(True)
        self._btn_pause.setText("暂停识别")

    def _on_stop_clicked(self):
        if self._worker is not None:
            self._worker.stop()
        else:
            self._camera.stop()
        self._timer.stop()
        self._paused = False

        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_pause.setEnabled(False)

        self._video_label.setText("摄像头已关闭")
        self._append_log("系统", "摄像头已关闭")
        self._update_status_panel({
            "camera": "已关闭",
            "fps": 0.0, "hands": 0,
            "gesture": "NONE", "confidence": 0.0,
            "event_state": "READY", "cooldown": 0.0,
        })

    def _on_pause_clicked(self):
        self._paused = not self._paused
        if self._paused:
            self._btn_pause.setText("继续识别")
            self._append_log("系统", "识别已暂停")
        else:
            self._btn_pause.setText("暂停识别")
            self._classifier.reset()
            self._append_log("系统", "识别已恢复")
        if self._worker is not None:
            self._worker.set_paused(self._paused)

    def _on_clear_clicked(self):
        self._log_view.clear()
        self._event_mgr.clear_history()

    @pyqtSlot(bool, str)
    def _on_worker_started(self, ok: bool, message: str):
        if ok:
            self._append_log("系统", "摄像头已启动，CPU 手势识别开始运行")
        else:
            self._on_stop_clicked()
            QMessageBox.warning(self, "识别启动失败", message)

    @pyqtSlot()
    def _on_worker_stopped(self):
        self._append_log("系统", "摄像头已关闭")

    @pyqtSlot()
    def _clear_worker(self):
        self._worker = None
        self._worker_thread = None

    # ─────────────────────────────────────────────────────
    # 主循环
    # ─────────────────────────────────────────────────────

    def _on_tick(self):
        """每帧刷新：取帧 → 检测 → 分类 → 状态机 → 渲染"""
        if not self._camera.running:
            return

        frame = self._camera.read_frame()
        if frame is None:
            return

        if not self._paused:
            # 送入 MediaPipe 检测（异步）
            self._detector.detect(frame)

            # 获取最新检测结果
            landmarks = self._detector.get_landmarks(0)
            hand_count = self._detector.get_hands_count()

            # 手势分类
            gesture, confidence = self._classifier.update(landmarks)

            # 状态机更新
            self._gesture_state.update(gesture, confidence)
            info = self._gesture_state.get_info()

            # 检查是否触发事件
            if self._gesture_state.should_trigger():
                event = GestureEvent(
                    gesture=info.gesture,
                    confidence=info.confidence,
                    hand_count=hand_count,
                )
                # 通过信号跨线程安全分发（虽然这里都在 UI 线程，但保持一致）
                self._gesture_event.emit(event)

            # 绘制手部关键点
            frame = self._draw_landmarks(frame, gesture, confidence)

        # 显示视频帧
        self._show_frame(frame)

        # 更新状态面板
        info = self._gesture_state.get_info()
        self._update_status_panel({
            "camera": f"{self._camera.width}×{self._camera.height}",
            "fps": round(self._camera.fps, 1),
            "hands": self._detector.get_hands_count(),
            "gesture": info.gesture,
            "confidence": round(info.confidence, 2),
            "event_state": info.state,
            "cooldown": round(info.cooldown_remaining, 2),
        })

    # ─────────────────────────────────────────────────────
    # 绘制
    # ─────────────────────────────────────────────────────

    def _draw_landmarks(self, frame: np.ndarray, gesture: str,
                        confidence: float) -> np.ndarray:
        """在图像上绘制手部关键点和骨骼连接。"""
        hands_count = self._detector.get_hands_count()
        if hands_count == 0:
            return frame

        # 为每只手绘制
        for hand_idx in range(hands_count):
            landmarks = self._detector.get_landmarks(hand_idx)
            if landmarks is None:
                continue

            h, w = frame.shape[:2]
            points = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]

            # 骨骼连接（MediaPipe 手部连接定义）
            connections = [
                (0, 1), (1, 2), (2, 3), (3, 4),       # 拇指
                (0, 5), (5, 6), (6, 7), (7, 8),       # 食指
                (5, 9), (9, 10), (10, 11), (11, 12),  # 中指
                (9, 13), (13, 14), (14, 15), (15, 16), # 无名指
                (13, 17), (17, 18), (18, 19), (19, 20), # 小指
                (0, 17),                                # 手掌底部
            ]

            # 绘制连线
            for s, e in connections:
                cv2.line(frame, points[s], points[e], (0, 255, 0), 2)

            # 绘制关键点
            for x, y in points:
                cv2.circle(frame, (x, y), 4, (255, 255, 0), -1)

        # 左上角显示当前手势
        if gesture != GESTURE_NONE:
            name = GESTURE_NAMES.get(gesture, gesture)
            text = f"Gesture: {name}  {confidence*100:.0f}%"
            cv2.putText(frame, text, (15, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        return frame

    def _show_frame(self, frame: np.ndarray):
        """将 OpenCV BGR 图像转换为 QPixmap 并显示。"""
        h, w = frame.shape[:2]
        # BGR → RGB
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        # 保持比例缩放
        scaled = pixmap.scaled(
            self._video_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._video_label.setPixmap(scaled)

    # ─────────────────────────────────────────────────────
    # 状态更新
    # ─────────────────────────────────────────────────────

    def _update_status_panel(self, data: dict):
        """更新状态栏显示。"""
        label_map = {
            "camera": lambda v: str(v),
            "fps": lambda v: f"{v:.1f}",
            "hands": lambda v: str(v),
            "gesture": lambda v: GESTURE_NAMES.get(v, v),
            "confidence": lambda v: f"{v*100:.0f}%" if v else "-",
            "event_state": lambda v: v,
            "cooldown": lambda v: f"{v:.1f}s" if v and v > 0 else "READY",
            "model_gesture": lambda v: str(v),
            "model_confidence": lambda v: f"{v*100:.0f}%" if v else "-",
        }
        for key, formatter in label_map.items():
            if key in self._status_labels:
                self._status_labels[key].setText(formatter(data.get(key, "-")))

        # 事件状态颜色
        state = data.get("event_state", STATE_READY)
        color = STATE_COLORS.get(state, "#94a3b8")
        if "event_state" in self._status_labels:
            self._status_labels["event_state"].setStyleSheet(
                f"color: {color}; font-weight: bold;"
            )

    # ─────────────────────────────────────────────────────
    # 事件处理
    # ─────────────────────────────────────────────────────

    @pyqtSlot(object)
    def _on_gesture_event_signal(self, event: GestureEvent):
        """接收手势事件信号，分发给事件管理器。"""
        self._event_mgr.dispatch(event)

    def _on_event_log(self, event: GestureEvent):
        """事件处理器：在日志视图中显示事件 + 模拟回复。"""
        # 模拟莲心回复
        reply = self._mock.get_reply(event.gesture)
        self._append_log(event.time_str,
                        f"检测到 {event.name} 手势（{event.confidence*100:.0f}%）",
                        f"模拟回复：{reply}")

    def _append_log(self, time_str: str, main_text: str, sub_text: str = ""):
        """向日志视图追加一条记录。"""
        if sub_text:
            html = (
                f'<div style="margin: 4px 0;">'
                f'<span style="color:#64748b;">[{time_str}]</span> '
                f'<span style="color:#e2e8f0;">{main_text}</span>'
                f'<br/>'
                f'<span style="color:#fbbf24; margin-left: 20px;">↳ {sub_text}</span>'
                f"</div>"
            )
        else:
            html = (
                f'<div style="margin: 4px 0;">'
                f'<span style="color:#64748b;">[{time_str}]</span> '
                f'<span style="color:#94a3b8;">{main_text}</span>'
                f"</div>"
            )
        self._log_view.append(html)

        # 限制日志行数
        doc = self._log_view.document()
        if doc.blockCount() > MAX_LOG_COUNT:
            cursor = self._log_view.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.select(cursor.BlockUnderCursor)
            cursor.deleteChar()

        # 自动滚动到底部
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ─────────────────────────────────────────────────────
    # 关闭事件
    # ─────────────────────────────────────────────────────

    def closeEvent(self, event):
        """窗口关闭时释放资源。"""
        self._timer.stop()
        if self._worker is not None:
            self._worker.stop()
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        self._camera.stop()
        self._detector.close()
        super().closeEvent(event)
