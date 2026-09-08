from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from .pet_assets import SpriteLoader
from .pet_state import PetState


class PetAnimation(QObject):
    frame_changed = pyqtSignal(object)

    def __init__(self, loader: SpriteLoader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.state = PetState.IDLE
        self.index = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.set_state(self.state)

    def set_state(self, state: PetState | str) -> None:
        self.state = PetState(state)
        self.index = 0
        interval = {
            PetState.IDLE: 220,
            PetState.SIT: 300,
            PetState.SLEEP: 420,
            PetState.HAPPY: 180,
            PetState.STUDY: 260,
            PetState.THINK: 260,
            PetState.WALK_LEFT: 120,
            PetState.WALK_RIGHT: 120,
        }[self.state]
        self.timer.start(interval)
        self._emit_current()

    def _next_frame(self) -> None:
        frames = self.loader.frames(self.state.value)
        if not frames:
            return
        self.index = (self.index + 1) % len(frames)
        self._emit_current()

    def _emit_current(self) -> None:
        frames = self.loader.frames(self.state.value)
        if frames:
            self.frame_changed.emit(frames[self.index % len(frames)])
