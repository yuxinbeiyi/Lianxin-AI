"""
QQ 聊天面板：桥接开关 + 参数设置。
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QSpinBox, QPushButton,
    QCheckBox, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from config import get_qq_timing_config, save_qq_timing_config


class QqSettingsDialog(QDialog):
    """QQ 聊天面板：桥接开关 + 定时参数设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge_controller = getattr(parent, "_bridge_controller", None)
        self.setWindowTitle("QQ 聊天")
        self.setMinimumSize(440, 560)
        self.resize(460, 600)

        self._config = get_qq_timing_config()
        self._build_ui()
        self._load_config()
        self._refresh_bridge_section()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # ── QQ 桥接开关 ──────────────────────────────────────
        bridge_frame = QFrame()
        bridge_frame.setFrameShape(QFrame.StyledPanel)
        bridge_frame.setStyleSheet("QFrame { background-color: #1E1E30; border-radius: 8px; }")
        bridge_layout = QHBoxLayout(bridge_frame)

        self._bridge_status = QLabel("QQ 桥接: 未连接")
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
        self._auto_start_cb = QCheckBox("启动莲心时自动连接 QQ 桥接")
        from config import get_qq_bridge_config
        self._auto_start_cb.setChecked(get_qq_bridge_config().get("auto_start", False))
        self._auto_start_cb.stateChanged.connect(self._on_auto_start_changed)
        layout.addWidget(self._auto_start_cb)

        self._fast_reply_cb = QCheckBox("极速回复（主人私聊）")
        self._fast_reply_cb.setToolTip("本次运行期间跳过人为思考、打字和发送等待；不影响其他用户与群聊")
        if self._bridge_controller:
            self._fast_reply_cb.setChecked(
                self._bridge_controller.is_qq_fast_reply_enabled()
            )
        self._fast_reply_cb.toggled.connect(self._on_fast_reply_toggled)
        layout.addWidget(self._fast_reply_cb)

        self._segmented_reply_cb = QCheckBox("分段发送回复")
        self._segmented_reply_cb.setToolTip("开启时按语义分段发送长回复；关闭时每次发送完整回复")
        layout.addWidget(self._segmented_reply_cb)

        # ── 回复速度 ────────────────────────────────────────
        grp_reply = QGroupBox("回复速度")
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
        self._speed_min.setRange(10, 2000)
        self._speed_min.setSuffix(" 字/分钟")
        row2.addWidget(self._speed_min)
        row2.addWidget(QLabel("~"))
        self._speed_max = QSpinBox()
        self._speed_max.setRange(10, 2000)
        self._speed_max.setSuffix(" 字/分钟")
        row2.addWidget(self._speed_max)
        row2.addStretch()
        grp_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("最短回复间隔"))
        self._min_interval = QDoubleSpinBox()
        self._min_interval.setRange(0.0, 30.0)
        self._min_interval.setSingleStep(0.5)
        self._min_interval.setSuffix(" 秒")
        row3.addWidget(self._min_interval)
        row3.addStretch()
        grp_layout.addLayout(row3)

        layout.addWidget(grp_reply)

        # ── 全局限制 ────────────────────────────────────────
        grp_global = QGroupBox("全局限制")
        g_layout = QVBoxLayout(grp_global)
        g_layout.setSpacing(14)
        g_layout.setContentsMargins(12, 16, 12, 12)

        row6 = QHBoxLayout()
        row6.addWidget(QLabel("全局发送间隔"))
        self._global_min = QDoubleSpinBox()
        self._global_min.setRange(0.0, 120.0)
        self._global_min.setSingleStep(0.5)
        self._global_min.setSuffix(" 秒")
        row6.addWidget(self._global_min)
        row6.addWidget(QLabel("~"))
        self._global_max = QDoubleSpinBox()
        self._global_max.setRange(0.0, 120.0)
        self._global_max.setSingleStep(0.5)
        self._global_max.setSuffix(" 秒")
        row6.addWidget(self._global_max)
        row6.addStretch()
        g_layout.addLayout(row6)

        row8cb = QHBoxLayout()
        self._limit_cb = QCheckBox("对用户实施QQ端聊天次数限制")
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

        # ── 跨端记忆 ──
        row9 = QHBoxLayout()
        row9.addWidget(QLabel("跨端参考条数"))
        self._cross_limit = QSpinBox()
        self._cross_limit.setRange(0, 50)
        self._cross_limit.setSuffix(" 条")
        self._cross_limit.setToolTip("桌面端⇔QQ端互相参考的最近聊天条数（0=关闭跨端记忆）")
        row9.addWidget(self._cross_limit)
        row9.addWidget(QLabel("（0=关闭跨端记忆）"))
        row9.addStretch()
        g_layout.addLayout(row9)

        # ── 语音回复开关 ──
        row10 = QHBoxLayout()
        row10.addWidget(QLabel("语音回复"))
        self._voice_cb = QCheckBox("收到语音或用【语音】前缀时以语音回复")
        row10.addWidget(self._voice_cb)
        row10.addStretch()
        g_layout.addLayout(row10)

        layout.addWidget(grp_global)

        # ── 拍一拍回应 ────────────────────────────────────
        grp_poke = QGroupBox("拍一拍回应")
        poke_layout = QVBoxLayout(grp_poke)

        self._poke_enabled_cb = QCheckBox("开启拍一拍回应")
        self._poke_enabled_cb.setToolTip("有人拍一拍莲心的QQ头像时，莲心会回应并可能反拍回去")
        poke_layout.addWidget(self._poke_enabled_cb)

        self._poke_llm_cb = QCheckBox("LLM 生成回应（失败时自动用萌语兜底）")
        self._poke_llm_cb.setToolTip("开启时走真实 LLM 链路生成个性化回应；关闭则直接使用固定萌语")
        poke_layout.addWidget(self._poke_llm_cb)

        rowp1 = QHBoxLayout()
        rowp1.addWidget(QLabel("反拍概率"))
        self._poke_prob = QDoubleSpinBox()
        self._poke_prob.setRange(0.0, 1.0)
        self._poke_prob.setSingleStep(0.05)
        self._poke_prob.setDecimals(2)
        self._poke_prob.setToolTip("被拍一拍后反拍回去的概率，默认 60%")
        rowp1.addWidget(self._poke_prob)
        rowp1.addWidget(QLabel("（0=不反拍，1=每次）"))
        rowp1.addStretch()
        poke_layout.addLayout(rowp1)

        rowp2 = QHBoxLayout()
        rowp2.addWidget(QLabel("反拍延迟"))
        self._poke_delay = QDoubleSpinBox()
        self._poke_delay.setRange(0.0, 10.0)
        self._poke_delay.setSingleStep(0.5)
        self._poke_delay.setDecimals(1)
        self._poke_delay.setSuffix(" 秒")
        self._poke_delay.setToolTip("用户拍完后多少秒莲心再反拍，默认 2 秒")
        rowp2.addWidget(self._poke_delay)
        rowp2.addStretch()
        poke_layout.addLayout(rowp2)

        rowp3 = QHBoxLayout()
        rowp3.addWidget(QLabel("冷却时间"))
        self._poke_cooldown = QSpinBox()
        self._poke_cooldown.setRange(0, 600)
        self._poke_cooldown.setSuffix(" 秒")
        self._poke_cooldown.setToolTip("同一用户两次拍一拍回应之间的最短间隔（防刷屏），默认 30 秒")
        rowp3.addWidget(self._poke_cooldown)
        rowp3.addStretch()
        poke_layout.addLayout(rowp3)

        layout.addWidget(grp_poke)

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
        self._global_min.setValue(self._config["global_send_interval_min"])
        self._global_max.setValue(self._config["global_send_interval_max"])
        self._limit_cb.setChecked(self._config.get("limit_enabled", True))
        self._other_limit.setValue(self._config["daily_limit_other"])
        self._cross_limit.setValue(self._config.get("cross_session_context_limit", 15))

        # 从桥接配置加载语音回复开关
        from config import get_qq_bridge_config
        bridge_cfg = get_qq_bridge_config()
        self._voice_cb.setChecked(bridge_cfg.get("voice_reply_enabled", True))
        self._segmented_reply_cb.setChecked(bridge_cfg.get("segmented_reply_enabled", True))
        # 拍一拍回应
        self._poke_enabled_cb.setChecked(bridge_cfg.get("poke_enabled", True))
        self._poke_llm_cb.setChecked(bridge_cfg.get("poke_llm", True))
        self._poke_prob.setValue(float(bridge_cfg.get("poke_poke_back_probability", 0.6)))
        self._poke_delay.setValue(float(bridge_cfg.get("poke_poke_back_delay_seconds", 2.0)))
        self._poke_cooldown.setValue(int(bridge_cfg.get("poke_cooldown_seconds", 30)))

    def _collect_config(self) -> dict:
        """从控件收集当前值并返回配置字典。"""
        think_min, think_max = sorted((self._think_min.value(), self._think_max.value()))
        speed_min, speed_max = sorted((self._speed_min.value(), self._speed_max.value()))
        global_min, global_max = sorted((self._global_min.value(), self._global_max.value()))
        return {
            "profile_version": self._config.get("profile_version", 2),
            "think_delay_min": think_min,
            "think_delay_max": think_max,
            "type_speed_min": speed_min,
            "type_speed_max": speed_max,
            "min_reply_interval": self._min_interval.value(),
            "segment_interval_min": 0.1,
            "segment_interval_max": 0.4,
            "global_send_interval_min": global_min,
            "global_send_interval_max": global_max,
            "daily_limit_other": self._other_limit.value(),
            "limit_enabled": self._limit_cb.isChecked(),
            "cross_session_context_limit": self._cross_limit.value(),
        }

    def _on_default(self):
        """恢复默认值。"""
        from config import _QQ_TIMING_DEFAULTS
        self._config = dict(_QQ_TIMING_DEFAULTS)
        self._load_config()
        self._voice_cb.setChecked(True)  # 语音回复默认开启
        self._segmented_reply_cb.setChecked(True)
        # 拍一拍默认值
        self._poke_enabled_cb.setChecked(True)
        self._poke_llm_cb.setChecked(True)
        self._poke_prob.setValue(0.6)
        self._poke_delay.setValue(2.0)
        self._poke_cooldown.setValue(30)

    def _on_apply(self):
        """保存配置并关闭对话框。"""
        config = self._collect_config()
        save_qq_timing_config(config)

        # 同时保存语音回复开关到桥接配置
        from config import get_qq_bridge_config, save_qq_bridge_config
        bridge_cfg = get_qq_bridge_config()
        bridge_cfg["voice_reply_enabled"] = self._voice_cb.isChecked()
        bridge_cfg["segmented_reply_enabled"] = self._segmented_reply_cb.isChecked()
        # 拍一拍回应
        bridge_cfg["poke_enabled"] = self._poke_enabled_cb.isChecked()
        bridge_cfg["poke_llm"] = self._poke_llm_cb.isChecked()
        bridge_cfg["poke_poke_back_probability"] = float(self._poke_prob.value())
        bridge_cfg["poke_poke_back_delay_seconds"] = float(self._poke_delay.value())
        bridge_cfg["poke_cooldown_seconds"] = int(self._poke_cooldown.value())
        save_qq_bridge_config(bridge_cfg)

        self._config = config
        self.accept()

    def _on_fast_reply_toggled(self, enabled: bool):
        if self._bridge_controller:
            self._bridge_controller.set_qq_fast_reply_enabled(enabled)

    def _on_bridge_toggle(self):
        """启动/停止 QQ 桥接。"""
        if self._bridge_controller is None:
            return
        if self._bridge_controller.is_qq_running():
            self._bridge_controller.stop_qq()
        else:
            self._bridge_controller.start_qq()
        self._refresh_bridge_section()

    def _refresh_bridge_section(self):
        """刷新桥接状态显示。"""
        if self._bridge_controller and self._bridge_controller.is_qq_connected():
            self._bridge_status.setText("QQ 桥接: ● 已连接")
            self._bridge_status.setStyleSheet("color: #34C759;")
            self._btn_bridge_toggle.setText("断开")
            self._btn_bridge_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #FF6B6B;
                    color: white;
                    border-radius: 6px;
                    border: none;
                    font-weight: bold;
                }
                QPushButton:hover  { background-color: #EE5A5A; }
            """)
        else:
            self._bridge_status.setText("QQ 桥接: ○ 未连接")
            self._bridge_status.setStyleSheet("color: #999999;")
            self._btn_bridge_toggle.setText("连接")
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

    def _on_auto_start_changed(self):
        """保存自动启动设置到持久化配置。"""
        from config import get_qq_bridge_config, save_qq_bridge_config
        cfg = get_qq_bridge_config()
        cfg["auto_start"] = self._auto_start_cb.isChecked()
        save_qq_bridge_config(cfg)
