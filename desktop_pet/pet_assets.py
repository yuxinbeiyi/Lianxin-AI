from pathlib import Path

from PyQt5.QtGui import QPixmap


class SpriteLoader:
    """Load and cache fixed-size transparent PNG frames."""

    def __init__(self, root: Path | None = None, scale: int = 1,
                 frame_size: tuple[int, int] = (95, 150)):
        self.root = root or Path(__file__).parent / "assets"
        self.scale = max(1, int(scale))
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
                self.frame_size[0] * self.scale, self.frame_size[1] * self.scale,
                aspectRatioMode=1,
                transformMode=0,  # Qt.FastTransformation
            ))
        self._cache[state] = frames
        return frames
