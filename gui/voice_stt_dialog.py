"""
VoiceSTTDialog：语音转录设置独立弹窗
统一管理4种 STT 引擎的配置、选择和测试
- 可拉伸窗口
- 右侧滚动条优化长列表体验
"""

from PyQt5.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QComboBox, QCheckBox,
    QFrame, QMessageBox, QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

from config import (
    get_stt_engine_config, save_stt_engine_config,
    detect_best_stt_engine, migrate_legacy_stt_config,
)
from gui.styles.settings_theme import apply_settings_theme


class VoiceSTTDialog(QDialog):
    """语音转录设置可拉伸弹窗（非模态，不阻塞主界面）"""
    
    config_saved = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.Window |
            Qt.WindowCloseButtonHint |
            Qt.WindowMinMaxButtonsHint
        )
        
        self.setWindowTitle("🎙️ 语音转录设置")
        self.setMinimumSize(580, 620)
        self.resize(650, 720)  # 默认大小
        
        # 🔑 关键：设置为非模态窗口（不阻塞主界面）
        self.setWindowModality(Qt.NonModal)
        
        # 设置窗口样式
        self.setStyleSheet("""
            QDialog {
                background-color: #1E1E30;
                color: #E0E0E0;
            }
        """)
        
        self._original_config = None
        self._build_ui()
        apply_settings_theme(self)
        self._load_config()
        self._check_migration()
    
    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # ── 顶部标题栏 ──
        title_bar = QFrame()
        title_bar.setFixedHeight(56)
        title_bar.setStyleSheet("""
            QFrame {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2D2D45, stop:1 #3D3D5A
                );
                border-bottom: 1px solid #4A4A6A;
            }
            QLabel { 
                color: #FFFFFF; 
                background: transparent; 
            }
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 14px;
                padding: 4px 12px;
                color: #B0B0C8;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: white;
            }
        """)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(20, 0, 16, 0)
        
        title = QLabel("🎙️ 语音转录中心")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title_layout.addWidget(title)
        
        title_layout.addStretch()
        
        subtitle = QLabel("管理语音识别引擎配置")
        subtitle.setFont(QFont("Microsoft YaHei UI", 9))
        subtitle.setStyleSheet("color: #8888AA; background: transparent;")
        title_layout.addWidget(subtitle)
        
        main_layout.addWidget(title_bar)
        
        # ── 主内容区（带滚动条）──
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background-color: #1E1E30;
            }
            QScrollBar:vertical {
                background-color: #252538;
                width: 10px;
                border-radius: 5px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background-color: #5A5A7A;
                border-radius: 5px;
                min-height: 40px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #6A6A8A;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        
        # 滚动内容容器
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(16)
        
        # ── 全局设置卡片 ──
        global_card = self._create_card_frame()
        global_layout = QVBoxLayout(global_card)
        global_layout.setContentsMargins(16, 14, 16, 14)
        global_layout.setSpacing(12)
        
        # 第一行：默认引擎选择
        engine_row = QHBoxLayout()
        engine_label = QLabel("⚡ 默认引擎:")
        engine_label.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        engine_label.setFixedWidth(100)
        engine_row.addWidget(engine_label)
        
        self._engine_combo = QComboBox()
        self._engine_combo.setMinimumWidth(220)
        self._engine_combo.setFixedHeight(32)
        self._engine_combo.addItem("🤖 自动检测 (推荐)", "auto")
        self._engine_combo.addItem("🟢 FunASR 本地识别", "funasr")
        self._engine_combo.addItem("☁️ 火山引擎云端", "volcano")
        self._engine_combo.addItem("🔒 阿里云实时STT", "aliyun")
        self._engine_combo.addItem("🔧 Whisper 开源", "whisper")
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_row.addWidget(self._engine_combo)
        engine_row.addStretch()
        global_layout.addLayout(engine_row)
        
        # 第二行：自动降级选项
        self._fallback_cb = QCheckBox("启用自动降级（首选引擎失败时自动切换到下一个可用引擎）")
        self._fallback_cb.setChecked(True)
        self._fallback_cb.setFont(QFont("Microsoft YaHei UI", 9))
        global_layout.addWidget(self._fallback_cb)
        
        content_layout.addWidget(global_card)
        
        # ── 引擎选项卡区域 ──
        tabs_container = QFrame()
        tabs_container.setStyleSheet("""
            QFrame {
                background-color: #252538;
                border: 1px solid #3D3D5A;
                border-radius: 10px;
            }
            QTabWidget::pane {
                border: none;
                background-color: transparent;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #2D2D45;
                border: 1px solid #3D3D5A;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 10px 18px;
                margin-right: 4px;
                font-size: 9pt;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: #6C7BFF;
                color: white;
            }
            QTabBar::tab:hover:!selected {
                background-color: #3D3D55;
                color: white;
            }
        """)
        tabs_layout = QVBoxLayout(tabs_container)
        tabs_layout.setContentsMargins(12, 12, 12, 12)
        
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        
        from gui.voice_stt_tabs.tab_funasr import FunASRTab
        from gui.voice_stt_tabs.tab_volcano import VolcanoTab
        from gui.voice_stt_tabs.tab_aliyun import AliyunTab
        from gui.voice_stt_tabs.tab_whisper import WhisperTab
        
        self._funasr_tab = FunASRTab()
        self._volcano_tab = VolcanoTab()
        self._aliyun_tab = AliyunTab()
        self._whisper_tab = WhisperTab()
        
        self._tabs.addTab(self._funasr_tab, "🟢 FunASR")
        self._tabs.addTab(self._volcano_tab, "☁️ 火山引擎")
        self._tabs.addTab(self._aliyun_tab, "🔒 阿里云")
        self._tabs.addTab(self._whisper_tab, "🔧 Whisper")
        
        tabs_layout.addWidget(self._tabs)
        content_layout.addWidget(tabs_container, stretch=1)
        
        # ── 底部操作栏 ──
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet("""
            QFrame {
                background-color: #252538;
                border-top: 1px solid #3D3D5A;
            }
            QPushButton {
                border-radius: 6px;
                padding: 8px 24px;
                font-size: 9pt;
                font-weight: bold;
            }
        """)
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(20, 12, 20, 12)
        
        diagnose_btn = QPushButton("🔍 一键诊断")
        diagnose_btn.setFixedHeight(36)
        diagnose_btn.setCursor(Qt.PointingHandCursor)
        diagnose_btn.setStyleSheet("""
            QPushButton {
                background-color: #2D2D45;
                color: #E0E0E0;
                border: 1px solid #3D3D5A;
            }
            QPushButton:hover { background-color: #3D3D58; }
        """)
        diagnose_btn.clicked.connect(self._run_diagnosis)
        bottom_layout.addWidget(diagnose_btn)
        
        bottom_layout.addStretch()
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(90, 36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #3D3D55;
                color: #E0E0E0;
                border: 1px solid #4D4D65;
            }
            QPushButton:hover { background-color: #4D4D65; }
        """)
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("💾 保存并应用")
        save_btn.setFixedSize(130, 36)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border: none;
            }
            QPushButton:hover { background-color: #5A6AEF; }
            QPushButton:pressed { background-color: #4A5ADF; }
        """)
        save_btn.clicked.connect(self._on_save)
        bottom_layout.addWidget(save_btn)
        
        content_layout.addWidget(bottom_bar)
        
        # 设置滚动区域的内容
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area, stretch=1)
    
    def _create_card_frame(self) -> QFrame:
        """创建卡片样式的框架"""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #252538;
                border: 1px solid #3D3D5A;
                border-radius: 10px;
            }
            QLabel { 
                color: #E0E0E0; 
                background: transparent; 
            }
            QComboBox { 
                color: #E0E0E0; 
                background: #1E1E30; 
                border: 1px solid #3D3D5A;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 9pt;
            }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox::down-arrow { image: none; border: none; }
            QComboBox QAbstractItemView {
                background: #1E1E30;
                color: #E0E0E0;
                selection-background-color: #6C7BFF;
                border: 1px solid #3D3D5A;
                border-radius: 4px;
            }
            QCheckBox { 
                color: #E0E0E0; 
                background: transparent;
                spacing: 8px;
            }
            QCheckBox::indicator { 
                width: 18px; 
                height: 18px;
                border-radius: 4px;
                border: 2px solid #5A5A7A;
                background: #1E1E30;
            }
            QCheckBox::indicator:checked {
                background-color: #6C7BFF;
                border-color: #6C7BFF;
            }
            QCheckBox::indicator:hover {
                border-color: #6C7BFF;
            }
        """)
        return frame
    
    def _load_config(self):
        """加载当前配置到界面"""
        cfg = get_stt_engine_config()
        self._original_config = cfg.copy()
        
        default_engine = cfg.get("default_engine", "auto")
        idx = self._engine_combo.findData(default_engine)
        if idx >= 0:
            self._engine_combo.setCurrentIndex(idx)
        
        self._fallback_cb.setChecked(cfg.get("auto_fallback", True))
        
        self._funasr_tab.load_config(cfg["engines"]["funasr"])
        self._volcano_tab.load_config(cfg["engines"]["volcano"])
        self._aliyun_tab.load_config(cfg["engines"]["aliyun"])
        self._whisper_tab.load_config(cfg["engines"]["whisper"])
    
    def _check_migration(self):
        """检查是否需要迁移旧配置"""
        migrated, msg = migrate_legacy_stt_config()
        if migrated:
            reply = QMessageBox.question(
                self,
                "配置迁移",
                f"检测到旧的语音识别配置，是否迁移到新的语音转录中心？\n\n{msg}\n\n"
                "迁移后旧配置仍可使用，但建议在新界面中管理。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self._load_config()
    
    def _on_engine_changed(self, index):
        """默认引擎改变时的处理"""
        engine = self._engine_combo.itemData(index)
        if engine and engine != "auto":
            tab_map = {
                "funasr": 0,
                "volcano": 1,
                "aliyun": 2,
                "whisper": 3,
            }
            tab_idx = tab_map.get(engine)
            if tab_idx is not None:
                self._tabs.setCurrentIndex(tab_idx)
    
    def _collect_config(self) -> dict:
        """收集界面中的所有配置"""
        cfg = self._original_config.copy()
        
        cfg["default_engine"] = self._engine_combo.currentData() or "auto"
        cfg["auto_fallback"] = self._fallback_cb.isChecked()
        
        cfg["engines"]["funasr"] = self._funasr_tab.collect_config()
        cfg["engines"]["volcano"] = self._volcano_tab.collect_config()
        cfg["engines"]["aliyun"] = self._aliyun_tab.collect_config()
        cfg["engines"]["whisper"] = self._whisper_tab.collect_config()
        
        return cfg
    
    def _on_save(self):
        """保存配置"""
        cfg = self._collect_config()
        save_stt_engine_config(cfg)
        
        self.config_saved.emit()
        QMessageBox.information(
            self,
            "✅ 保存成功",
            "语音转录配置已保存！\n\n"
            "下次启动语音聊天时将应用新配置。",
            QMessageBox.Ok
        )
        self.accept()
    
    def _run_diagnosis(self):
        """运行系统诊断"""
        issues = []
        suggestions = []
        
        try:
            from brain.stt_funasr import check_model_status
            status = check_model_status()
            if status.get("loaded"):
                suggestions.append(f"✅ FunASR 模型已就绪 ({status.get('device', 'unknown')})")
            else:
                issues.append(f"⚠️ FunASR 模型未就绪: {status.get('reason', '未知')}")
        except Exception as e:
            issues.append(f"❌ FunASR 检查失败: {e}")
        
        try:
            from utils.gpu_resources import get_gpu_memory
            gpu_memory = get_gpu_memory()
            if gpu_memory:
                suggestions.append(
                    f"✅ NVIDIA GPU 可用（剩余 {gpu_memory['free_mb']} / "
                    f"{gpu_memory['total_mb']} MiB）"
                )
            else:
                suggestions.append("ℹ️ 未检测到 NVIDIA GPU telemetry（可使用 CPU 模式）")
        except Exception:
            suggestions.append("ℹ️ GPU 状态暂时无法读取（可使用 CPU 模式）")
        
        vol_cfg = self._volcano_tab.collect_config()
        if vol_cfg.get("enabled") and vol_cfg.get("appid"):
            suggestions.append("✅ 火山引擎已配置")
        elif vol_cfg.get("enabled"):
            issues.append("⚠️ 火山引擎已启用但未填写 AppID")
        
        ali_cfg = self._aliyun_tab.collect_config()
        if ali_cfg.get("enabled") and ali_cfg.get("access_key_id"):
            suggestions.append("✅ 阿里云 STT 已配置")
        elif ali_cfg.get("enabled"):
            issues.append("⚠️ 阿里云 STT 已启用但配置不完整")
        
        diag_msg = "<b>🔍 系统诊断结果</b><br><br>"
        
        if suggestions:
            diag_msg += "<b>正常项：</b><br>"
            for s in suggestions:
                diag_msg += f"• {s}<br>"
            diag_msg += "<br>"
        
        if issues:
            diag_msg += "<b>需要注意：</b><br>"
            for i in issues:
                diag_msg += f"• {i}<br>"
        else:
            diag_msg += "🎉 所有检查通过！系统运行正常。<br>"
        
        diag_msg += "<br><b>💡 建议：</b><br>"
        default_engine = self._engine_combo.currentData()
        if default_engine == "auto":
            diag_msg += "• 当前设置为\"自动检测\"，会根据硬件智能选择最佳引擎<br>"
        diag_msg += "• 推荐至少启用 2 个引擎以实现自动降级<br>"
        
        QMessageBox.information(
            self,
            "诊断结果",
            diag_msg,
            QMessageBox.Ok
        )
    
    def showEvent(self, event):
        """显示时刷新配置"""
        super().showEvent(event)
        self._load_config()
