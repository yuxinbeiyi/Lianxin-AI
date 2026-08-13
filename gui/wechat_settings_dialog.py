"""
微信聊天面板：桥接开关 + 防封参数设置。
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QSpinBox, QPushButton,
    QCheckBox, QFrame, QLineEdit,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from config import (
    get_wechat_timing_config,
    save_wechat_timing_config,
    get_wechat_bridge_config,
    save_wechat_bridge_config,
)


class WeChatSettingsDialog(QDialog):
    """微信聊天面板：桥接开关 + 防封参数设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge_controller = getattr(parent, "_bridge_controller", None)
        self.setWindowTitle("微信聊天")
        self.setMinimumSize(460, 620)
        self.resize(480, 650)

        self._config = get_wechat_timing_config()
        self._build_ui()
        self._load_config()
        self._refresh_bridge_section()

    def _create_frame(self):
        f = QFrame()
        f.setFrameShape(QFrame.StyledPanel)
        f.setStyleSheet("QFrame { background-color: #1E1E30; border-radius: 8px; }")
        return f

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # ── 微信桥接开关 ──────────────────────────────────────
        bridge_frame = self._create_frame()
        bridge_layout = QHBoxLayout(bridge_frame)

        self._bridge_status = QLabel("微信桥接: 未连接")
        self._bridge_status.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        bridge_layout.addWidget(self._bridge_status)

        bridge_layout.addStretch()

        self._btn_bridge_toggle = QPushButton("连接")
        self._btn_bridge_toggle.setFixedSize(80, 30)
        self._btn_bridge_toggle.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border-radius: 6px;
                border: none;
                font-weight: bold;
            }
            QPushButton:hover  { background-color: #5A6AEE; }
        """)
        self._btn_bridge_toggle.clicked.connect(self._on_bridge_toggle)
        bridge_layout.addWidget(self._btn_bridge_toggle)

        layout.addWidget(bridge_frame)

        # ── 自动启动 ────────────────────────────────────────
        self._auto_start_cb = QCheckBox("启动莲心时自动连接微信桥接")
        self._auto_start_cb.setChecked(get_wechat_bridge_config().get("auto_start", False))
        layout.addWidget(self._auto_start_cb)

        # ── 网络配置 ────────────────────────────────────────
        grp_net = QGroupBox("网络配置")
        net_layout = QVBoxLayout(grp_net)
        net_layout.setSpacing(14)
        net_layout.setContentsMargins(12, 16, 12, 12)

        row_port = QHBoxLayout()
        row_port.addWidget(QLabel("监听端口"))
        self._listen_port = QSpinBox()
        self._listen_port.setRange(1024, 65535)
        self._listen_port.setValue(get_wechat_bridge_config().get("listen_port", 8088))
        row_port.addWidget(self._listen_port)
        row_port.addStretch()
        net_layout.addLayout(row_port)

        row_owner = QHBoxLayout()
        row_owner.addWidget(QLabel("主人微信ID"))
        self._owner_id = QLineEdit()
        self._owner_id.setText(get_wechat_bridge_config().get("owner_id", ""))
        self._owner_id.setPlaceholderText("你的微信ID，用于每日上限区分")
        row_owner.addWidget(self._owner_id, 1)
        net_layout.addLayout(row_owner)

        layout.addWidget(grp_net)

        # ── 回复速度 ────────────────────────────────────────
        grp_reply = QGroupBox("回复速度（防封，模拟真人）")
        grp_layout = QVBoxLayout(grp_reply)
        grp_layout.setSpacing(14)
        grp_layout.setContentsMargins(12, 16, 12, 12)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("思考延迟"))
        self._think_min = QDoubleSpinBox()
        self._think_min.setRange(0.5, 30.0)
        self._think_min.setSingleStep(0.5)
        self._think_min.setSuffix(" 秒")
        row1.addWidget(self._think_min)
        row1.addWidget(QLabel("~"))
        self._think_max = QDoubleSpinBox()
        self._think_max.setRange(0.5, 30.0)
        self._think_max.setSingleStep(0.5)
        self._think_max.setSuffix(" 秒")
        row1.addWidget(self._think_max)
        row1.addStretch()
        grp_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("打字速度"))
        self._speed_min = QSpinBox()
        self._speed_min.setRange(10, 999)
        self._speed_min.setSuffix(" 字/分钟")
        row2.addWidget(self._speed_min)
        row2.addWidget(QLabel("~"))
        self._speed_max = QSpinBox()
        self._speed_max.setRange(10, 999)
        self._speed_max.setSuffix(" 字/分钟")
        row2.addWidget(self._speed_max)
        row2.addStretch()
        grp_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("最短回复间隔"))
        self._min_interval = QDoubleSpinBox()
        self._min_interval.setRange(0.5, 60.0)
        self._min_interval.setSingleStep(0.5)
        self._min_interval.setSuffix(" 秒")
        row3.addWidget(self._min_interval)
        row3.addStretch()
        grp_layout.addLayout(row3)

        layout.addWidget(grp_reply)

        # ── 分段发送 ────────────────────────────────────────
        grp_seg = QGroupBox("分段发送（模拟真人打字）")
        seg_layout = QVBoxLayout(grp_seg)
        seg_layout.setSpacing(14)
        seg_layout.setContentsMargins(12, 16, 12, 12)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("分段字数阈值"))
        self._seg_min = QSpinBox()
        self._seg_min.setRange(20, 500)
        self._seg_min.setSingleStep(10)
        self._seg_min.setSuffix(" 字")
        row4.addWidget(self._seg_min)
        row4.addWidget(QLabel("~"))
        self._seg_max = QSpinBox()
        self._seg_max.setRange(20, 500)
        self._seg_max.setSingleStep(10)
        self._seg_max.setSuffix(" 字")
        row4.addWidget(self._seg_max)
        row4.addStretch()
        seg_layout.addLayout(row4)

        row5 = QHBoxLayout()
        row5.addWidget(QLabel("段间间隔"))
        self._seg_interval_min = QDoubleSpinBox()
        self._seg_interval_min.setRange(1.0, 60.0)
        self._seg_interval_min.setSingleStep(0.5)
        self._seg_interval_min.setSuffix(" 秒")
        row5.addWidget(self._seg_interval_min)
        row5.addWidget(QLabel("~"))
        self._seg_interval_max = QDoubleSpinBox()
        self._seg_interval_max.setRange(1.0, 60.0)
        self._seg_interval_max.setSingleStep(0.5)
        self._seg_interval_max.setSuffix(" 秒")
        row5.addWidget(self._seg_interval_max)
        row5.addStretch()
        seg_layout.addLayout(row5)

        layout.addWidget(grp_seg)

        # ── 全局限制 ────────────────────────────────────────
        grp_global = QGroupBox("全局限制（防封风控）")
        g_layout = QVBoxLayout(grp_global)
        g_layout.setSpacing(14)
        g_layout.setContentsMargins(12, 16, 12, 12)

        row6 = QHBoxLayout()
        row6.addWidget(QLabel("全局发送间隔"))
        self._global_min = QDoubleSpinBox()
        self._global_min.setRange(1.0, 120.0)
        self._global_min.setSingleStep(0.5)
        self._global_min.setSuffix(" 秒")
        row6.addWidget(self._global_min)
        row6.addWidget(QLabel("~"))
        self._global_max = QDoubleSpinBox()
        self._global_max.setRange(1.0, 120.0)
        self._global_max.setSingleStep(0.5)
        self._global_max.setSuffix(" 秒")
        row6.addWidget(self._global_max)
        row6.addStretch()
        g_layout.addLayout(row6)

        row7 = QHBoxLayout()
        row7.addWidget(QLabel("主人每日上限"))
        self._owner_limit = QSpinBox()
        self._owner_limit.setRange(5, 500)
        self._owner_limit.setSuffix(" 条")
        self._owner_limit.setEnabled(False)  # 主人不受聊天次数限制，此值不参与限制
        row7.addWidget(self._owner_limit)
        row7.addWidget(QLabel("（主人不受限）"))
        row7.addStretch()
        g_layout.addLayout(row7)

        row8cb = QHBoxLayout()
        self._limit_cb = QCheckBox("对用户实施微信端聊天次数限制")
        self._limit_cb.setToolTip("勾选后仅对其它用户生效，主人不受限；取消勾选则完全不限制其它用户")
        row8cb.addWidget(self._limit_cb)
        row8cb.addStretch()
        g_layout.addLayout(row8cb)

        row8 = QHBoxLayout()
        row8.addWidget(QLabel("其他用户上限"))
        self._other_limit = QSpinBox()
        self._other_limit.setRange(1, 999)
        self._other_limit.setSuffix(" 条")
        row8.addWidget(self._other_limit)
        row8.addStretch()
        g_layout.addLayout(row8)

        row8p = QHBoxLayout()
        row8p.addWidget(QLabel("单群每日上限"))
        self._group_limit = QSpinBox()
        self._group_limit.setRange(5, 200)
        self._group_limit.setSuffix(" 条")
        row8p.addWidget(self._group_limit)
        row8p.addStretch()
        g_layout.addLayout(row8p)

        # ── 链接过滤 ──
        row9 = QHBoxLayout()
        self._block_links_cb = QCheckBox("过滤所有链接（微信风控敏感，建议开启）")
        self._block_links_cb.setChecked(True)
        row9.addWidget(self._block_links_cb)
        row9.addStretch()
        g_layout.addLayout(row9)

        # ── 跨端记忆 ──
        row10 = QHBoxLayout()
        row10.addWidget(QLabel("跨端参考条数"))
        self._cross_limit = QSpinBox()
        self._cross_limit.setRange(0, 50)
        self._cross_limit.setSuffix(" 条")
        self._cross_limit.setToolTip("桌面端⇔微信端互相参考的最近聊天条数（0=关闭跨端记忆）")
        row10.addWidget(self._cross_limit)
        row10.addWidget(QLabel("（0=关闭跨端记忆）"))
        row10.addStretch()
        g_layout.addLayout(row10)

        # ── 语音回复开关 ──
        row11 = QHBoxLayout()
        row11.addWidget(QLabel("语音回复"))
        self._voice_cb = QCheckBox("收到语音或用【语音】前缀时以语音回复")
        row11.addWidget(self._voice_cb)
        row11.addStretch()
        g_layout.addLayout(row11)

        layout.addWidget(grp_global)

        # ── 按钮 ────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._btn_default = QPushButton("恢复默认")
        self._btn_default.setFixedSize(80, 28)
        self._btn_default.clicked.connect(self._on_default)
        btn_layout.addWidget(self._btn_default)

        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setFixedSize(60, 28)
        self._btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self._btn_cancel)

        self._btn_apply = QPushButton("立即生效")
        self._btn_apply.setFixedSize(80, 28)
        self._btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover  { background-color: #5A6AEE; }
            QPushButton:pressed{ background-color: #4A5ADE; }
        """)
        self._btn_apply.clicked.connect(self._on_apply)
        btn_layout.addWidget(self._btn_apply)

        layout.addLayout(btn_layout)

    def _load_config(self):
        """将配置值加载到控件中。"""
        self._think_min.setValue(self._config["think_delay_min"])
        self._think_max.setValue(self._config["think_delay_max"])
        self._speed_min.setValue(self._config["type_speed_min"])
        self._speed_max.setValue(self._config["type_speed_max"])
        self._min_interval.setValue(self._config["min_reply_interval"])
        self._seg_min.setValue(self._config["segment_threshold_min"])
        self._seg_max.setValue(self._config["segment_threshold_max"])
        self._seg_interval_min.setValue(self._config["segment_interval_min"])
        self._seg_interval_max.setValue(self._config["segment_interval_max"])
        self._global_min.setValue(self._config["global_send_interval_min"])
        self._global_max.setValue(self._config["global_send_interval_max"])
        self._limit_cb.setChecked(self._config.get("limit_enabled", True))
        self._owner_limit.setValue(self._config["daily_limit_owner"])
        self._other_limit.setValue(self._config["daily_limit_other"])
        self._group_limit.setValue(self._config.get("per_group_daily_limit", 30))
        self._block_links_cb.setChecked(self._config.get("block_links", True))
        self._cross_limit.setValue(self._config.get("cross_session_context_limit", 6))

        # 从桥接配置加载
        bridge_cfg = get_wechat_bridge_config()
        self._voice_cb.setChecked(bridge_cfg.get("voice_reply_enabled", True))

    def _collect_config(self) -> dict:
        """从控件收集当前值并返回配置字典。"""
        return {
            "think_delay_min": self._think_min.value(),
            "think_delay_max": self._think_max.value(),
            "type_speed_min": self._speed_min.value(),
            "type_speed_max": self._speed_max.value(),
            "min_reply_interval": self._min_interval.value(),
            "segment_threshold_min": self._seg_min.value(),
            "segment_threshold_max": self._seg_max.value(),
            "segment_interval_min": self._seg_interval_min.value(),
            "segment_interval_max": self._seg_interval_max.value(),
            "global_send_interval_min": self._global_min.value(),
            "global_send_interval_max": self._global_max.value(),
            "daily_limit_owner": self._owner_limit.value(),
            "limit_enabled": self._limit_cb.isChecked(),
            "daily_limit_other": self._other_limit.value(),
            "per_group_daily_limit": self._group_limit.value(),
            "block_links": self._block_links_cb.isChecked(),
            "cross_session_context_limit": self._cross_limit.value(),
        }

    def _collect_bridge_config(self) -> dict:
        """收集桥接配置。"""
        base = get_wechat_bridge_config()
        base.update({
            "auto_start": self._auto_start_cb.isChecked(),
            "listen_port": self._listen_port.value(),
            "owner_id": self._owner_id.text().strip(),
            "voice_reply_enabled": self._voice_cb.isChecked(),
        })
        return base

    def _on_default(self):
        """恢复默认值。"""
        from config import _WECHAT_TIMING_DEFAULTS
        self._config = dict(_WECHAT_TIMING_DEFAULTS)
        self._load_config()
        self._auto_start_cb.setChecked(False)
        self._voice_cb.setChecked(True)

    def _refresh_bridge_section(self):
        """更新桥接状态显示。"""
        if self._bridge_controller is None:
            return
        running = self._bridge_controller.is_wechat_running()
        if running:
            self._bridge_status.setText("微信桥接: 已连接")
            self._btn_bridge_toggle.setText("断开")
        else:
            self._bridge_status.setText("微信桥接: 未连接")
            self._btn_bridge_toggle.setText("连接")

    def _on_bridge_toggle(self):
        """点击连接/断开按钮。"""
        if self._bridge_controller is None:
            return
        running = self._bridge_controller.is_wechat_running()
        if running:
            self._bridge_controller.stop_wechat()
        else:
            self._bridge_controller.start_wechat()
        self._refresh_bridge_section()

    def _on_apply(self):
        """保存配置并关闭对话框。"""
        cfg = self._collect_config()
        save_wechat_timing_config(cfg)
        bridge_cfg = self._collect_bridge_config()
        save_wechat_bridge_config(bridge_cfg)

        if self._bridge_controller:
            self._bridge_controller.reload_wechat_config()

        self.accept()
