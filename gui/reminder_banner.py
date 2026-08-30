# -*- coding: utf-8 -*-
"""ReminderBanner：非模态提醒横幅（待办/准点提醒到期的统一交互）。

显示提醒标题与三个操作按钮：✔️完成 / ⏰10分钟后再提醒 / 忽略今天。
设计原则：不使用系统模态弹窗（不抢焦点、全屏时温和降级为普通浮窗），
替代旧版闹钟/待办的 QMessageBox 阻塞式交互。
"""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


class ReminderBanner(QWidget):
    """一条待办提醒的非模态横幅。"""

    completed = pyqtSignal(str)       # todo_id
    snoozed = pyqtSignal(str, int)    # todo_id, minutes
    dismissed = pyqtSignal(str)       # todo_id

    AUTO_CLOSE_MS = 120_000  # 无操作 2 分钟后自动收起（已确认，不会重复响）

    def __init__(self, todo_id: str, title: str, due_text: str = "",
                 parent=None):
        super().__init__(parent)
        self._todo_id = todo_id
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        self.setStyleSheet(
            "QWidget { background-color: #141B2E; border: 1px solid #303A5C;"
            " border-radius: 10px; }"
            "QLabel { color: #E9EDF2; border: none; background: transparent; }"
            "QPushButton { color: #E9EDF2; background-color: #26314F;"
            " border: 1px solid #3D4A73; border-radius: 6px; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #33406B; }"
        )

        header = QLabel(f"⏰ 提醒：{title}")
        header.setWordWrap(True)
        header.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        root.addWidget(header)

        if due_text:
            due_label = QLabel(due_text)
            due_label.setStyleSheet("color:#8FA0C0; font-size:9pt;")
            root.addWidget(due_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self._btn_done = QPushButton("✔️ 完成")
        self._btn_snooze = QPushButton("⏰ 10分钟后再提醒")
        self._btn_ignore = QPushButton("忽略今天")
        self._btn_done.clicked.connect(self._on_done)
        self._btn_snooze.clicked.connect(self._on_snooze)
        self._btn_ignore.clicked.connect(self._on_ignore)
        for btn in (self._btn_done, self._btn_snooze, self._btn_ignore):
            button_row.addWidget(btn)
        button_row.addStretch()
        root.addLayout(button_row)

        self._auto_close_timer = QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.timeout.connect(self.close)

    def show_reminder(self):
        """显示横幅（屏幕右下角），并启动自动收起计时。"""
        self.adjustSize()
        screen = self.screen() or self.windowHandle().screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.right() - self.width() - 24,
                geo.bottom() - self.height() - 24,
            )
        self.show()
        self.raise_()
        self._auto_close_timer.start(self.AUTO_CLOSE_MS)

    def _on_done(self):
        self.completed.emit(self._todo_id)
        self.close()

    def _on_snooze(self):
        self.snoozed.emit(self._todo_id, 10)
        self.close()

    def _on_ignore(self):
        self.dismissed.emit(self._todo_id)
        self.close()
