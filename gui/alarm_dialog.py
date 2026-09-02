"""
AlarmDialog：闹钟/倒计时/提醒/待办/自动化 设置对话框

视觉：深空玻璃（Windows 亚克力模糊 + 半透明深蓝卡片 + 莲心渐变强调色）。
亚克力在不可用/老系统上自动回退为不透明深色卡片，功能不受影响。
"""

import ctypes
import os
from datetime import datetime
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox,
    QListWidget, QListWidgetItem, QMessageBox, QFileDialog,
    QGroupBox, QFrame, QCheckBox, QTimeEdit, QMenu, QInputDialog,
    QDialogButtonBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QTime, QDateTime
from PyQt5.QtGui import QFont, QColor
from utils.auto_task_data import AutoTask, ActionStep, SCHEDULE_LABELS, MISSED_LABELS, STATUS_LABELS
from utils.alarm_manager import REPEAT_LABELS, REPEAT_VALUES
from pathlib import Path


def _apply_windows_acrylic(widget, gradient_abgr: int = 0x8C2B1A10) -> bool:
    """给顶层窗口开启 Win10/11 亚克力模糊背景。

    gradient_abgr 为 ABGR 格式的半透明底色（默认深蓝 55% 透明度）。
    仅 Windows 可用；任何异常都返回 False，由调用方回退为不透明卡片。
    """
    try:
        if ctypes.windll.user32 is None:
            return False

        class _ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class _WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        hwnd = int(widget.winId())
        # ACCENT_ENABLE_ACRYLICBLURBEHIND = 4；AccentFlags=2 表示绘制所有层级
        accent = _ACCENT_POLICY(AccentState=4, AccentFlags=2,
                                GradientColor=gradient_abgr, AnimationId=0)
        data = _WINDOWCOMPOSITIONATTRIBDATA(
            Attribute=19,  # WCA_ACCENT_POLICY
            Data=ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
            SizeOfData=ctypes.sizeof(accent),
        )
        result = ctypes.windll.user32.SetWindowCompositionAttribute(
            hwnd, ctypes.byref(data)
        )
        return bool(result)
    except Exception:
        return False


class AlarmDialog(QDialog):
    """闹钟/倒计时/提醒/待办设置对话框"""

    # 信号：闹钟列表变化时通知主窗口
    alarms_changed = pyqtSignal()

    def __init__(self, alarm_manager, parent=None, todo_manager=None, reminder_manager=None):
        super().__init__(parent)
        self._manager = alarm_manager  # 使用主窗口传入的实例
        self._todo_manager = todo_manager  # 待办管理器
        self._reminder_manager = reminder_manager  # 提醒管理器（共享实例）
        self.setWindowTitle("⏰ 闹钟&提醒")
        self.setMinimumSize(560, 640)
        self.resize(600, 700)
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        # 磨砂玻璃：顶层透明，配合 Windows 亚克力模糊（失败自动回退不透明卡片）
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._build_ui()
        self.setStyleSheet(self._QSS)
        self._apply_glass_backdrop()
        self._refresh_alarm_list()
        self._refresh_countdown_list()
        if self._todo_manager:
            self._refresh_todo_list()

        # 每秒刷新倒计时显示（只刷新显示，不处理结束事件）
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._refresh_countdown_list)
        self._update_timer.start(1000)

    # ── 磨砂玻璃 ─────────────────────────────────────────────

    def _apply_glass_backdrop(self):
        """尝试开启系统级亚克力模糊；失败则回退为不透明深色卡片。"""
        if _apply_windows_acrylic(self, 0x8C2B1A10):
            return
        # 回退：不透明深色（视觉接近，但没有真实模糊）
        card = self.findChild(QFrame, "glassCard")
        if card is not None:
            card.setStyleSheet(
                "#glassCard { background-color: #10182B;"
                " border: 1px solid rgba(120,140,220,90); border-radius: 14px; }"
            )

    # ── 全局设计令牌 ─────────────────────────────────────────

    _QSS = """
    QDialog { background: transparent; }
    #glassCard {
        background-color: rgba(16, 24, 43, 205);
        border: 1px solid rgba(120, 140, 220, 90);
        border-radius: 14px;
    }
    #glassTitle { color: #F0F3FF; font-size: 17px; font-weight: 700; }
    #glassSubtitle { color: #8FA0C0; font-size: 11px; }
    QLabel { color: #E9EDF2; background: transparent; }

    QTabWidget::pane {
        border: 1px solid rgba(120, 140, 220, 70);
        border-radius: 10px;
        top: -1px;
        background: rgba(20, 27, 46, 130);
    }
    QTabBar { background: transparent; }
    QTabBar::tab {
        background: rgba(28, 38, 66, 120);
        color: #8FA0C0;
        padding: 7px 13px;
        border-top-left-radius: 9px;
        border-top-right-radius: 9px;
        margin-right: 4px;
        font-size: 12px;
    }
    QTabBar::tab:hover { color: #C7D2F2; }
    QTabBar::tab:selected {
        background: rgba(108, 123, 255, 60);
        color: #FFFFFF;
        border: 1px solid rgba(108, 123, 255, 130);
        border-bottom: none;
    }

    QGroupBox {
        border: 1px solid rgba(120, 140, 220, 70);
        border-radius: 10px;
        margin-top: 12px;
        padding: 10px 8px 8px 8px;
        color: #C7D2F2;
        font-weight: 600;
        background: rgba(28, 38, 66, 110);
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: #8ED6E8;
    }

    QLineEdit, QComboBox, QSpinBox, QTimeEdit, QDateTimeEdit {
        background: rgba(9, 14, 28, 160);
        color: #E9EDF2;
        border: 1px solid #303A5C;
        border-radius: 8px;
        padding: 5px 8px;
        selection-background-color: #6C7BFF;
    }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus {
        border: 1px solid #6C7BFF;
        background: rgba(9, 14, 28, 200);
    }
    QComboBox::drop-down { border: none; width: 22px; }
    QComboBox::down-arrow {
        width: 0; height: 0;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 5px solid #8FA0C0;
    }
    QComboBox QAbstractItemView {
        background: #141B2E; color: #E9EDF2;
        border: 1px solid #303A5C; border-radius: 8px;
        selection-background-color: rgba(108, 123, 255, 90);
    }
    QSpinBox::up-button, QTimeEdit::up-button,
    QSpinBox::down-button, QTimeEdit::down-button {
        background: rgba(38, 49, 79, 160); border: none; width: 16px;
    }
    QSpinBox::up-button:hover, QTimeEdit::up-button:hover { background: #33406B; }

    QListWidget {
        background: rgba(9, 14, 28, 140);
        border: 1px solid rgba(120, 140, 220, 70);
        border-radius: 10px;
        color: #E9EDF2;
    }
    QListWidget::item { padding: 8px; border-radius: 6px; }
    QListWidget::item:selected {
        background: rgba(108, 123, 255, 75); color: #FFFFFF;
    }
    QListWidget::item:hover { background: rgba(108, 123, 255, 38); }

    QCheckBox { color: #AEB7D4; spacing: 6px; background: transparent; }
    QCheckBox::indicator {
        width: 16px; height: 16px;
        border: 1px solid #3D4A73; border-radius: 4px;
        background: rgba(9, 14, 28, 160);
    }
    QCheckBox::indicator:checked { background: #6C7BFF; border-color: #7C8BFF; }

    QPlainTextEdit {
        background: rgba(9, 14, 28, 140);
        color: #AEB7D4;
        border: 1px solid rgba(120, 140, 220, 70);
        border-radius: 10px;
        padding: 4px;
    }

    QPushButton {
        background: rgba(38, 49, 79, 200);
        color: #E9EDF2;
        border: 1px solid #3D4A73;
        border-radius: 8px;
        padding: 6px 14px;
        font-size: 12px;
    }
    QPushButton:hover { background: rgba(51, 64, 107, 220); border-color: #6C7BFF; }
    QPushButton:pressed { background: rgba(28, 36, 60, 230); }
    QPushButton[variant="primary"] {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #7C8BFF, stop:1 #5F6FF0);
        color: #FFFFFF; border: none; font-weight: 600;
    }
    QPushButton[variant="primary"]:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #8D9BFF, stop:1 #6B7BFA);
    }
    QPushButton[variant="success"] {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #3BC96A, stop:1 #2A9E4F);
        color: #FFFFFF; border: none; font-weight: 600;
    }
    QPushButton[variant="warning"] {
        background: rgba(224, 138, 46, 200);
        color: #FFFFFF; border: none;
    }
    QPushButton[variant="danger"] {
        background: rgba(255, 90, 110, 26);
        color: #FF8A9B; border: 1px solid rgba(255, 90, 110, 100);
    }
    QPushButton[variant="danger"]:hover { background: rgba(255, 90, 110, 60); }

    QMenu { background: #141B2E; color: #E9EDF2; border: 1px solid #303A5C; border-radius: 8px; padding: 4px; }
    QMenu::item { padding: 6px 18px; border-radius: 6px; }
    QMenu::item:selected { background: rgba(108, 123, 255, 90); }

    QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
    QScrollBar::handle:vertical { background: rgba(108, 123, 255, 110); border-radius: 4px; min-height: 30px; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    """

    # ── 界面构建 ─────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("glassCard")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setSpacing(10)
        layout.setContentsMargins(18, 14, 18, 14)

        # 标题区
        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("⏰ 闹钟与提醒")
        title.setObjectName("glassTitle")
        title_col.addWidget(title)
        subtitle = QLabel("定时响铃 · 倒计时 · 重复提醒 · 待办清单 · 自动化任务")
        subtitle.setObjectName("glassSubtitle")
        title_col.addWidget(subtitle)
        title_row.addLayout(title_col)
        title_row.addStretch()
        layout.addLayout(title_row)

        # 标签页
        self._tab_widget = QTabWidget()

        # 闹钟标签页
        alarm_tab = self._build_alarm_tab()
        # 倒计时标签页
        countdown_tab = self._build_countdown_tab()
        # 提醒标签页
        reminder_tab = self._build_reminder_tab()
        # 待办标签页
        todo_tab = self._build_todo_tab()

        self._tab_widget.addTab(alarm_tab, "⏰ 定时闹钟")
        self._tab_widget.addTab(countdown_tab, "⏳ 倒计时")
        self._tab_widget.addTab(reminder_tab, "📋 提醒")
        self._tab_widget.addTab(todo_tab, "✅ 待办")
        auto_tab = self._build_auto_task_tab()
        self._tab_widget.addTab(auto_tab, "🤖 自动化")
        layout.addWidget(self._tab_widget)

    def _build_alarm_tab(self):
        """构建闹钟设置标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # 添加闹钟表单
        form_group = QGroupBox("添加新闹钟")
        form_group.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        form_layout = QVBoxLayout(form_group)

        # 闹钟名称
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("名称："))
        self._alarm_name_edit = QLineEdit()
        self._alarm_name_edit.setPlaceholderText("例如：吃药、开会")
        name_row.addWidget(self._alarm_name_edit)
        form_layout.addLayout(name_row)

        # 时间设置（QTimeEdit 滚轮/上下键直观选择）
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("时间："))
        self._alarm_time_edit = QTimeEdit()
        self._alarm_time_edit.setDisplayFormat("HH:mm")
        self._alarm_time_edit.setTime(QTime.currentTime())
        self._alarm_time_edit.setFixedWidth(120)
        time_row.addWidget(self._alarm_time_edit)
        time_row.addStretch()
        form_layout.addLayout(time_row)

        # 重复模式
        repeat_row = QHBoxLayout()
        repeat_row.addWidget(QLabel("重复："))
        self._alarm_repeat_combo = QComboBox()
        self._alarm_repeat_combo.addItems(list(REPEAT_LABELS.values()))
        repeat_row.addWidget(self._alarm_repeat_combo)
        repeat_row.addStretch()
        form_layout.addLayout(repeat_row)

        # 添加按钮
        add_btn = QPushButton("➕ 添加闹钟")
        add_btn.setFixedHeight(34)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setProperty("variant", "primary")
        add_btn.clicked.connect(self._on_add_alarm)
        form_layout.addWidget(add_btn)

        layout.addWidget(form_group)

        # 闹钟列表
        list_group = QGroupBox("闹钟列表")
        list_group.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        list_layout = QVBoxLayout(list_group)

        self._alarm_list = QListWidget()
        list_layout.addWidget(self._alarm_list)

        # 操作按钮行
        alarm_btn_row = QHBoxLayout()
        self._alarm_delete_btn = QPushButton("🗑 删除选中")
        self._alarm_delete_btn.setProperty("variant", "danger")
        self._alarm_delete_btn.setCursor(Qt.PointingHandCursor)
        self._alarm_delete_btn.clicked.connect(self._on_delete_alarm)
        self._alarm_toggle_btn = QPushButton("启用/禁用")
        self._alarm_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._alarm_toggle_btn.clicked.connect(self._on_toggle_alarm)
        alarm_btn_row.addWidget(self._alarm_delete_btn)
        alarm_btn_row.addWidget(self._alarm_toggle_btn)
        alarm_btn_row.addStretch()
        list_layout.addLayout(alarm_btn_row)

        layout.addWidget(list_group)

        return tab

    def _build_countdown_tab(self):
        """构建倒计时标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # 动态倒计时大字显示区域
        self._dynamic_countdown_label = QLabel("")
        self._dynamic_countdown_label.setFont(QFont("Consolas", 28, QFont.Bold))
        self._dynamic_countdown_label.setAlignment(Qt.AlignCenter)
        self._dynamic_countdown_label.setStyleSheet(
            "color: #7C8BFF; padding: 15px;"
            " background-color: rgba(28, 38, 66, 130);"
            " border: 1px solid rgba(108, 123, 255, 90); border-radius: 12px;"
        )
        self._dynamic_countdown_label.hide()
        layout.addWidget(self._dynamic_countdown_label)

        # 添加倒计时表单
        form_group = QGroupBox("开始倒计时")
        form_group.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        form_layout = QVBoxLayout(form_group)

        # 倒计时名称
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("名称："))
        self._cd_name_edit = QLineEdit()
        self._cd_name_edit.setPlaceholderText("例如：煮鸡蛋、休息")
        name_row.addWidget(self._cd_name_edit)
        form_layout.addLayout(name_row)

        # 时间设置
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("时长："))
        self._cd_hour_spin = QSpinBox()
        self._cd_hour_spin.setRange(0, 99)
        self._cd_hour_spin.setSuffix(" 时")
        self._cd_hour_spin.setFixedWidth(80)
        self._cd_min_spin = QSpinBox()
        self._cd_min_spin.setRange(0, 59)
        self._cd_min_spin.setSuffix(" 分")
        self._cd_min_spin.setFixedWidth(80)
        self._cd_sec_spin = QSpinBox()
        self._cd_sec_spin.setRange(0, 59)
        self._cd_sec_spin.setSuffix(" 秒")
        self._cd_sec_spin.setFixedWidth(80)
        time_row.addWidget(self._cd_hour_spin)
        time_row.addWidget(self._cd_min_spin)
        time_row.addWidget(self._cd_sec_spin)
        time_row.addStretch()
        form_layout.addLayout(time_row)

        # 开始按钮
        start_btn = QPushButton("▶ 开始倒计时")
        start_btn.setFixedHeight(34)
        start_btn.setCursor(Qt.PointingHandCursor)
        start_btn.setProperty("variant", "success")
        start_btn.clicked.connect(self._on_start_countdown)
        form_layout.addWidget(start_btn)

        layout.addWidget(form_group)

        # 运行中倒计时列表
        running_group = QGroupBox("运行中的倒计时")
        running_group.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        running_layout = QVBoxLayout(running_group)

        self._countdown_list = QListWidget()
        running_layout.addWidget(self._countdown_list)

        # 取消按钮
        cancel_btn = QPushButton("✖ 取消选中")
        cancel_btn.setProperty("variant", "warning")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self._on_cancel_countdown)
        running_layout.addWidget(cancel_btn)

        layout.addWidget(running_group)

        return tab

    # ── 辅助方法 ─────────────────────────────────────────────

    def _refresh_alarm_list(self):
        """刷新闹钟列表显示"""
        self._alarm_list.clear()
        for alarm in self._manager.get_alarms():
            repeat_text = REPEAT_LABELS.get(alarm.repeat, "仅一次")
            status = "✓" if alarm.enabled else "✗"
            text = f"[{status}] {alarm.time_str} {alarm.name} | {repeat_text}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, alarm.id)
            if not alarm.enabled:
                item.setForeground(Qt.gray)
            self._alarm_list.addItem(item)

    def _refresh_countdown_list(self):
        """刷新倒计时列表显示，并更新动态大字显示（实时计算剩余时间）"""
        # 获取运行中的倒计时
        countdowns = self._manager.get_countdowns()
        now = datetime.now()
        
        self._countdown_list.clear()
        
        # 动态大字显示（显示第一个倒计时）
        if countdowns:
            cd = countdowns[0]
            if cd.end_time:
                remaining = int((cd.end_time - now).total_seconds())
                remaining = max(0, remaining)
                h = remaining // 3600
                m = (remaining % 3600) // 60
                s = remaining % 60
                time_str = f"{h:02d}:{m:02d}:{s:02d}"
                self._dynamic_countdown_label.setText(f"⏳ 倒计时中\n{time_str}")
                self._dynamic_countdown_label.repaint()
                self._dynamic_countdown_label.show()
            else:
                self._dynamic_countdown_label.hide()
        else:
            self._dynamic_countdown_label.hide()
        
        # 列表显示
        for cd in countdowns:
            if cd.end_time:
                remaining = int((cd.end_time - now).total_seconds())
                remaining = max(0, remaining)
                h = remaining // 3600
                m = (remaining % 3600) // 60
                s = remaining % 60
                time_str = f"{h:02d}:{m:02d}:{s:02d}"
                text = f"⏳ {cd.name} | 剩余 {time_str}"
            else:
                text = f"⏳ {cd.name} | 剩余 00:00:00"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, cd.id)
            self._countdown_list.addItem(item)

    # ── 闹钟操作 ─────────────────────────────────────────────

    def _on_add_alarm(self):
        """添加闹钟"""
        name = self._alarm_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入闹钟名称")
            return

        time_str = self._alarm_time_edit.time().toString("HH:mm")
        repeat_text = self._alarm_repeat_combo.currentText()
        repeat = REPEAT_VALUES.get(repeat_text, "once")

        self._manager.add_alarm(name, time_str, repeat)
        self._refresh_alarm_list()
        self.alarms_changed.emit()

        # 清空表单
        self._alarm_name_edit.clear()

    def _on_delete_alarm(self):
        """删除选中闹钟"""
        current = self._alarm_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个闹钟")
            return
        alarm_id = current.data(Qt.UserRole)
        reply = QMessageBox.question(self, "确认删除", "确定要删除这个闹钟吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._manager.delete_alarm(alarm_id)
            self._refresh_alarm_list()
            self.alarms_changed.emit()

    def _on_toggle_alarm(self):
        """启用/禁用选中闹钟"""
        current = self._alarm_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个闹钟")
            return
        alarm_id = current.data(Qt.UserRole)
        self._manager.toggle_enabled(alarm_id)
        self._refresh_alarm_list()
        self.alarms_changed.emit()

    # ── 倒计时操作 ───────────────────────────────────────────

    def _on_start_countdown(self):
        """开始倒计时"""
        name = self._cd_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入倒计时名称")
            return

        total_seconds = (self._cd_hour_spin.value() * 3600 +
                        self._cd_min_spin.value() * 60 +
                        self._cd_sec_spin.value())
        if total_seconds <= 0:
            QMessageBox.warning(self, "提示", "请设置大于0的时长")
            return

        self._manager.add_countdown(name, total_seconds)
        self._refresh_countdown_list()

        # 清空表单
        self._cd_name_edit.clear()
        self._cd_hour_spin.setValue(0)
        self._cd_min_spin.setValue(0)
        self._cd_sec_spin.setValue(0)

    def _on_cancel_countdown(self):
        """取消选中的倒计时"""
        current = self._countdown_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个倒计时")
            return
        cd_id = current.data(Qt.UserRole)
        self._manager.remove_countdown(cd_id)
        self._refresh_countdown_list()

    # ── 提醒标签页 ──────────────────────────────────────────

    def _build_reminder_tab(self):
        """构建提醒管理标签页"""
        from utils.settings import get_settings

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        if self._reminder_manager is None:
            from utils.reminder_manager import ReminderManager
            self._reminder_manager = ReminderManager()
        self._reminder_settings = get_settings()

        # 全局智能提醒开关
        self._reminder_smart_cb = QCheckBox("全局智能提醒（AI生成文案）")
        self._reminder_smart_cb.setChecked(self._reminder_settings.global_smart_reminder)
        self._reminder_smart_cb.stateChanged.connect(
            lambda: setattr(self._reminder_settings, 'global_smart_reminder', self._reminder_smart_cb.isChecked())
        )
        layout.addWidget(self._reminder_smart_cb)

        # 添加新提醒区域
        add_layout = QHBoxLayout()
        add_layout.addWidget(QLabel("名称:"))
        self._rem_name_edit = QLineEdit()
        self._rem_name_edit.setPlaceholderText("如：喝水")
        add_layout.addWidget(self._rem_name_edit)

        add_layout.addWidget(QLabel("时间:"))
        self._rem_time_edit = QTimeEdit()
        self._rem_time_edit.setDisplayFormat("HH:mm")
        self._rem_time_edit.setTime(QTime.currentTime())
        add_layout.addWidget(self._rem_time_edit)

        add_layout.addWidget(QLabel("重复:"))
        self._rem_rule_combo = QComboBox()
        self._rem_rule_combo.addItems(["一次", "每天", "每周", "每月"])
        add_layout.addWidget(self._rem_rule_combo)

        add_btn = QPushButton("添加")
        add_btn.setProperty("variant", "primary")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_reminder)
        add_layout.addWidget(add_btn)
        layout.addLayout(add_layout)

        # 提醒列表
        self._reminder_list = QListWidget()
        self._reminder_list.setSelectionMode(QListWidget.SingleSelection)
        self._reminder_list.itemDoubleClicked.connect(lambda item: self._toggle_reminder_item(item))
        layout.addWidget(self._reminder_list)

        # 操作按钮
        btn_layout = QHBoxLayout()
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete_reminder_item)
        toggle_btn = QPushButton("启用/禁用")
        toggle_btn.clicked.connect(lambda: self._toggle_reminder_item(None))
        btn_layout.addWidget(del_btn)
        btn_layout.addWidget(toggle_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._refresh_reminder_list()
        return tab

    def _refresh_reminder_list(self):
        self._reminder_list.clear()
        for r in self._reminder_manager.get_all():
            status = "✅" if r["enabled"] else "❌"
            repeat_text = {"once": "一次", "daily": "每天", "weekly": "每周", "monthly": "每月"}.get(r["rule"], r["rule"])
            text = f"{status} {r['name']}  {r['time']}  {repeat_text}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, r["id"])
            if r["enabled"]:
                item.setForeground(QColor(255, 140, 0))
            self._reminder_list.addItem(item)

    def _add_reminder(self):
        name = self._rem_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入提醒名称")
            return
        time_str = self._rem_time_edit.time().toString("HH:mm")
        rule_text = self._rem_rule_combo.currentText()
        rule_map = {"一次": "once", "每天": "daily", "每周": "weekly", "每月": "monthly"}
        self._reminder_manager.add(name, rule_map[rule_text], time_str)
        self._refresh_reminder_list()
        self._rem_name_edit.clear()

    def _delete_reminder_item(self):
        current = self._reminder_list.currentItem()
        if not current:
            return
        rid = current.data(Qt.UserRole)
        reply = QMessageBox.question(self, "确认删除", "确定要删除这个提醒吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._reminder_manager.delete(rid)
            self._refresh_reminder_list()

    def _toggle_reminder_item(self, item=None):
        if item is None:
            item = self._reminder_list.currentItem()
        if not item:
            return
        rid = item.data(Qt.UserRole)
        for r in self._reminder_manager.get_all():
            if r["id"] == rid:
                self._reminder_manager.enable(rid, not r["enabled"])
                self._refresh_reminder_list()
                return

    # ── 待办标签页 ──────────────────────────────────────────

    def _build_todo_tab(self):
        """构建待办清单标签页"""
        from utils.todo_manager import PRIORITY_DISPLAY

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍"))
        self._todo_search_edit = QLineEdit()
        self._todo_search_edit.setPlaceholderText("搜索待办...")
        self._todo_search_edit.textChanged.connect(lambda: self._refresh_todo_list())
        search_layout.addWidget(self._todo_search_edit)
        layout.addLayout(search_layout)

        # 待办列表
        self._todo_list = QListWidget()
        self._todo_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._todo_list.customContextMenuRequested.connect(self._todo_context_menu)
        self._todo_list.setAlternatingRowColors(True)
        self._todo_list.setStyleSheet(
            "QListWidget::item { padding: 4px; border-radius: 6px; }"
            "QListWidget::alternating-background-color: rgba(28, 38, 66, 90);"
        )
        layout.addWidget(self._todo_list)

        # 按钮行
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("➕ 添加待办")
        add_btn.setProperty("variant", "primary")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._todo_add)
        del_btn = QPushButton("🗑️ 删除")
        del_btn.setProperty("variant", "danger")
        del_btn.clicked.connect(self._todo_delete)
        complete_btn = QPushButton("✔️ 完成/重开")
        complete_btn.clicked.connect(self._todo_toggle_complete)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(del_btn)
        btn_layout.addWidget(complete_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # "自动添加时询问" 复选框（与确认弹窗双向同步）
        from config import get_todo_auto_confirm, save_todo_auto_confirm
        self._todo_confirm_check = QCheckBox("自动添加待办时询问我")
        self._todo_confirm_check.setChecked(get_todo_auto_confirm())
        self._todo_confirm_check.setStyleSheet("font-size: 12px; color: #8FA0C0; margin-top: 4px; background: transparent;")
        self._todo_confirm_check.toggled.connect(
            lambda checked: save_todo_auto_confirm(checked)
        )
        layout.addWidget(self._todo_confirm_check)

        # 控制对话结束后的自动待办提议；不影响用户主动设置待办或提醒。
        from config import get_todo_auto_suggest, save_todo_auto_suggest
        self._todo_suggest_check = QCheckBox("允许莲心根据聊天内容自动提议待办/提醒")
        self._todo_suggest_check.setChecked(get_todo_auto_suggest())
        self._todo_suggest_check.setToolTip("关闭后不再弹出“莲心想要为你添加以下待办/提醒”窗口")
        self._todo_suggest_check.setStyleSheet("font-size: 12px; color: #8FA0C0; background: transparent;")
        self._todo_suggest_check.toggled.connect(save_todo_auto_suggest)
        layout.addWidget(self._todo_suggest_check)

        if self._todo_manager is None:
            empty_label = QLabel("待办功能不可用")
            empty_label.setAlignment(Qt.AlignCenter)
            empty_label.setStyleSheet("color: #FF8A9B; padding: 40px; background: transparent;")
            layout.addWidget(empty_label)

        return tab

    def _refresh_todo_list(self):
        """刷新待办列表显示"""
        if self._todo_manager is None:
            return
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QThread
        from utils.todo_manager import PRIORITY_DISPLAY

        if QThread.currentThread() is not QApplication.instance().thread():
            QTimer.singleShot(0, self._refresh_todo_list)
            return

        keyword = self._todo_search_edit.text().strip().lower() if hasattr(self, '_todo_search_edit') else ""
        self._todo_list.clear()
        todos = self._todo_manager.get_todos(completed=True)

        def sort_key(t):
            status_order = 0 if not t.completed else 1
            priority_order = {"high": 0, "medium": 1, "low": 2}.get(t.priority, 1)
            due_order = 0 if t.due_time else 1
            due_time = t.due_time if t.due_time else "9999-12-31"
            return (status_order, priority_order, due_order, due_time)
        todos.sort(key=sort_key)

        for todo in todos:
            if keyword and keyword.lower() not in todo.title.lower():
                continue
            item = QListWidgetItem()
            item.setData(Qt.UserRole, todo.id)
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(5, 2, 5, 2)

            cb = QCheckBox()
            cb.setChecked(todo.completed)
            def make_handler(tid):
                return lambda state: self._todo_checkbox_changed(tid, state)
            cb.stateChanged.connect(make_handler(todo.id))
            row.addWidget(cb)

            priority_text = PRIORITY_DISPLAY.get(todo.priority, "中")
            plbl = QLabel(priority_text)
            plbl.setFixedWidth(50)
            if todo.priority == "high":
                plbl.setStyleSheet("color: #FF3B30; font-weight: bold;")
            elif todo.priority == "medium":
                plbl.setStyleSheet("color: #FF9500;")
            else:
                plbl.setStyleSheet("color: #888888;")
            row.addWidget(plbl)

            due_str = ""
            if todo.due_time:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(todo.due_time)
                    due_str = dt.strftime("%m-%d %H:%M")
                except:
                    due_str = ""
            due_lbl = QLabel(due_str)
            due_lbl.setFixedWidth(90)
            
            row.addWidget(due_lbl)

            title_lbl = QLabel(todo.title)
            title_lbl.setWordWrap(True)
            if todo.completed:
                title_lbl.setStyleSheet("color: #AAAAAA; text-decoration: line-through;")
            row.addWidget(title_lbl, 1)

            item.setSizeHint(widget.sizeHint())
            self._todo_list.addItem(item)
            self._todo_list.setItemWidget(item, widget)

    def _todo_checkbox_changed(self, todo_id, state):
        if self._todo_manager is None:
            return
        self._todo_manager.toggle_complete(todo_id)

    def _todo_add(self):
        if self._todo_manager is None:
            return
        from PyQt5.QtWidgets import QInputDialog, QDialog, QDateTimeEdit, QComboBox, QDialogButtonBox
        title, ok = QInputDialog.getText(self, "添加待办", "待办标题:")
        if not ok or not title.strip():
            return
        title = title.strip()
        dlg = QDialog(self)
        dlg.setWindowTitle("详细设置（可选）")
        dlg_layout = QVBoxLayout(dlg)
        dt_edit = QDateTimeEdit()
        dt_edit.setCalendarPopup(True)
        dt_edit.setDateTime(QDateTime.currentDateTime())
        dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        dlg_layout.addWidget(QLabel("截止时间（可选）:"))
        dlg_layout.addWidget(dt_edit)
        priority_combo = QComboBox()
        priority_combo.addItems(["中", "高", "低"])
        dlg_layout.addWidget(QLabel("优先级:"))
        dlg_layout.addWidget(priority_combo)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btn_box)
        if dlg.exec_() == QDialog.Accepted:
            due_time = dt_edit.dateTime().toString("yyyy-MM-ddTHH:mm:ss")
            priority_text = priority_combo.currentText()
            priority = {"高": "high", "中": "medium", "低": "low"}.get(priority_text, "medium")
            self._todo_manager.add_todo(title, due_time, priority)
            self._refresh_todo_list()

    def _todo_delete(self):
        if self._todo_manager is None:
            return
        current = self._todo_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个待办")
            return
        todo_id = current.data(Qt.UserRole)
        reply = QMessageBox.question(self, "确认删除", "确定要永久删除这个待办吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._todo_manager.delete_todo(todo_id)
            self._refresh_todo_list()

    def _todo_toggle_complete(self):
        if self._todo_manager is None:
            return
        current = self._todo_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个待办")
            return
        self._todo_manager.toggle_complete(current.data(Qt.UserRole))
        self._refresh_todo_list()

    def _todo_context_menu(self, pos):
        if self._todo_manager is None:
            return
        item = self._todo_list.itemAt(pos)
        if not item:
            return
        todo_id = item.data(Qt.UserRole)
        todo = self._todo_manager.get_todo_by_id(todo_id)
        if not todo:
            return
        menu = QMenu()
        edit_action = menu.addAction("✏️ 编辑")
        toggle_action = menu.addAction("✔️ 完成/重开")
        delete_action = menu.addAction("🗑️ 删除")
        action = menu.exec_(self._todo_list.mapToGlobal(pos))
        if action == edit_action:
            self._todo_edit(todo)
        elif action == toggle_action:
            self._todo_manager.toggle_complete(todo_id)
            self._refresh_todo_list()
        elif action == delete_action:
            reply = QMessageBox.question(self, "确认删除", "确定要永久删除这个待办吗？",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self._todo_manager.delete_todo(todo_id)
                self._refresh_todo_list()

    def _todo_edit(self, todo):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QDateTimeEdit, QComboBox, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑待办")
        layout = QVBoxLayout(dlg)
        title_edit = QLineEdit(todo.title)
        layout.addWidget(QLabel("标题:"))
        layout.addWidget(title_edit)
        dt_edit = QDateTimeEdit()
        if todo.due_time:
            try:
                dt = QDateTime.fromString(todo.due_time, "yyyy-MM-ddTHH:mm:ss")
                dt_edit.setDateTime(dt)
            except:
                dt_edit.setDateTime(QDateTime.currentDateTime())
        else:
            dt_edit.setDateTime(QDateTime.currentDateTime())
        dt_edit.setCalendarPopup(True)
        dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        layout.addWidget(QLabel("截止时间:"))
        layout.addWidget(dt_edit)
        priority_combo = QComboBox()
        priority_combo.addItems(["高", "中", "低"])
        priority_map = {"high": "高", "medium": "中", "low": "低"}
        priority_combo.setCurrentText(priority_map.get(todo.priority, "中"))
        layout.addWidget(QLabel("优先级:"))
        layout.addWidget(priority_combo)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)
        if dlg.exec_() == QDialog.Accepted:
            new_title = title_edit.text().strip()
            if new_title:
                new_due = dt_edit.dateTime().toString("yyyy-MM-ddTHH:mm:ss")
                new_priority = {"高": "high", "中": "medium", "低": "low"}.get(priority_combo.currentText(), "medium")
                self._todo_manager.update_todo(todo.id, title=new_title, due_time=new_due, priority=new_priority)
                self._refresh_todo_list()

    # ── 窗口事件 ─────────────────────────────────────────────

    def showEvent(self, event):
        """每次显示时注册待办观察者并刷新"""
        super().showEvent(event)
        if self._todo_manager:
            self._todo_manager.register_observer(self._refresh_todo_list)
            self._refresh_todo_list()
    # ── 自动化任务标签页 ────────────────────────────────────

    def _build_auto_task_tab(self):
        from brain.auto_task_manager import get_auto_task_manager
        self._auto_task_mgr = get_auto_task_manager()

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        hint = QLabel("🤖 莲心可根据自然语言指令自动执行定时任务，如「每天14:00清理回收站」")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8ED6E8; font-size: 12px; padding: 4px; background: transparent;")
        layout.addWidget(hint)

        # 添加任务表单
        form_group = QGroupBox("添加自动化任务")
        form_group.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        form_layout = QVBoxLayout(form_group)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("名称："))
        self._auto_name_edit = QLineEdit()
        self._auto_name_edit.setPlaceholderText("例如：每天清理回收站")
        name_row.addWidget(self._auto_name_edit)
        form_layout.addLayout(name_row)

        sched_row = QHBoxLayout()
        sched_row.addWidget(QLabel("类型："))
        self._auto_type_combo = QComboBox()
        self._auto_type_combo.addItems(list(SCHEDULE_LABELS.values()))
        self._auto_type_combo.currentTextChanged.connect(self._on_auto_type_changed)
        sched_row.addWidget(self._auto_type_combo)
        sched_row.addWidget(QLabel("时间："))
        self._auto_time_edit = QTimeEdit()
        self._auto_time_edit.setDisplayFormat("HH:mm")
        self._auto_time_edit.setTime(QTime(8, 0))
        sched_row.addWidget(self._auto_time_edit)
        sched_row.addStretch()
        form_layout.addLayout(sched_row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("间隔(分钟)："))
        self._auto_interval_spin = QSpinBox()
        self._auto_interval_spin.setRange(1, 1440)
        self._auto_interval_spin.setValue(60)
        self._auto_interval_spin.setVisible(False)
        interval_row.addWidget(self._auto_interval_spin)
        interval_row.addStretch()
        form_layout.addLayout(interval_row)
        self._auto_interval_row = interval_row

        missed_row = QHBoxLayout()
        missed_row.addWidget(QLabel("错过策略："))
        self._auto_missed_combo = QComboBox()
        self._auto_missed_combo.addItems(list(MISSED_LABELS.values()))
        missed_row.addWidget(self._auto_missed_combo)
        missed_row.addStretch()
        form_layout.addLayout(missed_row)

        add_btn = QPushButton("➕ 添加任务")
        add_btn.setFixedHeight(34)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setProperty("variant", "primary")
        add_btn.clicked.connect(self._on_add_auto_task)
        form_layout.addWidget(add_btn)

        layout.addWidget(form_group)

        # 任务列表
        list_group = QGroupBox("自动化任务列表")
        list_group.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        list_layout = QVBoxLayout(list_group)

        self._auto_task_list = QListWidget()
        self._auto_task_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._auto_task_list.customContextMenuRequested.connect(self._auto_context_menu)
        list_layout.addWidget(self._auto_task_list)

        btn_row = QHBoxLayout()
        self._auto_delete_btn = QPushButton("删除选中")
        self._auto_delete_btn.clicked.connect(self._on_delete_auto_task)
        self._auto_toggle_btn = QPushButton("暂停/恢复")
        self._auto_toggle_btn.clicked.connect(self._on_toggle_auto_task)
        self._auto_edit_btn = QPushButton("编辑")
        self._auto_edit_btn.clicked.connect(self._on_edit_auto_task)
        self._auto_view_log_btn = QPushButton("查看日志")
        self._auto_view_log_btn.clicked.connect(self._on_view_auto_log)
        # P4: 清理已完成的 once 任务
        self._auto_cleanup_btn = QPushButton("🧹 清理已完成")
        self._auto_cleanup_btn.setToolTip("移除已完成超过24小时的一次性任务")
        self._auto_cleanup_btn.clicked.connect(self._on_cleanup_completed)
        # P5: 取消执行按钮
        self._auto_cancel_btn = QPushButton("🛑 取消执行")
        self._auto_cancel_btn.setToolTip("取消当前正在执行的任务")
        self._auto_cancel_btn.clicked.connect(self._on_cancel_execution)
        btn_row.addWidget(self._auto_delete_btn)
        btn_row.addWidget(self._auto_toggle_btn)
        btn_row.addWidget(self._auto_edit_btn)
        btn_row.addWidget(self._auto_view_log_btn)
        btn_row.addWidget(self._auto_cleanup_btn)
        btn_row.addWidget(self._auto_cancel_btn)
        btn_row.addStretch()
        list_layout.addLayout(btn_row)

        layout.addWidget(list_group)

        self._refresh_auto_task_list()
        return tab

    def _on_auto_type_changed(self, text):
        is_interval = (text == "间隔执行")
        self._auto_interval_spin.setVisible(is_interval)
        self._auto_time_edit.setVisible(not is_interval)

    def _refresh_auto_task_list(self):
        # P3: 引入 executor 的 _running_tasks 判断是否正在执行
        try:
            from brain.auto_task_executor import _running_tasks as _exec_running
        except Exception:
            _exec_running = set()

        self._auto_task_list.clear()
        if not hasattr(self, '_auto_task_mgr'):
            return
        for task in self._auto_task_mgr.get_all_tasks():
            type_label = SCHEDULE_LABELS.get(task.schedule_type, task.schedule_type)
            time_info = task.schedule_time if task.schedule_type != "interval" else f"每{task.interval_minutes}分"

            # P3: 判断是否正在执行
            is_executing = task.task_id in _exec_running
            if is_executing:
                status_icon = "⏳"
            else:
                status_icon = {"active": "▶", "paused": "⏸", "completed": "✓", "failed": "✗"}.get(task.status, "?")

            if task.status == "completed":
                text = f"🔵 ✓ {task.name} | {type_label} {time_info} | 已完成({task.execution_count}次)"
            elif is_executing:
                text = f"🟢 ⏳ {task.name} | {type_label} {time_info} | 🔄 执行中…"
            else:
                text = f"🔵 {status_icon} {task.name} | {type_label} {time_info} | 执行{task.execution_count}次"

            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, task.task_id)
            if is_executing:
                item.setForeground(QColor(0, 200, 100))  # 绿色表示执行中
            elif not task.enabled or task.status not in ("active",):
                item.setForeground(QColor(150, 150, 180))
            else:
                item.setForeground(QColor(108, 123, 255))
            self._auto_task_list.addItem(item)

    def _on_add_auto_task(self):
        name = self._auto_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入任务名称")
            return

        type_text = self._auto_type_combo.currentText()
        type_map = {v: k for k, v in SCHEDULE_LABELS.items()}
        schedule_type = type_map.get(type_text, "daily")

        time_str = self._auto_time_edit.time().toString("HH:mm")
        interval_minutes = self._auto_interval_spin.value() if schedule_type == "interval" else 0

        missed_text = self._auto_missed_combo.currentText()
        missed_map = {v: k for k, v in MISSED_LABELS.items()}
        missed_action = missed_map.get(missed_text, "ask")

        task = AutoTask(
            name=name,
            schedule_type=schedule_type,
            schedule_time=time_str,
            interval_minutes=interval_minutes,
            missed_action=missed_action,
            tags=["auto"],
        )
        task.next_run = task.compute_next_run()
        self._auto_task_mgr.add_task(task)
        self._refresh_auto_task_list()
        self._auto_name_edit.clear()

    def _on_delete_auto_task(self):
        current = self._auto_task_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个任务")
            return
        task_id = current.data(Qt.UserRole)
        reply = QMessageBox.question(self, "确认删除", "确定要删除这个自动化任务吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._auto_task_mgr.delete_task(task_id)
            self._refresh_auto_task_list()

    def _on_toggle_auto_task(self):
        current = self._auto_task_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个任务")
            return
        task_id = current.data(Qt.UserRole)
        task = self._auto_task_mgr.get_task(task_id)
        if task:
            if task.status == "active":
                self._auto_task_mgr.pause_task(task_id)
            elif task.status == "paused":
                self._auto_task_mgr.resume_task(task_id)
            else:
                self._auto_task_mgr.toggle_enabled(task_id)
            self._refresh_auto_task_list()

    def _on_edit_auto_task(self):
        current = self._auto_task_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个任务")
            return
        task_id = current.data(Qt.UserRole)
        task = self._auto_task_mgr.get_task(task_id)
        if not task:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑任务 - {task.name}")
        dlg.setMinimumWidth(400)
        dlg_layout = QVBoxLayout(dlg)

        dlg_layout.addWidget(QLabel("名称："))
        name_edit = QLineEdit(task.name)
        dlg_layout.addWidget(name_edit)

        dlg_layout.addWidget(QLabel("描述："))
        desc_edit = QLineEdit(task.description)
        dlg_layout.addWidget(desc_edit)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("类型："))
        type_combo = QComboBox()
        type_combo.addItems(list(SCHEDULE_LABELS.values()))
        type_combo.setCurrentText(SCHEDULE_LABELS.get(task.schedule_type, "每天"))
        type_row.addWidget(type_combo)
        type_row.addStretch()
        dlg_layout.addLayout(type_row)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("时间："))
        time_edit = QTimeEdit()
        time_edit.setDisplayFormat("HH:mm")
        parts = task.schedule_time.split(":")
        time_edit.setTime(QTime(int(parts[0]), int(parts[1])))
        time_row.addWidget(time_edit)
        time_row.addStretch()
        dlg_layout.addLayout(time_row)

        dlg_layout.addWidget(QLabel("间隔(分钟)："))
        interval_spin = QSpinBox()
        interval_spin.setRange(1, 1440)
        interval_spin.setValue(task.interval_minutes)
        dlg_layout.addWidget(interval_spin)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btn_box)

        if dlg.exec_() == QDialog.Accepted:
            type_map = {v: k for k, v in SCHEDULE_LABELS.items()}
            self._auto_task_mgr.update_task(
                task_id,
                name=name_edit.text().strip(),
                description=desc_edit.text().strip(),
                schedule_type=type_map.get(type_combo.currentText(), "daily"),
                schedule_time=time_edit.time().toString("HH:mm"),
                interval_minutes=interval_spin.value(),
            )
            self._refresh_auto_task_list()

    def _on_view_auto_log(self):
        current = self._auto_task_list.currentItem()
        if not current:
            QMessageBox.warning(self, "提示", "请先选中一个任务")
            return
        task_id = current.data(Qt.UserRole)
        task = self._auto_task_mgr.get_task(task_id)
        logs = self._auto_task_mgr.get_logs(task_id, limit=20)
        workflow_runs = self._auto_task_mgr.get_workflow_runs(task_id)

        if not logs and not workflow_runs:
            QMessageBox.information(self, "执行日志", "暂无执行记录")
            return

        lines = [f"📋 {task.name} 执行日志\n"]
        if workflow_runs:
            run_ids = "、".join(str(item["workflow_run_id"]) for item in workflow_runs[:10])
            lines.append(f"🧭 关联 Workflow：{run_ids}\n")
        for log in logs:
            status = "✅" if log["success"] else "❌"
            lines.append(f"{status} [{log['timestamp']}] 步骤{log['step']}: {log['message'][:80]}")
            if log.get("duration_ms"):
                lines.append(f"   耗时: {log['duration_ms']}ms")

        msg = "\n".join(lines[-20:])
        dlg = QDialog(self)
        dlg.setWindowTitle(f"执行日志 - {task.name}")
        dlg.setMinimumSize(500, 400)
        layout = QVBoxLayout(dlg)
        text_edit = QLabel(msg)
        text_edit.setWordWrap(True)
        text_edit.setStyleSheet("font-family: Consolas; font-size: 12px; padding: 10px;")
        text_edit.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(text_edit)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec_()

    def _auto_context_menu(self, pos):
        item = self._auto_task_list.itemAt(pos)
        if not item:
            return
        task_id = item.data(Qt.UserRole)
        task = self._auto_task_mgr.get_task(task_id)
        if not task:
            return

        menu = QMenu()
        menu.addAction("✏️ 编辑").triggered.connect(self._on_edit_auto_task)
        if task.status == "active":
            menu.addAction("⏸ 暂停").triggered.connect(lambda: self._auto_task_mgr.pause_task(task_id))
        elif task.status == "paused":
            menu.addAction("▶ 恢复").triggered.connect(lambda: self._auto_task_mgr.resume_task(task_id))
        menu.addAction("✓ 标记完成").triggered.connect(lambda: self._auto_task_mgr.complete_task(task_id))
        menu.addAction("🔄 立即执行").triggered.connect(lambda: self._auto_execute_now(task_id))
        menu.addSeparator()
        menu.addAction("📋 查看日志").triggered.connect(self._on_view_auto_log)
        menu.addAction("🗑️ 删除").triggered.connect(self._on_delete_auto_task)
        menu.exec_(self._auto_task_list.mapToGlobal(pos))

    def _auto_execute_now(self, task_id):
        from brain.auto_task_executor import execute_auto_task
        task = self._auto_task_mgr.get_task(task_id)
        if task:
            execute_auto_task(task, on_complete=lambda tid, ok, msg: self._refresh_auto_task_list())
            QMessageBox.information(self, "提示", f"任务「{task.name}」已开始执行，请稍后查看日志")
            self._refresh_auto_task_list()

    # P4: 清理已完成的 once 任务
    def _on_cleanup_completed(self):
        reply = QMessageBox.question(
            self, "清理确认",
            "将移除所有已完成超过24小时的一次性任务，确定吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            count = self._auto_task_mgr.cleanup_old_completed_tasks(hours=24)
            QMessageBox.information(self, "清理完成", f"已清理 {count} 个过期任务")
            self._refresh_auto_task_list()

    # P5: 取消正在执行的任务
    def _on_cancel_execution(self):
        from brain.auto_task_executor import cancel_task, _running_tasks as _exec_running
        if not _exec_running:
            QMessageBox.information(self, "提示", "当前没有正在执行的任务")
            return

        # 找第一个正在执行的任务
        running_ids = list(_exec_running)
        task_id = running_ids[0] if running_ids else None
        if not task_id:
            return

        task = self._auto_task_mgr.get_task(task_id)
        name = task.name if task else task_id
        reply = QMessageBox.question(
            self, "取消确认",
            f"确定要取消正在执行的任务「{name}」吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            if cancel_task(task_id):
                QMessageBox.information(self, "已取消", f"任务「{name}」已标记取消，将在当前步骤完成后停止")
            else:
                QMessageBox.warning(self, "取消失败", "无法取消该任务")
        self._refresh_auto_task_list()

    def closeEvent(self, event):
        self._update_timer.stop()
        if self._todo_manager:
            self._todo_manager.unregister_observer(self._refresh_todo_list)
        event.accept()
