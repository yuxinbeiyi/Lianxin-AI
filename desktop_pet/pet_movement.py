from PyQt5.QtCore import QObject, QTimer, QPoint
from PyQt5.QtWidgets import QApplication

from .pet_state import PetState


class PetMovement(QObject):
    def __init__(self, window, on_state, parent=None):
        super().__init__(parent)
        self.window = window
        self.on_state = on_state
        self.direction = 0
        self.speed = 2
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(40)

    def set_direction(self, direction: int) -> None:
        self.direction = max(-1, min(1, int(direction)))
        if self.direction < 0:
            self.on_state(PetState.WALK_LEFT)
        elif self.direction > 0:
            self.on_state(PetState.WALK_RIGHT)
        else:
            self.on_state(PetState.IDLE)

    def stop(self) -> None:
        self.set_direction(0)

    def tick(self) -> None:
        if not self.direction:
            return
        screen = QApplication.screenAt(self.window.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(self.window.x() + self.direction * self.speed,
                                 area.right() - self.window.width() + 1))
        y = max(area.top(), min(self.window.y(), area.bottom() - self.window.height() + 1))
        self.window.move(QPoint(x, y))
