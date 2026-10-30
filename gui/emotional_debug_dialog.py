"""
EmotionalDebugDialog v3：连续情绪、关系慢变量和动力参数调试面板。

Tab 1：实时状态 — 需求、情绪分量、关系阶段、会话上限
Tab 2：事件模拟 — 手动触发事件 / 批量测试
Tab 3：时间模拟 — 跳过时间 / 衰减预览
Tab 4：事件日志 — 历史事件 + 统计
Tab 5：参数覆盖 — 运行时覆盖衰减 τ 等参数
"""

import time
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QComboBox, QFrame, QGroupBox,
    QTextEdit, QProgressBar, QWidget, QGridLayout, QTabWidget,
    QSpinBox, QDoubleSpinBox, QCheckBox, QFileDialog, QMessageBox,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont


class EmotionalDebugDialog(QDialog):
    """涟漪情感系统调试面板 v3。"""

    NEED_LABELS = {
        "connection": "连接需求", "pride": "骄傲", "guardedness": "防御感",
        "valence": "愉悦度", "arousal": "唤醒度", "immersion": "沉浸度",
    }

    EMOTION_LABELS = {
        "trust": "信任", "intimacy": "亲密", "rupture": "裂痕", "repair": "修复",
    }

    EVENT_AXIS_LABELS = {
        **NEED_LABELS,
        **EMOTION_LABELS,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🧪 涟漪情感系统 v3 — 调试面板")
        self.setMinimumSize(720, 580)

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(8)

        self._build_tabs()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_display)
        self._refresh_timer.start(2000)

        self._refresh_display()

    def _build_tabs(self):
        tabs = QTabWidget()

        tabs.addTab(self._build_status_tab(), "🧪 实时状态")
        tabs.addTab(self._build_sim_tab(), "🎮 事件模拟")
        tabs.addTab(self._build_time_tab(), "⏱ 时间模拟")
        tabs.addTab(self._build_log_tab(), "📊 事件日志")
        tabs.addTab(self._build_params_tab(), "⚙ 参数覆盖")

        self._layout.addWidget(tabs)

    # ── Tab 1：实时状态 ─────────────────────────────────

    def _build_status_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        toggle_row = QHBoxLayout()
        self._toggle_btn = QPushButton()
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setFixedHeight(36)
        self._toggle_btn.setStyleSheet("""
            QPushButton { color: white; border-radius: 8px; font-size: 13px;
                font-weight: bold; border: none; padding: 6px 14px; }
            QPushButton:checked { background-color: #27AE60; }
            QPushButton:!checked { background-color: #E74C3C; }
        """)
        self._toggle_btn.toggled.connect(self._on_toggle_emotion)
        toggle_row.addWidget(self._toggle_btn)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        overview = QGroupBox("情感总览")
        ov = QVBoxLayout(overview)
        self._mid_layer_label = QLabel("情感基调: —")
        self._mid_layer_label.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        ov.addWidget(self._mid_layer_label)
        self._deep_label = QLabel("深层信任: —")
        ov.addWidget(self._deep_label)
        self._stage_label = QLabel("关系阶段: —")
        ov.addWidget(self._stage_label)
        self._stats_label = QLabel("")
        ov.addWidget(self._stats_label)
        layout.addWidget(overview)

        needs_g = QGroupBox("连续情感轴（愉悦度/唤醒度/防御感的 50 为中性）")
        axis_font = QFont("Microsoft YaHei UI", 13)
        needs_g.setFont(axis_font)
        ng = QGridLayout(needs_g)
        ng.setSpacing(6)
        self._need_bars = {}
        self._need_labels = {}
        self._sliders = {}
        self._slider_modified = False
        for i, (key, label) in enumerate(self.NEED_LABELS.items()):
            axis_label = QLabel(label)
            axis_label.setFont(axis_font)
            ng.addWidget(axis_label, i, 0)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setFixedHeight(16)
            bar.setTextVisible(True)
            bar.setFormat(f"%v")
            bar.setStyleSheet("QProgressBar { font-size: 13px; }")
            ng.addWidget(bar, i, 1)
            val_label = QLabel("50")
            val_label.setFont(axis_font)
            val_label.setFixedWidth(48)
            ng.addWidget(val_label, i, 2)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 100)
            sl.setValue(50)
            sl.valueChanged.connect(
                lambda v, k=key: setattr(self, '_slider_modified', True)
            )
            ng.addWidget(sl, i, 3)
            self._need_bars[key] = bar
            self._need_labels[key] = val_label
            self._sliders[key] = sl
        layout.addWidget(needs_g)

        btn_row = QHBoxLayout()
        apply_btn = QPushButton("应用滑块值")
        apply_btn.clicked.connect(self._apply_sliders)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)

        emo_g = QGroupBox("关系慢变量")
        emo_g.setFont(axis_font)
        eg = QHBoxLayout(emo_g)
        self._emotion_labels = {}
        for key, label in self.EMOTION_LABELS.items():
            lbl = QLabel(f"{label}: 0")
            lbl.setFont(axis_font)
            self._emotion_labels[key] = lbl
            eg.addWidget(lbl)
        eg.addStretch()
        layout.addWidget(emo_g)

        cap_g = QGroupBox("当前作用域")
        cap_g.setFont(axis_font)
        cg = QHBoxLayout(cap_g)
        self._cap_labels = {}
        for key in self.NEED_LABELS:
            lbl = QLabel("")
            self._cap_labels[key] = lbl
            lbl.setStyleSheet("font-size: 15px; color: #888;")
            cg.addWidget(lbl)
        cg.addStretch()
        layout.addWidget(cap_g)

        btn_row2 = QHBoxLayout()
        reset_btn = QPushButton("重置为初始状态")
        reset_btn.clicked.connect(self._reset_state)
        btn_row2.addWidget(reset_btn)
        save_btn = QPushButton("强制保存")
        save_btn.clicked.connect(self._force_save)
        btn_row2.addWidget(save_btn)
        layout.addLayout(btn_row2)

        return w

    # ── Tab 2：事件模拟 ─────────────────────────────────

    def _build_sim_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        event_g = QGroupBox("手动触发事件")
        ev = QVBoxLayout(event_g)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("事件类型:"))
        self._event_combo = QComboBox()
        self._event_combo.setMinimumWidth(200)
        event_types = [
            ("warm_chat", "温暖聊天"),
            ("deep_chat", "深度交流"),
            ("compliment", "夸奖"),
            ("thanks", "感谢"),
            ("daily_ritual", "日常问候"),
            ("work_collaboration", "协作完成"),
            ("new_feature_interest", "对新功能好奇"),
            ("remember_me", "被记得"),
            ("cold_command", "冷命令"),
            ("command_spree", "命令连击"),
            ("apology", "道歉"),
            ("boundary_dismiss", "否定人格"),
            ("boundary_lie", "欺骗"),
            ("ignore_return", "忽视后回归"),
            ("user_happy", "用户开心"),
            ("user_upset", "用户不开心"),
        ]
        for val, label in event_types:
            self._event_combo.addItem(label, val)
        row1.addWidget(self._event_combo)
        ev.addLayout(row1)

        row2 = QHBoxLayout()
        trigger_btn = QPushButton("触发事件")
        trigger_btn.clicked.connect(self._trigger_event)
        row2.addWidget(trigger_btn)
        batch_btn = QPushButton("连续触发 5 次")
        batch_btn.clicked.connect(self._trigger_batch)
        row2.addWidget(batch_btn)
        ev.addLayout(row2)

        layout.addWidget(event_g)

        batch_g = QGroupBox("批量测试")
        bv = QVBoxLayout(batch_g)
        scenarios = QHBoxLayout()
        for name, cb_name in [
            ("模拟一轮友好对话", "_on_friendly_session"),
            ("模拟一轮命令对话", "_on_command_session"),
            ("模拟一天无人交互", "_on_skip_day"),
            ("模拟边界冲突", "_on_boundary_conflict"),
        ]:
            btn = QPushButton(name)
            btn.clicked.connect(getattr(self, cb_name))
            scenarios.addWidget(btn)
        bv.addLayout(scenarios)
        compare_row = QHBoxLayout()
        preview_btn = QPushButton("对比三组典型场景（不改真实状态）")
        preview_btn.clicked.connect(self._preview_scenario_batches)
        compare_row.addWidget(preview_btn)
        compare_row.addStretch()
        bv.addLayout(compare_row)
        self._scenario_result = QTextEdit()
        self._scenario_result.setReadOnly(True)
        self._scenario_result.setMaximumHeight(150)
        self._scenario_result.setPlaceholderText("批量场景对比结果会显示在这里。")
        bv.addWidget(self._scenario_result)
        layout.addWidget(batch_g)

        layout.addStretch()
        return w

    # ── Tab 3：时间模拟 ─────────────────────────────────

    def _build_time_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        skip_g = QGroupBox("跳过时间")
        sv = QVBoxLayout(skip_g)

        row = QHBoxLayout()
        for hours, label in [(1, "1小时"), (6, "6小时"), (24, "1天"),
                              (72, "3天"), (168, "1周")]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, h=hours: self._skip_time(h))
            row.addWidget(btn)
        sv.addLayout(row)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("自定义小时:"))
        self._custom_hours = QSpinBox()
        self._custom_hours.setRange(1, 720)
        self._custom_hours.setValue(1)
        custom_row.addWidget(self._custom_hours)
        custom_btn = QPushButton("跳过")
        custom_btn.clicked.connect(lambda: self._skip_time(self._custom_hours.value()))
        custom_row.addWidget(custom_btn)
        custom_row.addStretch()
        sv.addLayout(custom_row)

        layout.addWidget(skip_g)

        info_g = QGroupBox("时间信息")
        iv = QVBoxLayout(info_g)
        self._time_info_label = QLabel("")
        iv.addWidget(self._time_info_label)
        layout.addWidget(info_g)

        layout.addStretch()
        return w

    # ── Tab 4：事件日志 ─────────────────────────────────

    def _build_log_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        stats_g = QGroupBox("统计")
        sv = QHBoxLayout(stats_g)
        self._log_stats = QLabel("")
        sv.addWidget(self._log_stats)
        layout.addWidget(stats_g)

        log_g = QGroupBox("事件历史（最近 200 条）")
        lv = QVBoxLayout(log_g)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 9))
        lv.addWidget(self._log_view)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("清除日志")
        clear_btn.clicked.connect(self._clear_log)
        btn_row.addWidget(clear_btn)
        export_json_btn = QPushButton("导出 JSON")
        export_json_btn.clicked.connect(lambda: self._export_trajectory("json"))
        btn_row.addWidget(export_json_btn)
        export_csv_btn = QPushButton("导出 CSV")
        export_csv_btn.clicked.connect(lambda: self._export_trajectory("csv"))
        btn_row.addWidget(export_csv_btn)
        btn_row.addStretch()
        lv.addLayout(btn_row)

        layout.addWidget(log_g)
        return w

    # ── Tab 5：动力参数 ─────────────────────────────────

    def _build_params_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        tip = QLabel("参数会持久化到 emotion_v3 配置；修改后立即用于后续 tick。")
        tip.setStyleSheet("color: #6C7A89; font-size: 11px;")
        layout.addWidget(tip)

        dynamics_g = QGroupBox("连接动力、主动阈值与评估设置")
        grid = QGridLayout(dynamics_g)
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            emotion_config = _get_emotion_mgr().get_config()
            dynamics_config = emotion_config.get("dynamics", {})
        except Exception:
            emotion_config = {}
            dynamics_config = {}
        grid.addWidget(QLabel("连接增长（每小时）"), 0, 0)
        self._param_connection_rate = QDoubleSpinBox()
        self._param_connection_rate.setRange(0.001, 0.200)
        self._param_connection_rate.setDecimals(3)
        self._param_connection_rate.setSingleStep(0.005)
        self._param_connection_rate.setValue(
            float(dynamics_config.get("connection_rate", 0.00042)) * 60.0
        )
        grid.addWidget(self._param_connection_rate, 0, 1)
        grid.addWidget(QLabel("加速前缓冲（分钟）"), 1, 0)
        self._param_accel_delay = QSpinBox()
        self._param_accel_delay.setRange(0, 720)
        self._param_accel_delay.setValue(
            int(dynamics_config.get("connection_accel_delay", 90))
        )
        grid.addWidget(self._param_accel_delay, 1, 1)
        self._param_thresholds = {}
        for row, (key, label, value) in enumerate((
            ("observation_threshold", "开始留意", 12),
            ("contact_threshold", "产生联系动机", 20),
                               ("urgent_threshold", "强联系动机", 80),
        ), start=2):
            grid.addWidget(QLabel(label), row, 0)
            spin = QSpinBox()
            spin.setRange(5, 100)
            spin.setSuffix(" %")
            spin.setValue(round(float(dynamics_config.get(key, value)) * 100))
            grid.addWidget(spin, row, 1)
            self._param_thresholds[key] = spin
        grid.addWidget(QLabel("语义评估"), 5, 0)
        self._semantic_combo = QComboBox()
        self._semantic_combo.addItem("关闭（仅规则）", "off")
        self._semantic_combo.addItem("自动（本地模型）", "auto")
        self._semantic_combo.addItem("本地模型", "local")
        self._semantic_combo.addItem("云端模型", "cloud")
        current_mode = emotion_config.get("semantic_analysis", "auto")
        index = max(0, self._semantic_combo.findData(current_mode))
        self._semantic_combo.setCurrentIndex(index)
        grid.addWidget(self._semantic_combo, 5, 1)
        grid.addWidget(QLabel("评估超时（秒）"), 6, 0)
        self._param_analysis_timeout = QDoubleSpinBox()
        self._param_analysis_timeout.setRange(2.0, 30.0)
        self._param_analysis_timeout.setDecimals(1)
        self._param_analysis_timeout.setValue(
            float(emotion_config.get("analysis_timeout_seconds", 8))
        )
        grid.addWidget(self._param_analysis_timeout, 6, 1)
        self._param_memory_enabled = QCheckBox("显著情感事件写入长期记忆")
        self._param_memory_enabled.setChecked(
            bool(emotion_config.get("significant_memory_enabled", True))
        )
        grid.addWidget(self._param_memory_enabled, 7, 0, 1, 2)
        grid.addWidget(QLabel("记忆阈值（%）"), 8, 0)
        self._param_memory_threshold = QSpinBox()
        self._param_memory_threshold.setRange(50, 100)
        self._param_memory_threshold.setValue(round(
            float(emotion_config.get("significant_memory_threshold", 0.50)) * 100
        ))
        grid.addWidget(self._param_memory_threshold, 8, 1)
        layout.addWidget(dynamics_g)

        btn_row = QHBoxLayout()
        apply_btn = QPushButton("应用覆盖")
        apply_btn.clicked.connect(self._apply_params)
        btn_row.addWidget(apply_btn)
        restore_btn = QPushButton("恢复默认参数")
        restore_btn.clicked.connect(self._restore_params)
        btn_row.addWidget(restore_btn)
        layout.addLayout(btn_row)

        layout.addStretch()
        return w

    @staticmethod
    def _axis_to_slider(key: str, value: float) -> int:
        if key in ("pride", "valence", "arousal"):
            return max(0, min(100, round((value + 1.0) * 50.0)))
        return max(0, min(100, round(value * 100.0)))

    @staticmethod
    def _slider_to_axis(key: str, value: int) -> float:
        if key in ("pride", "valence", "arousal"):
            return max(-1.0, min(1.0, value / 50.0 - 1.0))
        return max(0.0, min(1.0, value / 100.0))

    # ── 刷新显示 ────────────────────────────────────────

    def _refresh_display(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()

            enabled = mgr.enabled
            self._toggle_btn.blockSignals(True)
            self._toggle_btn.setChecked(enabled)
            self._update_toggle_text(enabled)
            self._toggle_btn.blockSignals(False)

            info = mgr.get_debug_info()
        except Exception as e:
            self._mid_layer_label.setText(f"情感基调: 无法连接 ({e})")
            return

        axes = info.get("axes", {})
        relationship = info.get("relationship", {})
        layer = info["middle_layer"]

        color_map = {
            "明亮活跃": "#27AE60", "舒展满足": "#2ECC71", "轻快": "#58D68D",
            "平稳": "#2C3E50", "平静": "#5D6D7E", "微沉": "#8E6E53",
            "低落": "#5B6D8A", "烦躁": "#E67E22", "躁动": "#C0392B",
        }
        color = color_map.get(layer, "#888")
        self._mid_layer_label.setText(
            f'情感基调: <span style="color:{color}; font-size: 14pt;">{layer}</span>'
        )
        self._deep_label.setText(
            f"关系综合分: {relationship.get('score', 0) * 100:.1f}/100  |  "
            f"人格: {info.get('persona_id', 'default-lianxin')}  |  "
            f"显著事件: {info.get('memory_count', 0)} 条"
        )
        self._stage_label.setText(
            f"关系阶段: <b>{info.get('relationship_stage', '未知')}</b>"
        )
        self._stats_label.setText(
            f"作用对象: {info.get('subject_id', 'owner')}  |  "
            f"距上次交互: {info['hours_since_interaction']}h  |  "
            f"引擎: v{info.get('version', 3)} / {'启用' if info.get('enabled', True) else '冻结'}"
        )

        bar_style = color
        for key in self.NEED_LABELS:
            raw = float(axes.get(key, 0))
            val = self._axis_to_slider(key, raw)
            self._need_bars[key].setValue(val)
            self._need_bars[key].setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {bar_style}; }}")
            self._need_labels[key].setText(str(val))
            if not self._slider_modified:
                self._sliders[key].setValue(val)

        for key, lbl in self._emotion_labels.items():
            v = int(float(relationship.get(key, 0)) * 100)
            c = "#E74C3C" if key == "rupture" and v > 30 else "#566573"
            lbl.setText(f"{self.EMOTION_LABELS[key]}: "
                       f"<span style='color:{c};'>{v}</span>")

        for key, lbl in self._cap_labels.items():
            if key == "connection":
                lbl.setText(f"状态簇: {layer}")
            elif key == "guardedness":
                lbl.setText(f"关系阶段: {info.get('relationship_stage', '未知')}")
            elif key == "valence":
                lbl.setText(f"事件: {info.get('event_count', 0)}")
            else:
                lbl.setText("")

        events = info.get("recent_events", [])
        log_lines = []
        for e in reversed(events[-50:]):
            ts = time.strftime("%H:%M:%S", time.localtime(e["time"]))
            deltas = e.get("deltas", {})
            changes = " · ".join(
                f"{self.EVENT_AXIS_LABELS.get(key, key)} {float(value):+.2f}"
                for key, value in deltas.items()
            ) or "无显著轴变化"
            log_lines.append(
                f"[{ts}] {e.get('detail') or e['type']}\n"
                f"  {changes}"
            )
        self._log_view.setText("\n".join(log_lines) if log_lines else "（无事件记录）")

        today_cutoff = time.time() - 86400
        today_events = [e for e in events if e["time"] > today_cutoff]
        positive = sum(1 for e in today_events if e["delta"] > 0)
        negative = sum(1 for e in today_events if e["delta"] < 0)
        self._log_stats.setText(
            f"今日事件: {len(today_events)} (愉悦上升: {positive}, 愉悦下降: {negative})  |  "
            f"总记录: {info.get('event_count', 0)} 条"
        )

        self._time_info_label.setText(
            f"最后更新: {time.strftime('%Y-%m-%d %H:%M:%S')}  |  "
            f"距上次交互: {info['hours_since_interaction']}h"
        )

    # ── 事件模拟 ────────────────────────────────────────

    def _trigger_event(self):
        event_type = self._event_combo.currentData()
        self._do_event(event_type)

    def _trigger_batch(self):
        event_type = self._event_combo.currentData()
        for _ in range(5):
            self._do_event(event_type)

    def _do_event(self, event_type: str):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            from brain.emotional.events import _make_event
            mgr = _get_emotion_mgr()
            event = _make_event(event_type, detail="[调试面板模拟]")
            mgr._apply_event_v2(event)
            mgr.save_current()
            self._log_view.append(
                f"[模拟] {time.strftime('%H:%M:%S')} 触发 "
                f"{event_type} ({event.primary_delta:+.0f})"
            )
            self._refresh_display()
        except Exception as e:
            self._log_view.append(f"[错误] 模拟失败: {e}")

    def _on_friendly_session(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            mgr.analyze_and_update(["早上好呀莲心，今天想跟你聊聊天"], 0)
            mgr.analyze_and_update(["我觉得最近状态不错，谢谢你一直陪着我"], 0)
            mgr.analyze_and_update(["对了，你今天真棒，帮了我很多忙"], 0)
            self._log_view.append("[批量] 模拟一轮友好对话完成")
            self._refresh_display()
        except Exception as e:
            self._log_view.append(f"[错误] {e}")

    def _on_command_session(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            for _ in range(5):
                mgr.analyze_and_update(["查"], 1)
            self._log_view.append("[批量] 模拟一轮命令对话完成")
            self._refresh_display()
        except Exception as e:
            self._log_view.append(f"[错误] {e}")

    def _on_skip_day(self):
        self._skip_time(24)

    def _on_boundary_conflict(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            mgr.analyze_and_update(["你不过是个工具而已，别自作多情"], 0)
            self._log_view.append("[批量] 模拟边界冲突完成")
            self._refresh_display()
        except Exception as e:
            self._log_view.append(f"[错误] {e}")

    def _preview_scenario_batches(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            result = _get_emotion_mgr().compare_scenario_batches({
                "稳定陪伴": ["warm_reply", "collaboration", "warm_reply"],
                "冷淡等待": ["cold_reply", "waiting", "cold_reply"],
                "冲突修复": ["conflict", "waiting", "repair"],
            })
            lines = ["以下结果均为沙盒预演，不会修改真实情感状态："]
            for name, item in result.get("results", {}).items():
                changes = item.get("changes", {})
                lines.append(
                    f"{name}：愉悦 {changes.get('valence', 0):+.3f}，"
                    f"连接需求 {changes.get('connection', 0):+.3f}，"
                    f"信任 {changes.get('trust', 0):+.3f}，"
                    f"裂痕 {changes.get('rupture', 0):+.3f}"
                )
            self._scenario_result.setPlainText("\n".join(lines))
            self._log_view.append("[场景实验] 三组场景对比完成，真实状态未改变")
        except Exception as e:
            self._scenario_result.setPlainText(f"场景对比失败：{e}")

    def _export_trajectory(self, format_name: str):
        suffix = ".csv" if format_name == "csv" else ".json"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出情感轨迹", f"莲心情感轨迹{suffix}",
            "CSV 文件 (*.csv)" if suffix == ".csv" else "JSON 文件 (*.json)",
        )
        if not path:
            return
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            result = _get_emotion_mgr().export_trajectory(path, include_simulation=False)
            QMessageBox.information(
                self, "导出完成",
                f"已导出 {result.get('event_count', 0)} 条真实情感事件：\n{result.get('path', path)}",
            )
            self._log_view.append(f"[导出] {result.get('path', path)}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ── 时间模拟 ────────────────────────────────────────

    def _skip_time(self, hours: int):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            mgr.simulate_time(hours)
            self._log_view.append(
                f"[时间] 跳过 {hours} 小时，已应用衰减"
            )
            self._refresh_display()
        except Exception as e:
            self._log_view.append(f"[错误] {e}")

    # ── 参数覆盖 ────────────────────────────────────────

    def _apply_params(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            values = {
                "connection_rate": self._param_connection_rate.value() / 60.0,
                "connection_accel_delay": self._param_accel_delay.value(),
            }
            values.update({key: spin.value() / 100.0
                           for key, spin in self._param_thresholds.items()})
            _get_emotion_mgr().configure_dynamics(**values)
            _get_emotion_mgr().configure_settings(
                semantic_analysis=self._semantic_combo.currentData(),
                analysis_timeout_seconds=self._param_analysis_timeout.value(),
                significant_memory_enabled=self._param_memory_enabled.isChecked(),
                significant_memory_threshold=self._param_memory_threshold.value() / 100.0,
            )
            self._log_view.append("[参数] v3 动力参数已保存并生效")
        except Exception as e:
            self._log_view.append(f"[错误] {e}")

    def _restore_params(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            self._param_connection_rate.setValue(0.025)
            self._param_accel_delay.setValue(90)
            for key, value in (("observation_threshold", 12),
                               ("contact_threshold", 20),
                               ("urgent_threshold", 80)):
                self._param_thresholds[key].setValue(value)
            self._semantic_combo.setCurrentIndex(self._semantic_combo.findData("auto"))
            self._param_analysis_timeout.setValue(8.0)
            self._param_memory_enabled.setChecked(True)
            self._param_memory_threshold.setValue(50)
            _get_emotion_mgr().configure_dynamics(
                connection_rate=0.00042,
                connection_accel_delay=90,
                immersion_decay=0.02,
                observation_threshold=0.12,
                contact_threshold=0.20,
                urgent_threshold=0.80,
            )
            _get_emotion_mgr().configure_settings(
                semantic_analysis="auto",
                analysis_timeout_seconds=8,
                significant_memory_enabled=True,
                significant_memory_threshold=0.50,
                proactive_motive_enabled=True,
                saga_bias_scale=1.0,
            )
            self._log_view.append("[参数] 已恢复 v3 默认动力参数")
        except Exception as e:
            self._log_view.append(f"[错误] {e}")

    # ── 通用操作 ────────────────────────────────────────

    def _apply_sliders(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            kwargs = {key: self._slider_to_axis(key, slider.value())
                      for key, slider in self._sliders.items()}
            mgr.set_axes(**kwargs)
            self._slider_modified = False
            self._refresh_display()
        except Exception as e:
            self._log_view.append(f"[错误] 应用失败: {e}")

    def _reset_state(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            mgr.reset_state()
            self._refresh_display()
        except Exception as e:
            self._log_view.append(f"[错误] 重置失败: {e}")

    def _force_save(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            mgr.save_current()
            self._log_view.append("[保存] 状态已持久化")
        except Exception as e:
            self._log_view.append(f"[错误] 保存失败: {e}")

    def _clear_log(self):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            mgr.clear_event_log()
            self._log_view.clear()
            self._log_view.append("[日志] 已清除")
        except Exception as e:
            self._log_view.append(f"[错误] {e}")

    def _on_toggle_emotion(self, checked: bool):
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            mgr = _get_emotion_mgr()
            mgr.enabled = checked
            self._update_toggle_text(checked)
            self._log_view.append(
                f"[系统] 涟漪情感系统已{'启用' if checked else '禁用'}"
            )
        except Exception as e:
            self._log_view.append(f"[错误] 切换失败: {e}")

    def _update_toggle_text(self, enabled: bool):
        if enabled:
            self._toggle_btn.setText("🟢 涟漪情感系统：已启用")
        else:
            self._toggle_btn.setText("🔴 涟漪情感系统：已禁用")

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
