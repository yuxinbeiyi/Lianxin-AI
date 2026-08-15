"""Mode B 沉浸式音乐空间窗口

作为独立置顶窗口覆盖 MainWindow 音乐盒区域
复用 Mode A 的 HTML/CSS/JS 资源，以 mode=full 参数进入沉浸模式。
"""
from pathlib import Path

from PyQt5.QtCore import QEvent, QUrl, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import (QWebEnginePage, QWebEngineProfile,
                                      QWebEngineSettings, QWebEngineView)
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from utils.paths import get_user_data_dir


class MusicSpacePage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        try:
            print(f"[音乐Web] {message} ({source_id}:{line_number})")
        except Exception:
            pass
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class MusicSpaceWindow(QWidget):
    """沉浸式音乐空间主窗口"""

    space_visibility_changed = pyqtSignal(bool)

    def __init__(self, bridge, anchor=None, parent=None):
        super().__init__(parent)
        self._bridge = bridge
        self._anchor = anchor          # 跟随主界面音乐盒的区域
        self._ready = False
        self._pending_payload = ""
        self._last_geo = None

        # Keep the space above the parent window, while allowing other Windows
        # apps to cover it without fighting the compositor when focus changes.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setWindowTitle("莲心音乐空间")

        self._view = QWebEngineView(self)
        self._view.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._profile = QWebEngineProfile("LianxinMusicSpace", self)
        web_data_dir = get_user_data_dir() / "music_space_webengine"
        try:
            self._profile.setPersistentStoragePath(str(web_data_dir))
            self._profile.setCachePath(str(web_data_dir / "cache"))
            self._profile.setHttpCacheType(QWebEngineProfile.NoCache)
        except Exception:
            pass
        self._page = MusicSpacePage(self._profile, self._view)
        self._view.setPage(self._page)
        self._view.setContextMenuPolicy(Qt.NoContextMenu)
        try:
            self._view.page().setBackgroundColor(QColor("#0A0C12"))
        except Exception:
            pass
        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)

        self._channel = QWebChannel(self._view)
        self._channel.registerObject("musicBridge", bridge)
        self._view.page().setWebChannel(self._channel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._view)

        self._index = Path(__file__).with_name("web") / "index.html"
        page_url = QUrl.fromLocalFile(str(self._index.resolve()))
        try:
            page_url.setQuery(f"mode=full&v={self._index.stat().st_mtime_ns}")
        except Exception:
            page_url.setQuery("mode=full")
        self._view.loadFinished.connect(self._on_load_finished)
        self._view.load(page_url)

        # 定时跟随锚点，保持覆盖位置
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_anchor)
        self._follow_timer.start(250)

    def set_anchor(self, anchor):
        self._anchor = anchor
        self._follow_anchor()

    def _follow_anchor(self):
        anchor = self._anchor
        if anchor is None:
            return
        try:
            # Never resize/reposition the QWebEngine window while another app
            # owns focus. Repeated native geometry changes cause compositor
            # flicker when Windows overlays an external window.
            if self.isVisible() and not self.isActiveWindow():
                self._follow_timer.stop()
                return
            if not anchor.isVisible():
                return
            geo = anchor.frameGeometry()
            if geo.width() > 0 and geo.height() > 0:
                # 仅当几何真正变化时才重设，避免每 250ms 重复 setGeometry
                # 触发原生 QWebEngine 子窗口重排而导致的界面闪烁。
                # 用 self._last_geo 做守卫（不比较 self.geometry()，
                # 避免父/子窗口坐标系不一致导致守卫永远不成立）。
                if geo != self._last_geo:
                    self._last_geo = geo
                    self.setGeometry(geo)
        except Exception:
            pass

    def _on_load_finished(self, success):
        self._ready = True
        pending = self._pending_payload
        self._pending_payload = ""
        if success:
            self.push_state(pending)

    def push_state(self, payload: str):
        if not payload:
            return
        if not self._ready:
            self._pending_payload = payload
            return
        try:
            self._view.page().runJavaScript(
                "window.lianxinMusic && window.lianxinMusic.applyState(%s)" % payload
            )
        except Exception as exc:
            print(f"[音乐Web] push_state 失败: {exc}")

    def show_space(self):
        if self.isMaximized():
            self.showNormal()
        self._follow_anchor()
        self.show()
        self.raise_()
        self.activateWindow()
        self._follow_timer.start(250)

    def minimize_space(self):
        """最小化：暂停跟随锚点，避免 setGeometry 干扰最小化状态。"""
        self._follow_timer.stop()
        self.showMinimized()
        self.space_visibility_changed.emit(False)
        if self._anchor is not None:
            try:
                self._anchor.activateWindow()
                self._anchor.raise_()
            except Exception:
                pass

    def toggle_maximize(self):
        """最大化 / 还原：最大化时暂停跟随锚点，还原时恢复跟随。"""
        if self.isMaximized():
            self.showNormal()
            self._resume_follow()
        else:
            self._follow_timer.stop()
            self.showMaximized()

    def _resume_follow(self):
        self._follow_timer.start(250)
        self._follow_anchor()

    def changeEvent(self, event):
        """从任务栏恢复 / 还原窗口时恢复锚点跟随。"""
        super().changeEvent(event)
        try:
            if event.type() == QEvent.WindowStateChange:
                if (self.isVisible() and not self.isMinimized()
                        and not self.isMaximized()):
                    self._resume_follow()
                    self.space_visibility_changed.emit(True)
            elif event.type() == QEvent.ActivationChange:
                if self.isActiveWindow():
                    self._resume_follow()
                else:
                    self._follow_timer.stop()
        except Exception:
            pass

    def close_space(self):
        self._follow_timer.stop()
        self.hide()
        self.space_visibility_changed.emit(False)
        if self._anchor is not None:
            try:
                self._anchor.activateWindow()
                self._anchor.raise_()
            except Exception:
                pass

    def shutdown(self):
        self._follow_timer.stop()
        try:
            self._view.page().setWebChannel(None)
        except Exception:
            pass
        try:
            self._profile.clearHttpCache()
        except Exception:
            pass
        self._view.deleteLater()
        self.close()
