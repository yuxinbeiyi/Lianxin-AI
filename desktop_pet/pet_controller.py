from PyQt5.QtWidgets import QApplication

from .pet_state import PetState
from .pet_window import DesktopPetWindow


class DesktopPetController:
    """Small public API for future MainWindow/Agent signal integration."""

    def __init__(self, app: QApplication | None = None):
        self.app = app or QApplication.instance() or QApplication([])
        self.window = DesktopPetWindow()

    def show(self):
        self.window.show()

    def hide(self):
        self.window.hide()

    def set_state(self, state: PetState | str):
        self.window.set_state(state)

    def move_left(self):
        self.window.movement.set_direction(-1)

    def move_right(self):
        self.window.movement.set_direction(1)

    def stop(self):
        self.window.movement.stop()

    def close(self):
        self.window.close()
