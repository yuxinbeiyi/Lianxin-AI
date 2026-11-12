"""Cubism renderer using the existing desktop-pet window interactions."""

import html
import json
import logging
import time
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QBitmap, QColor, QCursor, QRegion
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineView
from PyQt5.QtWidgets import QApplication, QMenu, QWidget

from .tachie_window import TachieWindow


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "Live2D"
logger = logging.getLogger(__name__)


def load_runtime_config(directory=RUNTIME_DIR):
    from utils.live2d_assets import load_runtime_config as _load_runtime_config
    return _load_runtime_config(directory)


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
        self._tracking_enabled = True
        self._press_position = None
        self._click_dragged = False
        self._mask_region = QRegion()
        self._mask_size = None
        self._mask_samples = 0
        super().__init__(assets_dir, parent)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._finish_preview)
        self._mask_timer = QTimer(self)
        self._mask_timer.setInterval(700)
        self._mask_timer.timeout.connect(self._refresh_mask)
        self._tracking_timer = QTimer(self)
        self._tracking_timer.setInterval(100)
        self._tracking_timer.timeout.connect(self._update_tracking)

    def _init_window(self):
        super()._init_window()
        self.setWindowTitle("莲心 - Live2D")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)

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
        self._start_mask_capture()
        self._tracking_timer.start()
        self.model_ready.emit()

    def _call_interaction(self, method, *arguments):
        if not self._ready or self._disposed:
            return
        payload = ",".join(json.dumps(value, ensure_ascii=True) for value in arguments)
        self._view.page().runJavaScript(f"window.lianxinLive2D && window.lianxinLive2D.{method}({payload})")

    def react(self, action):
        if action in {"nod", "shake", "headpat", "tap", "smile", "sad", "angry", "surprised", "normal"}:
            self._call_interaction("react", action)

    def set_thinking(self, active):
        self._call_interaction("setThinking", bool(active))

    def _update_tracking(self):
        if not self.isVisible() or not self._ready or self._disposed:
            return
        pointer = self.mapFromGlobal(QCursor.pos())
        nearby = self.rect().adjusted(-180, -180, 180, 180).contains(pointer)
        if self._tracking_enabled and nearby and self._drag_pos is None:
            horizontal = (pointer.x() - self.width() * 0.5) / max(1, self.width() * 0.8)
            vertical = (self.height() * 0.28 - pointer.y()) / max(1, self.height() * 0.65)
            self._call_interaction("setTracking", horizontal, vertical)
        else:
            self._call_interaction("setTracking", 0, 0)

    def _toggle_tracking(self, enabled):
        self._tracking_enabled = enabled
        self._update_tracking()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_position = event.globalPos()
            self._press_time = time.monotonic()
            self._click_dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_position is not None:
            if (event.globalPos() - self._press_position).manhattanLength() > QApplication.startDragDistance():
                self._click_dragged = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_position is not None:
            distance = (event.globalPos() - self._press_position).manhattanLength()
            if not self._click_dragged and distance <= QApplication.startDragDistance() and time.monotonic() - self._press_time < 0.8:
                self.react("headpat" if event.pos().y() < self.height() * 0.38 else "tap")
            self._press_position = None
        super().mouseReleaseEvent(event)

    def _start_mask_capture(self):
        self._mask_size = self.size()
        self._mask_region = QRegion()
        self._mask_samples = 0
        if self.isVisible():
            self._mask_timer.start(250)

    def _refresh_mask(self):
        if not self.isVisible() or not self._ready or self._disposed or self._mask_samples >= 5:
            return
        self._mask_samples += 1
        image = self._view.grab().toImage()
        if image.size() != self._view.size():
            image = image.scaled(self._view.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        region = QRegion(QBitmap.fromImage(image.createAlphaMask()))
        if not region.isEmpty():
            margin = max(10, int(self.width() * 0.05))
            expanded = QRegion()
            for rectangle in region.rects():
                expanded = expanded.united(QRegion(rectangle.adjusted(-margin, -margin, margin, margin)))
            combined = self._mask_region.united(expanded)
            if combined != self._mask_region:
                self._mask_region = combined
                self.setMask(combined)
        if self._mask_samples >= 5:
            self._mask_timer.stop()
            if self._mask_region.isEmpty():
                self.load_error.emit("Live2D 角色遮罩采样失败")
            else:
                logger.info("Live2D mask stabilized after %s samples", self._mask_samples)
        elif self._mask_samples == 1:
            self._mask_timer.setInterval(700)

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
        if self._ready and self.size() != self._mask_size:
            self._start_mask_capture()
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
        self.set_thinking(False)
        self._talking = True
        self._previewing = False
        self._preview_timer.stop()
        self._sync_playback()

    def stop_talking(self):
        self._talking = False
        self._sync_playback()

    def preview_reply(self, text):
        self.set_thinking(False)
        if self._talking or not text.strip():
            return
        self.react("nod")
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
        reactions = menu.addMenu("互动动作与表情")
        for title, action in (("点头", "nod"), ("摇头", "shake"), ("摸摸头", "headpat"), ("微笑", "smile"), ("难过", "sad"), ("生气", "angry"), ("惊讶", "surprised"), ("恢复自然", "normal")):
            reactions.addAction(title, lambda checked=False, name=action: self.react(name))
        tracking = menu.addAction("视线跟随鼠标（附近）")
        tracking.setCheckable(True)
        tracking.setChecked(self._tracking_enabled)
        tracking.toggled.connect(self._toggle_tracking)
        menu.addSeparator()
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
            if self._mask_samples < 5:
                self._mask_timer.start(250)
            self._tracking_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_playback()
        if hasattr(self, "_mask_timer"):
            self._mask_timer.stop()
            self._tracking_timer.stop()

    def closeEvent(self, event):
        self._preview_timer.stop()
        self._mask_timer.stop()
        self._tracking_timer.stop()
        self._movement_timer.stop()
        if not self._disposed:
            self._view.page().runJavaScript("window.lianxinLive2D && window.lianxinLive2D.dispose()")
            self._disposed = True
            self._view.setUrl(QUrl("about:blank"))
        QWidget.closeEvent(self, event)
