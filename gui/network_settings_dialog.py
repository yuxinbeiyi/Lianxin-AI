"""
NetworkSettingsDialog：网络搜索设置对话框
独立配置 Tavily 搜索、Firecrawl 爬虫、重试回退策略。
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QMessageBox, QTabWidget,
    QWidget, QFormLayout, QCheckBox, QSpinBox, QButtonGroup, QRadioButton,
    QGroupBox, QListWidget, QListWidgetItem, QAbstractItemView, QScrollArea, QLayout,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from config import (
    get_tavily_config, save_tavily_config,
    get_firecrawl_config, save_firecrawl_config,
    get_zhihu_config, save_zhihu_config,
    get_search_fallback_config, save_search_fallback_config,
    get_network_tool_order_config, save_network_tool_order_config,
    get_proxy_config, save_proxy_config,
    get_builtin_tool_config, save_builtin_tool_config,
    get_bilibili_config, save_bilibili_config,
)
from gui.styles.settings_theme import SettingsScrollBar, apply_settings_theme


class NetworkSettingsDialog(QDialog):
    """网络搜索设置独立对话框。"""

    config_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🌐 联网搜索")
        self.setMinimumSize(520, 460)
        self.setStyleSheet("""
            QDialog {
                background-color: #1E1E2E;
            }
        """)
        self._build_ui()
        apply_settings_theme(self)
        self._apply_network_theme()
        self._load_config()

    def _apply_network_theme(self):
        """Keep every search settings tab on the same dark, high-contrast palette."""
        self.setStyleSheet("""
            QDialog, QWidget {
                background-color: #151B2B;
                color: #E6EDF7;
            }
            QDialog { background-color: #151B2B; }
            QLabel, QCheckBox, QRadioButton, QGroupBox {
                color: #E6EDF7;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 8px;
                margin-top: 10px;
                padding: 14px 10px 10px 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #E6EDF7;
            }
            QTabWidget::pane {
                background: #151B2B;
                border: 1px solid #334155;
                border-radius: 6px;
            }
            QTabBar::tab {
                background: #202B3D;
                color: #B8C1D8;
                border: 1px solid #334155;
                padding: 7px 14px;
                margin-right: 2px;
            }
            QTabBar::tab:hover {
                background: #293A50;
                color: #E6EDF7;
            }
            QTabBar::tab:selected {
                background: #2F6F62;
                color: #FFFFFF;
                border-color: #65B7A5;
                font-weight: bold;
            }
            QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
            QComboBox, QListWidget {
                background-color: #0F1724;
                color: #E6EDF7;
                border: 1px solid #334155;
                border-radius: 6px;
                selection-background-color: #2F6F62;
                selection-color: #FFFFFF;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
            QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
            QListWidget:focus {
                border-color: #65B7A5;
            }
            QComboBox QAbstractItemView {
                background-color: #0F1724;
                color: #E6EDF7;
                selection-background-color: #2F6F62;
                selection-color: #FFFFFF;
            }
            QListWidget::item {
                padding: 5px 6px;
            }
            QListWidget::item:selected {
                background: #2F6F62;
                color: #FFFFFF;
            }
            QPushButton {
                background-color: #26354A;
                color: #E6EDF7;
                border: 1px solid #465B78;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #324964;
                border-color: #65B7A5;
            }
            QPushButton:pressed { background-color: #1F3F3A; }
            QPushButton:disabled {
                background-color: #202B3D;
                color: #718096;
                border-color: #334155;
            }
            QScrollArea, QScrollArea > QWidget > QWidget {
                background: #151B2B;
                border: none;
            }
            QScrollBar:vertical {
                background: #0F1724;
                width: 10px;
                margin: 2px 0;
            }
            QScrollBar::handle:vertical {
                background: #526581;
                min-height: 48px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover { background: #65B7A5; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
            }
            QCheckBox::indicator, QRadioButton::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #718096;
                background: #0F1724;
            }
            QCheckBox::indicator:checked, QRadioButton::indicator:checked {
                background: #2F6F62;
                border-color: #65B7A5;
            }
        """)

        button_style = """
            QPushButton {
                background: #26354A; color: #E6EDF7;
                border: 1px solid #465B78; border-radius: 6px; padding: 6px 12px;
            }
            QPushButton:hover { background: #324964; border-color: #65B7A5; }
            QPushButton:pressed { background: #1F3F3A; }
            QPushButton:disabled { background: #202B3D; color: #718096; }
        """
        for button in self.findChildren(QPushButton):
            if not button.styleSheet().strip():
                button.setStyleSheet(button_style)

        for label in self.findChildren(QLabel):
            if not label.styleSheet().strip():
                label.setStyleSheet("color: #F4F7FB;")

        for checkbox in self.findChildren(QCheckBox):
            if not checkbox.styleSheet().strip():
                checkbox.setStyleSheet("color: #F4F7FB;")

        input_style = """
            QLineEdit {
                background: #0F1724;
                color: #FFFFFF;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 5px 8px;
                selection-background-color: #2F6F62;
                selection-color: #FFFFFF;
            }
            QLineEdit:focus { border-color: #65B7A5; }
        """
        for edit in self.findChildren(QLineEdit):
            if not edit.styleSheet().strip():
                edit.setStyleSheet(input_style)

        for group in self.findChildren(QGroupBox):
            if not group.styleSheet().strip():
                group.setStyleSheet("""
                    QGroupBox {
                        color: #FFFFFF;
                        border: 1px solid #334155;
                        border-radius: 8px;
                        margin-top: 10px;
                        padding: 14px 10px 10px 10px;
                    }
                    QGroupBox::title {
                        subcontrol-origin: margin;
                        left: 10px;
                        padding: 0 5px;
                        color: #FFFFFF;
                    }
                """)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 16, 20, 16)

        title = QLabel("🌐 网络搜索设置")
        title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
        title.setStyleSheet("color: #E8EAF2;")
        layout.addWidget(title)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #334155; max-height: 1px;")
        layout.addWidget(line)

        desc = QLabel(
            "配置 Tavily AI 搜索、Firecrawl 网页爬虫的 API Key，\n"
            "以及 MCP 搜索失败后的重试和回退策略。"
        )
        desc.setWordWrap(True)
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #1ABC9C; background: #1E1E30; padding: 8px; border-radius: 4px; border: 1px solid #3D3D5A;")
        layout.addWidget(desc)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #334155; background: #151B2B; }
            QTabBar::tab {
                background: #202B3D; border: 1px solid #334155; border-bottom: 0;
                border-radius: 6px 6px 0 0; padding: 6px 14px; margin-right: 2px;
                color: #B8C1D8;
            }
            QTabBar::tab:hover { background: #293A50; color: #E6EDF7; }
            QTabBar::tab:selected { background: #2F6F62; color: #FFFFFF; border-color: #65B7A5; font-weight: bold; }
            QTabBar QToolButton {
                background: #202B3D; color: #E6EDF7;
                border: 1px solid #334155; border-radius: 3px;
            }
        """)
        layout.addWidget(tabs)

        # ── Tab 1: Tavily 搜索 ────────────────────────────
        tab_tavily = QWidget()
        self._build_tab_tavily(tab_tavily)
        tabs.addTab(tab_tavily, "🔍 Tavily 搜索")

        # ── Tab 2: Firecrawl 爬虫 ─────────────────────────
        tab_firecrawl = QWidget()
        self._build_tab_firecrawl(tab_firecrawl)
        tabs.addTab(tab_firecrawl, "🕷️ Firecrawl 爬虫")

        # ── Tab 3: 知乎全搜索 ─────────────────────────
        tab_zhihu = QWidget()
        self._build_tab_zhihu(tab_zhihu)
        tabs.addTab(tab_zhihu, "🎓 知乎搜索")

        # ── Tab 4: 重试与回退 ─────────────────────────────
        tab_fallback = QWidget()
        self._build_tab_fallback(tab_fallback)
        tabs.addTab(tab_fallback, "⚙️ 工具调用顺序")

        # ── Tab 5: 代理设置 ─────────────────────────────
        tab_proxy = QWidget()
        self._build_tab_proxy(tab_proxy)
        tabs.addTab(tab_proxy, "🛡️ 代理设置")

        # ── Tab 6: B站账号 ─────────────────────────────
        tab_bilibili = QWidget()
        self._build_tab_bilibili(tab_bilibili)
        tabs.addTab(tab_bilibili, "📺 B站账号")

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

    # ── Tavily ────────────────────────────────────────────
    def _prepare_scroll_tab(self, parent: QWidget) -> QWidget:
        outer = QVBoxLayout(parent)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        parent.setStyleSheet("background: #151B2B;")
        content.setStyleSheet("background: #151B2B;")
        scroll.setWidget(content)
        scroll.setVerticalScrollBar(SettingsScrollBar(scroll))
        outer.addWidget(scroll)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        scroll.verticalScrollBar().setMinimumWidth(14)
        scroll.verticalScrollBar().setSingleStep(32)
        scroll.verticalScrollBar().setCursor(Qt.PointingHandCursor)
        return content

    def _build_tab_tavily(self, parent: QWidget):
        layout = QVBoxLayout(self._prepare_scroll_tab(parent))
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        section_title = QLabel("🔍 Tavily AI 搜索配置")
        section_title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        section_title.setStyleSheet("color: #E8EAF2;")
        layout.addWidget(section_title)

        desc = QLabel(
            "配置 Tavily API Key 后，莲心就能使用高质量的 AI 专属搜索引擎，\n"
            "支持实时网络搜索、网页内容提取、深度爬取。\n"
            "注册地址：https://tavily.com/，免费额度 1000次/月。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #AEB8C8;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        key_row = QHBoxLayout()
        self._tv_key_edit = QLineEdit()
        self._tv_key_edit.setPlaceholderText("tvly-xxxxxxxxxxxxxxxxxxxxxxxx")
        self._tv_key_edit.setEchoMode(QLineEdit.Password)
        self._tv_key_edit.setFont(QFont("Consolas", 10))
        self._tv_key_edit.setStyleSheet("""
            QLineEdit { border: 1px solid #334155; border-radius: 6px;
                padding: 6px 10px; background: #0F1724; color: #E6EDF7;
            }
            QLineEdit:focus { border-color: #6C7BFF; }
        """)
        key_row.addWidget(self._tv_key_edit)

        self._show_tv_btn = QPushButton("显示")
        self._show_tv_btn.setFixedSize(60, 34)
        self._show_tv_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._show_tv_btn.setCursor(Qt.PointingHandCursor)
        self._show_tv_btn.setCheckable(True)
        self._show_tv_btn.clicked.connect(self._toggle_tv_key_visibility)
        key_row.addWidget(self._show_tv_btn)
        form.addRow("Tavily API Key：", key_row)

        layout.addLayout(form)
        layout.addSpacing(8)

        help_text = QLabel(
            "💡 提示：MCP Tavily 请求从你本地发出，绕过后端被墙限制，搜索质量优于 DuckDuckGo。"
        )
        help_text.setFont(QFont("Microsoft YaHei UI", 8))
        help_text.setStyleSheet("color: #C8D2E2; background-color: #1B2435; padding: 8px; border-radius: 6px; border: 1px solid #334155;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addStretch()

    def _toggle_tv_key_visibility(self, checked: bool):
        self._tv_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_tv_btn.setText("隐藏" if checked else "显示")

    # ── Firecrawl ─────────────────────────────────────────
    def _build_tab_firecrawl(self, parent: QWidget):
        layout = QVBoxLayout(self._prepare_scroll_tab(parent))
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        section_title = QLabel("🕷️ Firecrawl 网页爬虫配置")
        section_title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        section_title.setStyleSheet("color: #E8EAF2;")
        layout.addWidget(section_title)

        desc = QLabel(
            "配置 Firecrawl API Key 后，莲心就能抓取任意网页的纯净内容，"
            "输出干净的 Markdown 格式，供 AI 分析使用。\n"
            "配合 Tavily 搜索：搜索 → 发现网页 → 爬取完整内容。\n"
            "注册地址：https://firecrawl.org.cn/，免费额度 500页/月。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #AEB8C8;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        key_row = QHBoxLayout()
        self._fc_key_edit = QLineEdit()
        self._fc_key_edit.setPlaceholderText("fc-xxxxxxxxxxxxxxxxxxxxxxxx")
        self._fc_key_edit.setEchoMode(QLineEdit.Password)
        self._fc_key_edit.setFont(QFont("Consolas", 10))
        self._fc_key_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #334155; border-radius: 6px;
                padding: 6px 10px; background: #0F1724; color: #E6EDF7;
            }
            QLineEdit:focus { border-color: #6C7BFF; }
        """)
        key_row.addWidget(self._fc_key_edit)

        self._show_fc_btn = QPushButton("显示")
        self._show_fc_btn.setFixedSize(60, 34)
        self._show_fc_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._show_fc_btn.setCursor(Qt.PointingHandCursor)
        self._show_fc_btn.setCheckable(True)
        self._show_fc_btn.clicked.connect(self._toggle_fc_key_visibility)
        key_row.addWidget(self._show_fc_btn)
        form.addRow("Firecrawl API Key：", key_row)

        layout.addLayout(form)
        layout.addStretch()

    def _toggle_fc_key_visibility(self, checked: bool):
        self._fc_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_fc_btn.setText("隐藏" if checked else "显示")

    # ── 知乎全搜索 ────────────────────────────────────────
    def _build_tab_zhihu(self, parent: QWidget):
        layout = QVBoxLayout(self._prepare_scroll_tab(parent))
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = QLabel("🎓 知乎全网搜索配置")
        title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        title.setStyleSheet("color: #E8EAF2;")
        layout.addWidget(title)

        desc = QLabel(
            "接入知乎开放平台 MCP 服务，支持全站搜索和实时热榜。\n"
            "配置后莲心可以直接搜索知乎最新内容、热门话题。\n"
            "注册地址：https://developer.zhihu.com/，在个人中心获取 Access Secret。"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #AEB8C8;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        key_row = QHBoxLayout()
        self._zhihu_key_edit = QLineEdit()
        self._zhihu_key_edit.setPlaceholderText("你的知乎开放平台 Access Secret")
        self._zhihu_key_edit.setEchoMode(QLineEdit.Password)
        self._zhihu_key_edit.setFont(QFont("Consolas", 10))
        self._zhihu_key_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #334155; border-radius: 6px;
                padding: 6px 10px; background: #0F1724; color: #E6EDF7;
            }
            QLineEdit:focus { border-color: #6C7BFF; }
        """)
        key_row.addWidget(self._zhihu_key_edit)

        self._show_zh_btn = QPushButton("显示")
        self._show_zh_btn.setFixedSize(60, 34)
        self._show_zh_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._show_zh_btn.setCursor(Qt.PointingHandCursor)
        self._show_zh_btn.setCheckable(True)
        self._show_zh_btn.clicked.connect(self._toggle_zh_key_visibility)
        key_row.addWidget(self._show_zh_btn)
        form.addRow("知乎 Access Secret：", key_row)

        layout.addLayout(form)
        layout.addStretch()

    def _toggle_zh_key_visibility(self, checked: bool):
        self._zhihu_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_zh_btn.setText("隐藏" if checked else "显示")

    # ── 工具调用顺序 ──────────────────────────────────────
    def _build_tab_fallback(self, parent: QWidget):
        layout = QVBoxLayout(self._prepare_scroll_tab(parent))
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        desc = QLabel(
            "从上到下就是实际优先级。拖动整行排序；取消勾选即禁止该工具参与联网路由。\n"
            "搜索来源负责找信息；网页读取仅在已有 URL 时将正文转换为可阅读内容。"
        )
        desc.setWordWrap(True)
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #C8D2E2; background: #1B2435; padding: 8px; border-radius: 4px; border: 1px solid #334155;")
        layout.addWidget(desc)

        search_group = QGroupBox("搜索来源（关键词搜索）")
        search_layout = QVBoxLayout(search_group)
        self._search_order_list = self._build_order_list("search")
        search_layout.addWidget(self._search_order_list)
        layout.addWidget(search_group)

        fetch_group = QGroupBox("网页读取（已有 URL 后）")
        fetch_layout = QVBoxLayout(fetch_group)
        self._fetch_order_list = self._build_order_list("fetch")
        fetch_layout.addWidget(self._fetch_order_list)
        layout.addWidget(fetch_group)

        restore_row = QHBoxLayout()
        restore_row.addStretch()
        restore_btn = QPushButton("恢复默认排序")
        restore_btn.setToolTip("恢复推荐顺序和默认启用状态，不会清除 API Key、代理或 Cookie。")
        restore_btn.clicked.connect(self._restore_network_defaults)
        restore_row.addWidget(restore_btn)
        layout.addLayout(restore_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(6)

        self._search_max_retries_spin = QSpinBox()
        self._search_max_retries_spin.setRange(0, 5)
        self._search_max_retries_spin.setSuffix(" 次")
        form.addRow("临时失败重试次数：", self._search_max_retries_spin)

        self._search_auto_fallback_check = QCheckBox("启用")
        form.addRow("额度不足自动换下一个：", self._search_auto_fallback_check)

        layout.addLayout(form)
        layout.addStretch()

    def _build_order_list(self, kind: str):
        widget = QListWidget()
        widget.setStyleSheet("""
            QListWidget {
                background: #0F1724;
                color: #FFFFFF;
                border: 1px solid #334155;
                border-radius: 6px;
            }
            QListWidget::item { padding: 5px 6px; color: #FFFFFF; }
            QListWidget::item:selected {
                background: #2F6F62;
                color: #FFFFFF;
            }
        """)
        widget.setObjectName(f"network_{kind}_order")
        widget.setDragDropMode(QAbstractItemView.InternalMove)
        widget.setDefaultDropAction(Qt.MoveAction)
        widget.setSelectionMode(QAbstractItemView.SingleSelection)
        widget.setMinimumHeight(116)
        widget.setToolTip("拖动整行调整优先级；勾选表示允许该工具参与联网路由。")
        return widget

    @staticmethod
    def _list_order(widget: QListWidget):
        return [widget.item(index).data(Qt.UserRole) for index in range(widget.count())]

    def _load_order_list(self, widget: QListWidget, kind: str):
        from brain.network_router import tool_catalog
        cfg = get_network_tool_order_config()
        catalog = {item["id"]: item for item in tool_catalog(kind)}
        ids = [item for item in cfg.get(f"{kind}_order", []) if item in catalog]
        ids += [item for item in catalog if item not in ids]
        widget.clear()
        for tool_id in ids:
            info = catalog[tool_id]
            item = QListWidgetItem(f"{info['name']}  ·  {info['state']}")
            item.setData(Qt.UserRole, tool_id)
            item.setToolTip(info["description"] + "\n当前状态：" + info["state"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            item.setCheckState(Qt.Checked if info["checked"] else Qt.Unchecked)
            widget.addItem(item)

    def _restore_network_defaults(self):
        from brain.network_router import FETCH_IDS, SEARCH_IDS, set_tool_enabled
        for tool_id in (*SEARCH_IDS, *FETCH_IDS):
            set_tool_enabled(tool_id, True)
        save_network_tool_order_config({
            "search_order": list(SEARCH_IDS), "fetch_order": list(FETCH_IDS),
            "retry_count": 2, "fallback_on_quota": True,
        })
        self._load_config()

    # ── 代理设置 ─────────────────────────────────────────
    def _build_tab_proxy(self, parent: QWidget):
        layout = QVBoxLayout(self._prepare_scroll_tab(parent))
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        self._proxy_enabled_cb = QCheckBox("启用智能代理（先直连，不通自动切代理）")
        self._proxy_enabled_cb.setFont(QFont("Microsoft YaHei UI", 9))
        self._proxy_enabled_cb.setCursor(Qt.PointingHandCursor)
        self._proxy_enabled_cb.setToolTip(
            "开启后，莲心的所有网络请求会先尝试直连，只有直连不通时才会走代理。"
        )
        layout.addWidget(self._proxy_enabled_cb)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        http_row = QHBoxLayout()
        self._proxy_http_edit = QLineEdit()
        self._proxy_http_edit.setPlaceholderText("http://127.0.0.1:7890")
        self._proxy_http_edit.setToolTip("Clash 默认 http://127.0.0.1:7890")
        http_row.addWidget(self._proxy_http_edit, 1)
        form.addRow("HTTP 代理地址：", http_row)

        https_row = QHBoxLayout()
        self._proxy_https_edit = QLineEdit()
        self._proxy_https_edit.setPlaceholderText("http://127.0.0.1:7890")
        self._proxy_https_edit.setToolTip("通常与 HTTP 相同")
        https_row.addWidget(self._proxy_https_edit, 1)
        form.addRow("HTTPS 代理地址：", https_row)

        noproxy_row = QHBoxLayout()
        self._proxy_noproxy_edit = QLineEdit()
        self._proxy_noproxy_edit.setPlaceholderText("localhost,127.0.0.1")
        self._proxy_noproxy_edit.setToolTip("不走代理的地址，多个逗号分隔")
        noproxy_row.addWidget(self._proxy_noproxy_edit, 1)
        form.addRow("直连白名单：", noproxy_row)

        layout.addLayout(form)

        tip = QLabel(
            "💡 Clash 默认代理地址为 http://127.0.0.1:7890。\n"
            "如果代理工具使用不同端口，请在上方修改。\n"
            "\n"
            "🔍 智能代理策略：\n"
            "· 国内网站（百度等）→ 直连，速度不受影响\n"
            "· 国外网站（GitHub 等）→ 先直连，能通则通，不通自动切代理\n"
            "· 代理失败时不再尝试直连，避免反复超时"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #AEB8C8; font-size: 12px; padding: 8px;")
        layout.addWidget(tip)
        layout.addStretch()

    # ── B站账号 ─────────────────────────────────────────
    def _build_tab_bilibili(self, parent: QWidget):
        layout = QVBoxLayout(self._prepare_scroll_tab(parent))
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        section_title = QLabel("📺 B站账号 Cookie 配置")
        section_title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        section_title.setStyleSheet("color: #E8EAF2;")
        layout.addWidget(section_title)

        desc = QLabel(
            "配置 B站登录 Cookie 后，莲心就能提取视频的 AI 自动字幕，\n"
            "生成带时间戳的结构化视频摘要。\n"
            "获取方式：浏览器登录 B站 → F12 → Application → Cookies → bilibili.com"
        )
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #AEB8C8;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # SESSDATA
        sess_row = QHBoxLayout()
        self._bl_sess_edit = QLineEdit()
        self._bl_sess_edit.setPlaceholderText("从浏览器 Cookies 中复制 SESSDATA 的值")
        self._bl_sess_edit.setEchoMode(QLineEdit.Password)
        self._bl_sess_edit.setFont(QFont("Consolas", 10))
        self._bl_sess_edit.setStyleSheet("""
            QLineEdit { border: 1px solid #334155; border-radius: 6px;
                padding: 6px 10px; background: #0F1724; color: #E6EDF7; }
            QLineEdit:focus { border-color: #6C7BFF; }
        """)
        sess_row.addWidget(self._bl_sess_edit)

        self._show_bl_sess_btn = QPushButton("显示")
        self._show_bl_sess_btn.setFixedSize(60, 34)
        self._show_bl_sess_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._show_bl_sess_btn.setCursor(Qt.PointingHandCursor)
        self._show_bl_sess_btn.setCheckable(True)
        self._show_bl_sess_btn.clicked.connect(self._toggle_bl_sess_visibility)
        sess_row.addWidget(self._show_bl_sess_btn)
        form.addRow("SESSDATA：", sess_row)

        # bili_jct
        jct_row = QHBoxLayout()
        self._bl_jct_edit = QLineEdit()
        self._bl_jct_edit.setPlaceholderText("从浏览器 Cookies 中复制 bili_jct 的值")
        self._bl_jct_edit.setEchoMode(QLineEdit.Password)
        self._bl_jct_edit.setFont(QFont("Consolas", 10))
        self._bl_jct_edit.setStyleSheet("""
            QLineEdit { border: 1px solid #334155; border-radius: 6px;
                padding: 6px 10px; background: #0F1724; color: #E6EDF7; }
            QLineEdit:focus { border-color: #6C7BFF; }
        """)
        jct_row.addWidget(self._bl_jct_edit)

        self._show_bl_jct_btn = QPushButton("显示")
        self._show_bl_jct_btn.setFixedSize(60, 34)
        self._show_bl_jct_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._show_bl_jct_btn.setCursor(Qt.PointingHandCursor)
        self._show_bl_jct_btn.setCheckable(True)
        self._show_bl_jct_btn.clicked.connect(self._toggle_bl_jct_visibility)
        jct_row.addWidget(self._show_bl_jct_btn)
        form.addRow("bili_jct：", jct_row)

        layout.addLayout(form)
        layout.addSpacing(8)

        help_text = QLabel(
            "💡 提示：\n"
            "· SESSDATA 是 B站登录的核心凭证，有效期约 30 天，过期需重新获取\n"
            "· Cookie 保存在本地用户数据目录（user_config.json），不会上传到 Git\n"
            "· 未配置 Cookie 时，B站视频摘要功能只能返回视频基本信息，无法提取字幕"
        )
        help_text.setFont(QFont("Microsoft YaHei UI", 8))
        help_text.setStyleSheet("color: #C8D2E2; background-color: #1B2435; padding: 8px; border-radius: 6px; border: 1px solid #334155;")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addStretch()

    def _toggle_bl_sess_visibility(self, checked: bool):
        self._bl_sess_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_bl_sess_btn.setText("隐藏" if checked else "显示")

    def _toggle_bl_jct_visibility(self, checked: bool):
        self._bl_jct_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_bl_jct_btn.setText("隐藏" if checked else "显示")

    # ── 重试与回退 ────────────────────────────────────────
    def _load_config(self):
        tv_cfg = get_tavily_config()
        self._tv_key_edit.setText(tv_cfg.get("api_key", ""))

        fc_cfg = get_firecrawl_config()
        self._fc_key_edit.setText(fc_cfg.get("api_key", ""))

        zh_cfg = get_zhihu_config()
        self._zhihu_key_edit.setText(zh_cfg.get("access_secret", ""))

        cfg = get_network_tool_order_config()
        self._search_max_retries_spin.setValue(cfg.get("retry_count", 2))

        proxy_cfg = get_proxy_config()
        self._proxy_enabled_cb.setChecked(proxy_cfg.get("enabled", False))
        self._proxy_http_edit.setText(proxy_cfg.get("http_proxy", ""))
        self._proxy_https_edit.setText(proxy_cfg.get("https_proxy", ""))
        self._proxy_noproxy_edit.setText(proxy_cfg.get("no_proxy", ""))
        self._search_auto_fallback_check.setChecked(cfg.get("fallback_on_quota", True))
        self._load_order_list(self._search_order_list, "search")
        self._load_order_list(self._fetch_order_list, "fetch")

        bl_cfg = get_bilibili_config()
        self._bl_sess_edit.setText(bl_cfg.get("sessdata", ""))
        self._bl_jct_edit.setText(bl_cfg.get("bili_jct", ""))

        bl_cfg = get_bilibili_config()
        self._bl_sess_edit.setText(bl_cfg.get("sessdata", ""))
        self._bl_jct_edit.setText(bl_cfg.get("bili_jct", ""))

    def _on_save(self):
        save_tavily_config({"api_key": self._tv_key_edit.text().strip()})
        save_firecrawl_config({"api_key": self._fc_key_edit.text().strip()})
        save_zhihu_config({"access_secret": self._zhihu_key_edit.text().strip()})
        from brain.network_router import set_tool_enabled
        for widget in (self._search_order_list, self._fetch_order_list):
            for index in range(widget.count()):
                item = widget.item(index)
                set_tool_enabled(item.data(Qt.UserRole), item.checkState() == Qt.Checked)
        save_network_tool_order_config({
            "search_order": self._list_order(self._search_order_list),
            "fetch_order": self._list_order(self._fetch_order_list),
            "retry_count": self._search_max_retries_spin.value(),
            "fallback_on_quota": self._search_auto_fallback_check.isChecked(),
        })
        save_proxy_config({
            "enabled":     self._proxy_enabled_cb.isChecked(),
            "http_proxy":  self._proxy_http_edit.text().strip(),
            "https_proxy": self._proxy_https_edit.text().strip(),
            "no_proxy":    self._proxy_noproxy_edit.text().strip(),
        })

        save_bilibili_config({
            "sessdata":  self._bl_sess_edit.text().strip(),
            "bili_jct":  self._bl_jct_edit.text().strip(),
        })

        save_bilibili_config({
            "sessdata":  self._bl_sess_edit.text().strip(),
            "bili_jct":  self._bl_jct_edit.text().strip(),
        })

        self.config_saved.emit()
        self.accept()
