"""sprite_animation：莲心 Galgame 角色精灵加载与逐帧动画（桌宠移植版，零依赖桌面端）。"""
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap

from utils.pet_behavior import SELECTABLE_STATES, STATE_INTERVALS  # noqa: F401  (兼容旧导入)


class SpriteLoader:
    """按状态加载并缓存固定尺寸的透明 PNG 帧。"""

    def __init__(self, root: Path | None = None,
                 frame_size: tuple[int, int] = (95, 150)):
        self.root = Path(root) if root is not None else Path(__file__).parent / "assets" / "animations"
        self.frame_size = frame_size
        self._cache: dict[str, list[QPixmap]] = {}

    def frames(self, state: str) -> list[QPixmap]:
        if state in self._cache:
            return self._cache[state]
        folder = self.root / state
        paths = sorted(folder.glob("frame_*.png"))
        frames = []
        for path in paths:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                continue
            frames.append(pixmap.scaled(
                self.frame_size[0], self.frame_size[1],
                aspectRatioMode=1,   # Qt.KeepAspectRatio
                transformMode=0,     # Qt.FastTransformation
            ))
        self._cache[state] = frames
        return frames


class SpriteAnimation(QObject):
    """按状态切换的逐帧动画控制器。"""

    frame_changed = pyqtSignal(object)

    def __init__(self, loader: SpriteLoader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.state = "idle"
        self.index = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.set_state(self.state)

    def set_state(self, state: str) -> None:
        state = state if state in STATE_INTERVALS else "idle"
        self.state = state
        self.index = 0
        self.timer.start(STATE_INTERVALS[state])
        self._emit_current()

    def _next_frame(self) -> None:
        frames = self.loader.frames(self.state)
        if not frames:
            return
        self.index = (self.index + 1) % len(frames)
        self._emit_current()

    def _emit_current(self) -> None:
        frames = self.loader.frames(self.state)
        if frames:
            self.frame_changed.emit(frames[self.index % len(frames)])

    def current_pixmap(self) -> QPixmap | None:
        frames = self.loader.frames(self.state)
        if frames:
            return frames[self.index % len(frames)]
        return None
