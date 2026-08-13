# -*- coding: utf-8 -*-
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QSpinBox, QWidget,
    QPushButton, QScrollArea, QFrame, QLineEdit, QFileDialog, QComboBox,
    QTabWidget, QMessageBox, QMenu, QTextEdit, QSizePolicy
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from config import get_memory_config, save_memory_config
from brain.graph_memory import list_all_facts, delete_facts, add_fact, update_facts, ALL_MEMORY_CATEGORIES
from gui.current_state_panel import CurrentStatePanel

class MemorySettingsDialog(QDialog):
    """记忆系统独立设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mem_cfg = get_memory_config()

        self.setWindowTitle("棱镜记忆系统")
        self.setMinimumSize(620, 900)
        self.resize(660, 1200)
        self.setWindowFlags(Qt.Window)
        
        self._build_ui()
        self._load_from_config()
        self._all_facts = list_all_facts()
        self._refresh_memory_list()

    def _create_frame(self):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #1E1E30;
                border-radius: 8px;
                border: 1px solid #E0E0E8;
            }
        """)
        return frame

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                background-color: rgba(222, 184, 135, 0.3);
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background-color: #8B4513;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 16, 20, 16)

        # 标题
        title = QLabel("棱镜记忆系统")
        title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
        title.setStyleSheet("color: #1ABC9C;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #E0E0E8; max-height: 1px;")
        layout.addWidget(line)

        # 选项卡
        tabs = QTabWidget()
        self._tabs = tabs
        tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 0;
                background: transparent;
            }
            QTabBar::tab {
                background: #1E1E30;
                border: 1px solid #3D3D5A;
                border-bottom: 0;
                border-radius: 6px 6px 0 0;
                padding: 6px 14px;
                margin-right: 2px;
                color: #A0A0B0;
            }
            QTabBar::tab:selected {
                background: #2D2D3F;
                color: #E0E0E0;
                font-weight: bold;
            }
            QComboBox {
                background: #2D2D3F;
                color: #E0E0E0;
                border: 1px solid #3D3D5A;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QComboBox QAbstractItemView {
                background: #2D2D3F;
                color: #E0E0E0;
                selection-background-color: #3D3D5A;
                outline: none;
            }
            QComboBox::drop-down {
                border: none;
            }
        """)
        layout.addWidget(tabs)

        # ── 选项卡 1：记忆提取 ─────────────────────────────
        tab1 = QWidget()
        tab1_layout = QVBoxLayout(tab1)
        tab1_layout.setSpacing(14)

        # 自动提取开关
        auto_frame = self._create_frame()
        auto_vbox = QVBoxLayout(auto_frame)
        auto_vbox.setSpacing(8)
        self._memory_auto_cb = QCheckBox("自动提取对话记忆")
        self._memory_auto_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        auto_vbox.addWidget(self._memory_auto_cb)
        auto_desc = QLabel(
            "开启后，莲心会自动从对话中提取关键信息保存到长期记忆中，\n"
            "下次对话时会根据记忆回忆起你的过往信息。\n"
            "关闭后记忆系统仍然可用，但不会自动新增记忆。"
        )
        auto_desc.setWordWrap(True)
        auto_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        auto_vbox.addWidget(auto_desc)

        self._memory_auto_save_cb = QCheckBox("对话过程中自动保存记忆")
        self._memory_auto_save_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        auto_vbox.addWidget(self._memory_auto_save_cb)
        auto_save_desc = QLabel(
            "开启后，莲心在对话中留意到值得长期保存的内容（个人档案/偏好/事件/知识）时，\n"
            "会自主分类并直接保存，无需你逐条确认。\n"
            "关闭后仅在你明确说\"记住\"时才保存。"
        )
        auto_save_desc.setWordWrap(True)
        auto_save_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        auto_vbox.addWidget(auto_save_desc)
        tab1_layout.addWidget(auto_frame)

        # 后台提取触发与失败保护
        interval_frame = self._create_frame()
        interval_vbox = QVBoxLayout(interval_frame)
        interval_vbox.setSpacing(8)
        interval_title = QLabel("后台提取触发与失败保护")
        interval_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        interval_vbox.addWidget(interval_title)

        trigger_row = QHBoxLayout()
        trigger_row.addWidget(QLabel("空闲触发"))
        self._memory_extract_idle_spin = QSpinBox()
        self._memory_extract_idle_spin.setRange(30, 1800)
        self._memory_extract_idle_spin.setSuffix(" 秒")
        trigger_row.addWidget(self._memory_extract_idle_spin)
        trigger_row.addSpacing(16)
        trigger_row.addWidget(QLabel("积压优先"))
        self._memory_extract_backlog_spin = QSpinBox()
        self._memory_extract_backlog_spin.setRange(3, 100)
        self._memory_extract_backlog_spin.setSuffix(" 条")
        trigger_row.addWidget(self._memory_extract_backlog_spin)
        trigger_row.addStretch()
        interval_vbox.addLayout(trigger_row)

        failure_row = QHBoxLayout()
        failure_row.addWidget(QLabel("首次重试"))
        self._memory_extract_retry_spin = QSpinBox()
        self._memory_extract_retry_spin.setRange(1, 60)
        self._memory_extract_retry_spin.setSuffix(" 分钟")
        failure_row.addWidget(self._memory_extract_retry_spin)
        failure_row.addSpacing(16)
        failure_row.addWidget(QLabel("连续失败暂停"))
        self._memory_extract_pause_threshold_spin = QSpinBox()
        self._memory_extract_pause_threshold_spin.setRange(2, 20)
        self._memory_extract_pause_threshold_spin.setSuffix(" 次")
        failure_row.addWidget(self._memory_extract_pause_threshold_spin)
        failure_row.addStretch()
        interval_vbox.addLayout(failure_row)

        interval_desc = QLabel(
            "对话安静后自动处理；消息积压较多时会优先处理。失败采用指数退避，"
            "达到阈值后自动暂停，可在后台职责中心手动触发恢复。"
        )
        interval_desc.setWordWrap(True)
        interval_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        interval_vbox.addWidget(interval_desc)
        tab1_layout.addWidget(interval_frame)

        # 单次提取最大消息数
        count_frame = self._create_frame()
        count_vbox = QVBoxLayout(count_frame)
        count_vbox.setSpacing(8)
        count_title = QLabel("单次提取最大消息数")
        count_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        count_vbox.addWidget(count_title)
        self._memory_extract_msgs_spin = QSpinBox()
        self._memory_extract_msgs_spin.setRange(5, 100)
        self._memory_extract_msgs_spin.setSuffix(" 条")
        count_vbox.addWidget(self._memory_extract_msgs_spin)
        count_desc = QLabel("单次自动提取最多包含多少条最近消息，数值越大包含上下文越多但也越慢。")
        count_desc.setWordWrap(True)
        count_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        count_vbox.addWidget(count_desc)
        tab1_layout.addWidget(count_frame)

        # 每类记忆上限
        max_frame = self._create_frame()
        max_vbox = QVBoxLayout(max_frame)
        max_vbox.setSpacing(8)
        max_title = QLabel("每类记忆最大条数")
        max_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        max_vbox.addWidget(max_title)
        self._memory_max_items_spin = QSpinBox()
        self._memory_max_items_spin.setRange(50, 500)
        self._memory_max_items_spin.setSuffix(" 条")
        max_vbox.addWidget(self._memory_max_items_spin)
        max_desc = QLabel("每个分类最多保留多少条记忆，超出自动淘汰最旧+强度最低的记忆。")
        max_desc.setWordWrap(True)
        max_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        max_vbox.addWidget(max_desc)
        tab1_layout.addWidget(max_frame)

        # 后台维护
        maintenance_frame = self._create_frame()
        maintenance_layout = QVBoxLayout(maintenance_frame)
        maintenance_layout.setSpacing(8)
        self._maintenance_enabled_cb = QCheckBox("启用后台记忆维护")
        self._maintenance_enabled_cb.setFont(
            QFont("Microsoft YaHei UI", 10, QFont.Bold)
        )
        maintenance_layout.addWidget(self._maintenance_enabled_cb)
        maintenance_row = QHBoxLayout()
        maintenance_row.addWidget(QLabel("维护间隔"))
        self._maintenance_interval_spin = QSpinBox()
        self._maintenance_interval_spin.setRange(1, 168)
        self._maintenance_interval_spin.setSuffix(" 小时")
        maintenance_row.addWidget(self._maintenance_interval_spin)
        maintenance_row.addSpacing(16)
        maintenance_row.addWidget(QLabel("每轮冲突扫描"))
        self._maintenance_conflict_batch_spin = QSpinBox()
        self._maintenance_conflict_batch_spin.setRange(1, 50)
        self._maintenance_conflict_batch_spin.setSuffix(" 条")
        maintenance_row.addWidget(self._maintenance_conflict_batch_spin)
        maintenance_row.addStretch()
        maintenance_layout.addLayout(maintenance_row)
        self._maintenance_status_label = QLabel("")
        self._maintenance_status_label.setStyleSheet(
            "color: #9298B7; font-size: 12px;"
        )
        maintenance_layout.addWidget(self._maintenance_status_label)
        tab1_layout.addWidget(maintenance_frame)

        narrative_frame = self._create_frame()
        narrative_layout = QVBoxLayout(narrative_frame)
        self._narrative_enabled_cb = QCheckBox("启用叙事记忆整合")
        self._narrative_enabled_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        narrative_layout.addWidget(self._narrative_enabled_cb)
        narrative_row = QHBoxLayout()
        narrative_row.addWidget(QLabel("整合间隔"))
        self._narrative_interval_spin = QSpinBox()
        self._narrative_interval_spin.setRange(1, 168)
        self._narrative_interval_spin.setSuffix(" 小时")
        narrative_row.addWidget(self._narrative_interval_spin)
        narrative_row.addSpacing(12)
        narrative_row.addWidget(QLabel("每轮碎片"))
        self._narrative_batch_spin = QSpinBox()
        self._narrative_batch_spin.setRange(4, 100)
        self._narrative_batch_spin.setSuffix(" 条")
        narrative_row.addWidget(self._narrative_batch_spin)
        narrative_row.addStretch()
        narrative_layout.addLayout(narrative_row)
        narrative_layout.addWidget(QLabel("后台模型会把有来源的碎片整理成实体档案、Episode 和 Saga；原始碎片不会删除。"))
        tab1_layout.addWidget(narrative_frame)

        # 默认保存分类
        cat_frame = self._create_frame()
        cat_vbox = QVBoxLayout(cat_frame)
        cat_vbox.setSpacing(8)
        cat_title = QLabel("默认保存分类")
        cat_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        cat_vbox.addWidget(cat_title)
        self._memory_default_cat_combo = QComboBox()
        categories = [
            ("profile", "个人档案"),
            ("preferences", "偏好"),
            ("events", "事件"),
            ("knowledge", "知识"),
            ("behaviors", "行为模式"),
            ("skills", "技能"),
        ]
        for key, name in categories:
            self._memory_default_cat_combo.addItem(name, key)
        cat_vbox.addWidget(self._memory_default_cat_combo)
        cat_desc = QLabel("当用户要求记住某件事但没有指定分类时，默认存到哪个分类。")
        cat_desc.setWordWrap(True)
        cat_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        cat_vbox.addWidget(cat_desc)
        tab1_layout.addWidget(cat_frame)

        tab1_layout.addStretch()
        tabs.addTab(tab1, "📝 记忆提取")

        # ── 选项卡 2：知识图谱 ─────────────────────────────
        tab2 = QWidget()
        tab2_layout = QVBoxLayout(tab2)
        tab2_layout.setSpacing(14)

        # 图记忆总开关
        graph_frame = self._create_frame()
        graph_vbox = QVBoxLayout(graph_frame)
        graph_vbox.setSpacing(8)
        self._graph_enabled_cb = QCheckBox("启用知识图谱记忆")
        self._graph_enabled_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        graph_vbox.addWidget(self._graph_enabled_cb)
        graph_desc = QLabel(
            "知识图谱存储实体之间的关联关系，让莲心能回答类似\n"
            "\"莲心AI项目用到了哪些技术\"这种关联查询。\n"
            "关闭后只使用分类事实记忆，不影响基本功能。"
        )
        graph_desc.setWordWrap(True)
        graph_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        graph_vbox.addWidget(graph_desc)
        tab2_layout.addWidget(graph_frame)

        rag_frame = self._create_frame()
        rag_vbox = QVBoxLayout(rag_frame)
        rag_vbox.setSpacing(8)
        rag_title = QLabel("语义记忆检索")
        rag_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        rag_vbox.addWidget(rag_title)
        self._semantic_retrieval_combo = QComboBox()
        self._semantic_retrieval_combo.addItem("按需加载（推荐）", "on_demand")
        self._semantic_retrieval_combo.addItem("始终使用语义检索", "always")
        self._semantic_retrieval_combo.addItem("仅使用关键词检索", "off")
        rag_vbox.addWidget(self._semantic_retrieval_combo)
        rag_desc = QLabel(
            "按需加载会让普通聊天保持轻量，仅在明确询问回忆、时间线、实体或事实时加载本地语义模型。\n"
            "始终使用语义检索会获得最完整的召回，但会在首次聊天时加载模型。"
        )
        rag_desc.setWordWrap(True)
        rag_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        rag_vbox.addWidget(rag_desc)
        tab2_layout.addWidget(rag_frame)

        # 自动提取五元组
        auto_quin_frame = self._create_frame()
        auto_quin_vbox = QVBoxLayout(auto_quin_frame)
        auto_quin_vbox.setSpacing(8)
        self._graph_auto_quin_cb = QCheckBox("自动提取实体关系")
        self._graph_auto_quin_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        auto_quin_vbox.addWidget(self._graph_auto_quin_cb)
        auto_quin_desc = QLabel(
            "开启后，莲心会自动识别对话中的实体（人物、地点、物品等）\n"
            "并提取它们之间的关联关系保存到图谱中。\n"
            "不需要手动调用工具添加关系。"
        )
        auto_quin_desc.setWordWrap(True)
        auto_quin_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        auto_quin_vbox.addWidget(auto_quin_desc)
        tab2_layout.addWidget(auto_quin_frame)

        tab2_layout.addStretch()
        tabs.addTab(tab2, "🔗 知识图谱")

        # ── 选项卡 3：上下文压缩 ─────────────────────────────
        tab3 = QWidget()
        tab3_layout = QVBoxLayout(tab3)
        tab3_layout.setSpacing(14)

        # 滑动窗口大小
        window_frame = self._create_frame()
        window_vbox = QVBoxLayout(window_frame)
        window_vbox.setSpacing(8)
        window_title = QLabel("上下文窗口大小")
        window_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        window_vbox.addWidget(window_title)
        self._context_window_spin = QSpinBox()
        self._context_window_spin.setRange(4, 40)
        self._context_window_spin.setSuffix(" 条消息")
        window_vbox.addWidget(self._context_window_spin)
        window_desc = QLabel(
            "对话中始终保留最近 N 条消息作为完整上下文。\n"
            "数值越大：记忆越完整，但 Token 消耗越高。\n"
            "数值越小：越省 Token，但对早期事情回忆可能需要依赖摘要。\n"
            "推荐：15-25"
        )
        window_desc.setWordWrap(True)
        window_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        window_vbox.addWidget(window_desc)
        tab3_layout.addWidget(window_frame)

        # 摘要压缩开关
        summary_frame = self._create_frame()
        summary_vbox = QVBoxLayout(summary_frame)
        summary_vbox.setSpacing(8)
        self._summary_enabled_cb = QCheckBox("启用早期对话摘要压缩")
        self._summary_enabled_cb.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        summary_vbox.addWidget(self._summary_enabled_cb)
        summary_desc = QLabel(
            "开启后，窗口外的早期对话会被压缩成一段摘要注入上下文，\n"
            "既保持对话连续性，又节省大量 Token。\n"
            "关闭后只保留窗口内对话，早期内容直接截断。"
        )
        summary_desc.setWordWrap(True)
        summary_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        summary_vbox.addWidget(summary_desc)
        tab3_layout.addWidget(summary_frame)

        # 摘要触发阈值
        trigger_frame = self._create_frame()
        trigger_vbox = QVBoxLayout(trigger_frame)
        trigger_vbox.setSpacing(8)
        trigger_title = QLabel("摘要触发阈值")
        trigger_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        trigger_vbox.addWidget(trigger_title)
        self._summary_trigger_spin = QSpinBox()
        self._summary_trigger_spin.setRange(0, 100)
        self._summary_trigger_spin.setSuffix(" 条消息")
        trigger_vbox.addWidget(self._summary_trigger_spin)
        trigger_desc = QLabel(
            "历史消息总数超过多少条后，才开始启动摘要压缩。\n"
            "0 = 无论多少条都压缩（适合非常短对话），推荐 20-40。"
        )
        trigger_desc.setWordWrap(True)
        trigger_desc.setStyleSheet("color: #888; font-size: 15px; padding: 4px 0;")
        trigger_vbox.addWidget(trigger_desc)
        tab3_layout.addWidget(trigger_frame)

        # 估算提示
        estimate_label = QLabel(
            "💡 效果参考：\n"
            "· 关闭摘要 + 窗口 10 条 → Token ~ 1200/轮，早期内容丢失\n"
            "· 开启摘要 + 窗口 20 条 → Token ~ 2500/轮，长对话不增长\n"
            "· 原全量历史 → 30轮后 Token > 6000，持续增长"
        )
        estimate_label.setWordWrap(True)
        estimate_label.setStyleSheet("color: #CCC; font-size: 15px; background: #1E1E30; padding: 8px; border-radius: 4px;")
        tab3_layout.addWidget(estimate_label)

        tab3_layout.addStretch()
        tabs.addTab(tab3, "⚙️ 上下文压缩")

        # ── 选项卡 4：当前状态 ───────────────────────────────
        self._current_state_panel = CurrentStatePanel()
        tabs.addTab(self._current_state_panel, "◉ 当前状态")

        # ── 选项卡 6：记忆诊断 ───────────────────────────────
        from gui.memory_debug_panel import MemoryDebugPanel
        self._debug_panel = MemoryDebugPanel()
        tabs.addTab(self._debug_panel, "🔬 记忆诊断")

        # ── 选项卡 7：记忆浏览 ─────────────────────────────
        tab4 = QWidget()
        tab4_layout = QVBoxLayout(tab4)
        tab4_layout.setSpacing(10)

        # 搜索栏 + 分类过滤
        search_row = QHBoxLayout()
        self._memory_search_input = QLineEdit()
        self._memory_search_input.setPlaceholderText("🔍 搜索记忆关键词...")
        self._memory_search_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D0D0E0; border-radius: 6px;
                padding: 6px 10px; background: #FFFFFF;
                color: #2C2C2C;
                font-size: 12px;
            }
        """)
        self._memory_search_input.textChanged.connect(self._refresh_memory_list)
        search_row.addWidget(self._memory_search_input, 3)

        self._memory_cat_filter = QComboBox()
        self._memory_cat_filter.addItem("全部分类", "")
        cat_names = {
            "profile": "👤 个人档案", "preferences": "❤️ 偏好",
            "events": "📅 事件", "knowledge": "📚 知识",
            "behaviors": "💬 行为模式", "skills": "🛠️ 技能",
        }
        for key in ALL_MEMORY_CATEGORIES:
            self._memory_cat_filter.addItem(cat_names.get(key, key), key)
        self._memory_cat_filter.currentIndexChanged.connect(self._refresh_memory_list)
        search_row.addWidget(self._memory_cat_filter, 1)

        add_btn = QPushButton("➕ 新增记忆")
        add_btn.setFixedWidth(90)
        add_btn.setStyleSheet("""
            QPushButton {
                background: #E8ECFF; color: #4A4A8A; border: 1px solid #C0C8E8;
                border-radius: 6px; padding: 6px 10px; font-size: 13px;
            }
            QPushButton:hover { background: #D0D8FF; }
        """)
        add_btn.clicked.connect(self._toggle_add_form)
        search_row.addWidget(add_btn)
        tab4_layout.addLayout(search_row)

        # 新增记忆表单（默认隐藏）
        self._add_form = QFrame()
        self._add_form.setStyleSheet("""
            QFrame {
                background: #1E1E30; border-radius: 8px;
                border: 1px solid #3D3D5A;
            }
        """)
        add_form_layout = QVBoxLayout(self._add_form)
        add_form_layout.setContentsMargins(12, 10, 12, 10)
        add_form_layout.setSpacing(8)

        form_row1 = QHBoxLayout()
        form_row1.addWidget(QLabel("分类："))
        self._new_cat_combo = QComboBox()
        cat_names2 = {
            "profile": "👤 个人档案", "preferences": "❤️ 偏好",
            "events": "📅 事件", "knowledge": "📚 知识",
            "behaviors": "💬 行为模式", "skills": "🛠️ 技能",
        }
        for key in ALL_MEMORY_CATEGORIES:
            self._new_cat_combo.addItem(cat_names2.get(key, key), key)
        form_row1.addWidget(self._new_cat_combo, 1)
        add_form_layout.addLayout(form_row1)

        self._new_content_input = QTextEdit()
        self._new_content_input.setPlaceholderText("输入记忆内容...")
        self._new_content_input.setMaximumHeight(60)
        self._new_content_input.setStyleSheet("""
            QTextEdit {
                border: 1px solid #3D3D5A; border-radius: 6px;
                padding: 6px; background: #2D2D3F;
                color: #E0E0E0;
                font-size: 12px;
            }
        """)
        add_form_layout.addWidget(self._new_content_input)

        form_btns = QHBoxLayout()
        form_btns.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(60, 26)
        cancel_btn.clicked.connect(lambda: self._add_form.hide())
        form_btns.addWidget(cancel_btn)
        save_btn = QPushButton("💾 保存")
        save_btn.setFixedSize(70, 26)
        save_btn.setStyleSheet("""
            QPushButton {
                background: #6A7BFF; color: #FFFFFF; border: 0;
                border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover { background: #5A6BEF; }
        """)
        save_btn.clicked.connect(self._on_add_memory)
        form_btns.addWidget(save_btn)
        add_form_layout.addLayout(form_btns)
        self._add_form.hide()
        tab4_layout.addWidget(self._add_form)

        # 统计标签
        self._memory_count_label = QLabel("")
        self._memory_count_label.setStyleSheet("color: #888; font-size: 15px;")
        tab4_layout.addWidget(self._memory_count_label)

        # 提示标签
        hint_label = QLabel("💡 右键条目修改记忆")
        hint_label.setStyleSheet("color: #888; font-size: 13px; padding: 4px 0;")
        tab4_layout.addWidget(hint_label)

        # 可滚动记忆列表
        self._memory_scroll = QScrollArea()
        self._memory_scroll.setWidgetResizable(True)
        self._memory_scroll.setStyleSheet("""
            QScrollArea { border: 1px solid #3D3D5A; border-radius: 8px; background: #1E1E30;
                color: #E0E0E0;
                font-size: 12px;
            }
            QScrollArea::vertical { background: transparent; }
            QScrollArea::horizontal { background: transparent; }
        """)
        self._memory_list_widget = QWidget()
        self._memory_list_layout = QVBoxLayout(self._memory_list_widget)
        self._memory_list_layout.setSpacing(6)
        self._memory_list_layout.setContentsMargins(10, 10, 10, 10)
        self._memory_list_layout.addStretch()
        self._memory_scroll.setWidget(self._memory_list_widget)
        tab4_layout.addWidget(self._memory_scroll, 1)

        tabs.addTab(tab4, "📋 记忆浏览")

        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(20, 8, 20, 16)
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

        outer_layout.addLayout(btn_row)

    def showEvent(self, event):
        """Refresh persisted memory data whenever the cached dialog is reopened."""
        self._all_facts = list_all_facts()
        self._refresh_memory_list()
        self._current_state_panel.refresh()
        self._refresh_maintenance_status()
        super().showEvent(event)

    def _load_from_config(self):
        from config import get_memory_config, get_graph_config
        self._mem_cfg = get_memory_config()
        self._graph_cfg = get_graph_config()

        # 记忆提取
        self._memory_auto_cb.setChecked(self._mem_cfg.get("auto_extract", True))
        self._memory_auto_save_cb.setChecked(self._mem_cfg.get("conversation_auto_save", False))
        self._memory_extract_idle_spin.setValue(
            self._mem_cfg.get("extraction_idle_seconds", 120)
        )
        self._memory_extract_backlog_spin.setValue(
            self._mem_cfg.get("extraction_backlog_messages", 20)
        )
        self._memory_extract_retry_spin.setValue(
            self._mem_cfg.get("extraction_retry_base_minutes", 5)
        )
        self._memory_extract_pause_threshold_spin.setValue(
            self._mem_cfg.get("extraction_failure_pause_threshold", 5)
        )
        self._memory_extract_msgs_spin.setValue(self._mem_cfg.get("extract_message_count", 20))
        self._memory_max_items_spin.setValue(self._mem_cfg.get("max_items_per_category", 200))
        # 默认分类
        default_cat = self._mem_cfg.get("default_save_category", "knowledge")
        for i in range(self._memory_default_cat_combo.count()):
            if self._memory_default_cat_combo.itemData(i) == default_cat:
                self._memory_default_cat_combo.setCurrentIndex(i)
                break

        # 知识图谱
        self._graph_enabled_cb.setChecked(self._graph_cfg.get("graph_enabled", True))
        self._graph_auto_quin_cb.setChecked(self._graph_cfg.get("auto_extract_quintuples", True))
        semantic_mode = self._mem_cfg.get("semantic_retrieval_mode", "on_demand")
        semantic_index = self._semantic_retrieval_combo.findData(semantic_mode)
        self._semantic_retrieval_combo.setCurrentIndex(max(0, semantic_index))

        # 上下文压缩
        self._context_window_spin.setValue(self._mem_cfg.get("context_window_size", 20))
        self._summary_enabled_cb.setChecked(self._mem_cfg.get("enable_conversation_summary", True))
        self._summary_trigger_spin.setValue(self._mem_cfg.get("summary_trigger_threshold", 30))
        self._maintenance_enabled_cb.setChecked(
            self._mem_cfg.get("maintenance_enabled", True)
        )
        self._maintenance_interval_spin.setValue(
            self._mem_cfg.get("maintenance_interval_hours", 6)
        )
        self._maintenance_conflict_batch_spin.setValue(
            self._mem_cfg.get("maintenance_conflict_scan_batch", 10)
        )
        self._narrative_enabled_cb.setChecked(self._mem_cfg.get("narrative_enabled", True))
        self._narrative_interval_spin.setValue(self._mem_cfg.get("narrative_interval_hours", 12))
        self._narrative_batch_spin.setValue(self._mem_cfg.get("narrative_candidate_batch", 36))
        self._refresh_maintenance_status()

    def _on_save(self):
        cfg = dict(self._mem_cfg)
        cfg.update({
            "auto_extract": self._memory_auto_cb.isChecked(),
            "conversation_auto_save": self._memory_auto_save_cb.isChecked(),
            "extract_message_count": self._memory_extract_msgs_spin.value(),
            "extraction_idle_seconds": self._memory_extract_idle_spin.value(),
            "extraction_backlog_messages": self._memory_extract_backlog_spin.value(),
            "extraction_retry_base_minutes": self._memory_extract_retry_spin.value(),
            "extraction_failure_pause_threshold": self._memory_extract_pause_threshold_spin.value(),
            "max_items_per_category": self._memory_max_items_spin.value(),
            "default_save_category": self._memory_default_cat_combo.currentData(),
            "context_window_size": self._context_window_spin.value(),
            "enable_conversation_summary": self._summary_enabled_cb.isChecked(),
            "summary_trigger_threshold": self._summary_trigger_spin.value(),
            "maintenance_enabled": self._maintenance_enabled_cb.isChecked(),
            "maintenance_interval_hours": self._maintenance_interval_spin.value(),
            "maintenance_conflict_scan_batch": self._maintenance_conflict_batch_spin.value(),
            "narrative_enabled": self._narrative_enabled_cb.isChecked(),
            "narrative_interval_hours": self._narrative_interval_spin.value(),
            "narrative_candidate_batch": self._narrative_batch_spin.value(),
            "working_memory_ttl_minutes": self._mem_cfg.get("working_memory_ttl_minutes", 120),
            "semantic_retrieval_mode": self._semantic_retrieval_combo.currentData(),
        })
        from config import save_memory_config
        save_memory_config(cfg)

        # 保存图记忆配置
        graph_cfg = {
            "graph_enabled": self._graph_enabled_cb.isChecked(),
            "auto_extract_quintuples": self._graph_auto_quin_cb.isChecked(),
            "graph_max_edges": self._graph_cfg.get("graph_max_edges", 2000),
        }
        from config import save_graph_config
        save_graph_config(graph_cfg)

        self.accept()

    def _refresh_maintenance_status(self):
        try:
            from brain.memory_maintenance import get_last_maintenance_run
            last = get_last_maintenance_run()
        except Exception:
            last = None
        if not last:
            self._maintenance_status_label.setText("尚未执行后台维护")
            return
        status = {
            "success": "成功", "failed": "失败", "running": "运行中",
        }.get(last.get("status"), last.get("status", "未知"))
        finished = str(last.get("finished_at") or last.get("started_at") or "").replace("T", " ")[:19]
        self._maintenance_status_label.setText(
            f"最近维护：{finished} · {status} · {float(last.get('duration_ms') or 0):.0f} ms"
        )

    def _refresh_memory_list(self):
        """根据搜索关键词和分类过滤，重建记忆列表。"""
        # 清空现有项目
        for i in reversed(range(self._memory_list_layout.count())):
            w = self._memory_list_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        keyword = self._memory_search_input.text().strip().lower()
        cat_filter = self._memory_cat_filter.currentData()

        total = 0
        for cat in ALL_MEMORY_CATEGORIES:
            if cat_filter and cat != cat_filter:
                continue
            items = self._all_facts.get(cat, [])
            # 关键词过滤
            if keyword:
                items = [it for it in items if keyword in it.get("content", "").lower()]
            if not items:
                continue

            # 分类标题
            cat_names = {
                "profile": "👤 个人档案", "preferences": "❤️ 偏好",
                "events": "📅 事件", "knowledge": "📚 知识",
                "behaviors": "💬 行为模式", "skills": "🛠️ 技能",
            }
            cat_header = QLabel(cat_names.get(cat, cat))
            cat_header.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
            cat_header.setStyleSheet("color: #B0B0D0; padding: 6px 0 2px 0;")
            self._memory_list_layout.addWidget(cat_header)

            for item in items:
                content = item.get("content", "")
                strength = item.get("strength", 1)
                source = "自动" if item.get("source") == "auto_extracted" else "手动"
                created = item.get("created_at", "")[:10] if item.get("created_at") else ""
                quality = float(item.get("quality_score", 0.5) or 0.5)
                review_status = item.get("review_status", "normal")

                row = QFrame()
                row.setContextMenuPolicy(Qt.CustomContextMenu)
                row.customContextMenuRequested.connect(
                    lambda pos, c=content, cat_name=cat, r=row: self._show_context_menu(pos, c, cat_name, r)
                )
                row.setContextMenuPolicy(Qt.CustomContextMenu)
                row.customContextMenuRequested.connect(
                    lambda pos, c=content, cat_name=cat, r=row: self._show_context_menu(pos, c, cat_name, r)
                )
                row.setContextMenuPolicy(Qt.CustomContextMenu)
                row.customContextMenuRequested.connect(
                    lambda pos, c=content, cat_name=cat, r=row: self._show_context_menu(pos, c, cat_name, r)
                )
                row.setStyleSheet("""
                    QFrame {
                        background: #1E1E30; border-radius: 6px;
                        border: 1px solid #3D3D5A;
                    }
                    QFrame:hover { background: #2D2D3F; }
                """)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(10, 6, 6, 6)
                row_layout.setSpacing(8)

                # 左侧：内容
                content_label = QLabel(content)
                content_label.setWordWrap(True)
                content_label.setStyleSheet("border: 0; background: transparent; font-size: 15px; color: #FFFFFF; font-weight: bold;")
                row_layout.addWidget(content_label, 1)

                # 右侧：元信息
                meta = (
                    f"<span style='color:#CCCCCC;'>强度:{strength} · "
                    f"质量:{quality:.0%} · {source}</span>"
                )
                if review_status != "normal":
                    meta += f"<span style='color:#F0B35A;'> · {review_status}</span>"
                if created:
                    meta += f"<span style='color:#1ABC9C;'> · {created}</span>"
                meta_label = QLabel(meta)
                meta_label.setStyleSheet("border: 0; background: transparent; font-size: 13px; color: #CCCCCC; white-space: nowrap;")
                row_layout.addWidget(meta_label)

                # 删除按钮
                del_btn = QPushButton("✕")
                del_btn.setFixedSize(22, 22)
                del_btn.setToolTip("删除这条记忆")
                del_btn.setStyleSheet("""
                    QPushButton {
                        background: #FFE0E0; color: #CC4444; border: 0;
                        border-radius: 11px; font-weight: bold; font-size: 13px;
                    }
                    QPushButton:hover { background: #FF8888; color: #FFFFFF; }
                """)
                del_btn.clicked.connect(
                    lambda checked, c=content, cat_name=cat: self._on_delete_memory(c, cat_name)
                )
                row_layout.addWidget(del_btn)

                self._memory_list_layout.addWidget(row)
                total += 1

        if total == 0:
            empty = QLabel("📭 没有匹配的记忆" if keyword or cat_filter else "📭 还没有任何记忆")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #888; font-size: 13px; padding: 30px; border: 0;")
            self._memory_list_layout.addWidget(empty)

        self._memory_list_layout.addStretch()
        self._memory_count_label.setText(f"📊 共 {total} 条记忆" + (f"（已过滤）" if keyword or cat_filter else ""))

    def _on_delete_memory(self, content: str, category: str):
        """确认后删除指定记忆条目。"""
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除这条记忆吗？\n\n[{category}] {content[:50]}{'...' if len(content) > 50 else ''}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        delete_facts(content, category)
        # 重新加载并刷新
        self._all_facts = list_all_facts()
        self._refresh_memory_list()



    def _toggle_add_form(self):
        """切换新增记忆表单的显示/隐藏。"""
        self._add_form.setVisible(not self._add_form.isVisible())
        if self._add_form.isVisible():
            self._new_content_input.setFocus()

    def _on_add_memory(self):
        """保存用户手动新增的记忆。"""
        content = self._new_content_input.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "记忆内容不能为空。")
            return
        category = self._new_cat_combo.currentData()
        add_fact(content, category, source="user_saved")
        # 清空表单
        self._new_content_input.clear()
        self._add_form.hide()
        # 重新加载并刷新
        self._all_facts = list_all_facts()
        self._refresh_memory_list()

    def _show_context_menu(self, pos, content: str, category: str, row: QFrame):
        """右键菜单：修改记忆。"""
        menu = QMenu(self)
        edit_action = menu.addAction("✏️ 修改这条记忆")
        action = menu.exec_(row.mapToGlobal(pos))
        if action == edit_action:
            self._on_edit_memory(content, category)

    def _on_edit_memory(self, old_content: str, category: str):
        """弹出编辑对话框，修改记忆内容。"""
        from PyQt5.QtWidgets import QInputDialog
        new_content, ok = QInputDialog.getMultiLineText(
            self, "修改记忆", f"分类：{category}\n请输入新内容：", old_content
        )
        if not ok or not new_content or new_content.strip() == old_content:
            return
        new_content = new_content.strip()
        update_facts(old_content, new_content, category)
        # 重新加载并刷新
        self._all_facts = list_all_facts()
        self._refresh_memory_list()
