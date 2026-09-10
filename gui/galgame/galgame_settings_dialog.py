"""
GalgameSettingsDialog：莲心 Galgame 模式设置面板（桌宠风格多页小面板）
包含：外观与对话、行为动作触发 两个页面。
不含主动聊天 / 记忆 / API 配置（galgame 模式已有更完整的主动聊天能力）。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .expression_manager import EMOTION_TRIGGER_KEYS
from .sprite_animation import SELECTABLE_STATES

# 触发事件（设置键 → 显示名），不含情绪（情绪单独列出）
GENERAL_TRIGGERS: list[tuple[str, str]] = [
    ("thinking", "思考中"),
    ("speaking", "说话中"),
]

# 情绪触发（复用表达管理器的触发键顺序）
EMOTION_TRIGGERS: list[tuple[str, str]] = [
    ("emotion_happy", "开心"),
    ("emotion_angry", "生气"),
    ("emotion_sad", "伤心"),
    ("emotion_surprised", "惊讶"),
    ("emotion_confused", "疑惑"),
    ("emotion_shy", "害羞"),
    ("emotion_coquettish", "撒娇"),
    ("emotion_tired", "疲惫"),
    ("emotion_default", "默认"),
]

DEFAULT_TRIGGERS: dict[str, str] = {
    "thinking": "think",
    "speaking": "happy",
    "emotion_happy": "happy",
    "emotion_angry": "think",
    "emotion_sad": "sit",
    "emotion_surprised": "happy",
    "emotion_confused": "think",
    "emotion_shy": "sit",
    "emotion_coquettish": "happy",
    "emotion_tired": "sleep",
    "emotion_default": "idle",
}


class GalgameSettingsDialog(QDialog):
    """Galgame 模式设置：桌宠风格多页小面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        from utils.settings import get_settings
        self._settings = get_settings()
        self.setWindowTitle("莲心 Galgame 设置")
        self.setMinimumSize(560, 420)
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(self._style())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(14)

        heading = QLabel("莲心 Galgame 设置")
        heading.setObjectName("SettingsHeading")
        subtitle = QLabel("调整角色的外观、对话与行为动作触发")
        subtitle.setObjectName("SettingsSubtitle")
        root.addWidget(heading)
        root.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(150)
        self.navigation.setSpacing(3)
        self.navigation.setSelectionMode(QListWidget.SingleSelection)
        self.pages = QStackedWidget()
        body.addWidget(self.navigation)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

        self._build_appearance()
        self._build_triggers()
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        save = QPushButton("保存设置")
        save.setObjectName("PrimaryButton")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    @staticmethod
    def _style():
        return """
        QDialog, QWidget { background: #f0f1f5; color: #303548; }
        QStackedWidget { background: #f4f5f9; border: none; }
        QFrame { background: #f4f5f9; }
        QLabel { color: #4d5368; }
        QLabel#SettingsHeading { color: #303548; font-size: 18px; font-weight: 700; }
        QLabel#SettingsSubtitle { color: #8a90a3; font-size: 11px; }
        QLabel#PageTitle { color: #303548; font-size: 15px; font-weight: 700; }
        QLabel#PageHint { color: #858b9e; font-size: 11px; }
        QListWidget { background: #e9eaf2; border: 1px solid #dfe1eb; border-radius: 10px; padding: 8px; outline: none; }
        QListWidget::item { color: #5e6478; padding: 10px 12px; border-radius: 7px; }
        QListWidget::item:hover { background: #e1e3ef; color: #4654b6; }
        QListWidget::item:selected { background: #6573d8; color: #ffffff; font-weight: 600; }
        QFrame#SettingsCard { background: #ffffff; border: 1px solid #e0e2ec; border-radius: 10px; }
        QSpinBox, QComboBox { background: #ffffff; color: #2f3448; border: 1px solid #d5d8e4; border-radius: 7px; padding: 5px 8px; min-height: 18px; }
        QSpinBox:focus, QComboBox:focus { border: 1px solid #7783dc; }
        QComboBox::drop-down { border: none; width: 22px; }
        QComboBox QAbstractItemView { background: #ffffff; border: 1px solid #d5d8e4; selection-background-color: #6573d8; selection-color: white; }
        QCheckBox { background: transparent; color: #4d5368; spacing: 8px; }
        QCheckBox::indicator { width: 16px; height: 16px; }
        QCheckBox::indicator:unchecked { background: #ffffff; border: 1px solid #c9ccda; border-radius: 4px; }
        QCheckBox::indicator:checked { background: #6573d8; border: 1px solid #6573d8; border-radius: 4px; }
        QPushButton { background: #e8eaf4; color: #4f5bb7; border: 1px solid #d4d7e8; border-radius: 7px; padding: 7px 14px; }
        QPushButton:hover { background: #dde1fb; border-color: #aab3ed; }
        QPushButton:pressed { background: #cbd2f5; }
        QPushButton#PrimaryButton { background: #6573d8; color: white; border: none; font-weight: 600; }
        QPushButton#PrimaryButton:hover { background: #5664ca; }
        """

    def _page(self, title, hint):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(12)
        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        hint_label = QLabel(hint)
        hint_label.setObjectName("PageHint")
        hint_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(hint_label)
        self.pages.addWidget(page)
        item = QListWidgetItem(title)
        self.navigation.addItem(item)
        return page, layout

    def _card(self, layout):
        card = QFrame()
        card.setObjectName("SettingsCard")
        card_layout = QFormLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setVerticalSpacing(12)
        layout.addWidget(card)
        return card_layout

    def _state_combo(self, current: str):
        combo = QComboBox()
        current = current if current in dict(SELECTABLE_STATES) else "idle"
        index = 0
        for i, (value, label) in enumerate(SELECTABLE_STATES):
            combo.addItem(label, value)
            if value == current:
                index = i
        combo.setCurrentIndex(index)
        return combo

    # ── 外观与对话 ─────────────────────────────────────────

    def _build_appearance(self):
        page, layout = self._page("外观与对话", "调整对话窗口的字体、逐字速度、透明度与角色显示比例。")
        form = self._card(layout)

        self._size_spin = QSpinBox()
        self._size_spin.setRange(8, 24)
        self._size_spin.setValue(self._settings.galgame_font_size)
        self._size_spin.setSuffix(" pt")
        form.addRow("字体大小:", self._size_spin)

        self._bold_cb = QCheckBox("加粗")
        self._bold_cb.setChecked(self._settings.galgame_font_bold)
        form.addRow("字体粗细:", self._bold_cb)

        self._speed_spin = QSpinBox()
        self._speed_spin.setRange(5, 200)
        self._speed_spin.setValue(self._settings.galgame_typing_speed)
        self._speed_spin.setSuffix(" ms/字")
        form.addRow("逐字速度:", self._speed_spin)

        self._opacity_spin = QSpinBox()
        self._opacity_spin.setRange(30, 100)
        self._opacity_spin.setValue(self._settings.galgame_panel_opacity)
        self._opacity_spin.setSuffix(" %")
        form.addRow("面板透明度:", self._opacity_spin)

        self._scale_spin = QSpinBox()
        self._scale_spin.setRange(50, 300)
        self._scale_spin.setValue(self._settings.galgame_sprite_scale)
        self._scale_spin.setSuffix(" %")
        form.addRow("角色显示比例:", self._scale_spin)

        self._panel_w_spin = QSpinBox()
        self._panel_w_spin.setRange(240, 900)
        self._panel_w_spin.setValue(self._settings.galgame_panel_width)
        self._panel_w_spin.setSuffix(" px")
        form.addRow("面板宽度:", self._panel_w_spin)

        self._panel_h_spin = QSpinBox()
        self._panel_h_spin.setRange(200, 900)
        self._panel_h_spin.setValue(self._settings.galgame_panel_height)
        self._panel_h_spin.setSuffix(" px")
        form.addRow("面板高度:", self._panel_h_spin)

        self._bounce_cb = QCheckBox("说话时弹跳")
        self._bounce_cb.setChecked(self._settings.galgame_speaking_bounce)
        form.addRow("说话动画:", self._bounce_cb)

        hint = QLabel("启动/隐藏快捷键：<b>Ctrl+Alt+X</b>（全局热键）")
        hint.setObjectName("PageHint")
        layout.addWidget(hint)
        layout.addStretch()

    # ── 行为动作触发 ───────────────────────────────────────

    def _build_triggers(self):
        page, layout = self._page(
            "行为动作触发",
            "配置不同事件发生时莲心播放的动画动作（角色形象为桌宠动画帧）。")
        form = self._card(layout)
        triggers = dict(DEFAULT_TRIGGERS)
        try:
            triggers.update(self._settings.galgame_action_triggers or {})
        except Exception:
            pass
        self._trigger_combos: dict[str, QComboBox] = {}
        for key, label in GENERAL_TRIGGERS + EMOTION_TRIGGERS:
            combo = self._state_combo(triggers.get(key, DEFAULT_TRIGGERS.get(key, "idle")))
            self._trigger_combos[key] = combo
            form.addRow(f"{label}:", combo)
        layout.addStretch()

    # ── 保存 ───────────────────────────────────────────────

    def _save(self):
        s = self._settings
        s.galgame_font_size = self._size_spin.value()
        s.galgame_font_bold = self._bold_cb.isChecked()
        s.galgame_typing_speed = self._speed_spin.value()
        s.galgame_panel_opacity = self._opacity_spin.value()
        s.galgame_panel_width = self._panel_w_spin.value()
        s.galgame_panel_height = self._panel_h_spin.value()
        s.galgame_sprite_scale = self._scale_spin.value()
        s.galgame_speaking_bounce = self._bounce_cb.isChecked()
        triggers = {key: combo.currentData() for key, combo in self._trigger_combos.items()}
        s.galgame_action_triggers = triggers
        self.accept()
