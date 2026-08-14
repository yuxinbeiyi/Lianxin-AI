
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
    minimize_space_requested = pyqtSignal()
    maximize_space_requested = pyqtSignal()
    toggle_favorite_requested = pyqtSignal()

    def __init__(self, state_provider, parent=None,
                 space_settings_provider=None, space_settings_saver=None):
        super().__init__(parent)
        self._state_provider = state_provider
        self._space_settings_provider = space_settings_provider
        self._space_settings_saver = space_settings_saver

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
    def minimizeMusicSpace(self):
        self.minimize_space_requested.emit()

    @pyqtSlot()
    def maximizeMusicSpace(self):
        self.maximize_space_requested.emit()

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

    @pyqtSlot(result=str)
    def getSpaceSettings(self):
        """返回音乐空间设置（壁纸列表 + 当前配置）。"""
        try:
            if self._space_settings_provider is None:
                return "{}"
            data = self._space_settings_provider() or {}
            return json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            print(f"[musicbox] getSpaceSettings failed: {exc}")
            return "{}"

    @pyqtSlot(str, float, float, str, result=str)
    def saveSpaceSettings(self, wallpaper, wallpaper_opacity, content_mask_opacity, fit):
        """持久化音乐空间设置，返回更新后的设置载荷。"""
        try:
            if self._space_settings_saver is None:
                return "{}"
            data = self._space_settings_saver(
                wallpaper, wallpaper_opacity, content_mask_opacity, fit) or {}
            return json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            print(f"[musicbox] saveSpaceSettings failed: {exc}")
            return "{}"
