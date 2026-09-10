"""
TachieWindow：莲心 Galgame 模式 — 角色精灵窗口（桌宠动画移植版）
无边框、透明背景、可拖拽、始终置顶。
角色形象使用桌宠莲心的逐帧动画：
  - 左键：拖动精灵（对话面板跟随）
  - 右键：切换对话面板显示/隐藏
  - 中键：打开动作列表（左右移动 / 切换动作状态 / 退出）
  - 滚轮：缩放精灵
  - Ctrl+Alt+X：隐藏/显示整个 Galgame 模式（由主窗口热键处理）
"""
from pathlib import Path

from PyQt5.QtCore import (Qt, QPoint, QTimer, pyqtSignal, pyqtProperty,
                          QPropertyAnimation, QSequentialAnimationGroup,
                          QAbstractAnimation, QEasingCurve)
from PyQt5.QtGui import QBitmap, QPixmap, QMouseEvent
from PyQt5.QtWidgets import QWidget, QLabel, QMenu, QApplication

from .sprite_animation import SpriteLoader, SpriteAnimation, SELECTABLE_STATES


class TachieWindow(QWidget):
    """Galgame 风格的角色精灵窗口。"""

    # 拖拽/移动时发射当前位置（主窗口用来移动对话框）
    position_changed = pyqtSignal(int, int)
    # 右键点击发射（主窗口用来切换对话框显示）
    toggle_dialog_requested = pyqtSignal()
    # 动作列表请求退出（主窗口用来关闭 Galgame 模式）
    close_requested = pyqtSignal()

    def __init__(self, assets_dir: str | Path, parent=None):
        super().__init__(parent)
        self._assets_dir = Path(assets_dir)
        self._pinned = True
        self._base_scale = 1.0
        self._scale = 1.0
        self._breath_offset = 0.0
        self._breath_anim = None
        self._bounce_timer = None
        self._bounce_anim = None
        self._speaking_bounce = True
        self._current_pixmap = None
        self._drag_pos = None
        self._movement_direction = 0
        self._movement_timer = QTimer(self)
        self._movement_timer.timeout.connect(self._movement_tick)

        self._init_window()
        self._init_ui()
        self.apply_settings()

    # ── 初始化 ─────────────────────────────────────────────

    def _init_window(self):
        self.setWindowTitle("莲心 - Galgame 角色")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMinimumSize(50, 50)

    def _init_ui(self):
        self._image_label = QLabel(self)
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("background: transparent;")
        animations_dir = self._assets_dir / "animations"
        self._loader = SpriteLoader(animations_dir)
        self._animation = SpriteAnimation(self._loader, self)
        self._animation.frame_changed.connect(self._show_frame)
        self._animation.set_state("idle")

    def apply_settings(self):
        """从全局设置读取精灵缩放比例（默认 100% = 桌宠 95x150）。"""
        from utils.settings import get_settings
        try:
            percent = max(50, min(300, int(get_settings().galgame_sprite_scale)))
        except Exception:
            percent = 100
        self._base_scale = percent / 100.0
        self._scale = self._base_scale
        try:
            self._speaking_bounce = bool(get_settings().galgame_speaking_bounce)
        except Exception:
            self._speaking_bounce = True
        if self._current_pixmap is not None:
            self._apply_current_frame()

    # ── 帧显示 ─────────────────────────────────────────────

    def _show_frame(self, pixmap: QPixmap):
        self._current_pixmap = pixmap
        self._apply_current_frame()

    def _apply_current_frame(self):
        if self._current_pixmap is None:
            return
        old_center = self.geometry().center()
        effective_scale = self._scale * (1.0 + self._breath_offset * 0.02)
        w = max(40, int(self._current_pixmap.width() * effective_scale))
        h = max(40, int(self._current_pixmap.height() * effective_scale))
        scaled = self._current_pixmap.scaled(
            w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())
        if self.size() != scaled.size():
            self.resize(scaled.size())
        # 按当前帧 alpha 生成窗口掩码，透明区域自然穿透，不挡鼠标
        try:
            alpha_mask = QBitmap.fromImage(scaled.toImage().createAlphaMask())
            if alpha_mask.isNull():
                self.clearMask()
            else:
                self.setMask(alpha_mask)
        except Exception:
            self.clearMask()
        delta = old_center - self.geometry().center()
        self.move(self.pos() + delta)

    # ── 动画状态 ───────────────────────────────────────────

    def set_animation_state(self, state: str):
        """切换精灵动作状态（idle/sit/sleep/happy/study/think/...）。"""
        self._animation.set_state(state)

    def current_state(self) -> str:
        return self._animation.state

    # ── 呼吸动画（轻微缩放脉动） ─────────────────────────────

    def _get_breath_offset(self):
        return self._breath_offset

    def _set_breath_offset(self, value):
        self._breath_offset = value
        self._apply_current_frame()

    breath_offset = pyqtProperty(float, _get_breath_offset, _set_breath_offset)

    def start_breathing(self):
        if self._breath_anim is not None:
            return
        group = QSequentialAnimationGroup(self)
        for start, end, duration, curve in (
            (0.0, 1.0, 1500, QEasingCurve.OutCubic),
            (1.0, 0.0, 1500, QEasingCurve.InCubic),
            (0.0, -1.0, 1500, QEasingCurve.OutCubic),
            (-1.0, 0.0, 1500, QEasingCurve.InCubic),
        ):
            anim = QPropertyAnimation(self, b"breath_offset")
            anim.setDuration(duration)
            anim.setStartValue(start)
            anim.setEndValue(end)
            anim.setEasingCurve(curve)
            group.addAnimation(anim)
        group.setLoopCount(-1)
        self._breath_anim = group
        group.start()

    def stop_breathing(self):
        if self._breath_anim:
            self._breath_anim.stop()
            self._breath_anim = None
        self._breath_offset = 0.0
        self._apply_current_frame()

    # ── 说话弹跳 ───────────────────────────────────────────

    def start_talking(self):
        if self._bounce_timer is not None:
            return
        if not self._speaking_bounce:
            return
        self._bounce_timer = QTimer(self)
        self._bounce_timer.timeout.connect(self._play_bounce)
        self._bounce_timer.start(450)

    def stop_talking(self):
        if self._bounce_timer:
            self._bounce_timer.stop()
            self._bounce_timer = None

    def _play_bounce(self):
        if self._bounce_anim is not None and self._bounce_anim.state() == QAbstractAnimation.Running:
            return
        pos = self._image_label.pos()
        anim = QSequentialAnimationGroup(self)
        up = QPropertyAnimation(self._image_label, b"pos")
        up.setDuration(100)
        up.setStartValue(pos)
        up.setEndValue(pos + QPoint(0, -16))
        down = QPropertyAnimation(self._image_label, b"pos")
        down.setDuration(100)
        down.setStartValue(pos + QPoint(0, -16))
        down.setEndValue(pos)
        anim.addAnimation(up)
        anim.addAnimation(down)
        anim.finished.connect(lambda: setattr(self, '_bounce_anim', None))
        self._bounce_anim = anim
        anim.start()

    # ── 左右移动 ───────────────────────────────────────────

    def move_left(self):
        self._movement_direction = -1
        self._animation.set_state("walk_left")
        self._movement_timer.start(40)

    def move_right(self):
        self._movement_direction = 1
        self._animation.set_state("walk_right")
        self._movement_timer.start(40)

    def stop_moving(self):
        self._movement_direction = 0
        self._movement_timer.stop()
        if self._animation.state in ("walk_left", "walk_right"):
            self._animation.set_state("idle")

    def _movement_tick(self):
        if not self._movement_direction:
            return
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(self.x() + self._movement_direction * 2,
                                 area.right() - self.width() + 1))
        y = max(area.top(), min(self.y(), area.bottom() - self.height() + 1))
        self.move(QPoint(x, y))
        self.position_changed.emit(self.x(), self.y())

    # ── 置顶控制 ───────────────────────────────────────────

    def is_pinned(self) -> bool:
        return self._pinned

    def set_pinned(self, pinned: bool):
        self._pinned = pinned
        flags = Qt.FramelessWindowHint | Qt.Tool
        if pinned:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    # ── 鼠标交互 ───────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
        elif event.button() == Qt.RightButton:
            self.toggle_dialog_requested.emit()
            event.accept()
        elif event.button() == Qt.MiddleButton:
            self._show_action_menu(event)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if (event.buttons() & Qt.LeftButton) and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            self.position_changed.emit(self.x(), self.y())
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
            event.accept()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._scale = min(3.0, self._scale * 1.06)
        else:
            self._scale = max(0.4, self._scale / 1.06)
        self._apply_current_frame()
        event.accept()

    # ── 中键动作列表 ───────────────────────────────────────

    def _show_action_menu(self, event: QMouseEvent):
        menu = QMenu(self)
        menu.addAction("向左移动", self.move_left)
        menu.addAction("向右移动", self.move_right)
        menu.addAction("停止移动", self.stop_moving)
        menu.addSeparator()
        for state, label in SELECTABLE_STATES:
            menu.addAction(label, lambda checked=False, s=state: self.set_animation_state(s))
        menu.addSeparator()
        menu.addAction("隐藏/显示对话窗口", self.toggle_dialog_requested.emit)
        menu.addAction("退出 Galgame 模式", self.close_requested.emit)
        menu.exec_(event.globalPos())

    def closeEvent(self, event):
        self._animation.timer.stop()
        self._movement_timer.stop()
        if self._breath_anim:
            self._breath_anim.stop()
        self.stop_talking()
        super().closeEvent(event)
