from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QLabel, QMenu, QWidget

from .pet_animation import PetAnimation
from .pet_assets import SpriteLoader
from .pet_movement import PetMovement
from .pet_state import PetState


class DesktopPetWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(95, 150)
        self.label = QLabel(self)
        self.label.setFixedSize(self.size())
        self.label.setAlignment(Qt.AlignCenter)
        self.loader = SpriteLoader(scale=1)
        self.animation = PetAnimation(self.loader, self)
        self.animation.frame_changed.connect(self._show_frame)
        self.movement = PetMovement(self, self.set_state, self)
        self._drag_offset: QPoint | None = None
        self._show_initial_position()

    def _show_initial_position(self):
        screen = self.screen() or self.windowHandle().screen() if self.windowHandle() else None
        screen = screen or __import__("PyQt5.QtWidgets", fromlist=["QApplication"]).QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 40, area.bottom() - self.height() - 40)
        self.show()

    def _show_frame(self, pixmap: QPixmap):
        self.label.setPixmap(pixmap)

    def set_state(self, state: PetState | str):
        self.animation.set_state(state)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.pos()
        elif event.button() == Qt.RightButton:
            menu = QMenu(self)
            menu.addAction("向左移动", lambda: self.movement.set_direction(-1))
            menu.addAction("向右移动", lambda: self.movement.set_direction(1))
            menu.addAction("停止移动", self.movement.stop)
            menu.addSeparator()
            for state, label in ((PetState.IDLE, "待机"), (PetState.SIT, "坐下"),
                                 (PetState.SLEEP, "睡眠"), (PetState.HAPPY, "开心"),
                                 (PetState.STUDY, "学习"), (PetState.THINK, "思考")):
                menu.addAction(label, lambda checked=False, s=state: self.set_state(s))
            menu.addAction("退出桌宠", self.close)
            menu.exec_(event.globalPos())

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None

    def closeEvent(self, event):
        self.animation.timer.stop()
        self.movement.timer.stop()
        event.accept()
