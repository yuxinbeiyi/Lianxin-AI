"""
ProactiveDialog：主动聊天 + 调皮观察 设置界面
使用 QTabWidget 分离两个功能区域。
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSlider, QCheckBox, QGroupBox, QScrollArea,
    QWidget, QSizePolicy, QFrame, QSpinBox,
    QTabWidget, QComboBox, QMessageBox,
    QLineEdit, QListWidget, QListWidgetItem,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont
import os
import webbrowser

from utils.proactive_chat import ProactiveChatScheduler


# 24 小时标签
_HOUR_LABELS = [f"{h:02d}:00" for h in range(24)]

# 权重颜色（0=灰, 1-3=蓝, 4-6=橙, 7-10=红）
def _weight_color(w: int) -> str:
    if w == 0:   return "#CCCCCC"
    if w <= 3:   return "#6C7BFF"
    if w <= 6:   return "#FF9500"
    return "#FF3B30"


class _HourRow(QWidget):
    """单行小时权重控件：标签 + 滑块 + 数字显示。"""

    value_changed = pyqtSignal(int, int)

    def __init__(self, hour: int, initial_weight: int, parent=None):
        super().__init__(parent)
        self._hour = hour
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        time_lbl = QLabel(_HOUR_LABELS[hour])
        time_lbl.setFixedWidth(44)
        time_lbl.setFont(QFont("Microsoft YaHei UI", 8))
     
        layout.addWidget(time_lbl)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 10)
        self._slider.setValue(initial_weight)
        self._slider.setFixedHeight(18)
        self._slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px;
                background: #E0E0E8;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                height: 14px;
                margin: -5px 0;
                background: #6C7BFF;
                border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #6C7BFF;
                border-radius: 2px;
            }
        """)
        self._slider.valueChanged.connect(self._on_value_changed)
        layout.addWidget(self._slider, 1)

        self._val_lbl = QLabel(str(initial_weight))
        self._val_lbl.setFixedWidth(20)
        self._val_lbl.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
        self._val_lbl.setAlignment(Qt.AlignCenter)
        self._update_val_label(initial_weight)
        layout.addWidget(self._val_lbl)

    def _on_value_changed(self, val: int):
        self._update_val_label(val)
        self.value_changed.emit(self._hour, val)

    def _update_val_label(self, val: int):
        self._val_lbl.setText(str(val))
        self._val_lbl.setStyleSheet(f"color: {_weight_color(val)};")

    def get_value(self) -> int:
        return self._slider.value()

    def set_value(self, val: int):
        self._slider.setValue(val)


class ProactiveDialog(QDialog):
    """统一主动行为调度设置。"""

    debug_trigger = pyqtSignal()
    debug_observe_signal = pyqtSignal(str)

    def __init__(self, scheduler: ProactiveChatScheduler, parent=None):
        super().__init__(parent)
        self._scheduler = scheduler
        self._hour_rows: list[_HourRow] = []

        self.setWindowTitle("主动聊天设置")
        self.resize(700, 720)
        self.setMinimumSize(620, 520)
        self.setWindowFlags(Qt.Window)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        

        self._build_ui()
        self._load_from_scheduler()

        # 概率定时器（每分钟更新）
        self._prob_timer = QTimer(self)
        self._prob_timer.timeout.connect(self._update_current_prob)
        self._prob_timer.start(60 * 1000)
        self._update_current_prob()

    # ── 界面构建 ──────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(16, 16, 16, 16)

        # 标题 + 描述
        title = QLabel("主动聊天设置")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        
        root.addWidget(title)

        desc = QLabel("达到触发条件后，莲心会按权重随机选择一种主动行为。")
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        
        root.addWidget(desc)

        # ── 选项卡 ──
        tab = QTabWidget()
        tab.setStyleSheet("""
            QTabBar::tab:selected {
                border-bottom: 2px solid #6C7BFF;
            }
        """)
        root.addWidget(tab, 1)

        # ────── Tab 1: 总体调度 ──────
        chat_tab = QWidget()
        chat_layout = QVBoxLayout(chat_tab)
        chat_layout.setSpacing(8)
        chat_layout.setContentsMargins(8, 8, 8, 8)

        self._build_chat_tab(chat_layout)
        chat_scroll = QScrollArea()
        chat_scroll.setWidgetResizable(True)
        chat_scroll.setWidget(chat_tab)
        chat_scroll.setStyleSheet("QScrollArea { border: none; }")
        tab.addTab(chat_scroll, "总体调度")

        # ────── Tab 2: 普通聊天 ──────
        normal_tab = QWidget()
        normal_layout = QVBoxLayout(normal_tab)
        normal_layout.setSpacing(8)
        normal_layout.setContentsMargins(8, 8, 8, 8)
        self._build_normal_tab(normal_layout)
        tab.addTab(normal_tab, "普通聊天")

        # ────── Tab 3: 调皮观察 ──────
        obs_tab = QWidget()
        obs_layout = QVBoxLayout(obs_tab)
        obs_layout.setSpacing(8)
        obs_layout.setContentsMargins(8, 8, 8, 8)

        self._build_observe_tab(obs_layout)
        obs_scroll = QScrollArea()
        obs_scroll.setWidgetResizable(True)
        obs_scroll.setWidget(obs_tab)
        obs_scroll.setStyleSheet("QScrollArea { border: none; }")
        tab.addTab(obs_scroll, "调皮观察")

        # ────── Tab 4: B站冲浪 ──────
        bl_tab = QWidget()
        bl_layout = QVBoxLayout(bl_tab)
        bl_layout.setSpacing(8)
        bl_layout.setContentsMargins(8, 8, 8, 8)

        self._build_bilibili_tab(bl_layout)
        bl_scroll = QScrollArea()
        bl_scroll.setWidgetResizable(True)
        bl_scroll.setWidget(bl_tab)
        bl_scroll.setStyleSheet("QScrollArea { border: none; }")
        tab.addTab(bl_scroll, "B站冲浪")

        # ────── Tab 5: 莲心摸鱼 ──────
        slack_tab = QWidget()
        slack_layout = QVBoxLayout(slack_tab)
        slack_layout.setSpacing(8)
        slack_layout.setContentsMargins(8, 8, 8, 8)

        self._build_slack_tab(slack_layout)
        slack_scroll = QScrollArea()
        slack_scroll.setWidgetResizable(True)
        slack_scroll.setWidget(slack_tab)
        slack_scroll.setStyleSheet("QScrollArea { border: none; }")
        tab.addTab(slack_scroll, "莲心摸鱼")

        # ── 底部按钮 ──
        btn_row = QHBoxLayout()

        self._btn_debug = QPushButton("立即触发（调试）")
        self._btn_debug.setFixedHeight(34)
        self._btn_debug.setFont(QFont("Microsoft YaHei UI", 9))
        self._btn_debug.setCursor(Qt.PointingHandCursor)
        self._btn_debug.setStyleSheet("""
            QPushButton {
                background-color: #FF9500;
                color: white;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover  { background-color: #E08600; }
            QPushButton:pressed{ background-color: #C07600; }
            QPushButton:disabled{ background-color: #CCCCCC; }
        """)
        self._btn_debug.clicked.connect(self._on_debug_trigger)
        btn_row.addWidget(self._btn_debug)

        btn_row.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedSize(72, 34)
        btn_cancel.setFont(QFont("Microsoft YaHei UI", 9))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #1E1E30;
               
                border-radius: 8px;
                border: 1px solid #D8D8EE;
            }
            QPushButton:hover { background-color: #E4E4F0; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("保存")
        btn_save.setFixedSize(72, 34)
        btn_save.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover  { background-color: #5A6AEE; }
            QPushButton:pressed{ background-color: #4A5ADE; }
        """)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        root.addLayout(btn_row)

    # ────────── 主动聊天选项卡 ─────────────────────────────

    def _build_chat_tab(self, layout: QVBoxLayout):
        # 桌面主动聊天开关
        toggle_row = QHBoxLayout()
        toggle_lbl = QLabel("启用桌面主动聊天")
        toggle_lbl.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        toggle_lbl.setStyleSheet("color: #3A3A5C;")
        toggle_row.addWidget(toggle_lbl)
        toggle_row.addStretch()
        self._enable_cb = QCheckBox()
        self._enable_cb.setFixedSize(20, 20)
        toggle_row.addWidget(self._enable_cb)
        layout.addLayout(toggle_row)

        # QQ 主动聊天开关
        qq_row = QHBoxLayout()
        qq_lbl = QLabel("启用QQ主动聊天")
        qq_lbl.setFont(QFont("Microsoft YaHei UI", 9))
      
        qq_row.addWidget(qq_lbl)
        qq_row.addStretch()
        self._qq_cb = QCheckBox()
        self._qq_cb.setFixedSize(20, 20)
        qq_row.addWidget(self._qq_cb)
        layout.addLayout(qq_row)
        # 独立于主动聊天开关
        music_row = QHBoxLayout()
        music_lbl = QLabel("启用听歌反馈")
        music_lbl.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        music_lbl.setStyleSheet("color: #3A3A5C;")
        music_lbl.setToolTip("开启后，播放歌曲时莲心会针对当前歌曲发表评论（不受桌面主动聊天开关影响）。")
        music_row.addWidget(music_lbl)
        music_row.addStretch()
        self._music_feedback_cb = QCheckBox()
        self._music_feedback_cb.setFixedSize(20, 20)
        music_row.addWidget(self._music_feedback_cb)
        layout.addLayout(music_row)

        behavior_group = QGroupBox("行为随机权重")
        behavior_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        behavior_inner = QVBoxLayout(behavior_group)
        behavior_hint = QLabel("百分比是四类行为都可用时的配置占比；实际只在可用且不在冷却中的行为间重新归一化。")
        behavior_hint.setWordWrap(True)
        behavior_hint.setStyleSheet("color: #777788;")
        behavior_inner.addWidget(behavior_hint)
        self._behavior_weight_sliders = {}
        self._behavior_weight_labels = {}
        for key, title in (("normal", "普通聊天"), ("observe", "调皮观察"),
                           ("bilibili", "B站冲浪"), ("slack", "莲心摸鱼")):
            row = QHBoxLayout()
            label = QLabel(title)
            label.setFixedWidth(72)
            row.addWidget(label)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.valueChanged.connect(self._update_behavior_distribution)
            row.addWidget(slider, 1)
            value_label = QLabel("0")
            value_label.setFixedWidth(72)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(value_label)
            behavior_inner.addLayout(row)
            self._behavior_weight_sliders[key] = slider
            self._behavior_weight_labels[key] = value_label
        layout.addWidget(behavior_group)

        policy_group = QGroupBox("选择策略")
        policy_inner = QVBoxLayout(policy_group)
        self._avoid_repeat_cb = QCheckBox("降低与上一次相同行为的权重")
        self._fallback_cb = QCheckBox("行为失败或无内容时，回退尝试另一种行为")
        policy_inner.addWidget(self._avoid_repeat_cb)
        policy_inner.addWidget(self._fallback_cb)
        layout.addWidget(policy_group)

        recent_group = QGroupBox("最近成功行为")
        recent_inner = QVBoxLayout(recent_group)
        self._recent_behavior_label = QLabel("暂无记录")
        self._recent_behavior_label.setWordWrap(True)
        recent_inner.addWidget(self._recent_behavior_label)
        layout.addWidget(recent_group)

        # 频率滑块
        freq_group = QGroupBox("每日消息频率")
        freq_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        freq_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #5060DD;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        freq_inner = QVBoxLayout(freq_group)

        freq_desc = QHBoxLayout()
        freq_low  = QLabel("少（约1条/天）")
        freq_high = QLabel("多（约50条/天）")
        for lbl in (freq_low, freq_high):
            lbl.setFont(QFont("Microsoft YaHei UI", 8))
            lbl.setStyleSheet("color: #888888;")
        freq_desc.addWidget(freq_low)
        freq_desc.addStretch()
        freq_desc.addWidget(freq_high)
        freq_inner.addLayout(freq_desc)

        freq_slider_row = QHBoxLayout()
        self._freq_slider = QSlider(Qt.Horizontal)
        self._freq_slider.setRange(1, 50)
        self._freq_slider.setFixedHeight(20)
        self._freq_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px;
                background: #E0E0E8;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 18px;
                height: 18px;
                margin: -6px 0;
                background: #6C7BFF;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: #6C7BFF;
                border-radius: 3px;
            }
        """)
        self._freq_slider.valueChanged.connect(self._on_freq_changed)
        freq_slider_row.addWidget(self._freq_slider)

        self._freq_val_lbl = QLabel("3")
        self._freq_val_lbl.setFixedWidth(24)
        self._freq_val_lbl.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        self._freq_val_lbl.setStyleSheet("color: #6C7BFF;")
        self._freq_val_lbl.setAlignment(Qt.AlignCenter)
        freq_slider_row.addWidget(self._freq_val_lbl)
        freq_inner.addLayout(freq_slider_row)
        layout.addWidget(freq_group)

        # 实时概率显示
        prob_frame = QFrame()
        prob_frame.setStyleSheet("background-color: #1E1E30; border-radius: 8px;")
        prob_inner = QHBoxLayout(prob_frame)
        prob_inner.setContentsMargins(16, 10, 16, 10)
        self._prob_label = QLabel()
        self._prob_label.setFont(QFont("Microsoft YaHei UI", 9))
        self._prob_label.setStyleSheet("color: #A0B0FF;")
        prob_inner.addWidget(self._prob_label)
        layout.addWidget(prob_frame)

        # 高级设置
        advanced_group = QGroupBox("高级设置")
        advanced_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        advanced_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #5060DD;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        adv_inner = QVBoxLayout(advanced_group)

        int_row = QHBoxLayout()
        int_lbl = QLabel("两次主动消息最小间隔（分钟）")
        int_lbl.setFont(QFont("Microsoft YaHei UI", 9))
       
        int_row.addWidget(int_lbl)
        int_row.addStretch()
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(10, 120)
        self._interval_spin.setSuffix(" 分钟")
        self._interval_spin.setFixedWidth(100)
        int_row.addWidget(self._interval_spin)
        adv_inner.addLayout(int_row)

        defer_row = QHBoxLayout()
        defer_lbl = QLabel("用户发消息后推迟时间（分钟）")
        defer_lbl.setFont(QFont("Microsoft YaHei UI", 9))
   
        defer_row.addWidget(defer_lbl)
        defer_row.addStretch()
        self._defer_spin = QSpinBox()
        self._defer_spin.setRange(5, 60)
        self._defer_spin.setSuffix(" 分钟")
        self._defer_spin.setFixedWidth(100)
        defer_row.addWidget(self._defer_spin)
        adv_inner.addLayout(defer_row)

        self._memory_link_cb = QCheckBox("允许记忆驱动的主动关怀")
        self._memory_link_cb.setToolTip("模型判断是否值得联系；代码仅负责时间、冷却和防重复")
        adv_inner.addWidget(self._memory_link_cb)
        memory_row = QHBoxLayout()
        memory_row.addWidget(QLabel("记忆线索评估间隔"))
        memory_row.addStretch()
        self._memory_eval_spin = QSpinBox()
        self._memory_eval_spin.setRange(10, 1440)
        self._memory_eval_spin.setSuffix(" 分钟")
        self._memory_eval_spin.setFixedWidth(105)
        memory_row.addWidget(self._memory_eval_spin)
        adv_inner.addLayout(memory_row)
        layout.addWidget(advanced_group)

        # 每小时权重
        hour_group = QGroupBox("各时间段触发权重（0=不发，10=最高）")
        hour_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        hour_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #5060DD;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        hour_inner = QVBoxLayout(hour_group)

        quick_row = QHBoxLayout()
        btn_reset = QPushButton("恢复默认权重")
        btn_reset.setFixedHeight(24)
        btn_reset.setFont(QFont("Microsoft YaHei UI", 8))
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.setStyleSheet("""
            QPushButton {
                background-color: #ECEEFF;
                color: #5060DD;
                border-radius: 6px;
                border: 1px solid #C8CCEE;
            }
            QPushButton:hover { background-color: #DDE0FF; }
        """)
        btn_reset.clicked.connect(self._reset_weights)
        quick_row.addStretch()
        quick_row.addWidget(btn_reset)
        hour_inner.addLayout(quick_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(100)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(0)
        scroll_layout.setContentsMargins(0, 0, 4, 0)

        self._hour_rows = []
        from utils.proactive_chat import _DEFAULT_WEIGHTS
        for h in range(24):
            row = _HourRow(h, _DEFAULT_WEIGHTS[h])
            row.value_changed.connect(self._on_weight_changed)
            scroll_layout.addWidget(row)
            self._hour_rows.append(row)

        scroll.setWidget(scroll_content)
        hour_inner.addWidget(scroll, 1)
        layout.addWidget(hour_group, 1)

    def _build_normal_tab(self, layout: QVBoxLayout):
        hint = QLabel("不调用观察、B站或摸鱼工具，直接结合近期对话发起自然聊天。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._normal_enable_cb = QCheckBox("启用普通主动聊天")
        self._normal_enable_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        layout.addWidget(self._normal_enable_cb)
        self._add_behavior_cooldown(layout, "normal")
        layout.addStretch(1)

    def _add_behavior_cooldown(self, layout: QVBoxLayout, behavior: str):
        if not hasattr(self, "_behavior_cooldown_spins"):
            self._behavior_cooldown_spins = {}
        row = QHBoxLayout()
        row.addWidget(QLabel("该行为成功后的冷却时间"))
        row.addStretch()
        spin = QSpinBox()
        spin.setRange(0, 1440)
        spin.setSuffix(" 分钟")
        spin.setFixedWidth(110)
        row.addWidget(spin)
        layout.addLayout(row)
        self._behavior_cooldown_spins[behavior] = spin

    def _update_behavior_distribution(self):
        if not hasattr(self, "_behavior_weight_sliders"):
            return
        total = sum(slider.value() for slider in self._behavior_weight_sliders.values())
        for key, slider in self._behavior_weight_sliders.items():
            percent = slider.value() * 100 / total if total else 0
            self._behavior_weight_labels[key].setText(f"{slider.value()} / {percent:.0f}%")

    # ────────── 调皮观察选项卡 ────────────────────────────

    def _build_observe_tab(self, layout: QVBoxLayout):
        # 说明文字
        hint = QLabel(
            '莲心可以偶尔"偷看"你在干什么——截图或打开摄像头瞄一眼，\n'
            '然后基于看到的东西调皮地跟你打招呼。'
        )
        hint.setFont(QFont("Microsoft YaHei UI", 9))
     
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 启用开关
        toggle_row = QHBoxLayout()
        toggle_lbl = QLabel("启用调皮观察")
        toggle_lbl.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        toggle_lbl.setStyleSheet("color: #3A3A5C;")
        toggle_row.addWidget(toggle_lbl)
        toggle_row.addStretch()
        self._observe_cb = QCheckBox()
        self._observe_cb.setFixedSize(20, 20)
        toggle_row.addWidget(self._observe_cb)
        layout.addLayout(toggle_row)

        # 分割
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #E0E0E8;")
        layout.addWidget(line)

        self._add_behavior_cooldown(layout, "observe")

        # 观察来源权重
        scr_row = QHBoxLayout()
        scr_lbl = QLabel("截图来源权重")
        scr_lbl.setFont(QFont("Microsoft YaHei UI", 9))
        
        scr_row.addWidget(scr_lbl)
        scr_row.addStretch()
        self._scr_slider = QSlider(Qt.Horizontal)
        self._scr_slider.setRange(0, 100)
        self._scr_slider.setFixedWidth(140)
        self._scr_slider.setFixedHeight(18)
        self._scr_slider.valueChanged.connect(
            lambda v: self._scr_val_lbl.setText(str(v)))
        scr_row.addWidget(self._scr_slider)
        self._scr_val_lbl = QLabel("30")
        self._scr_val_lbl.setFixedWidth(36)
        self._scr_val_lbl.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        self._scr_val_lbl.setStyleSheet("color: #FF6B8A;")
        scr_row.addWidget(self._scr_val_lbl)
        layout.addLayout(scr_row)

        # 摄像头来源权重
        cam_row = QHBoxLayout()
        cam_lbl = QLabel("摄像头来源权重")
        cam_lbl.setFont(QFont("Microsoft YaHei UI", 9))
         
        cam_row.addWidget(cam_lbl)
        cam_row.addStretch()
        self._cam_slider = QSlider(Qt.Horizontal)
        self._cam_slider.setRange(0, 100)
        self._cam_slider.setFixedWidth(140)
        self._cam_slider.setFixedHeight(18)
        self._cam_slider.valueChanged.connect(
            lambda v: self._cam_val_lbl.setText(str(v)))
        cam_row.addWidget(self._cam_slider)
        self._cam_val_lbl = QLabel("15")
        self._cam_val_lbl.setFixedWidth(36)
        self._cam_val_lbl.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        self._cam_val_lbl.setStyleSheet("color: #FF6B8A;")
        cam_row.addWidget(self._cam_val_lbl)
        layout.addLayout(cam_row)

        # 摄像头参数
        params_row = QHBoxLayout()
        cam_wait_lbl = QLabel("等待")
        cam_wait_lbl.setFont(QFont("Microsoft YaHei UI", 9))
        
        params_row.addWidget(cam_wait_lbl)
        self._cam_wait_spin = QSpinBox()
        self._cam_wait_spin.setRange(3, 30)
        self._cam_wait_spin.setSuffix(" 秒")
        self._cam_wait_spin.setFixedWidth(70)
        params_row.addWidget(self._cam_wait_spin)
        layout.addLayout(params_row)

        # 摄像头选择行：标签 + 下拉 + 刷新按钮
        cam_select_row = QHBoxLayout()
        cam_sel_lbl = QLabel("使用摄像头")
        cam_sel_lbl.setFont(QFont("Microsoft YaHei UI", 9))
       
        cam_select_row.addWidget(cam_sel_lbl)
        self._cam_combo = QComboBox()
        self._cam_combo.setMinimumWidth(180)
        cam_select_row.addWidget(self._cam_combo)

        btn_refresh = QPushButton("刷新")
        btn_refresh.setFixedHeight(26)
        btn_refresh.setFont(QFont("Microsoft YaHei UI", 8))
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: #E8F0FF;
                color: #5070DD;
                border-radius: 6px;
                border: 1px solid #C8D8F0;
            }
            QPushButton:hover { background-color: #D8E4FF; }
        """)
        btn_refresh.clicked.connect(self._refresh_camera_list)
        cam_select_row.addWidget(btn_refresh)
        cam_select_row.addStretch()
        layout.addLayout(cam_select_row)

        # 是否发送到 QQ
        qq_row = QHBoxLayout()
        self._obs_qq_cb = QCheckBox()
        self._obs_qq_cb.setFixedSize(18, 18)
        qq_row.addWidget(self._obs_qq_cb)
        qq_lbl = QLabel("观察消息同时发送到 QQ")
        qq_lbl.setFont(QFont("Microsoft YaHei UI", 9))
    
        qq_row.addWidget(qq_lbl)
        qq_row.addStretch()
        layout.addLayout(qq_row)

        # 调试按钮行
        debug_row = QHBoxLayout()
        self._btn_debug_scr = btn_scr = QPushButton("调试：截图观察")
        btn_scr.setFixedHeight(32)
        btn_scr.setFont(QFont("Microsoft YaHei UI", 9))
        btn_scr.setCursor(Qt.PointingHandCursor)
        btn_scr.setStyleSheet("""
            QPushButton {
                background-color: #FFE0E8;
                color: #D6406A;
                border-radius: 8px;
                border: 1px solid #FCC0D0;
            }
            QPushButton:hover { background-color: #FFD0DC; }
        """)
        btn_scr.clicked.connect(lambda: self._on_debug_observe("screenshot"))
        debug_row.addWidget(btn_scr)

        self._btn_debug_cam = btn_cam = QPushButton("调试：摄像头观察")
        btn_cam.setFixedHeight(32)
        btn_cam.setFont(QFont("Microsoft YaHei UI", 9))
        btn_cam.setCursor(Qt.PointingHandCursor)
        btn_cam.setStyleSheet("""
            QPushButton {
                background-color: #FFE0E8;
                color: #D6406A;
                border-radius: 8px;
                border: 1px solid #FCC0D0;
            }
            QPushButton:hover { background-color: #FFD0DC; }
        """)
        btn_cam.clicked.connect(lambda: self._on_debug_observe("camera"))
        debug_row.addWidget(btn_cam)
        debug_row.addStretch()
        layout.addLayout(debug_row)

        layout.addStretch(1)

        # 不自动扫描摄像头（避免物理激活 + 刷屏警告），仅在用户点"刷新"时扫描
        self._cam_combo.addItem('（点击"刷新"检测摄像头）', -1)

    # ────────── B站冲浪选项卡 ────────────────────────────

    # ────────── 莲心摸鱼设置选项卡 ────────────────────────

    def _build_slack_tab(self, layout: QVBoxLayout):
        desc = QLabel(
            "以下功能通过 24h 权重系统与主动聊天统一触发，<br>"
            "请在左侧「主动聊天」中调节权重和频率。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #5A5A8A; margin-bottom: 4px;")
        layout.addWidget(desc)

        # 总开关
        enable_row = QHBoxLayout()
        self._slack_enable_cb = QCheckBox("启用莲心摸鱼")
        self._slack_enable_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        self._slack_enable_cb.setToolTip("开关：是否允许莲心在空闲时主动摸鱼")
        enable_row.addWidget(self._slack_enable_cb)
        enable_row.addStretch()
        layout.addLayout(enable_row)

        self._add_behavior_cooldown(layout, "slack")

        idle_row = QHBoxLayout()
        idle_row.addWidget(QLabel("用户安静至少"))
        idle_row.addStretch()
        self._slack_idle_spin = QSpinBox()
        self._slack_idle_spin.setRange(0, 240)
        self._slack_idle_spin.setSuffix(" 分钟")
        self._slack_idle_spin.setFixedWidth(110)
        idle_row.addWidget(self._slack_idle_spin)
        layout.addLayout(idle_row)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #E0E0E8;")
        layout.addWidget(line)

        # 功能勾选区域
        func_group = QGroupBox("基于现有功能延伸")
        func_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        func_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #5060DD;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        func_inner = QVBoxLayout(func_group)

        self._slack_cbs = {}
        slack_items = [
            ("slack_supplement_diary", "📝 留下后续笔迹", "今天的右侧书页已经写好后，莲心还可以主动补充一句感慨（每天最多2次）"),
            ("slack_review_old_diary", "🌙 重温共同书页", "随机翻开时间胶囊里过去的一天，和你分享回忆"),
            ("slack_search_old_topic", "🔍 搜索旧话题", "从对话历史中找一个旧话题，突然问起你"),
            ("slack_remind_todo", "✅ 提醒未完成Todo", "检查待办列表，温柔提醒你未完成的事"),
            ("slack_random_question", "💭 随机提问", "基于你说过的话，问一个开放性问题"),
            ("slack_weather_chitchat", "🌤️ 报天气+碎碎念", "看看今天天气，顺便说一句闲话"),
        ]

        for key, title, desc in slack_items:
            item_row = QHBoxLayout()
            cb = QCheckBox(title)
            cb.setFont(QFont("Microsoft YaHei UI", 9))
            cb.setToolTip(desc)
            item_row.addWidget(cb)
            item_row.addStretch()
            func_inner.addLayout(item_row)
            self._slack_cbs[key] = cb

        layout.addWidget(func_group)

        # 第二组：探索本地文件/系统
        explore_group = QGroupBox("探索本地文件/系统")
        explore_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        explore_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #E06080;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        explore_inner = QVBoxLayout(explore_group)

        explore_items = [
            ("slack_read_local_files", "📄 读本地文件", "随机打开一个 txt/docx/pdf 文件，看看内容"),
            ("slack_browser_history", "🌐 浏览器历史记录", "看看你最近浏览了什么网站"),
            ("slack_check_cpu_disk", "💻 查看CPU/磁盘", '看看系统状态，"咦这个进程在干嘛？"'),
            ("slack_check_recycle_bin", "🗑️ 查看回收站", "看看回收站里有什么，提醒你清理"),
        ]

        for key, title, desc in explore_items:
            item_row = QHBoxLayout()
            cb = QCheckBox(title)
            cb.setFont(QFont("Microsoft YaHei UI", 9))
            cb.setToolTip(desc)
            item_row.addWidget(cb)
            item_row.addStretch()
            explore_inner.addLayout(item_row)
            self._slack_cbs[key] = cb

        layout.addWidget(explore_group)

        # 第三组：情绪/陪伴类
        emotion_group = QGroupBox("情绪/陪伴类")
        emotion_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        emotion_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #E09040;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        emotion_inner = QVBoxLayout(emotion_group)
        emotion_items = [
            ("slack_remind_rest", "🧘 提醒休息", "检测到电脑开机很久，温柔提醒起来活动一下"),
            ("slack_remind_water", "☕ 提醒喝水", "间隔一段时间，温柔提醒你喝水"),
            ("slack_anniversary_remind", "🎉 纪念日提醒", "联动陪伴系统，记住相识日期等重要日子"),
        ]
        for key, title, desc in emotion_items:
            item_row = QHBoxLayout()
            cb = QCheckBox(title)
            cb.setFont(QFont("Microsoft YaHei UI", 9))
            cb.setToolTip(desc)
            item_row.addWidget(cb)
            item_row.addStretch()
            emotion_inner.addLayout(item_row)
            self._slack_cbs[key] = cb
        layout.addWidget(emotion_group)

        # 第四组：音乐互动
        music_group = QGroupBox("音乐互动")
        music_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        music_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #40A0C0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        music_inner = QVBoxLayout(music_group)
        music_items = [
            ("slack_next_song", "🎵 切歌放音乐", "莲心自己点下一首，放给你听"),
        ]
        for key, title, desc in music_items:
            item_row = QHBoxLayout()
            cb = QCheckBox(title)
            cb.setFont(QFont("Microsoft YaHei UI", 9))
            cb.setToolTip(desc)
            item_row.addWidget(cb)
            item_row.addStretch()
            music_inner.addLayout(item_row)
            self._slack_cbs[key] = cb
        layout.addWidget(music_group)

        # 调试按钮区
        debug_lbl = QLabel("🧪 调试：手动触发摸鱼动作")
        debug_lbl.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        debug_lbl.setStyleSheet("color: #5A5A8A; margin-top: 4px;")
        layout.addWidget(debug_lbl)
        debug_actions = [
            ("supplement_diary", "📝 后续笔迹"), ("review_old_diary", "🌙 重温书页"),
            ("search_old_topic", "🔍 搜索旧话题"), ("remind_todo", "✅ 提醒Todo"),
            ("random_question", "💭 随机提问"), ("weather_chitchat", "🌤️ 天气碎碎念"),
            ("read_local_files", "📄 读本地文件"),
            ("browser_history", "🌐 浏览器历史"), ("check_cpu_disk", "💻 CPU/磁盘"),
            ("check_recycle_bin", "🗑️ 回收站"), ("remind_rest", "🧘 提醒休息"),
            ("remind_water", "☕ 提醒喝水"), ("anniversary_remind", "🎉 纪念日提醒"),
            ("next_song", "🎵 切歌"),
        ]
        debug_row = QHBoxLayout()
        self._slack_debug_combo = QComboBox()
        self._slack_debug_combo.setFont(QFont("Microsoft YaHei UI", 9))
        for action, label in debug_actions:
            self._slack_debug_combo.addItem(label, action)
        debug_btn = QPushButton("▶ 触发调试")
        debug_btn.setFixedHeight(30)
        debug_btn.setFont(QFont("Microsoft YaHei UI", 9))
        debug_btn.setCursor(Qt.PointingHandCursor)
        debug_btn.clicked.connect(self._on_slack_debug_trigger)
        debug_row.addWidget(self._slack_debug_combo, 1)
        debug_row.addWidget(debug_btn)
        layout.addLayout(debug_row)
        layout.addStretch(1)

    def _build_bilibili_tab(self, layout: QVBoxLayout):
        hint = QLabel(
            "莲心会偷偷去B站搜索你感兴趣的视频，然后推荐给你。\n"
            "兴趣标签会根据你的聊天记录自动提取，也可以手动添加。"
        )
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 启用开关
        toggle_row = QHBoxLayout()
        toggle_lbl = QLabel("启用B站冲浪")
        toggle_lbl.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        toggle_lbl.setStyleSheet("color: #3A3A5C;")
        toggle_row.addWidget(toggle_lbl)
        toggle_row.addStretch()
        self._bl_enable_cb = QCheckBox()
        self._bl_enable_cb.setFixedSize(20, 20)
        toggle_row.addWidget(self._bl_enable_cb)
        layout.addLayout(toggle_row)

        self._add_behavior_cooldown(layout, "bilibili")

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #E0E0E8;")
        layout.addWidget(line)

        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet("color: #E0E0E8;")
        layout.addWidget(line2)

        # 兴趣标签区域
        tag_group = QGroupBox("兴趣标签")
        tag_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        tag_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #5060DD;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        tag_inner = QVBoxLayout(tag_group)

        tag_input_row = QHBoxLayout()
        self._bl_tag_input = QLineEdit()
        self._bl_tag_input.setPlaceholderText("输入兴趣关键词，如：口琴、赛博朋克")
        self._bl_tag_input.setFont(QFont("Microsoft YaHei UI", 9))
        self._bl_tag_input.setFixedHeight(28)
        tag_input_row.addWidget(self._bl_tag_input)

        btn_add_tag = QPushButton("添加")
        btn_add_tag.setFixedHeight(28)
        btn_add_tag.setFont(QFont("Microsoft YaHei UI", 9))
        btn_add_tag.setCursor(Qt.PointingHandCursor)
        btn_add_tag.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover { background-color: #5A6AEE; }
        """)
        btn_add_tag.clicked.connect(self._on_bl_add_tag)
        tag_input_row.addWidget(btn_add_tag)
        tag_inner.addLayout(tag_input_row)

        tag_btn_row = QHBoxLayout()
        btn_pause = QPushButton("暂停/恢复")
        btn_pause.setFixedHeight(24)
        btn_pause.setFont(QFont("Microsoft YaHei UI", 8))
        btn_pause.setCursor(Qt.PointingHandCursor)
        btn_pause.setStyleSheet("""
            QPushButton {
                background-color: #ECEEFF;
                color: #5060DD;
                border-radius: 6px;
                border: 1px solid #C8CCEE;
            }
            QPushButton:hover { background-color: #DDE0FF; }
        """)
        btn_pause.clicked.connect(self._on_bl_pause_tag)
        tag_btn_row.addWidget(btn_pause)

        btn_delete = QPushButton("删除")
        btn_delete.setFixedHeight(24)
        btn_delete.setFont(QFont("Microsoft YaHei UI", 8))
        btn_delete.setCursor(Qt.PointingHandCursor)
        btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #FFE0E0;
                color: #D64040;
                border-radius: 6px;
                border: 1px solid #FCC0C0;
            }
            QPushButton:hover { background-color: #FFD0D0; }
        """)
        btn_delete.clicked.connect(self._on_bl_delete_tag)
        tag_btn_row.addWidget(btn_delete)
        tag_btn_row.addStretch()
        tag_inner.addLayout(tag_btn_row)

        self._bl_tag_list = QListWidget()
        self._bl_tag_list.setFixedHeight(100)
        self._bl_tag_list.setFont(QFont("Microsoft YaHei UI", 9))
        tag_inner.addWidget(self._bl_tag_list)
        layout.addWidget(tag_group)

        # 浏览记录区域
        hist_group = QGroupBox("浏览记录")
        hist_group.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        hist_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #D8D8EE;
                border-radius: 8px;
                margin-top: 8px;
                padding: 8px;
                color: #5060DD;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        hist_inner = QVBoxLayout(hist_group)

        hist_filter_row = QHBoxLayout()
        self._bl_hist_filter = QLineEdit()
        self._bl_hist_filter.setPlaceholderText("搜索关键词过滤...")
        self._bl_hist_filter.setFont(QFont("Microsoft YaHei UI", 9))
        self._bl_hist_filter.setFixedHeight(26)
        self._bl_hist_filter.textChanged.connect(self._on_bl_filter_history)
        hist_filter_row.addWidget(self._bl_hist_filter)

        btn_clear = QPushButton("清空")
        btn_clear.setFixedHeight(26)
        btn_clear.setFont(QFont("Microsoft YaHei UI", 8))
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.setStyleSheet("""
            QPushButton {
                background-color: #FFE0E0;
                color: #D64040;
                border-radius: 6px;
                border: 1px solid #FCC0C0;
            }
            QPushButton:hover { background-color: #FFD0D0; }
        """)
        btn_clear.clicked.connect(self._on_bl_clear_history)
        hist_filter_row.addWidget(btn_clear)
        hist_inner.addLayout(hist_filter_row)

        self._bl_hist_list = QListWidget()
        self._bl_hist_list.setFont(QFont("Microsoft YaHei UI", 8))
        self._bl_hist_list.setMinimumHeight(80)
        hist_inner.addWidget(self._bl_hist_list)

        hist_btn_row = QHBoxLayout()
        btn_like = QPushButton("👍 点赞")
        btn_like.setFixedHeight(26)
        btn_like.setFont(QFont("Microsoft YaHei UI", 8))
        btn_like.setCursor(Qt.PointingHandCursor)
        btn_like.setStyleSheet("""
            QPushButton {
                background-color: #E8F8E8;
                color: #2A8A2A;
                border-radius: 6px;
                border: 1px solid #C0E0C0;
            }
            QPushButton:hover { background-color: #D8F0D8; }
        """)
        btn_like.clicked.connect(lambda: self._on_bl_react("liked"))
        hist_btn_row.addWidget(btn_like)

        btn_dislike = QPushButton("👎 点踩")
        btn_dislike.setFixedHeight(26)
        btn_dislike.setFont(QFont("Microsoft YaHei UI", 8))
        btn_dislike.setCursor(Qt.PointingHandCursor)
        btn_dislike.setStyleSheet("""
            QPushButton {
                background-color: #FFE8E8;
                color: #D64040;
                border-radius: 6px;
                border: 1px solid #FCC0C0;
            }
            QPushButton:hover { background-color: #FFD8D8; }
        """)
        btn_dislike.clicked.connect(lambda: self._on_bl_react("disliked"))
        hist_btn_row.addWidget(btn_dislike)

        btn_open = QPushButton("🔗 打开视频")
        btn_open.setFixedHeight(26)
        btn_open.setFont(QFont("Microsoft YaHei UI", 8))
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.setStyleSheet("""
            QPushButton {
                background-color: #E8F0FF;
                color: #5070DD;
                border-radius: 6px;
                border: 1px solid #C8D8F0;
            }
            QPushButton:hover { background-color: #D8E4FF; }
        """)
        btn_open.clicked.connect(self._on_bl_open_video)
        hist_btn_row.addWidget(btn_open)
        hist_btn_row.addStretch()
        hist_inner.addLayout(hist_btn_row)
        layout.addWidget(hist_group)

        # 调试按钮
        debug_row = QHBoxLayout()
        self._btn_debug_bl = QPushButton("🧪 调试：立即测试B站冲浪")
        self._btn_debug_bl.setFixedHeight(32)
        self._btn_debug_bl.setFont(QFont("Microsoft YaHei UI", 9))
        self._btn_debug_bl.setCursor(Qt.PointingHandCursor)
        self._btn_debug_bl.setStyleSheet("""
            QPushButton {
                background-color: #FFE0E8;
                color: #D6406A;
                border-radius: 8px;
                border: 1px solid #FCC0D0;
            }
            QPushButton:hover { background-color: #FFD0DC; }
        """)
        self._btn_debug_bl.clicked.connect(self._on_bl_debug)
        debug_row.addWidget(self._btn_debug_bl)
        debug_row.addStretch()
        layout.addLayout(debug_row)

    # ── B站标签列表刷新 ─────────────────────────────────────

    def _refresh_bl_tags(self):
        self._bl_tag_list.clear()
        from utils.bilibili_history import get_bilibili_history
        for t in get_bilibili_history().get_tags():
            score = t["base_score"] + t.get("boost_score", 0)
            stars = "★" * min(5, max(1, score // 20))
            item = QListWidgetItem(f"{t['keyword']}  {stars}  ({t.get('source', 'auto')})")
            item.setToolTip(f"基础分: {t['base_score']}  加成: {t.get('boost_score', 0)}")
            self._bl_tag_list.addItem(item)
        for t in get_bilibili_history().get_tags("paused"):
            item = QListWidgetItem(f"{t['keyword']}  ⏸ 已暂停")
            item.setForeground(Qt.gray)
            self._bl_tag_list.addItem(item)

    def _refresh_bl_history(self, keyword: str = ""):
        self._bl_hist_list.clear()
        from utils.bilibili_history import get_bilibili_history
        records = get_bilibili_history().get_history(limit=50, keyword=keyword)
        for rec in records:
            date_str = rec["date"][:16].replace("T", " ")
            header = QListWidgetItem(f"📅 {date_str}  |  🔍 {rec['keyword']}")
            header.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
            header.setFlags(header.flags() & ~Qt.ItemIsSelectable)
            self._bl_hist_list.addItem(header)
            for v in rec["results"]:
                reaction = {"liked": "👍", "disliked": "👎"}.get(v.get("user_reaction"), "")
                sub = QListWidgetItem(
                    f"    {reaction} {v['title']} — {v['author']}  {v['play_count']}播放"
                )
                sub.setData(Qt.UserRole, (rec["id"], v["bvid"], v["link"]))
                sub.setFont(QFont("Microsoft YaHei UI", 8))
                self._bl_hist_list.addItem(sub)

    # ── B站事件处理 ─────────────────────────────────────────

    def _on_bl_add_tag(self):
        kw = self._bl_tag_input.text().strip()
        if not kw:
            return
        from utils.bilibili_history import get_bilibili_history
        get_bilibili_history().add_tag(kw, source="manual")
        get_bilibili_history().save()
        self._bl_tag_input.clear()
        self._refresh_bl_tags()

    def _on_bl_pause_tag(self):
        item = self._bl_tag_list.currentItem()
        if not item:
            return
        kw = item.text().split("  ")[0].strip()
        from utils.bilibili_history import get_bilibili_history
        mgr = get_bilibili_history()
        tags = mgr.get_tags()
        found = False
        for t in tags:
            if t["keyword"] == kw:
                if t.get("status") == "active":
                    mgr.pause_tag(kw)
                else:
                    mgr.resume_tag(kw)
                found = True
                break
        if not found:
            for t in mgr.get_tags("paused"):
                if t["keyword"] == kw:
                    mgr.resume_tag(kw)
                    found = True
                    break
        mgr.save()
        self._refresh_bl_tags()

    def _on_bl_delete_tag(self):
        item = self._bl_tag_list.currentItem()
        if not item:
            return
        kw = item.text().split("  ")[0].strip()
        from utils.bilibili_history import get_bilibili_history
        get_bilibili_history().remove_tag(kw)
        get_bilibili_history().save()
        self._refresh_bl_tags()

    def _on_bl_filter_history(self):
        kw = self._bl_hist_filter.text().strip()
        self._refresh_bl_history(keyword=kw)

    def _on_bl_clear_history(self):
        from utils.bilibili_history import get_bilibili_history
        get_bilibili_history().clear_history()
        get_bilibili_history().save()
        self._refresh_bl_history()

    def _on_bl_open_video(self):
        item = self._bl_hist_list.currentItem()
        if not item:
            return
        data = item.data(Qt.UserRole)
        if data and len(data) >= 3:
            webbrowser.open(data[2])

    def _on_bl_react(self, reaction: str):
        item = self._bl_hist_list.currentItem()
        if not item:
            return
        data = item.data(Qt.UserRole)
        if not data or len(data) < 2:
            return
        record_id, bvid, _ = data
        from utils.bilibili_history import get_bilibili_history
        get_bilibili_history().react_to_video(record_id, bvid, reaction)
        get_bilibili_history().save()
        self._refresh_bl_history()
    def _on_bl_debug(self):
        if not self._bl_enable_cb.isChecked():
            print("[B站冲浪] 调试按钮被点击，但B站冲浪开关未开启，请先勾选启用开关")
            return
        print("[B站冲浪] 调试按钮被点击，1秒后触发冲浪...")
        self._btn_debug_bl.setEnabled(False)
        self._btn_debug_bl.setText("正在测试...")
        QTimer.singleShot(1000, self._do_bl_debug)

    def _do_bl_debug(self):
        print("[B站冲浪] 发射 debug_observe_signal(bilibili) → main_window")
        self._btn_debug_bl.setEnabled(True)
        self._btn_debug_bl.setText("🧪 调试：立即测试B站冲浪")
        self.debug_observe_signal.emit("bilibili")

    # ── 摄像头扫描 ────────────────────────────────────────────

    def _scan_cameras(self) -> list[tuple[int, str]]:
        """扫描可用摄像头，返回 [(index, label), ...]。
        注意：此操作会物理激活摄像头（LED 会亮），仅在用户点击"刷新"时调用。
        """
        try:
            import cv2
        except ImportError:
            return []

        # 抑制 OpenCV C 层日志
        try:
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:
            pass
        try:
            os.environ["OPENCV_LOG_LEVEL"] = "ERROR"
        except Exception:
            pass

        available = []
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0
        for i in range(4):
            try:
                cap = cv2.VideoCapture(i, backend)
                if cap.isOpened():
                    label = f"摄像头 {i}"
                    available.append((i, label))
                    cap.release()
            except Exception:
                continue
        return available

    def _refresh_camera_list(self):
        """扫描可用摄像头并刷新下拉列表。"""
        self._cam_combo.clear()
        cams = self._scan_cameras()
        if cams:
            for idx, label in cams:
                self._cam_combo.addItem(label, idx)
            # 选中当前保存的 index（如果可用）
            current = self._scheduler.camera_index
            for i in range(self._cam_combo.count()):
                if self._cam_combo.itemData(i) == current:
                    self._cam_combo.setCurrentIndex(i)
                    break
        else:
            self._cam_combo.addItem("（未检测到摄像头）", -1)

    # ── 实时概率更新 ─────────────────────────────────────────

    def _update_current_prob(self):
        from datetime import datetime
        now = datetime.now()
        hour = now.hour
        weight = self._scheduler.weights[hour] if hour < len(self._scheduler.weights) else 0
        freq = self._scheduler.frequency
        prob = (weight / 10.0) * (freq / 30.0) * 0.08 * 1.5
        prob = min(prob, 0.3)
        self._prob_label.setText(
            f"当前时段（{hour:02d}:00）权重={weight}，"
            f"每5分钟触发概率约 {prob * 100:.2f}%"
        )

    # ── 数据加载/保存 ─────────────────────────────────────────

    def _load_from_scheduler(self):
        self._enable_cb.setChecked(self._scheduler.desktop_enabled)
        self._music_feedback_cb.setChecked(self._scheduler.music_feedback_enabled)
        self._qq_cb.setChecked(self._scheduler.qq_enabled)
        self._freq_slider.setValue(self._scheduler.frequency)
        self._interval_spin.setValue(self._scheduler.min_interval_minutes)
        self._defer_spin.setValue(self._scheduler.user_defer_minutes)
        self._memory_link_cb.setChecked(self._scheduler.memory_link_enabled)
        self._memory_eval_spin.setValue(self._scheduler.memory_evaluation_interval_minutes)
        self._normal_enable_cb.setChecked(self._scheduler.normal_enabled)
        for key, value in self._scheduler.behavior_weights.items():
            self._behavior_weight_sliders[key].setValue(value)
        for key, value in self._scheduler.behavior_cooldowns.items():
            self._behavior_cooldown_spins[key].setValue(value)
        self._avoid_repeat_cb.setChecked(self._scheduler.avoid_behavior_repeat)
        self._fallback_cb.setChecked(self._scheduler.fallback_on_failure)
        self._update_behavior_distribution()
        names = {"normal": "普通聊天", "observe": "调皮观察",
                 "bilibili": "B站冲浪", "slack": "莲心摸鱼"}
        history = self._scheduler._settings.get("_behavior_history", [])[-4:]
        self._recent_behavior_label.setText(
            "  →  ".join(names.get(item, item) for item in history) if history else "暂无记录"
        )
        weights = self._scheduler.weights
        for h, row in enumerate(self._hour_rows):
            if h < len(weights):
                row.set_value(weights[h])

        # 观察设置
        self._observe_cb.setChecked(self._scheduler.observe_enabled)
        self._scr_slider.setValue(self._scheduler.screenshot_prob)
        self._scr_val_lbl.setText(str(self._scheduler.screenshot_prob))
        self._cam_slider.setValue(self._scheduler.camera_prob)
        self._cam_val_lbl.setText(str(self._scheduler.camera_prob))
        self._cam_wait_spin.setValue(self._scheduler.camera_wait)
        self._obs_qq_cb.setChecked(self._scheduler.observe_send_to_qq)

        # 摄像头下拉选中（_refresh_camera_list 已做，但确保外部加载也生效）
        current = self._scheduler.camera_index
        for i in range(self._cam_combo.count()):
            if self._cam_combo.itemData(i) == current:
                self._cam_combo.setCurrentIndex(i)
                break

        self._update_debug_btn_state()

        # 加载 B站设置
        self._bl_enable_cb.setChecked(self._scheduler.bilibili_enabled)
        from utils.bilibili_history import get_bilibili_history
        self._refresh_bl_tags()
        self._refresh_bl_history()

        # 加载 摸鱼设置
        self._slack_enable_cb.setChecked(self._scheduler.slack_enabled)
        self._slack_idle_spin.setValue(self._scheduler.slack_idle_minutes)
    
        self._slack_cbs["slack_supplement_diary"].setChecked(self._scheduler.slack_supplement_diary)
        self._slack_cbs["slack_review_old_diary"].setChecked(self._scheduler.slack_review_old_diary)
        self._slack_cbs["slack_search_old_topic"].setChecked(self._scheduler.slack_search_old_topic)
        self._slack_cbs["slack_remind_todo"].setChecked(self._scheduler.slack_remind_todo)
        self._slack_cbs["slack_random_question"].setChecked(self._scheduler.slack_random_question)
        self._slack_cbs["slack_weather_chitchat"].setChecked(self._scheduler.slack_weather_chitchat)
        self._slack_cbs["slack_read_local_files"].setChecked(self._scheduler.slack_read_local_files)
        self._slack_cbs["slack_browser_history"].setChecked(self._scheduler.slack_browser_history)
        self._slack_cbs["slack_check_cpu_disk"].setChecked(self._scheduler.slack_check_cpu_disk)
        self._slack_cbs["slack_check_recycle_bin"].setChecked(self._scheduler.slack_check_recycle_bin)
        self._slack_cbs["slack_remind_rest"].setChecked(self._scheduler.slack_remind_rest)
        self._slack_cbs["slack_remind_water"].setChecked(self._scheduler.slack_remind_water)
        self._slack_cbs["slack_anniversary_remind"].setChecked(self._scheduler.slack_anniversary_remind)
        self._slack_cbs["slack_next_song"].setChecked(self._scheduler.slack_next_song)

    def _on_save(self):
        if sum(slider.value() for slider in self._behavior_weight_sliders.values()) == 0:
            QMessageBox.warning(self, "无法保存", "四类主动行为的权重不能同时为 0。")
            return
        self._scheduler.desktop_enabled = self._enable_cb.isChecked()
        self._scheduler.music_feedback_enabled = self._music_feedback_cb.isChecked()
        self._scheduler.qq_enabled = self._qq_cb.isChecked()
        self._scheduler.frequency = self._freq_slider.value()
        self._scheduler.min_interval_minutes = self._interval_spin.value()
        self._scheduler.user_defer_minutes = self._defer_spin.value()
        self._scheduler.memory_link_enabled = self._memory_link_cb.isChecked()
        self._scheduler.memory_evaluation_interval_minutes = self._memory_eval_spin.value()
        self._scheduler.normal_enabled = self._normal_enable_cb.isChecked()
        self._scheduler.behavior_weights = {
            key: slider.value() for key, slider in self._behavior_weight_sliders.items()
        }
        self._scheduler.behavior_cooldowns = {
            key: spin.value() for key, spin in self._behavior_cooldown_spins.items()
        }
        self._scheduler.avoid_behavior_repeat = self._avoid_repeat_cb.isChecked()
        self._scheduler.fallback_on_failure = self._fallback_cb.isChecked()
        self._scheduler.weights = [row.get_value() for row in self._hour_rows]

        self._scheduler.observe_enabled = self._observe_cb.isChecked()
        self._scheduler.screenshot_prob = self._scr_slider.value()
        self._scheduler.camera_prob = self._cam_slider.value()
        self._scheduler.camera_wait = self._cam_wait_spin.value()
        self._scheduler.observe_send_to_qq = self._obs_qq_cb.isChecked()
        idx = self._cam_combo.currentData()
        if idx is not None and idx >= 0:
            self._scheduler.camera_index = idx

        self._scheduler.bilibili_enabled = self._bl_enable_cb.isChecked()

        self._scheduler.slack_enabled = self._slack_enable_cb.isChecked()
        self._scheduler.slack_idle_minutes = self._slack_idle_spin.value()

        self._scheduler.slack_supplement_diary = self._slack_cbs["slack_supplement_diary"].isChecked()
        self._scheduler.slack_review_old_diary = self._slack_cbs["slack_review_old_diary"].isChecked()
        self._scheduler.slack_search_old_topic = self._slack_cbs["slack_search_old_topic"].isChecked()
        self._scheduler.slack_remind_todo = self._slack_cbs["slack_remind_todo"].isChecked()
        self._scheduler.slack_random_question = self._slack_cbs["slack_random_question"].isChecked()
        self._scheduler.slack_weather_chitchat = self._slack_cbs["slack_weather_chitchat"].isChecked()
        self._scheduler.slack_read_local_files = self._slack_cbs["slack_read_local_files"].isChecked()
        self._scheduler.slack_browser_history = self._slack_cbs["slack_browser_history"].isChecked()
        self._scheduler.slack_check_cpu_disk = self._slack_cbs["slack_check_cpu_disk"].isChecked()
        self._scheduler.slack_check_recycle_bin = self._slack_cbs["slack_check_recycle_bin"].isChecked()
        self._scheduler.slack_remind_rest = self._slack_cbs["slack_remind_rest"].isChecked()
        self._scheduler.slack_remind_water = self._slack_cbs["slack_remind_water"].isChecked()
        self._scheduler.slack_anniversary_remind = self._slack_cbs["slack_anniversary_remind"].isChecked()
        self._scheduler.slack_next_song = self._slack_cbs["slack_next_song"].isChecked()

        self._scheduler.save_settings()
        self.accept()

    # ── 事件处理 ──────────────────────────────────────────────

    def _on_freq_changed(self, val: int):
        self._freq_val_lbl.setText(str(val))
        self._update_current_prob()

    def _on_weight_changed(self, hour: int, weight: int):
        from datetime import datetime
        if hour == datetime.now().hour:
            self._update_current_prob()

    def _reset_weights(self):
        from utils.proactive_chat import _DEFAULT_WEIGHTS
        for h, row in enumerate(self._hour_rows):
            row.set_value(_DEFAULT_WEIGHTS[h])

    def _on_slack_debug_trigger(self):
        action = self._slack_debug_combo.currentData()
        if action:
            self.debug_observe_signal.emit(f"slack:{action}")

    def _on_debug_trigger(self):
        self.debug_trigger.emit()

    def _on_debug_observe(self, mode: str):
        if not self._observe_cb.isChecked():
            return
        btn = self._btn_debug_scr if mode == "screenshot" else self._btn_debug_cam
        orig_text = btn.text()
        btn.setEnabled(False)
        btn.setText("3 秒后…")
        self._btn_debug_scr.setEnabled(False)
        self._btn_debug_cam.setEnabled(False)
        QTimer.singleShot(3000, lambda: self._do_debug_observe(mode, btn, orig_text))

    def _do_debug_observe(self, mode: str, btn, orig_text: str):
        btn.setText(orig_text)
        btn.setEnabled(True)
        self._btn_debug_scr.setEnabled(True)
        self._btn_debug_cam.setEnabled(True)
        self.debug_observe_signal.emit(mode)

    def _update_debug_btn_state(self):
        any_enabled = self._enable_cb.isChecked() or self._qq_cb.isChecked()
        self._btn_debug.setEnabled(any_enabled)
        self._enable_cb.stateChanged.connect(self._refresh_debug_btn)
        self._qq_cb.stateChanged.connect(self._refresh_debug_btn)

    def _refresh_debug_btn(self):
        self._btn_debug.setEnabled(
            self._enable_cb.isChecked() or self._qq_cb.isChecked()
        )

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._refresh_bl_tags()
            self._refresh_bl_history()
        except Exception:
            pass

    def closeEvent(self, event):
        self._prob_timer.stop()
        event.accept()
