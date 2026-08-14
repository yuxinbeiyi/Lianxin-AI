"""Mode A 主界面嵌入式音乐盒（QWebEngineView 渲染）

复用 CharacterWidget 同款 Qt 渲染方案（HTML/CSS/JS 实现）
通过 QWebChannel + MusicBoxBridge 桥接 Python，由 push_state() 推送状态
（不重建整套 Qt 控件）
"""
from pathlib import Path

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import (QWebEnginePage, QWebEngineProfile,
                                      QWebEngineSettings, QWebEngineView)
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from utils.paths import get_user_data_dir


class MusicBoxPage(QWebEnginePage):
    """捕获页面 / 脚本控制台输出的 Web 调试信息"""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        try:
            print(f"[音乐Web] {message} ({source_id}:{line_number})")
        except Exception:
            pass
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class MusicBoxWidget(QWidget):
    """Mode A 主界面嵌入式音乐盒"""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self._bridge = bridge
        self._ready = False
        self._pending_payload = ""

        self._view = QWebEngineView(self)
        self._profile = QWebEngineProfile("LianxinMusicBoxA", self)
        web_data_dir = get_user_data_dir() / "music_box_webengine"
        try:
            self._profile.setPersistentStoragePath(str(web_data_dir))
            self._profile.setCachePath(str(web_data_dir / "cache"))
            self._profile.setHttpCacheType(QWebEngineProfile.NoCache)
        except Exception:
            pass
        self._page = MusicBoxPage(self._profile, self._view)
        self._view.setPage(self._page)
        self._view.setContextMenuPolicy(Qt.NoContextMenu)
        self._view.setAttribute(Qt.WA_OpaquePaintEvent, True)
        try:
            self._view.page().setBackgroundColor(QColor(0, 0, 0, 0))
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
            page_url.setQuery(f"mode=compact&v={self._index.stat().st_mtime_ns}")
        except Exception:
            page_url.setQuery("mode=compact")
        self._view.loadFinished.connect(self._on_load_finished)
        self._view.load(page_url)

    def _on_load_finished(self, success):
        self._ready = True
        pending = self._pending_payload
        self._pending_payload = ""
        if success:
            self.push_state(pending)

    def push_state(self, payload: str):
        """将 JSON 状态注入页面前端显示"""
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

    def shutdown(self):
        try:
            self._view.page().setWebChannel(None)
        except Exception:
            pass
        try:
            self._profile.clearHttpCache()
        except Exception:
            pass
        self._view.deleteLater()
