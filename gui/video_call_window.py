"""Standalone immersive voice-call presentation for the first UI phase.

This module intentionally has no imports from the voice, agent, or TTS layers.
The preview can therefore be exercised without a microphone or model runtime.
"""

from pathlib import Path
import random

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap, QImage
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import (
    QDialog, QFrame, QGraphicsBlurEffect, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget, QFileDialog, QDialogButtonBox,
    QRadioButton, QLineEdit, QGroupBox,
)

from utils.resource_path import get_base_dir
from utils.settings import get_settings


class _PortraitSurface(QFrame):
    """Portrait media area with a blurred source-photo surround."""

    media_failed = pyqtSignal(str)

    def __init__(self, poster_path: Path, video_paths: dict, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QFrame { background: #09090b; border: none; }")
        self._poster_path = poster_path
        self._poster_pixmap = QPixmap(str(poster_path))
        self._video_paths = video_paths
        self._current_key = None
        self._animation_mode = False
        self._startup_path = None
        self._waiting_paths = []
        self._last_waiting = None
        self._current_path = None
        self._cv_cap = None
        self._cv_timer = QTimer(self)
        self._cv_timer.timeout.connect(self._read_cv_frame)

        self._background = QLabel(self)
        self._background.setAlignment(Qt.AlignCenter)
        self._background.setStyleSheet("background: #09090b; border: none;")
        blur = QGraphicsBlurEffect(self._background)
        blur.setBlurRadius(28)
        self._background.setGraphicsEffect(blur)

        self._media_frame = QFrame(self)
        self._media_frame.setStyleSheet(
            "QFrame { background: #111014; border: 1px solid rgba(255,255,255,35); }"
        )
        self._video = QVideoWidget(self._media_frame)
        self._video.setAspectRatioMode(Qt.KeepAspectRatio)
        self._video.setStyleSheet("background: #111014; border: none;")
        self._cv_video = QLabel(self._media_frame)
        self._cv_video.setAlignment(Qt.AlignCenter)
        self._cv_video.setStyleSheet("background: #111014; border: none;")
        self._cv_video.hide()

        self._poster = QLabel(self._media_frame)
        self._poster.setAlignment(Qt.AlignCenter)
        self._poster.setStyleSheet("background: #111014; border: none;")
        self._poster.setPixmap(self._poster_pixmap)

        self._player = QMediaPlayer(self, QMediaPlayer.VideoSurface)
        self._player.setVideoOutput(self._video)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.stateChanged.connect(self._on_state_changed)
        self._player.error.connect(self._on_error)
        self._show_poster(True)

    def resizeEvent(self, event):
        self._background.setGeometry(self.rect())
        self._media_frame.setGeometry(self._portrait_rect())
        self._video.setGeometry(self._media_frame.rect())
        self._cv_video.setGeometry(self._media_frame.rect())
        self._poster.setGeometry(self._media_frame.rect())
        self._fit_pixmaps()
        super().resizeEvent(event)

    def _portrait_rect(self):
        # The animation assets are portrait 720x1280 videos.
        aspect = 720.0 / 1280.0
        width = min(self.width(), int(self.height() * aspect))
        height = min(self.height(), int(width / aspect)) if width else 0
        return self.rect().adjusted(
            (self.width() - width) // 2,
            (self.height() - height) // 2,
            -((self.width() - width) // 2),
            -((self.height() - height) // 2),
        )

    def _fit_pixmaps(self):
        if self._poster_pixmap.isNull():
            return
        self._background.setPixmap(self._poster_pixmap.scaled(
            self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        ))
        self._poster.setPixmap(self._poster_pixmap.scaled(
            self._media_frame.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def set_background_pixmap(self, pixmap: QPixmap):
        if not pixmap.isNull():
            self._background.setPixmap(pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            ))

    def _show_poster(self, visible: bool):
        self._poster.setVisible(visible)
        self._video.setVisible(not visible and not self._cv_timer.isActive())
        self._cv_video.setVisible(not visible and self._cv_timer.isActive())
        self._poster.raise_()

    def _on_media_status(self, status):
        if status in (QMediaPlayer.InvalidMedia, QMediaPlayer.NoMedia,
                      QMediaPlayer.StalledMedia):
            self._show_poster(True)
            self.media_failed.emit("视频无法播放，已切换为静态画面")
        elif status == QMediaPlayer.EndOfMedia:
            if self._animation_mode and self._waiting_paths:
                self._play_next_waiting()
            else:
                self._player.setPosition(0)
                self._player.play()

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self._show_poster(False)

    def _on_error(self, *_args):
        if self._start_cv_fallback():
            return
        self._show_poster(True)
        self.media_failed.emit("视频播放异常，已切换为静态画面")

    def play_state(self, state: str):
        path = self._video_paths.get(state)
        if path is None or not path.exists():
            self._show_poster(True)
            self.media_failed.emit("缺少当前状态视频，已使用静态画面")
            return
        if self._current_key == state and self._player.state() == QMediaPlayer.PlayingState:
            return
        self._current_key = state
        self._show_poster(True)
        self._player.stop()
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path))))
        self._player.play()

    def configure_animation(self, startup_path: Path, waiting_paths: list[Path]):
        self._animation_mode = True
        self._startup_path = startup_path
        self._waiting_paths = [p for p in waiting_paths if p.exists()]

    def configure_static(self, image_path: Path):
        self._animation_mode = False
        if image_path.exists():
            self._poster_pixmap = QPixmap(str(image_path))
            self._fit_pixmaps()
            self._show_poster(True)

    def start_animation(self):
        if not self._animation_mode or not self._startup_path or not self._startup_path.exists():
            return
        self._play_path(self._startup_path, "startup")

    def _play_next_waiting(self):
        candidates = [p for p in self._waiting_paths if p != self._last_waiting]
        if not candidates:
            candidates = self._waiting_paths
        if candidates:
            path = random.choice(candidates)
            self._last_waiting = path
            self._play_path(path, "waiting")

    def _play_path(self, path: Path, key: str):
        self._current_key = key
        self._current_path = path
        self._stop_cv_fallback()
        self._show_poster(True)
        self._player.stop()
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path))))
        self._player.play()

    def _start_cv_fallback(self):
        if not self._current_path or not self._current_path.exists():
            return False
        try:
            import cv2
            self._stop_cv_fallback()
            self._cv_cap = cv2.VideoCapture(str(self._current_path))
            if not self._cv_cap.isOpened():
                self._stop_cv_fallback()
                return False
            fps = self._cv_cap.get(cv2.CAP_PROP_FPS) or 30.0
            self._show_poster(False)
            self._cv_timer.start(max(15, int(1000 / min(fps, 60.0))))
            self._show_poster(False)
            self._read_cv_frame()
            return True
        except Exception:
            self._stop_cv_fallback()
            return False

    def _read_cv_frame(self):
        if self._cv_cap is None:
            return
        import cv2
        ok, frame = self._cv_cap.read()
        if not ok:
            if self._animation_mode and self._waiting_paths:
                self._play_next_waiting()
            else:
                self._player.setPosition(0)
                self._player.play()
            return
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(frame.data, frame.shape[1], frame.shape[0],
                       frame.strides[0], QImage.Format_RGB888).copy()
        self._cv_video.setPixmap(QPixmap.fromImage(image).scaled(
            self._cv_video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def _stop_cv_fallback(self):
        self._cv_timer.stop()
        if self._cv_cap is not None:
            self._cv_cap.release()
        self._cv_cap = None
        self._cv_video.clear()
        self._cv_video.hide()

    def stop(self):
        self._stop_cv_fallback()
        self._player.stop()
        self._player.setMedia(QMediaContent())
        self._show_poster(True)


class _UserTile(QFrame):
    def __init__(self, avatar_path: Path, parent=None):
        super().__init__(parent)
        self.setFixedSize(174, 190)
        self.setStyleSheet(
            "QFrame { background: rgba(250,248,250,238); border: 1px solid "
            "rgba(255,255,255,180); border-radius: 14px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        avatar = QLabel()
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setPixmap(QPixmap(str(avatar_path)).scaled(
            172, 154, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        ))
        avatar.setStyleSheet("border-radius: 14px 14px 0 0;")
        layout.addWidget(avatar, 1)
        self._label = QLabel("🎙 你 · 仅语音")
        self._label.setStyleSheet(
            "color: #242126; background: rgba(255,255,255,235); "
            "padding: 7px 8px; border-radius: 0 0 14px 14px;"
        )
        layout.addWidget(self._label)

    def set_speaking(self, active: bool):
        if active:
            self.setStyleSheet(
                "QFrame { background: rgba(250,248,250,248); border: 2px solid #ef8da5; "
                "border-radius: 14px; }"
            )
            self._label.setText("🎙 你 · 正在说话")
        else:
            self.setStyleSheet(
                "QFrame { background: rgba(250,248,250,238); border: 1px solid "
                "rgba(255,255,255,180); border-radius: 14px; }"
            )
            self._label.setText("🎙 你 · 仅语音")


class _VolumeWave(QWidget):
    """Small animated input-level indicator for the user's voice tile."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 112)
        self._active = False
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

    def set_active(self, active: bool):
        self._active = bool(active)
        if self._active:
            self._timer.start(90)
        else:
            self._timer.stop()
            self._phase = 0
        self.update()

    def _advance(self):
        self._phase = (self._phase + 1) % 24
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colors = [QColor(245, 143, 169, 210), QColor(255, 205, 219, 220)]
        heights = (0.34, 0.58, 0.86, 0.58, 0.34)
        for index, base in enumerate(heights):
            if self._active:
                wave = 0.55 + 0.45 * abs(((self._phase + index * 4) % 18) - 9) / 9
                height = max(8.0, 72.0 * base * wave)
            else:
                height = 8.0 * base
            x = 3 + index * 7
            y = (self.height() - height) / 2
            painter.setBrush(colors[index % len(colors)])
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(int(x), int(y), 4, int(height), 2, 2)


class _ConnectionAnimation(QWidget):
    """Minimal connection indicator shown before the portrait is available."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

    def set_active(self, active: bool):
        if active:
            self._timer.start(90)
        else:
            self._timer.stop()
        self.setVisible(active)
        self.update()

    def _advance(self):
        self._phase = (self._phase + 1) % 24
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor(255, 188, 210, 210))
        center_y = self.height() // 2
        points = []
        for x in range(0, self.width(), 5):
            wave = ((x + self._phase * 9) % 72) / 72.0
            y = center_y + int(__import__('math').sin(wave * 6.283) * 15)
            points.append((x, y))
        for first, second in zip(points, points[1:]):
            painter.drawLine(first[0], first[1], second[0], second[1])


class VideoCallSettingsDialog(QDialog):
    saved = pyqtSignal(dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("莲心视频形象")
        self.setMinimumWidth(460)
        self._config = dict(config)
        layout = QVBoxLayout(self)
        mode_box = QGroupBox("莲心视频形象")
        mode_row = QHBoxLayout(mode_box)
        self._static = QRadioButton("静态图片")
        self._animation = QRadioButton("动画状态机")
        self._static.setChecked(self._config.get("mode") == "static")
        self._animation.setChecked(not self._static.isChecked())
        mode_row.addWidget(self._static)
        mode_row.addWidget(self._animation)
        layout.addWidget(mode_box)

        image_row = QHBoxLayout()
        self._image = QLineEdit(self._config.get("static_image_path", ""))
        image_row.addWidget(self._image, 1)
        choose_image = QPushButton("选择图片")
        choose_image.clicked.connect(self._choose_image)
        image_row.addWidget(choose_image)
        layout.addLayout(image_row)

        background_row = QHBoxLayout()
        self._wallpaper = QRadioButton("使用桌面壁纸")
        self._custom_background = QRadioButton("选择背景图片")
        self._wallpaper.setChecked(self._config.get("background_mode", "wallpaper") == "wallpaper")
        self._custom_background.setChecked(not self._wallpaper.isChecked())
        background_row.addWidget(self._wallpaper)
        background_row.addWidget(self._custom_background)
        layout.addLayout(background_row)
        custom_row = QHBoxLayout()
        self._background = QLineEdit(self._config.get("background_image_path", ""))
        custom_row.addWidget(self._background, 1)
        choose_background = QPushButton("选择背景")
        choose_background.clicked.connect(self._choose_background)
        custom_row.addWidget(choose_background)
        layout.addLayout(custom_row)

        hint = QLabel("动画状态机将播放内置的开机视频和三个随机等待视频。")
        hint.setStyleSheet("color: #777; padding: 6px 0;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择莲心图片", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._image.setText(path)

    def _choose_background(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择背景图片", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if path:
            self._background.setText(path)
            self._custom_background.setChecked(True)

    def _save(self):
        config = {
            "mode": "static" if self._static.isChecked() else "animation",
            "static_image_path": self._image.text().strip(),
            "background_mode": "wallpaper" if self._wallpaper.isChecked() else "custom",
            "background_image_path": self._background.text().strip(),
        }
        get_settings().update_video_call(**config)
        self.saved.emit(config)
        self.accept()


class VideoCallWindow(QDialog):
    """Video-call presentation. It remains independent of voice backends."""

    closed = pyqtSignal()
    hangup_requested = pyqtSignal()
    microphone_toggled = pyqtSignal(bool)
    speaker_toggled = pyqtSignal(bool)
    chat_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    _STATE_TEXT = {
        "CONNECTING": "莲心待机中...",
        "LISTENING": "可以直接说话",
        "USER_SPEAKING": "莲心倾听中...",
        "PROCESSING": "莲心思考中…",
        "SPEAKING": "莲心正在说话",
        "ERROR": "语音识别暂不可用",
        "ENDED": "通话已结束",
    }

    def __init__(self, parent=None, preview_mode: bool = False, user_name: str = "用户"):
        super().__init__(parent)
        self.setWindowTitle("莲心 AI 视频聊天预览")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMinimumSize(760, 560)
        self.resize(960, 680)
        self._seconds = 0
        self._subtitle_enabled = True
        self._mic_enabled = True
        self._speaker_enabled = True
        self._closed = False
        self._host_closing = False
        self._preview_mode = preview_mode
        self._drag_offset = None
        self._user_name = str(user_name or "用户")
        self._settings = get_settings()
        self._video_config = self._settings.video_call
        self._loading_phase = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.timeout.connect(self._update_loading_text)

        base = get_base_dir()
        asset_dir = base / "assets" / "video_call"
        video_dir = base / "assets" / "GIF" / "正常与说话"
        poster = asset_dir / "莲心视频照片.jpg"
        self._video_paths = {
            "LISTENING": video_dir / "normal.mp4",
            "PROCESSING": video_dir / "正常待机.mp4",
            "SPEAKING": video_dir / "normal1.mp4",
            "USER_SPEAKING": video_dir / "normal.mp4",
        }
        video_dir = base / "assets" / "视频通话" / "兼容"
        # The checked-in asset is named 开启.mp4; keep the fallback for a
        # future rename so the state machine remains data-driven.
        self._startup_video = video_dir / "开启.mp4"
        if not self._startup_video.exists():
            self._startup_video = video_dir / "开机.mp4"
        self._waiting_videos = [video_dir / f"循环等待{i}.mp4" for i in range(1, 4)]
        self._build_ui(poster, asset_dir / "用户头像.jpg")
        self._apply_video_config()
        self.set_state("CONNECTING")
        if self._preview_mode:
            QTimer.singleShot(500, lambda: self.set_state("LISTENING"))

        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)

    def _build_ui(self, poster: Path, avatar: Path):
        self.setStyleSheet(
            "QDialog { background: #09090b; color: #f8f6f8; } "
            "QLabel { color: #f8f6f8; } QPushButton { font-family: 'Microsoft YaHei UI'; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._surface = _PortraitSurface(poster, self._video_paths, self)
        self._surface.media_failed.connect(self._on_media_failed)
        root.addWidget(self._surface, 1)

        overlay = QVBoxLayout(self._surface)
        overlay.setContentsMargins(22, 15, 22, 20)
        overlay.setSpacing(6)

        self._top_bar = QWidget(self._surface)
        self._top_bar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._top_bar.setStyleSheet("background: transparent;")
        top = QHBoxLayout(self._top_bar)
        top.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("莲心 AI 视频聊天")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        self._duration = QLabel("语音通话  00:00")
        self._duration.setStyleSheet("color: rgba(255,255,255,180); font-size: 10pt;")
        self._state = QLabel()
        self._state.setStyleSheet(
            "color: #FFD2DF; background: transparent; font-size: 10pt; padding-top: 2px;"
        )
        title_box.addWidget(title)
        title_box.addWidget(self._duration)
        title_box.addWidget(self._state)
        top.addLayout(title_box)
        top.addStretch()
        overlay.addWidget(self._top_bar, 0, Qt.AlignLeft | Qt.AlignTop)
        overlay.addStretch(1)

        self._subtitle = QLabel(self._surface)
        self._subtitle.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._subtitle.setWordWrap(True)
        self._subtitle.hide()
        self._connection_animation = _ConnectionAnimation(self._surface)
        self._connection_label = QLabel("莲心正在连接FunASR，请稍等...", self._surface)
        self._connection_label.setAlignment(Qt.AlignCenter)
        self._connection_label.setStyleSheet(
            "color: #FFD2DF; background: transparent; font-size: 12pt;"
        )
        self._connection_animation.hide()
        self._connection_label.hide()
        self._user_tile = _UserTile(avatar, self._surface)
        overlay.addWidget(self._user_tile, 0, Qt.AlignRight)
        self._volume_wave = _VolumeWave(self._surface)

        # Keep presentation widgets above the media surface.
        self._surface._background.lower()
        self._surface._media_frame.raise_()
        self._surface._video.lower()
        self._surface._poster.lower()
        for widget in (self._top_bar, self._subtitle, self._user_tile, self._volume_wave,
                       self._connection_animation, self._connection_label):
            widget.raise_()

        controls = QFrame(self)
        controls.setStyleSheet(
            "QFrame { background: rgba(32,28,32,242); border-top: 1px solid rgba(255,255,255,25); }"
        )
        row = QHBoxLayout(controls)
        row.setContentsMargins(24, 13, 24, 13)
        row.setSpacing(14)
        self._mic = self._button("🎙\n麦克风", self._toggle_mic)
        self._speaker = self._button("🔊\n扬声器", self._toggle_speaker)
        self._subtitles = self._button("▣\n字幕", self._toggle_subtitle)
        self._more = self._button(
            "•••\n演示状态" if self._preview_mode else "聊天\n窗口",
            self._cycle_demo_state if self._preview_mode else self.chat_requested.emit,
        )
        self._settings_button = self._button("⚙", self.settings_requested.emit)
        self._settings_button.setToolTip("莲心视频形象设置")
        self._hangup = self._button("☎", self._request_hangup)
        self._hangup.setToolTip("挂断电话")
        self._hangup.setStyleSheet(
            "QPushButton { color: white; background: #d94b64; border: none; "
            "border-radius: 29px; font-size: 24pt; } QPushButton:hover { background: #ef657c; }"
        )
        row.addStretch(1)
        for button in (self._mic, self._speaker, self._subtitles, self._more,
                       self._settings_button, self._hangup):
            row.addWidget(button)
        row.addStretch(1)
        root.addWidget(controls)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_volume_wave"):
            self._volume_wave.move(
                max(0, self._surface.width() - self._volume_wave.width() - 42),
                max(0, int(self._surface.height() * 0.42)),
            )
            subtitle_width = min(410, max(250, int(self._surface.width() * 0.40)))
            self._subtitle.setGeometry(
                28, max(112, self._surface.height() - 122), subtitle_width, 86
            )
            self._connection_animation.setGeometry(
                max(80, (self._surface.width() - 250) // 2),
                max(180, self._surface.height() // 2 - 34), 250, 48
            )
            self._connection_label.setGeometry(
                max(40, (self._surface.width() - 360) // 2),
                max(180, self._surface.height() // 2 + 24), 360, 32
            )

    def _button(self, text: str, callback):
        button = QPushButton(text, self)
        button.setFixedSize(96, 58)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(callback)
        button.setStyleSheet(
            "QPushButton { color: white; background: #514b52; border: 1px solid rgba(255,255,255,45); "
            "border-radius: 16px; font-size: 10pt; } QPushButton:hover { background: #6a626b; }"
        )
        return button

    def _tick(self):
        self._seconds += 1
        minutes, seconds = divmod(self._seconds, 60)
        self._duration.setText(f"语音通话  {minutes:02d}:{seconds:02d}")

    def set_state(self, state: str):
        state = state.upper()
        self._state.setText(self._STATE_TEXT.get(state, state))
        if state != "CONNECTING":
            self._set_connection_overlay(False)
        if state in self._video_paths and self._video_config.get("mode", "animation") != "animation":
            self._surface.play_state(state)
        if state == "USER_SPEAKING":
            self.set_user_speaking(True)
        elif state != "SPEAKING":
            self.set_user_speaking(False)

    def set_user_speaking(self, active: bool):
        self._user_tile.set_speaking(active)
        self._volume_wave.set_active(active)

    def set_subtitle(self, text: str, speaker: str):
        if not self._subtitle_enabled or not text.strip():
            self._subtitle.hide()
            return
        color = "#FFD2DF" if speaker == "莲心" else "#FFFFFF"
        self._subtitle.setStyleSheet(
            f"color: {color}; background: rgba(16,14,18,120); "
            "border-left: 3px solid rgba(255,210,223,180); "
            "padding: 8px 12px; border-radius: 4px; font-size: 11pt;"
        )
        self._subtitle.setText(f"{speaker}：{text.strip()[-160:]}")
        self._subtitle.show()

    def set_stt_loading(self, active: bool):
        """Show a connection animation while the speech model is loading."""
        if not active:
            self._loading_timer.stop()
            self._set_connection_overlay(False)
            return
        self._set_connection_overlay(True)
        self._loading_phase = 0
        self._loading_timer.start(180)
        self._update_loading_text()

    def _update_loading_text(self):
        marks = ("·  ", "·· ", "···", " ··", "  ·")
        self._state.setText("正在接通" + marks[self._loading_phase % len(marks)])
        self._loading_phase += 1

    def _set_connection_overlay(self, active: bool):
        self._surface._media_frame.setVisible(not active)
        self._surface._background.setVisible(not active)
        self._connection_animation.set_active(active)
        self._connection_label.setVisible(active)
        if active:
            self._surface.setStyleSheet("QFrame { background: #050507; border: none; }")
        else:
            self._surface.setStyleSheet("QFrame { background: #09090b; border: none; }")

    def _apply_video_config(self):
        mode = self._video_config.get("mode", "animation")
        if mode == "static":
            path = Path(self._video_config.get("static_image_path", ""))
            if path.exists():
                self._surface.configure_static(path)
        else:
            self._surface.configure_animation(self._startup_video, self._waiting_videos)

        background = self._video_config.get("background_image_path", "")
        if self._video_config.get("background_mode") == "wallpaper":
            background = self._settings.background_source
        if background and Path(background).exists():
            self._surface.set_background_pixmap(QPixmap(background))

    def apply_video_config(self, config: dict):
        self._video_config = dict(config)
        self._apply_video_config()
        if self._video_config.get("mode") == "animation" and not self._surface._player.state() == QMediaPlayer.PlayingState:
            self._surface.start_animation()

    def start_animation_if_ready(self):
        if self._video_config.get("mode", "animation") == "animation":
            self._surface.start_animation()

    def open_video_settings(self):
        dialog = VideoCallSettingsDialog(self._video_config, self)
        dialog.saved.connect(self.apply_video_config)
        dialog.exec_()

    def _cycle_demo_state(self):
        states = ["USER_SPEAKING", "PROCESSING", "SPEAKING", "LISTENING"]
        current = self._state.text()
        labels = [self._STATE_TEXT[s] for s in states]
        next_index = (labels.index(current) + 1) % len(states) if current in labels else 0
        state = states[next_index]
        if state == "PROCESSING":
            self.set_subtitle("系统：正在识别并准备回复…", "演示")
        elif state == "SPEAKING":
            self.set_subtitle("莲心：我在这里，慢慢说给我听。", "演示")
        self.set_state(state)

    def _toggle_mic(self):
        self._mic_enabled = not self._mic_enabled
        self._mic.setText(("🎙" if self._mic_enabled else "🔇") + "\n" +
                          ("麦克风" if self._mic_enabled else "已静音"))
        self.microphone_toggled.emit(self._mic_enabled)

    def _toggle_speaker(self):
        self._speaker_enabled = not self._speaker_enabled
        self._speaker.setText(("🔊" if self._speaker_enabled else "🔇") + "\n" +
                              ("扬声器" if self._speaker_enabled else "已静音"))
        self.speaker_toggled.emit(self._speaker_enabled)

    def _toggle_subtitle(self):
        self._subtitle_enabled = not self._subtitle_enabled
        self._subtitles.setText("▣\n" + ("字幕" if self._subtitle_enabled else "字幕关"))
        if not self._subtitle_enabled:
            self._subtitle.hide()

    def _on_media_failed(self, message: str):
        # Keep playback diagnostics out of the portrait area. The first phase
        # must remain visually usable even when Qt's local MP4 backend is not
        # available, so the poster remains the primary fallback.
        self._state.setText("莲心待机中...")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() <= 104:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _request_hangup(self):
        self.hangup_requested.emit()

    def close_from_host(self):
        self._host_closing = True
        self.close()

    def closeEvent(self, event):
        if not self._closed:
            self._closed = True
            self._clock.stop()
            self._surface.stop()
            self.closed.emit()
        if not self._host_closing:
            self.hangup_requested.emit()
        event.accept()


def create_preview_window() -> VideoCallWindow:
    return VideoCallWindow()
