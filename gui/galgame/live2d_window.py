"""Cubism renderer using the existing desktop-pet window interactions."""

import html
import json
import logging
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QBitmap, QColor, QRegion
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineView
from PyQt5.QtWidgets import QApplication, QMenu, QWidget

from .tachie_window import TachieWindow


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "Live2D"
logger = logging.getLogger(__name__)


def load_runtime_config(directory=RUNTIME_DIR):
    directory = Path(directory).resolve()
    config_file = directory / "config.json"
    if not config_file.is_file():
        raise FileNotFoundError("尚未准备 Live2D 本地资源，请按 docs/莲心Live2D接入说明.md 操作")
    config = json.loads(config_file.read_text(encoding="utf-8"))
    files = {key: (directory / config[key]).resolve() for key in ("model", "core")}
    files.update(pixi=directory / "pixi.min.js", display=directory / "cubism4.min.js")
    for path in files.values():
        if not path.is_file():
            raise FileNotFoundError(f"Live2D 资源不存在：{path}")
    return files


class Live2DPage(QWebEnginePage):
    ready = pyqtSignal()
    error = pyqtSignal(str)

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        logger.info("Live2D JS[%s] %s (%s:%s)", level, message, source_id, line_number)
        if message == "LIANXIN_LIVE2D_READY":
            self.ready.emit()
        elif message.startswith("LIANXIN_LIVE2D_ERROR:"):
            self.error.emit(message.split(":", 1)[1])

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.scheme() in ("file", "data", "about")


class Live2DTachieWindow(TachieWindow):
    model_ready = pyqtSignal()
    load_error = pyqtSignal(str)

    def __init__(self, assets_dir, parent=None, *, runtime_dir=None):
        self._runtime_dir = Path(runtime_dir) if runtime_dir else RUNTIME_DIR
        self._runtime_files = load_runtime_config(self._runtime_dir)
        self._ready = False
        self._talking = False
        self._previewing = False
        self._state = "idle"
        self._disposed = False
        super().__init__(assets_dir, parent)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._finish_preview)
        self._mask_timer = QTimer(self)
        self._mask_timer.setInterval(700)
        self._mask_timer.timeout.connect(self._refresh_mask)

    def _init_window(self):
        super()._init_window()
        self.setWindowTitle("莲心 - Live2D")
        self.setAttribute(Qt.WA_DeleteOnClose)

    def _init_ui(self):
        self._view = QWebEngineView(self)
        self._profile = QWebEngineProfile(self._view)
        page = Live2DPage(self._profile, self._view)
        page.ready.connect(self._on_model_ready)
        page.error.connect(self.load_error)
        self._view.setPage(page)
        self._view.page().setBackgroundColor(QColor(Qt.transparent))
        self._view.setStyleSheet("background: transparent;")
        self._view.setAttribute(Qt.WA_TranslucentBackground)
        self._view.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._view.setContextMenuPolicy(Qt.NoContextMenu)
        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        self._view.loadFinished.connect(self._on_page_loaded)
        self._view.page().renderProcessTerminated.connect(
            lambda status, code: self.load_error.emit(f"模型渲染进程退出：{status}/{code}")
        )
        web_dir = Path(__file__).with_name("live2d_web")
        document = (web_dir / "index.html").read_text(encoding="utf-8")
        for key in ("core", "pixi", "display"):
            document = document.replace(f"__{key.upper()}__", html.escape(self._runtime_files[key].as_uri(), quote=True))
        model_url = json.dumps(self._runtime_files["model"].as_uri()).replace("</", "<\\/")
        document = document.replace("__MODEL_URL__", model_url)
        self._view.setHtml(document, QUrl.fromLocalFile(str(web_dir / "index.html")))

    def _on_page_loaded(self, success):
        if not success:
            self.load_error.emit("本地播放器页面加载失败")

    def _on_model_ready(self):
        if self._disposed:
            return
        self._ready = True
        self._sync_playback()
        self._mask_timer.start()
        self.model_ready.emit()

    def _refresh_mask(self):
        if not self.isVisible() or not self._ready or self._disposed:
            return
        image = self._view.grab().toImage()
        region = QRegion(QBitmap.fromImage(image.createAlphaMask()))
        if region.isEmpty():
            return
        margin = max(10, int(self.width() * 0.05))
        expanded = QRegion(region)
        for delta_x, delta_y in ((margin, 0), (-margin, 0), (0, margin), (0, -margin)):
            expanded = expanded.united(region.translated(delta_x, delta_y))
        self.setMask(expanded)

    def _sync_playback(self):
        if self._ready and not self._disposed:
            state = "talking" if self._talking or self._previewing else "idle"
            self._view.page().runJavaScript(
                f"window.lianxinLive2D.setState({json.dumps(state)}, {str(self.isVisible()).lower()})"
            )

    def apply_settings(self):
        from utils.settings import get_settings
        self._base_scale = max(50, min(300, int(get_settings().galgame_sprite_scale))) / 100.0
        self._scale = self._base_scale
        self._apply_current_frame()

    def _apply_current_frame(self):
        old_center = self.geometry().center()
        screen = QApplication.screenAt(old_center) or QApplication.primaryScreen()
        height = min(int(620 * self._scale), screen.availableGeometry().height() - 70)
        self.resize(max(100, int(height * 0.56)), max(180, height))
        self._view.setGeometry(self.rect())
        if self._ready:
            self.clearMask()
            QTimer.singleShot(250, self._refresh_mask)
        self.move(self.pos() + old_center - self.geometry().center())
        if self.isVisible():
            self.position_changed.emit(self.x(), self.y())

    def set_animation_state(self, state):
        self._state = state

    def current_state(self):
        return self._state

    def start_breathing(self):
        self._sync_playback()

    def stop_breathing(self):
        pass

    def start_talking(self):
        self._talking = True
        self._previewing = False
        self._preview_timer.stop()
        self._sync_playback()

    def stop_talking(self):
        self._talking = False
        self._sync_playback()

    def preview_reply(self, text):
        if self._talking or not text.strip():
            return
        self._previewing = True
        self._preview_timer.start(max(1500, min(14000, len(text) * 60)))
        self._sync_playback()

    def _finish_preview(self):
        self._previewing = False
        self._sync_playback()

    def move_left(self):
        self._movement_direction = -1
        self._movement_timer.start(40)

    def move_right(self):
        self._movement_direction = 1
        self._movement_timer.start(40)

    def stop_moving(self):
        self._movement_direction = 0
        self._movement_timer.stop()

    def _show_action_menu(self, event):
        menu = QMenu(self)
        menu.addAction("向左移动", self.move_left)
        menu.addAction("向右移动", self.move_right)
        menu.addAction("停止移动", self.stop_moving)
        menu.addSeparator()
        menu.addAction("试播说话动作", lambda: self.preview_reply("莲心 Live2D 说话动作演示。" * 5))
        menu.addAction("停止试播", self._finish_preview)
        menu.addAction("隐藏/显示对话窗口", self.toggle_dialog_requested.emit)
        menu.addAction("退出 Live2D 模式", self.close_requested.emit)
        menu.exec_(event.globalPos())

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_playback()
        if self._ready:
            self._mask_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_playback()
        if hasattr(self, "_mask_timer"):
            self._mask_timer.stop()

    def closeEvent(self, event):
        self._preview_timer.stop()
        self._mask_timer.stop()
        self._movement_timer.stop()
        if not self._disposed:
            self._view.page().runJavaScript("window.lianxinLive2D && window.lianxinLive2D.dispose()")
            self._disposed = True
            self._view.setUrl(QUrl("about:blank"))
        QWidget.closeEvent(self, event)
