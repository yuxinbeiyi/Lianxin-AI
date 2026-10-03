"""Shared high-contrast theme for settings dialogs.

The dialogs keep their existing layouts and business logic.  This module only
provides a common palette and safe defaults for controls which otherwise fall
back to the Windows theme.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QScrollBar, QStyle, QStyleOptionSlider


class SettingsScrollBar(QScrollBar):
    """Keep thumb dragging reliable across Qt styles and Windows themes."""

    def __init__(self, parent=None):
        super().__init__(Qt.Vertical, parent)
        self._drag_offset = None

    def _handle_rect(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, self
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._handle_rect().contains(event.pos()):
            self._drag_offset = event.pos().y() - self._handle_rect().top()
            self.setSliderDown(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is None or not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarGroove, self
        )
        handle = self._handle_rect()
        travel = max(1, groove.height() - handle.height())
        position = event.pos().y() - groove.top() - self._drag_offset
        ratio = max(0.0, min(1.0, position / travel))
        self.setValue(self.minimum() + round(ratio * self.maximum()))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


SETTINGS_THEME = """
QDialog, QWidget {
    background-color: #1E1E2E;
    color: #E8EAF2;
    font-family: "Microsoft YaHei UI";
    font-size: 9pt;
}
QLabel, QCheckBox, QRadioButton, QGroupBox {
    color: #E8EAF2;
}
QGroupBox {
    border: 1px solid #3D3D5A;
    border-radius: 8px;
    margin-top: 10px;
    padding: 14px 10px 10px 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #E8EAF2;
}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #FFFFFF;
    color: #20232B;
    border: 1px solid #B9BDD0;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #6C7BFF;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #6C7BFF;
}
QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    color: #20232B;
    selection-background-color: #6C7BFF;
    selection-color: #FFFFFF;
}
QPushButton {
    background-color: #2D2D3F;
    color: #E8EAF2;
    border: 1px solid #505071;
    border-radius: 6px;
    padding: 6px 12px;
}
QPushButton:hover { background-color: #3D3D58; border-color: #6C7BFF; }
QPushButton:pressed { background-color: #252538; }
QPushButton:disabled { background-color: #303044; color: #858BA0; border-color: #3D3D5A; }
QTabWidget::pane { border: 1px solid #3D3D5A; background: #1E1E2E; }
QTabBar::tab {
    background: #252538;
    color: #AEB3C2;
    border: 1px solid #3D3D5A;
    padding: 7px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #6C7BFF; color: #FFFFFF; font-weight: bold; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #1E1E2E; width: 10px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #6A6A8A; min-height: 48px; border-radius: 6px; }
QScrollBar::handle:vertical:hover { background: #6C7BFF; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; border: none; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 14px; height: 14px;
    border: 1px solid #858BA0;
    background: #252538;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: #6C7BFF;
    border-color: #6C7BFF;
}
QSlider::groove:horizontal { height: 4px; background: #3D3D5A; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #6C7BFF; border-radius: 7px; }
QToolTip { background: #252538; color: #E8EAF2; border: 1px solid #6C7BFF; }
"""


def apply_settings_theme(dialog: QDialog) -> None:
    """Apply shared defaults without touching widget logic or configuration."""
    dialog.setStyleSheet(SETTINGS_THEME)
