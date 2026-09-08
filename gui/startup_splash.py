"""Lianxin startup splash with lightweight, truthful progress reporting."""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)


class StartupSplash(QWidget):
    """Borderless startup screen that never owns model initialization."""

    def __init__(self, startup_mode="fast"):
        # 无边框但不强制置顶，避免启动期间遮挡用户正在使用的其他窗口。
        super().__init__(None, Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(960, 576)
        self._drag_offset = None

        root = QFrame(self)
        root.setObjectName("root")
        root.setGeometry(self.rect())
        root.setStyleSheet(
            "QFrame#root { background: #F6F8F8; border: 1px solid #D6E7E2; "
            "border-radius: 12px; }"
            "QLabel#status { color: #344B49; font-size: 12px; }"
            "QLabel#detail { color: #718482; font-size: 11px; }"
            "QProgressBar { background: rgba(218, 236, 231, 180); border: 0; "
            "border-radius: 4px; height: 8px; text-align: center; }"
            "QProgressBar::chunk { border-radius: 4px; background: "
            "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #B9F2DF, "
            "stop:0.55 #87DEC5, stop:1 #55C5A5); }"
        )

        image = QLabel(root)
        image.setGeometry(0, 0, 960, 576)
        image.setPixmap(self._background())
        image.setScaledContents(True)

        overlay = QFrame(root)
        overlay.setGeometry(44, 420, 500, 120)
        overlay.setStyleSheet(
            "QFrame { background: rgba(255, 255, 255, 205); "
            "border: 1px solid rgba(188, 218, 210, 150); border-radius: 10px; }"
        )
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        self._status = QLabel("⟳ 正在唤醒莲心…")
        self._status.setObjectName("status")
        self._status.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        detail = "完整启动：正在准备基础模型" if startup_mode == "complete" else "正在准备基础环境"
        self._detail = QLabel(detail)
        self._detail.setObjectName("detail")
        layout.addWidget(self._detail)

    @staticmethod
    def _background() -> QPixmap:
        path = Path(__file__).resolve().parents[1] / "assets" / "加载界面.png"
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            pixmap = QPixmap(960, 576)
            pixmap.fill(Qt.white)
        return pixmap

    def update_status(self, status: str, detail: str = "", progress: int | None = None):
        self._status.setText(status)
        if detail:
            self._detail.setText(detail)
        if progress is not None:
            self._progress.setValue(max(0, min(100, int(progress))))
        QApplication.processEvents()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
