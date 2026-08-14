
"""
MusicBoxBridge - HTML music box <-> Python backend QWebChannel bridge.
"""
import json
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot


class MusicBoxBridge(QObject):
    toggle_play_requested = pyqtSignal()
    play_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    next_requested = pyqtSignal()
    previous_requested = pyqtSignal()
    seek_requested = pyqtSignal(float)
    volume_requested = pyqtSignal(float)
    play_mode_requested = pyqtSignal(str)
    track_requested = pyqtSignal(int)
    open_space_requested = pyqtSignal()
    close_space_requested = pyqtSignal()
    toggle_favorite_requested = pyqtSignal()

    def __init__(self, state_provider, parent=None):
        super().__init__(parent)
        self._state_provider = state_provider

    @pyqtSlot()
    def togglePlay(self):
        self.toggle_play_requested.emit()

    @pyqtSlot()
    def play(self):
        self.play_requested.emit()

    @pyqtSlot()
    def pause(self):
        self.pause_requested.emit()

    @pyqtSlot()
    def next(self):
        self.next_requested.emit()

    @pyqtSlot()
    def previous(self):
        self.previous_requested.emit()

    @pyqtSlot(float)
    def seek(self, position):
        self.seek_requested.emit(float(position or 0))

    @pyqtSlot(float)
    def setVolume(self, volume):
        self.volume_requested.emit(float(volume or 0))

    @pyqtSlot(str)
    def setPlayMode(self, mode):
        self.play_mode_requested.emit(str(mode))

    @pyqtSlot(int)
    def selectTrack(self, index):
        self.track_requested.emit(int(index))

    @pyqtSlot()
    def openMusicSpace(self):
        self.open_space_requested.emit()

    @pyqtSlot()
    def closeMusicSpace(self):
        self.close_space_requested.emit()

    @pyqtSlot()
    def toggleFavorite(self):
        self.toggle_favorite_requested.emit()

    @pyqtSlot(result=str)
    def getState(self):
        try:
            data = self._state_provider() or {}
            return json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            print(f"[musicbox] getState failed: {exc}")
            return "{}"
