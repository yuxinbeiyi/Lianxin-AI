# -*- coding: utf-8 -*-
import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QScrollArea, QFrame, QComboBox, QLineEdit, QCheckBox, QDialogButtonBox,
    QWidget, QFileDialog, QTabWidget, QMessageBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from utils.settings import get_settings
from config import get_tts_config, save_tts_config
from .settings_dialog import SettingsDialog


# 高级参数默认值
_ADVANCED_DEFAULTS = {
    "temperature": 0.3,
    "top_k": 5,
    "top_p": 0.9,
    "sample_steps": 32,
    "how_to_cut": "不切",
    "pause_second": 0.3,
}


class SoundSettingsDialog(QDialog):
    """声音设置独立对话框（包含音量、音效、TTS引擎、语音合成参数）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = get_settings()
        self._tts_cfg = get_tts_config()

        self.setWindowTitle("🔊 声音设置")
        self.setMinimumSize(520, 640)
        self.resize(540, 680)
        self.setWindowFlags(Qt.Window)

        self._adv_sliders = {}
        self._build_ui()
        self._load_from_settings()

    def _create_frame(self):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #1E1E30;
                border-radius: 8px;
                border: 1px solid #3D3D5A;
            }
        """)
        return frame

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 16, 20, 16)

        # 标题
        title = QLabel("🔊 声音设置")
        title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
        layout.addWidget(title)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #3D3D5A; max-height: 1px;")
        layout.addWidget(line)

        # ── 静默模式（全局开关）────────────
        silent_frame = self._create_frame()
        silent_vbox = QHBoxLayout(silent_frame)
        self._silent_cb = QCheckBox("开启静默模式（所有消息只显示气泡，不语音朗读）")
        self._silent_cb.setFont(QFont("Microsoft YaHei UI", 9))
        self._silent_cb.setCursor(Qt.PointingHandCursor)
        silent_vbox.addWidget(self._silent_cb)
        layout.addWidget(silent_frame)

        # ── 选项卡：基本设置 / 高级参数 ──
        self._tab_widget = QTabWidget()
        self._tab_widget.setStyleSheet("""
            QTabWidget::pane { border: none; background: transparent; }
            QTabBar::tab { padding: 8px 20px; font-size: 13px; }
        """)
        layout.addWidget(self._tab_widget)

        # 选项卡 1：基本设置
        self._build_basic_tab()
        # 选项卡 2：高级参数
        self._build_advanced_tab()

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedSize(80, 32)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("保存")
        btn_save.setFixedSize(80, 32)
        btn_save.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    def _build_basic_tab(self):
        """构建「基本设置」选项卡。"""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(14)
        scroll_layout.setContentsMargins(0, 4, 0, 0)

        # TTS 音量
        tts_frame = self._create_frame()
        tts_vbox = QVBoxLayout(tts_frame)
        tts_vbox.addWidget(QLabel("🗣️ 莲心语音音量"))
        self.tts_slider = QSlider(Qt.Horizontal)
        self.tts_slider.setRange(0, 100)
        self.tts_slider.setValue(int(self._settings.tts_volume * 100))
        self.tts_slider.valueChanged.connect(self._on_tts_volume_changed)
        tts_vbox.addWidget(self.tts_slider)
        scroll_layout.addWidget(tts_frame)

        # 音效音量
        sfx_frame = self._create_frame()
        sfx_vbox = QVBoxLayout(sfx_frame)
        sfx_vbox.addWidget(QLabel("🔊 按键/反馈音效音量"))
        self.sfx_slider = QSlider(Qt.Horizontal)
        self.sfx_slider.setRange(0, 100)
        self.sfx_slider.setValue(int(self._settings.sfx_volume * 100))
        self.sfx_slider.valueChanged.connect(self._on_sfx_volume_changed)
        sfx_vbox.addWidget(self.sfx_slider)
        scroll_layout.addWidget(sfx_frame)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background-color: #3D3D5A; max-height: 1px;")
        scroll_layout.addWidget(sep)

        # 引擎选择
        engine_frame = self._create_frame()
        engine_vbox = QVBoxLayout(engine_frame)
        engine_vbox.setSpacing(6)
        engine_title = QLabel("TTS 引擎")
        engine_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        engine_vbox.addWidget(engine_title)

        self._tts_engine_combo = QComboBox()
        self._tts_engine_combo.addItems([
            "auto — 优先 GPT-SoVITS（不可用则回退 Edge-TTS）",
            "edge_tts — 仅使用 Edge-TTS（云端标准发音）",
        ])
        engine_vbox.addWidget(self._tts_engine_combo)
        engine_desc = QLabel(
            "GPT-SoVits 需要安装并配置路径后方可使用。\n"
            "Edge-TTS 无需安装，配置后即可使用。"
        )
        engine_desc.setWordWrap(True)
        engine_desc.setStyleSheet("font-size: 12px; padding: 4px 0;")
        engine_vbox.addWidget(engine_desc)
        scroll_layout.addWidget(engine_frame)

        # GPT-SoVits 路径
        gs_frame = self._create_frame()
        gs_vbox = QVBoxLayout(gs_frame)
        gs_vbox.setSpacing(6)
        gs_title = QLabel("GPT-SoVITS 安装路径")
        gs_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        gs_vbox.addWidget(gs_title)

        gs_row = QHBoxLayout()
        self._tts_gs_path_edit = QLineEdit()
        self._tts_gs_path_edit.setPlaceholderText("例如: C:\\GPT-SoVITS-v2pro")
        gs_row.addWidget(self._tts_gs_path_edit)
        gs_browse_btn = QPushButton("浏览…")
        gs_browse_btn.setFixedWidth(80)
        gs_browse_btn.clicked.connect(self._browse_gs_path)
        gs_row.addWidget(gs_browse_btn)
        gs_vbox.addLayout(gs_row)

        version_row = QHBoxLayout()
        version_label = QLabel("模型版本")
        version_label.setStyleSheet("font-size: 12px;")
        version_row.addWidget(version_label)
        self._tts_version_combo = QComboBox()
        self._tts_version_combo.addItem("v2Pro（默认，声音稳定）", "v2Pro")
        self._tts_version_combo.addItem("v3（情感更丰富，24kHz）", "v3")
        self._tts_version_combo.addItem("v4（v3 改进版，48kHz 防闷）", "v4")
        version_row.addWidget(self._tts_version_combo, 1)
        gs_vbox.addLayout(version_row)

        version_desc = QLabel(
            "切换版本后会自动重启 GPT-SoVITS 进程重新加载模型。\n"
            "v3/v4 需要参考音频文本，莲心会用 FunASR 自动转录参考音频内容；\n"
            "也可在 ref_wavs/config.json 中手动修改「text」字段覆盖。"
        )
        version_desc.setWordWrap(True)
        version_desc.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0;")
        gs_vbox.addWidget(version_desc)

        self._tts_gs_status = QLabel()
        self._tts_gs_status.setStyleSheet("font-size: 12px; padding: 2px 0;")
        gs_vbox.addWidget(self._tts_gs_status)
        scroll_layout.addWidget(gs_frame)

        # 参考音频来源
        ref_frame = self._create_frame()
        ref_vbox = QVBoxLayout(ref_frame)
        ref_vbox.setSpacing(6)
        ref_title = QLabel("🎤 参考音频来源")
        ref_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        ref_vbox.addWidget(ref_title)

        ref_row = QHBoxLayout()
        self._ref_wav_combo = QComboBox()
        self._ref_wav_combo.setMinimumWidth(280)
        self._refresh_ref_wav_list()
        ref_row.addWidget(self._ref_wav_combo, 1)

        refresh_btn = QPushButton("🔄 刷新列表")
        refresh_btn.setFixedWidth(100)
        refresh_btn.clicked.connect(self._refresh_ref_wav_list)
        ref_row.addWidget(refresh_btn)

        browse_btn = QPushButton("📁 浏览…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_ref_wav)
        ref_row.addWidget(browse_btn)
        ref_vbox.addLayout(ref_row)

        transcribe_row = QHBoxLayout()
        transcribe_btn = QPushButton("🎙 重新转录参考音频")
        transcribe_btn.setToolTip(
            "用 FunASR 重新转录所有参考音频的内容并写入 config.json。\n"
            "v3/v4 需要参考文本与音频内容一致，新增音频后建议点此转录。"
        )
        transcribe_btn.clicked.connect(self._on_re_transcribe_refs)
        transcribe_row.addWidget(transcribe_btn)
        self._transcribe_status = QLabel("")
        self._transcribe_status.setStyleSheet("color: #888; font-size: 12px;")
        transcribe_row.addWidget(self._transcribe_status, 1)
        ref_vbox.addLayout(transcribe_row)

        ref_desc = QLabel(
            "选择后莲心将固定使用该音色，不随情绪自动切换。\n"
            "选择「自动匹配」则根据每句话的情绪关键词自动选择参考音频。\n"
            "将 WAV 文件放入 skills/语音合成/ref_wavs/ 目录后点击刷新。"
        )
        ref_desc.setWordWrap(True)
        ref_desc.setStyleSheet("color: #888; font-size: 12px; padding: 4px 0;")
        ref_vbox.addWidget(ref_desc)
        scroll_layout.addWidget(ref_frame)

        # 默认情绪
        mood_frame = self._create_frame()
        mood_vbox = QVBoxLayout(mood_frame)
        mood_vbox.setSpacing(6)
        mood_title = QLabel("默认语音情绪")
        mood_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        mood_vbox.addWidget(mood_title)

        self._tts_mood_combo = QComboBox()
        mood_items = [
            ("auto", "自动匹配（根据文本内容自动选择）"),
            ("casual", "日常温柔"),
            ("tsundere", "傲娇"),
            ("romantic", "深情"),
            ("long", "长句稳定"),
        ]
        for val, label in mood_items:
            self._tts_mood_combo.addItem(label, val)
        mood_vbox.addWidget(self._tts_mood_combo)
        scroll_layout.addWidget(mood_frame)

        # 语速
        speed_frame = self._create_frame()
        speed_vbox = QVBoxLayout(speed_frame)
        speed_vbox.setSpacing(6)
        speed_title = QLabel("语速（仅 GPT-SoVITS 生效）")
        speed_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        speed_vbox.addWidget(speed_title)

        speed_row = QHBoxLayout()
        self._tts_speed_slider = QSlider(Qt.Horizontal)
        self._tts_speed_slider.setRange(50, 200)
        self._tts_speed_value = QLabel("1.0x")
        self._tts_speed_value.setFixedWidth(45)
        self._tts_speed_slider.valueChanged.connect(self._on_tts_speed_changed)
        speed_row.addWidget(self._tts_speed_slider)
        speed_row.addWidget(self._tts_speed_value)
        speed_vbox.addLayout(speed_row)
        scroll_layout.addWidget(speed_frame)

        # 启动预热选项
        warmup_frame = self._create_frame()
        warmup_vbox = QVBoxLayout(warmup_frame)
        warmup_vbox.setSpacing(6)
        self._tts_warmup_cb = QCheckBox("启动时预热语音引擎")
        self._tts_warmup_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        warmup_vbox.addWidget(self._tts_warmup_cb)
        warmup_desc = QLabel(
            "开启后，莲心启动时会在后台自动加载 GPT-SoVITS 模型。\n"
            "可大幅缩短首次语音回复的等待时间（约 5-15 秒）。\n"
            "仅在 GPT-SoVITS 可用时生效，关闭可节省 GPU 显存。"
        )
        warmup_desc.setWordWrap(True)
        warmup_desc.setStyleSheet("color: #888; font-size: 12px; padding: 4px 0;")
        warmup_vbox.addWidget(warmup_desc)
        scroll_layout.addWidget(warmup_frame)

        resource_frame = self._create_frame()
        resource_vbox = QVBoxLayout(resource_frame)
        resource_vbox.setSpacing(6)
        resource_vbox.addWidget(QLabel("GPT-SoVITS 资源释放"))
        self._tts_idle_combo = QComboBox()
        for seconds, label in (
            (60, "空闲 1 分钟后释放"),
            (300, "空闲 5 分钟后释放（推荐）"),
            (900, "空闲 15 分钟后释放"),
            (0, "保持运行，不自动释放"),
        ):
            self._tts_idle_combo.addItem(label, seconds)
        resource_vbox.addWidget(self._tts_idle_combo)
        resource_desc = QLabel(
            "释放后会回收 GPT-SoVITS 独立进程及其显存；下次使用会重新加载，Edge-TTS 不受影响。"
        )
        resource_desc.setWordWrap(True)
        resource_desc.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0;")
        resource_vbox.addWidget(resource_desc)
        release_btn = QPushButton("立即释放 GPT-SoVITS GPU")
        release_btn.clicked.connect(self._release_gpt_worker)
        resource_vbox.addWidget(release_btn)
        scroll_layout.addWidget(resource_frame)

        # 试听按钮
        test_frame = self._create_frame()
        test_hbox = QHBoxLayout(test_frame)
        test_hbox.addWidget(QLabel("测试语音合成："))
        self._tts_test_btn = QPushButton("🔊 试听")
        self._tts_test_btn.setFixedWidth(120)
        self._tts_test_btn.clicked.connect(self._on_tts_test)
        test_hbox.addWidget(self._tts_test_btn)
        self._tts_test_status = QLabel("")
        self._tts_test_status.setStyleSheet("color: #888; font-size: 12px;")
        test_hbox.addWidget(self._tts_test_status)
        test_hbox.addStretch()
        scroll_layout.addWidget(test_frame)

        # 提示信息
        tts_tip = QLabel(
            "💡 GPT-SoVITS 支持声音克隆和情绪表达。\n"
            "· 在 skills/语音合成/ref_wavs/ 下放置参考音频即可激活声音克隆\n"
            "· 未配置时自动使用 Edge-TTS 标准发音，语音功能不受影响\n"
            "· 参考音频格式：WAV 文件，5-15 秒，24000Hz 采样率"
        )
        tts_tip.setWordWrap(True)
        tts_tip.setStyleSheet("color: #888; font-size: 12px; padding: 8px;")
        scroll_layout.addWidget(tts_tip)

        scroll_layout.addStretch()
        scroll_area.setWidget(scroll_content)
        self._tab_widget.addTab(scroll_area, "  基本设置  ")

    # ── 高级参数选项卡 ─────────────────────────────────────

    def _build_advanced_tab(self):
        """构建「高级参数」选项卡。"""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        adv_layout = QVBoxLayout(scroll_content)
        adv_layout.setSpacing(14)
        adv_layout.setContentsMargins(0, 4, 0, 0)

        # 说明
        tip = QLabel(
            "以下参数影响 GPT-SoVITS 的合成行为。\n"
            "修改后点「试听」可立即感受效果，满意后点「保存」。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888; font-size: 12px; padding: 8px;")
        adv_layout.addWidget(tip)

        # ── temperature ──
        adv_layout.addWidget(self._make_slider_frame(
            "稳定性 (temperature)",
            self._make_adv_slider(10, 100, 30, "%.2f"),  # 0.1 - 1.0, default 0.3
            "低 → 更稳定，每次合成结果一致",
            "高 → 更多随机变化"
        ))

        # ── top_k ──
        adv_layout.addWidget(self._make_slider_frame(
            "采样范围 (top_k)",
            self._make_adv_slider(1, 20, 5, "%d"),
            "小 → 只选最可能的候选，声音保守",
            "大 → 候选范围更广，声音丰富"
        ))

        # ── top_p ──
        adv_layout.addWidget(self._make_slider_frame(
            "核采样 (top_p)",
            self._make_adv_slider(10, 100, 90, "%.2f"),  # 0.1 - 1.0
            "小 → 更保守稳定",
            "大 → 更丰富多变"
        ))

        # ── sample_steps ──
        adv_layout.addWidget(self._make_slider_frame(
            "合成步数 (sample_steps)",
            self._make_adv_slider(4, 64, 32, "%d"),
            "小 → 合成快，音质稍低",
            "大 → 合成慢，音质更好"
        ))

        # ── pause_second ──
        adv_layout.addWidget(self._make_slider_frame(
            "句间停顿 (pause_second)",
            self._make_adv_slider(0, 100, 30, "%.1fs"),  # 0.0 - 1.0s
            "短 → 句子紧凑",
            "长 → 句子间有明显停顿"
        ))

        # ── how_to_cut ──
        cut_frame = self._create_frame()
        cut_vbox = QVBoxLayout(cut_frame)
        cut_vbox.setSpacing(6)
        cut_title = QLabel("切句方式 (how_to_cut)")
        cut_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        cut_vbox.addWidget(cut_title)

        self._adv_cut_combo = QComboBox()
        cut_items = [
            ("不切", "不切（整段合成，长文本可能截断）"),
            ("凑四句一切", "凑四句一切"),
            ("凑50字一切", "凑50字一切"),
            ("按中文句号。切", "按中文句号。切"),
            ("按英文句号.切", "按英文句号.切"),
            ("按标点符号切", "按标点符号切"),
        ]
        for val, label in cut_items:
            self._adv_cut_combo.addItem(label, val)
        cut_vbox.addWidget(self._adv_cut_combo)
        adv_layout.addWidget(cut_frame)

        adv_layout.addStretch()

        # ── 恢复默认按钮 ──
        reset_frame = self._create_frame()
        reset_vbox = QVBoxLayout(reset_frame)
        reset_vbox.setSpacing(6)
        reset_title = QLabel("恢复默认")
        reset_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        reset_vbox.addWidget(reset_title)

        reset_btn = QPushButton("🔄 恢复高级参数默认值")
        reset_btn.setFixedHeight(36)
        reset_btn.clicked.connect(self._on_reset_advanced)
        reset_vbox.addWidget(reset_btn)

        reset_desc = QLabel("将所有高级参数重置为推荐的默认值。")
        reset_desc.setWordWrap(True)
        reset_desc.setStyleSheet("color: #888; font-size: 12px; padding: 4px 0;")
        reset_vbox.addWidget(reset_desc)
        adv_layout.addWidget(reset_frame)

        scroll_area.setWidget(scroll_content)
        self._tab_widget.addTab(scroll_area, "  高级参数  ")

    def _make_adv_slider(self, min_val, max_val, default, fmt):
        """创建高级参数滑块，返回 (slider, value_label)。"""
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(default)
        value_label = QLabel()
        value_label.setFixedWidth(50)
        slider.valueChanged.connect(
            lambda v, lbl=value_label, f=fmt: lbl.setText(f % (v / 100.0 if "%." in f else v))
        )
        return slider, value_label

    def _make_slider_frame(self, title, slider_pair, left_desc, right_desc):
        """创建带标题、滑块和描述的 Frame。"""
        frame = self._create_frame()
        vbox = QVBoxLayout(frame)
        vbox.setSpacing(6)
        title_label = QLabel(title)
        title_label.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        vbox.addWidget(title_label)

        slider, value_label = slider_pair
        self._adv_sliders[title] = (slider, value_label)

        row = QHBoxLayout()
        row.addWidget(slider)
        row.addWidget(value_label)
        vbox.addLayout(row)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel(left_desc))
        desc_row.addStretch()
        desc_row.addWidget(QLabel(right_desc))
        for lbl in (desc_row.itemAt(0).widget(), desc_row.itemAt(2).widget()):
            lbl.setStyleSheet("color: #888; font-size: 12px;")
        vbox.addLayout(desc_row)

        return frame

    def _on_reset_advanced(self):
        """重置高级参数为默认值。"""
        confirm = QMessageBox.question(
            self, "恢复默认",
            "确定要将所有高级参数恢复为默认值吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        self._adv_sliders["稳定性 (temperature)"][0].setValue(int(_ADVANCED_DEFAULTS["temperature"] * 100))
        self._adv_sliders["稳定性 (temperature)"][1].setText("%.2f" % _ADVANCED_DEFAULTS["temperature"])

        self._adv_sliders["采样范围 (top_k)"][0].setValue(_ADVANCED_DEFAULTS["top_k"])
        self._adv_sliders["采样范围 (top_k)"][1].setText("%d" % _ADVANCED_DEFAULTS["top_k"])

        self._adv_sliders["核采样 (top_p)"][0].setValue(int(_ADVANCED_DEFAULTS["top_p"] * 100))
        self._adv_sliders["核采样 (top_p)"][1].setText("%.2f" % _ADVANCED_DEFAULTS["top_p"])

        self._adv_sliders["合成步数 (sample_steps)"][0].setValue(_ADVANCED_DEFAULTS["sample_steps"])
        self._adv_sliders["合成步数 (sample_steps)"][1].setText("%d" % _ADVANCED_DEFAULTS["sample_steps"])

        self._adv_sliders["句间停顿 (pause_second)"][0].setValue(int(_ADVANCED_DEFAULTS["pause_second"] * 100))
        self._adv_sliders["句间停顿 (pause_second)"][1].setText("%.1fs" % _ADVANCED_DEFAULTS["pause_second"])

        for i in range(self._adv_cut_combo.count()):
            if self._adv_cut_combo.itemData(i) == _ADVANCED_DEFAULTS["how_to_cut"]:
                self._adv_cut_combo.setCurrentIndex(i)
                break

    def _get_advanced_params(self):
        """从 UI 收集当前高级参数值。"""
        return {
            "temperature": self._adv_sliders["稳定性 (temperature)"][0].value() / 100.0,
            "top_k": self._adv_sliders["采样范围 (top_k)"][0].value(),
            "top_p": self._adv_sliders["核采样 (top_p)"][0].value() / 100.0,
            "sample_steps": self._adv_sliders["合成步数 (sample_steps)"][0].value(),
            "how_to_cut": self._adv_cut_combo.currentData(),            
            "pause_second": self._adv_sliders["句间停顿 (pause_second)"][0].value() / 100.0,
        }

    # ── 原有方法（从旧代码迁移，无改动）────────────────────

    def _load_from_settings(self):
        idx = 0 if self._tts_cfg.get("engine", "auto") != "edge_tts" else 1
        self._tts_engine_combo.setCurrentIndex(idx)
        self._tts_gs_path_edit.setText(self._tts_cfg.get("gpt_sovits_path", ""))

        version = self._tts_cfg.get("gpt_sovits_version", "v2Pro") or "v2Pro"
        version_idx = self._tts_version_combo.findData(version)
        self._tts_version_combo.setCurrentIndex(max(0, version_idx))

        def_mood = self._tts_cfg.get("default_mood", "auto")
        for i in range(self._tts_mood_combo.count()):
            if self._tts_mood_combo.itemData(i) == def_mood:
                self._tts_mood_combo.setCurrentIndex(i)
                break

        speed = int(self._tts_cfg.get("speed", 1.0) * 100)
        self._tts_speed_slider.setValue(speed)
        self._tts_speed_value.setText(f"{speed / 100:.1f}x")
        self._tts_warmup_cb.setChecked(self._tts_cfg.get("tts_warmup", True))
        idle_timeout = int(self._tts_cfg.get("gpt_sovits_idle_timeout_seconds", 300) or 0)
        idle_index = self._tts_idle_combo.findData(idle_timeout)
        self._tts_idle_combo.setCurrentIndex(max(0, idle_index))
        self._update_gs_status()

        override = self._tts_cfg.get("ref_wav_override", "")
        self._refresh_ref_wav_list(select_path=override)

        self._silent_cb.setChecked(self._settings.silent_mode)

        # 加载高级参数
        temp = self._tts_cfg.get("temperature", _ADVANCED_DEFAULTS["temperature"])
        self._adv_sliders["稳定性 (temperature)"][0].setValue(int(temp * 100))
        self._adv_sliders["稳定性 (temperature)"][1].setText("%.2f" % temp)

        topk = self._tts_cfg.get("top_k", _ADVANCED_DEFAULTS["top_k"])
        self._adv_sliders["采样范围 (top_k)"][0].setValue(topk)
        self._adv_sliders["采样范围 (top_k)"][1].setText("%d" % topk)

        topp = self._tts_cfg.get("top_p", _ADVANCED_DEFAULTS["top_p"])
        self._adv_sliders["核采样 (top_p)"][0].setValue(int(topp * 100))
        self._adv_sliders["核采样 (top_p)"][1].setText("%.2f" % topp)

        steps = self._tts_cfg.get("sample_steps", _ADVANCED_DEFAULTS["sample_steps"])
        self._adv_sliders["合成步数 (sample_steps)"][0].setValue(steps)
        self._adv_sliders["合成步数 (sample_steps)"][1].setText("%d" % steps)

        pause = self._tts_cfg.get("pause_second", _ADVANCED_DEFAULTS["pause_second"])
        self._adv_sliders["句间停顿 (pause_second)"][0].setValue(int(pause * 100))
        self._adv_sliders["句间停顿 (pause_second)"][1].setText("%.1fs" % pause)

        cut = self._tts_cfg.get("how_to_cut", _ADVANCED_DEFAULTS["how_to_cut"])
        for i in range(self._adv_cut_combo.count()):
            if self._adv_cut_combo.itemData(i) == cut:
                self._adv_cut_combo.setCurrentIndex(i)
                break

    def _browse_gs_path(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择 GPT-SoVITS 安装目录", "")
        if dir_path:
            self._tts_gs_path_edit.setText(dir_path)
            self._update_gs_status()

    def _release_gpt_worker(self):
        try:
            from brain.tts_engine import release_gpt_sovits_worker
            release_gpt_sovits_worker()
            self._tts_gs_status.setText("GPT-SoVITS worker 已释放，下次使用时按需加载")
        except Exception as exc:
            self._tts_gs_status.setText(f"释放 GPT-SoVITS 失败：{exc}")

    def _update_gs_status(self):
        path = self._tts_gs_path_edit.text().strip()
        if not path:
            self._tts_gs_status.setText("未配置 GPT-SoVITS 路径，将使用 Edge-TTS 标准发音")
            self._tts_gs_status.setStyleSheet("color: #888; font-size: 12px;")
            return
        if not os.path.isdir(path):
            self._tts_gs_status.setText("路径不存在")
            self._tts_gs_status.setStyleSheet("color: #E74C3C; font-size: 12px;")
            return
        inference_dir = os.path.join(path, "GPT_SoVITS")
        if os.path.isdir(inference_dir):
            self._tts_gs_status.setText("GPT-SoVITS 目录已识别 ✓（具体可用性取决于 GPU 和模型配置）")
            self._tts_gs_status.setStyleSheet("color: #27AE60; font-size: 12px;")
        else:
            self._tts_gs_status.setText("已选择目录但未检测到 GPT_SoVITS 模块，请确认路径正确")
            self._tts_gs_status.setStyleSheet("color: #F39C12; font-size: 12px;")

    def _refresh_ref_wav_list(self, select_path: str = ""):
        self._ref_wav_combo.clear()
        self._ref_wav_combo.addItem("自动匹配（根据情绪选择）", "")

        ref_dirs = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills", "语音合成", "ref_wavs"),
            os.path.join(str(__import__("pathlib").Path.home()), ".lianxin", "tts", "ref_wavs"),
        ]
        found_wavs = []
        for ref_dir in ref_dirs:
            if not os.path.isdir(ref_dir):
                continue
            for root, dirs, files in os.walk(ref_dir):
                for fname in sorted(files):
                    if fname.lower().endswith(".wav"):
                        full = os.path.join(root, fname)
                        mood_dir = os.path.basename(os.path.dirname(full))
                        label = f"{fname}"
                        if mood_dir in ("casual", "tsundere", "romantic", "long", "angry"):
                            label = f"[{mood_dir}] {fname}"
                        found_wavs.append((label, full))

        target_idx = 0
        for i, (label, path) in enumerate(found_wavs, 1):
            self._ref_wav_combo.addItem(label, path)
            if select_path and os.path.normpath(path) == os.path.normpath(select_path):
                target_idx = i
        self._ref_wav_combo.setCurrentIndex(target_idx)

    def _browse_ref_wav(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择参考音频", "", "WAV 音频文件 (*.wav)")
        if not path:
            return
        fname = os.path.basename(path)
        self._ref_wav_combo.insertItem(1, f"📁 {fname}", path)
        self._ref_wav_combo.setCurrentIndex(1)

    def _on_re_transcribe_refs(self):
        """用 FunASR 重新转录所有参考音频（force=True），后台执行避免卡 UI。"""
        from brain.ref_transcriber import get_ref_transcripts
        from brain.tts_engine import _get_ref_wavs_dir, reset_gpt_sovits_cache

        ref_dir = _get_ref_wavs_dir()
        if not ref_dir:
            self._transcribe_status.setText("未找到参考音频目录")
            return

        self._transcribe_status.setText("正在转录（首次需加载 FunASR 模型，请稍候）…")
        self._ref_wav_combo.setEnabled(False)

        def _work():
            try:
                cache = get_ref_transcripts(ref_dir, force=True)
                done = sum(1 for v in cache.values() if (v.get("text") or "").strip())
                total = len(cache)
                # 让 tts_engine 的 ref 缓存失效，下次合成读新转录
                reset_gpt_sovits_cache()
                self._transcribe_status.setText(
                    f"转录完成：{done}/{total} 个参考音频已配置文本"
                    if total else "未找到参考音频"
                )
            except Exception as e:
                self._transcribe_status.setText(f"转录失败：{e}")
            finally:
                self._ref_wav_combo.setEnabled(True)

        import threading
        threading.Thread(target=_work, daemon=True).start()

    def _on_tts_speed_changed(self, value: int):
        speed = value / 100.0
        self._tts_speed_value.setText(f"{speed:.1f}x")

    def _on_tts_volume_changed(self, value):
        vol = value / 100.0
        get_settings().tts_volume = vol

    def _on_sfx_volume_changed(self, value):
        vol = value / 100.0
        get_settings().sfx_volume = vol

    def _on_tts_test(self):
        test_text = "你好，我是莲心。很高兴见到你。"
        self._tts_test_btn.setEnabled(False)
        self._tts_test_btn.setText("合成中…")

        engine_idx = self._tts_engine_combo.currentIndex()
        engine = "auto" if engine_idx == 0 else "edge_tts"
        gs_path = self._tts_gs_path_edit.text().strip()
        mood = self._tts_mood_combo.currentData()
        speed_val = self._tts_speed_slider.value() / 100.0
        ref_wav_override = ""
        if self._ref_wav_combo.currentData():
            ref_wav_override = self._ref_wav_combo.currentData()

        adv = self._get_advanced_params()

        save_tts_config({
            "engine": engine,
            "gpt_sovits_path": gs_path,
            "gpt_sovits_version": self._tts_version_combo.currentData(),
            "default_mood": mood,
            "speed": speed_val,
            "temperature": adv["temperature"],
            "top_k": adv["top_k"],
            "top_p": adv["top_p"],
            "sample_steps": adv["sample_steps"],
            "how_to_cut": adv["how_to_cut"],
            "pause_second": adv["pause_second"],
            "edge_tts_voice": self._tts_cfg.get("edge_tts_voice", "zh-CN-XiaoxiaoNeural"),
            "tts_warmup": self._tts_cfg.get("tts_warmup", True),
            "gpt_sovits_idle_timeout_seconds": self._tts_idle_combo.currentData(),
            "gpt_sovits_min_free_vram_mb": self._tts_cfg.get("gpt_sovits_min_free_vram_mb", 2048),
            "ref_wav_override": ref_wav_override,
        })
        from brain.tts_engine import reset_gpt_sovits_cache
        reset_gpt_sovits_cache()

        def _test():
            import tempfile, os, threading
            from brain.tts_engine import TtsEngine
            engine = TtsEngine()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            wav_path = tmp.name
            tmp.close()
            failed = False
            try:
                success = engine.synthesize(test_text, wav_path)
                if not success:
                    failed = True
                    return
                import pygame
                if not pygame.mixer.get_init():
                    pygame.init()
                    pygame.mixer.init()
                sound = pygame.mixer.Sound(wav_path)
                channel = pygame.mixer.find_channel()
                if channel:
                    channel.play(sound)
                else:
                    sound.play()
                while pygame.mixer.get_busy():
                    pygame.time.wait(50)
            finally:
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass
                if failed:
                    self._tts_test_status.setText("❌ 合成失败：可能未找到 FFmpeg 或 TTS 异常，详见 logs/debug.log")
                else:
                    self._tts_test_status.setText("✅ 试听完成")
                self._tts_test_btn.setText("🔊 试听")
                self._tts_test_btn.setEnabled(True)

        import threading
        threading.Thread(target=_test, daemon=True).start()

    def _on_save(self):
        self._settings.silent_mode = self._silent_cb.isChecked()

        engine_idx = self._tts_engine_combo.currentIndex()
        engine = "auto" if engine_idx == 0 else "edge_tts"
        gs_path = self._tts_gs_path_edit.text().strip()
        mood = self._tts_mood_combo.currentData()
        speed = self._tts_speed_slider.value() / 100.0
        warmup = self._tts_warmup_cb.isChecked()
        ref_wav_override = ""
        if self._ref_wav_combo.currentData():
            ref_wav_override = self._ref_wav_combo.currentData()

        adv = self._get_advanced_params()

        save_tts_config({
            "engine": engine,
            "gpt_sovits_path": gs_path,
            "gpt_sovits_version": self._tts_version_combo.currentData(),
            "default_mood": mood,
            "speed": speed,
            "temperature": adv["temperature"],
            "top_k": adv["top_k"],
            "top_p": adv["top_p"],
            "sample_steps": adv["sample_steps"],
            "how_to_cut": adv["how_to_cut"],
            "pause_second": adv["pause_second"],
            "edge_tts_voice": self._tts_cfg.get("edge_tts_voice", "zh-CN-XiaoxiaoNeural"),
            "tts_warmup": warmup,
            "gpt_sovits_idle_timeout_seconds": self._tts_idle_combo.currentData(),
            "gpt_sovits_min_free_vram_mb": self._tts_cfg.get("gpt_sovits_min_free_vram_mb", 2048),
            "ref_wav_override": ref_wav_override,
        })
        from brain.tts_engine import reset_gpt_sovits_cache
        reset_gpt_sovits_cache()
        self.accept()
