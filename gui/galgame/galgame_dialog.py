"""
GalgameDialog：莲心 Galgame 模式 — 对话窗口
半透明圆角对话框，附着在立绘旁边，
包含对话气泡、逐字显示、输入框。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton,
    QCheckBox, QDialog
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QEvent, QRect
from PyQt5.QtGui import (
    QFont, QPainter, QPainterPath, QColor, QKeyEvent
)


class GalgameDialog(QWidget):

    """Galgame 风格对话窗口，附着在立绘窗口旁。"""

    # 用户发送消息时发射（文本内容）
    message_submitted = pyqtSignal(str)
    voice_requested = pyqtSignal()
    mute_toggled = pyqtSignal(bool)        # ← 新增：静音状态变化信号
    settings_changed = pyqtSignal()        # 设置面板保存后发射（主窗口用来应用角色缩放等）
    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = "莲心"
        self._full_text = ""
        self._current_index = 0
        self._is_typing = False
        self._status_text = "就绪"
        self._typing_speed = 40

        self._init_window()
        self._init_ui()
        self._apply_settings()
        self._init_typing_timer()


    def _init_window(self):
        """窗口基础设置。"""
        self.setWindowTitle("莲心 - 对话")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowOpacity(0.92)
        self.setMouseTracking(True)   # ← 新增：启用鼠标追踪
        self.resize(360, 300)


    def _init_ui(self):
        """创建 UI 组件。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(4)

        # ── 回复文本区（只读，可滚动） ──
        self._reply_area = QTextEdit()
        self._reply_area.setReadOnly(True)
        self._reply_area.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))  # 加 QFont.Bold
        self._reply_area.setStyleSheet("""
            QTextEdit {
                background: transparent;
                border: none;
                color: #000000;
                padding: 2px 0;
            }
            QScrollBar:vertical {
                width: 4px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: rgba(136,136,136,0.4);
                border-radius: 2px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self._reply_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self._reply_area, stretch=1)

        # ── 输入框 ──
        self._input_edit = QTextEdit()
        self._input_edit.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        self._input_edit.setPlaceholderText("说点什么吧（Enter发送 Shift+Enter换行）")
        self._input_edit.setFixedHeight(36)  # 初始1行高度
        self._input_edit.textChanged.connect(self._auto_resize_input)
        self._input_edit.setStyleSheet("""
            QTextEdit {
                background: rgba(255,255,255,200);
                border: 1px solid #d0d0e8;
                border-radius: 8px;
                padding: 6px 10px;
                color: #000000;
            }
            QTextEdit:focus {
                border-color: #6C7BFF;
            }
        """)
        self._input_edit.verticalScrollBar().setVisible(False)
        # 事件过滤器：捕获输入框的回车键
        self._input_edit.installEventFilter(self)
        layout.addWidget(self._input_edit)


        # ── 底部按钮栏 ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        self._status_label = QLabel("● 就绪")
        self._status_label.setStyleSheet("color: #4F8F7B; font-size: 10px; background: transparent;")
        btn_layout.addWidget(self._status_label)

        self._voice_btn = QPushButton("🎤")
        self._voice_btn.setFixedSize(30, 28)
        self._voice_btn.setFont(QFont("Segoe UI Emoji", 12))
        self._voice_btn.setCursor(Qt.PointingHandCursor)
        self._voice_btn.setToolTip("点击录音，自动转换为文字")
        self._voice_btn.setStyleSheet(self._action_button_style())
        self._voice_btn.clicked.connect(self.voice_requested.emit)
        btn_layout.addWidget(self._voice_btn)

        self._auto_send_cb = QCheckBox("自动发送")
        self._auto_send_cb.setChecked(True)
        self._auto_send_cb.setStyleSheet("QCheckBox { color: #506E65; font-size: 10px; background: transparent; }")
        btn_layout.addWidget(self._auto_send_cb)

        # 设置按钮
        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setFont(QFont("Segoe UI Emoji", 12))
        self._settings_btn.setFixedSize(28, 28)
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        self._settings_btn.setToolTip("Galgame 设置\n快捷键 Ctrl+Alt+X 切换模式")
        self._settings_btn.setStyleSheet("""
            QPushButton {
                background: rgba(200,200,210,120);
                color: #000000;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover  { background: rgba(180,180,200,160); }
        """)

        self._settings_btn.clicked.connect(self._on_settings)
        btn_layout.addWidget(self._settings_btn)

        # 静音按钮
        self._mute_btn = QPushButton("🔊")
        self._mute_btn.setFont(QFont("Segoe UI Emoji", 12))
        self._mute_btn.setFixedSize(28, 28)
        self._mute_btn.setCursor(Qt.PointingHandCursor)
        self._mute_btn.setToolTip("点击静音")
        self._mute_btn.setStyleSheet("""
            QPushButton {
                background: rgba(200,200,210,120);
                color: #555;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover  { background: rgba(180,180,200,160); }
        """)
        self._mute_btn.clicked.connect(self._on_mute_toggle)
        btn_layout.addWidget(self._mute_btn)

        btn_layout.addStretch()

        self._send_btn = QPushButton("发送")
        self._send_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._send_btn.setFixedSize(60, 28)
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setStyleSheet("""
            QPushButton {
                background: #347767;
                color: #FFFFFF;
                border: 1px solid #83CDB8;
                border-radius: 6px;
            }
            QPushButton:hover  { background: #3E8A73; }
            QPushButton:pressed{ background: #2A5148; }
        """)
        self._send_btn.clicked.connect(self._on_send)
        btn_layout.addWidget(self._send_btn)
        layout.addLayout(btn_layout)

        self._update_mute_button()
        self.setLayout(layout)

    @staticmethod
    def _action_button_style():
        return """
            QPushButton { background: rgba(34, 75, 64, 190); color: #DCEFE8;
                border: 1px solid #5C9D8B; border-radius: 6px; }
            QPushButton:hover { background: #347261; color: #FFFFFF; border-color: #83CDB8; }
            QPushButton:pressed { background: #2A5148; }
        """

    def set_status(self, text: str, active: bool = False):
        self._status_text = text
        if hasattr(self, "_status_label"):
            color = "#B47742" if active else "#4F8F7B"
            self._status_label.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
            self._status_label.setText(f"● {text}")

    def set_voice_recording(self, recording: bool):
        self._voice_btn.setText("■" if recording else "🎤")
        self._voice_btn.setToolTip("点击停止录音" if recording else "点击录音，自动转换为文字")
        self._voice_btn.setStyleSheet("""
            QPushButton { background: #B85C5C; color: #FFFFFF; border: 1px solid #E49A9A; border-radius: 6px; }
            QPushButton:hover { background: #D06A6A; }
        """ if recording else self._action_button_style())

    def set_input_text(self, text: str):
        self._input_edit.setPlainText(text)
        self._input_edit.setFocus()

    def auto_send_enabled(self) -> bool:
        return self._auto_send_cb.isChecked()


    def _init_typing_timer(self):
        """逐字显示定时器。"""
        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._update_typing)

    def set_name(self, name: str):
        """设置角色名。"""
        self._name = name
        self._name_label.setText(name)

    def show_reply(self, text: str):
        """逐字显示 AI 回复。"""
        self._full_text = text
        self._current_index = 0
        self._is_typing = True
        self._reply_area.setHtml("")
        # 滚动到底部
        scrollbar = self._reply_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self._typing_timer.start(self._typing_speed)

    def clear_reply(self):
        """清空回复显示区。"""
        self._typing_timer.stop()
        self._reply_area.setHtml("")

    def show_thinking(self):
        """显示思考中。"""
        self.clear_reply()
        self._reply_area.setHtml("（思考中…）")

    def _update_typing(self):
        """逐字显示更新。"""
        if self._current_index < len(self._full_text):
            text = self._full_text[:self._current_index + 1]
            # 用 html 保留换行
            html = f"<span style='color:#000000;'>{text.replace(chr(10), '<br>')}</span>"
            self._reply_area.setHtml(html)
            self._current_index += 1
            # 逐字时自动滚到底
            scrollbar = self._reply_area.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        else:
            self._typing_timer.stop()
            self._is_typing = False

    def _on_send(self):
        """发送按钮点击。"""
        text = self._input_edit.toPlainText().strip()
        if text:
            self._input_edit.clear()
            self.show_thinking()
            self.message_submitted.emit(text)

    # ── 事件过滤器：捕获输入框回车键 ──
    def eventFilter(self, obj, event):
        if obj == self._input_edit and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Return and not (event.modifiers() & Qt.ShiftModifier):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    # ── 键盘事件 ──
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Return and not (event.modifiers() & Qt.ShiftModifier):
            self._on_send()
            event.accept()
        else:
            super().keyPressEvent(event)

    # ── 外观与对话设置 ──
    def _apply_settings(self):
        from utils.settings import get_settings
        s = get_settings()
        size = s.galgame_font_size
        bold = s.galgame_font_bold
        weight = QFont.Bold if bold else QFont.Normal
        font = QFont("Microsoft YaHei UI", size, weight)
        self._reply_area.setFont(font)
        self._input_edit.setFont(font)
        self._auto_resize_input()
        self._typing_speed = max(5, s.galgame_typing_speed)
        self.setWindowOpacity(max(0.3, min(1.0, s.galgame_panel_opacity / 100.0)))


    def _auto_resize_input(self):
        """根据内容自动调整输入框高度（1~3行）。"""
        QTimer.singleShot(0, self._do_auto_resize)

    def _do_auto_resize(self):
        doc = self._input_edit.document()
        doc_height = doc.size().height()
        margins = self._input_edit.contentsMargins()
        extra = self._input_edit.frameWidth() * 2 + margins.top() + margins.bottom() + 4
        height = doc_height + extra
        height = max(36, min(height, 100))
        self._input_edit.setFixedHeight(int(height))


    def _on_settings(self):
        from .galgame_settings_dialog import GalgameSettingsDialog
        dlg = GalgameSettingsDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self._apply_settings()
            self.settings_changed.emit()

    # ── 静音切换 ──
    def _on_mute_toggle(self):
        from utils.settings import get_settings
        s = get_settings()
        s.silent_mode = not s.silent_mode
        self._update_mute_button()
        if s.silent_mode:
            try:
                from skills.语音合成.tools import stop_voice_playback
                stop_voice_playback()
            except Exception:
                pass
        self.mute_toggled.emit(s.silent_mode)   # ← 新增：通知主窗口



    def _update_mute_button(self):
        from utils.settings import get_settings
        s = get_settings()
        if s.silent_mode:
            self._mute_btn.setText("🔇")
            self._mute_btn.setToolTip("已静音，点击取消静音")
        else:
            self._mute_btn.setText("🔊")
            self._mute_btn.setToolTip("点击静音")


    # ── 自定义圆角绘制 ──
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        r = self.rect().adjusted(6, 6, -6, -6)
        path.addRoundedRect(float(r.x()), float(r.y()), float(r.width()), float(r.height()), 12, 12)
        painter.fillPath(path, QColor(230, 230, 235, 225))

        painter.setPen(QColor(0, 0, 0, 30))
        for i in range(3):
            shrink = 3 - i
            sr = r.adjusted(-shrink, -shrink, shrink, shrink)
            sp = QPainterPath()
            sp.addRoundedRect(float(sr.x()), float(sr.y()), float(sr.width()), float(sr.height()), 12, 12)
            painter.setPen(QColor(0, 0, 0, 10 - i * 3))
            painter.drawPath(sp)
        painter.end()

    # ── 鼠标拖拽 ──
    def _get_resize_edge(self, pos):
        """返回鼠标所在的边缘方向，不在边缘返回 None。"""
        b = 8
        r = self.rect()

        left = pos.x() < b
        right = pos.x() > r.width() - b
        top = pos.y() < b
        bottom = pos.y() > r.height() - b
        if top and left:     return 'topleft'
        if top and right:    return 'topright'
        if bottom and left:  return 'bottomleft'
        if bottom and right: return 'bottomright'
        if top:              return 'top'
        if bottom:           return 'bottom'
        if left:             return 'left'
        if right:            return 'right'
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edge = self._get_resize_edge(event.pos())
            if edge:
                self._resize_edge = edge
                self._resize_start_geometry = self.geometry()
                self._resize_start_pos = event.globalPos()
            else:
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            if hasattr(self, '_resize_edge') and self._resize_edge:
                self._do_resize(event.globalPos())
            elif hasattr(self, '_drag_pos'):
                self.move(event.globalPos() - self._drag_pos)
            event.accept()
        else:
            edge = self._get_resize_edge(event.pos())
            if edge in ('top', 'bottom'):
                self.setCursor(Qt.SizeVerCursor)
            elif edge in ('left', 'right'):
                self.setCursor(Qt.SizeHorCursor)
            elif edge in ('topleft', 'bottomright'):
                self.setCursor(Qt.SizeFDiagCursor)
            elif edge in ('topright', 'bottomleft'):
                self.setCursor(Qt.SizeBDiagCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if hasattr(self, '_resize_edge'):
                del self._resize_edge
            if hasattr(self, '_drag_pos'):
                del self._drag_pos
            event.accept()

    def _do_resize(self, global_pos):
        """根据拖拽边缘调整窗口大小和位置。"""
        delta = global_pos - self._resize_start_pos
        geo = QRect(self._resize_start_geometry)
        edge = self._resize_edge
        min_w, min_h = 200, 150

        if 'left' in edge:
            new_left = geo.left() + delta.x()
            new_width = geo.width() - delta.x()
            if new_width >= min_w:
                geo.setLeft(new_left)
        if 'right' in edge:
            new_width = geo.width() + delta.x()
            if new_width >= min_w:
                geo.setRight(geo.right() + delta.x())
        if 'top' in edge:
            new_top = geo.top() + delta.y()
            new_height = geo.height() - delta.y()
            if new_height >= min_h:
                geo.setTop(new_top)
        if 'bottom' in edge:
            new_height = geo.height() + delta.y()
            if new_height >= min_h:
                geo.setBottom(geo.bottom() + delta.y())

        self.setGeometry(geo)
