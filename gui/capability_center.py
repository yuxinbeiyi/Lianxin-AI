# gui/capability_center.py
"""能力中枢 — 浏览和管理莲心的 Skills 与 MCP 服务"""

from datetime import datetime
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QPushButton, QScrollArea, QFrame, QLineEdit, QTextEdit,
    QCheckBox, QGridLayout, QSizePolicy, QComboBox, QMessageBox,
    QFileDialog,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont


class CapabilityCenter(QDialog):
    tool_requested = pyqtSignal(str, str)
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🧩 能力中枢")
        self.setMinimumSize(820, 620)
        self.resize(960, 700)
        self.setStyleSheet("""
            QDialog {
                background-color: #FFFFFF;
            }
            QWidget {
                background-color: #FFFFFF;
                color: #2C2C2C;
            }
            QTabWidget::pane {
                background: #FFFFFF;
                border: none;
            }
            QTabBar::tab {
                background: #F0F0F5;
                border: 1px solid #D0D0D8;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 8px 18px;
                margin-right: 4px;
                color: #2C2C2C;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                font-weight: bold;
                color: #2C2C2C;
            }
            QFrame {
                background: #FFFFFF;
                border: 1px solid #E0E0E8;
                border-radius: 10px;
            }
            QScrollArea {
                background: #FFFFFF;
                border: none;
            }
            QLabel {
                background: transparent;
                color: #2C2C2C;
            }
            QLineEdit {
                background: #FFFFFF;
                color: #2C2C2C;
                border: 1px solid #D0D0E0;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QLineEdit:focus {
                border-color: #6C7BFF;
            }
        """)
        self._last_refresh = datetime.now()
        self._skill_seen = set()
        self._mcp_seen = set()
        self._build_ui()
        self.refresh_requested.connect(self._refresh_all)
        self._ensure_initialized()
        self._refresh_all()

    def _ensure_initialized(self):
        # 兼容新版经 legacy_ui_launcher 独立进程打开：补齐技能激活与 MCP 扫描。
        # 旧版由 main.py 启动时执行，子进程需要在这里补齐，否则 Skills 0/N、MCP 0/0。
        try:
            from brain.skill_manager import discover_skills, activate_all_skills
            discover_skills()
            activate_all_skills()
        except Exception as exc:
            print("[能力中心] Skills 初始化失败: %s" % exc)

        def _scan_mcp():
            try:
                from brain.mcp.mcp_manager import get_mcp_manager
                mgr = get_mcp_manager()
                mgr.initialize()
                if getattr(mgr, "_scan_ready", None) is not None:
                    mgr._scan_ready.wait(45)
            except Exception as exc:
                print("[能力中心] MCP 初始化失败: %s" % exc)
            finally:
                try:
                    self.refresh_requested.emit()
                except Exception:
                    pass

        import threading
        threading.Thread(target=_scan_mcp, daemon=True).start()


    def closeEvent(self, event):
        """关闭窗口时自动保存技能和 MCP 配置"""
        try:
            from brain.skill_manager import save_skill_config
            save_skill_config()
        except Exception:
            pass
        try:
            from brain.mcp.mcp_registry import save_mcp_config
            save_mcp_config()
        except Exception:
            pass
        super().closeEvent(event)

    # ── UI 构建 ─────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(10)

        # 标题栏 + 刷新按钮
        header = QHBoxLayout()
        title = QLabel("🧩 能力中枢")
        title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
        title.setStyleSheet("color: #2C2C2C;")
        header.addWidget(title)
        header.addStretch()

        self._refresh_label = QLabel()
        self._refresh_label.setFont(QFont("Microsoft YaHei UI", 8))
        self._refresh_label.setStyleSheet("color: #555;")
        header.addWidget(self._refresh_label)

        self._refresh_btn = QPushButton("🔄 刷新")
        self._refresh_btn.setFixedSize(72, 28)
        self._refresh_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #2F6F62; color: #FFFFFF;
                border-radius: 6px; border: 1px solid #24584E;
            }
            QPushButton:hover { background-color: #3C8273; }
        """)
        self._refresh_btn.clicked.connect(self._on_refresh)
        header.addWidget(self._refresh_btn)
        self._browser_debug_btn = QPushButton("🌐 浏览器日志")
        self._browser_debug_btn.setFixedSize(112, 28)
        self._browser_debug_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self._browser_debug_btn.setCursor(Qt.PointingHandCursor)
        self._browser_debug_btn.clicked.connect(self._open_browser_debug)
        header.addWidget(self._browser_debug_btn)
        root.addLayout(header)

        # 统计卡片
        self._stats_widget = QWidget()
        root.addWidget(self._stats_widget)

        # 搜索栏
        search_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍 搜索技能或工具...")
        self._search_edit.setFont(QFont("Microsoft YaHei UI", 10))
        self._search_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D0D0E0; border-radius: 8px;
                padding: 6px 12px; background: #F6F7F8; color: #2C2C2C;
            }
            QLineEdit:focus { border-color: #6C7BFF; }
        """)
        self._search_edit.textChanged.connect(self._on_search)
        search_row.addWidget(self._search_edit)
        root.addLayout(search_row)

        # 选项卡：用户先看能力，再看使用情况；技术来源统一放到扩展管理。
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("")

        self._tool_tab = QWidget()
        tool_wrapper = QVBoxLayout(self._tool_tab)
        tool_wrapper.setContentsMargins(0, 0, 0, 0)
        library_filters = QHBoxLayout()
        self._category_filter = QComboBox()
        self._category_filter.addItem("全部分类")
        self._source_filter = QComboBox()
        self._source_filter.addItems(["全部来源", "莲心内置", "Skills", "MCP"])
        self._category_filter.currentTextChanged.connect(self._load_tools)
        self._source_filter.currentTextChanged.connect(self._load_tools)
        library_filters.addWidget(self._category_filter)
        library_filters.addWidget(self._source_filter)
        library_filters.addStretch()
        tool_wrapper.addLayout(library_filters)
        self._tool_scroll = self._make_scroll_area()
        self._tool_layout = QVBoxLayout(self._tool_scroll.widget())
        self._tool_layout.setContentsMargins(0, 0, 0, 0)
        self._tool_layout.setSpacing(6)
        self._tool_layout.addStretch()
        tool_wrapper.addWidget(self._tool_scroll)
        self._tabs.addTab(self._tool_tab, "能力库")

        self._usage_tab = QWidget()
        self._build_usage_tab()
        self._tabs.addTab(self._usage_tab, "使用情况")

        self._extensions_tab = QWidget()
        extensions_layout = QVBoxLayout(self._extensions_tab)
        extensions_layout.setContentsMargins(0, 0, 0, 0)
        self._extension_tabs = QTabWidget()

        self._skill_tab = QWidget()
        self._skill_scroll = self._make_scroll_area()
        self._skill_layout = QVBoxLayout(self._skill_scroll.widget())
        self._skill_layout.setContentsMargins(0, 0, 0, 0)
        self._skill_layout.setSpacing(8)
        self._skill_layout.addStretch()
        skill_wrapper = QVBoxLayout(self._skill_tab)
        skill_wrapper.setContentsMargins(0, 0, 0, 0)
        skill_wrapper.addWidget(self._skill_scroll)
        self._extension_tabs.addTab(self._skill_tab, "Skills")

        self._mcp_tab = QWidget()
        self._mcp_scroll = self._make_scroll_area()
        self._mcp_layout = QVBoxLayout(self._mcp_scroll.widget())
        self._mcp_layout.setContentsMargins(0, 0, 0, 0)
        self._mcp_layout.setSpacing(8)
        self._mcp_layout.addStretch()
        mcp_wrapper = QVBoxLayout(self._mcp_tab)
        mcp_wrapper.setContentsMargins(0, 0, 0, 0)
        mcp_wrapper.addWidget(self._mcp_scroll)
        self._extension_tabs.addTab(self._mcp_tab, "MCP 服务")

        self._install_tab = QWidget()
        self._build_install_tab()
        self._extension_tabs.addTab(self._install_tab, "安装插件")

        self._log_tab = QWidget()
        self._build_log_tab()
        self._extension_tabs.addTab(self._log_tab, "日志")
        extensions_layout.addWidget(self._extension_tabs)
        self._tabs.addTab(self._extensions_tab, "扩展管理")

        root.addWidget(self._tabs)

    def _make_scroll_area(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        scroll.setWidget(container)
        return scroll

    def _build_usage_tab(self):
        layout = QVBoxLayout(self._usage_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self._usage_range = QComboBox()
        self._usage_range.addItems(["今天", "近 7 天", "近 30 天", "全部时间"])
        self._usage_range.setCurrentText("近 30 天")
        self._usage_range.currentTextChanged.connect(self._load_usage)
        controls.addWidget(self._usage_range)
        controls.addStretch()
        reset_btn = QPushButton("清空统计")
        reset_btn.clicked.connect(self._reset_usage)
        controls.addWidget(reset_btn)
        layout.addLayout(controls)

        self._usage_overview = QLabel()
        self._usage_overview.setWordWrap(True)
        self._usage_overview.setStyleSheet(
            "background: #F3F6F5; border: 1px solid #D9E2DF; border-radius: 6px; padding: 10px;"
        )
        layout.addWidget(self._usage_overview)
        self._usage_scroll = self._make_scroll_area()
        self._usage_layout = QVBoxLayout(self._usage_scroll.widget())
        self._usage_layout.setContentsMargins(0, 0, 0, 0)
        self._usage_layout.setSpacing(5)
        self._usage_layout.addStretch()
        layout.addWidget(self._usage_scroll)

    def _load_usage(self, *_):
        if not hasattr(self, "_usage_layout"):
            return
        while self._usage_layout.count() > 1:
            item = self._usage_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        ranges = {"今天": 1, "近 7 天": 7, "近 30 天": 30, "全部时间": None}
        days = ranges.get(self._usage_range.currentText(), 30)
        from brain.capability_catalog import list_capabilities
        from brain.tool_usage import get_tool_usage_store
        store = get_tool_usage_store()
        capabilities = {item.name: item for item in list_capabilities()}
        summaries = store.summaries(capabilities, days=days)
        overview = store.overview(days=days)
        unused = sum(1 for item in summaries.values() if not item.call_count)
        self._usage_overview.setText(
            f"调用 {overview['call_count']} 次    "
            f"成功率 {overview['success_rate'] * 100:.0f}%    "
            f"平均耗时 {overview['avg_duration_ms']:.0f}ms    "
            f"使用过 {overview['used_tool_count']} 项    从未使用 {unused} 项"
        )
        used = sorted(
            (item for item in summaries.values() if item.call_count),
            key=lambda item: (-item.call_count, item.tool_name),
        )
        if not used:
            empty = QLabel("这个时间范围内还没有工具调用。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #8A9299; padding: 36px;")
            self._usage_layout.insertWidget(self._usage_layout.count() - 1, empty)
            return
        for summary in used:
            descriptor = capabilities.get(summary.tool_name)
            display = descriptor.display_name if descriptor else summary.tool_name
            provider = descriptor.provider_name if descriptor else "未知来源"
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background: #FAFBFC; border: 1px solid #DDE2E7; border-radius: 5px; }"
            )
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 7, 10, 7)
            label = QLabel(f"{display}\n{summary.tool_name} · {provider}")
            label.setStyleSheet("color: #30363B;")
            row_layout.addWidget(label, 1)
            stats = QLabel(
                f"{summary.call_count} 次  ·  {summary.success_rate * 100:.0f}%  ·  "
                f"{summary.avg_duration_ms:.0f}ms"
            )
            stats.setStyleSheet("color: #5C6770;")
            row_layout.addWidget(stats)
            self._usage_layout.insertWidget(self._usage_layout.count() - 1, row)

    def _reset_usage(self):
        answer = QMessageBox.question(
            self, "清空工具统计", "确定清空本地保存的全部工具使用统计吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        from brain.tool_usage import get_tool_usage_store
        get_tool_usage_store().reset()
        self._load_usage()
        self._load_tools()

    # ── 统计卡片 ────────────────────────────────────────

    def _build_stats(self, skill_count, active_count, mcp_count, mcp_connected, tool_count):
        # 清空旧布局
        old_layout = self._stats_widget.layout()
        if old_layout:
            while old_layout.count():
                item = old_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            del old_layout

        layout = QGridLayout(self._stats_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._stat_labels = {}
        self._stat_cards = {}

        cards = [
            ("skill", f"{active_count}/{skill_count}", "📦 Skills", "#6C7BFF",
             "已激活", "未激活"),
            ("mcp",   f"{mcp_connected}/{mcp_count}", "⚡ MCP", "#E67E22",
             "已连接", "未连接"),
            ("tool",  f"{tool_count}", "🔧 工具", "#8E44AD",
             "", ""),
        ]
        for i, (key, num, label, color, on_label, off_label) in enumerate(cards):
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: #F6F7F8; border: 1px solid #DDE2E7;
                    border-radius: 10px;
                }
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(2)
            num_lbl = QLabel(num)
            num_lbl.setFont(QFont("Segoe UI", 20, QFont.Bold))
            num_lbl.setStyleSheet(f"color: {color};")
            num_lbl.setAlignment(Qt.AlignCenter)
            card_layout.addWidget(num_lbl)
            lbl = QLabel(label)
            lbl.setFont(QFont("Microsoft YaHei UI", 9))
            lbl.setStyleSheet("color: #555;")
            lbl.setAlignment(Qt.AlignCenter)
            card_layout.addWidget(lbl)
            layout.addWidget(card, 0, i)

            self._stat_labels[key] = num_lbl
            self._stat_cards[key] = {"card": card, "on_label": on_label, "off_label": off_label}

    # ── 卡片组件 ────────────────────────────────────────

    def _make_card(self, icon, name, description, version, status_text, status_color,
                   tool_count, tools, extra_label="", extra_color="#888"):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #F6F7F8; border: 1px solid #DDE2E7;
                border-radius: 10px;
            }
        """)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(6)

        # 第一行：图标 + 名称 + 状态
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        icon_lbl = QLabel(icon)
        icon_lbl.setFont(QFont("Segoe UI Emoji", 16))
        row1.addWidget(icon_lbl)

        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        name_lbl.setStyleSheet("color: #2C2C2C;")
        row1.addWidget(name_lbl)

        if version:
            ver = QLabel(f"v{version}")
            ver.setFont(QFont("Microsoft YaHei UI", 8))
            ver.setStyleSheet("color: #888;")
            row1.addWidget(ver)

        row1.addStretch()

        status = QLabel(status_text)
        status.setFont(QFont("Microsoft YaHei UI", 9))
        status.setStyleSheet(f"color: {status_color}; font-weight: bold;")
        row1.addWidget(status)
        layout.addLayout(row1)

        # 描述
        desc = QLabel(description)
        desc.setFont(QFont("Microsoft YaHei UI", 9))
        desc.setStyleSheet("color: #555;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 状态标签
        if extra_label:
            extra = QLabel(extra_label)
            extra.setFont(QFont("Microsoft YaHei UI", 8))
            extra.setStyleSheet(f"color: {extra_color};")
            extra.setWordWrap(True)
            layout.addWidget(extra)

        # 工具行
        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        tool_badge = QLabel(f"▸ {tool_count} 个工具")
        tool_badge.setFont(QFont("Microsoft YaHei UI", 9))
        tool_badge.setStyleSheet("color: #2C2C2C;")
        tool_badge.setCursor(Qt.PointingHandCursor)
        tool_row.addWidget(tool_badge)
        tool_row.addStretch()

        layout.addLayout(tool_row)

        # 展开的工具列表
        tool_list = QWidget()
        tool_list.setVisible(False)
        tool_list_layout = QVBoxLayout(tool_list)
        tool_list_layout.setContentsMargins(8, 4, 0, 4)
        tool_list_layout.setSpacing(4)
        for t in tools:
            tool_item = self._make_tool_item(t)
            tool_list_layout.addWidget(tool_item)
        layout.addWidget(tool_list)

        # 点击展开/折叠
        tool_badge.mousePressEvent = lambda e: tool_list.setVisible(not tool_list.isVisible())

        return card, tool_badge

    def _make_tool_item(self, tool_info):
        """单个工具条目：名称 + 描述 + 可展开参数"""
        name = tool_info.get("name", "?")
        desc = tool_info.get("description", "")
        params = tool_info.get("parameters", {})

        frame = QFrame()
        frame.setStyleSheet("background: #F6F7F8; border-radius: 6px; padding: 4px; border: 1px solid #DDE2E7;")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(8, 4, 8, 4)
        fl.setSpacing(2)

        name_lbl = QLabel(f"🔹 {name}")
        name_lbl.setFont(QFont("Consolas", 9, QFont.Bold))
        name_lbl.setStyleSheet("color: #2C2C2C;")
        fl.addWidget(name_lbl)

        if desc:
            d = QLabel(desc)
            d.setFont(QFont("Microsoft YaHei UI", 8))
            d.setStyleSheet("color: #888;")
            d.setWordWrap(True)
            fl.addWidget(d)

        if params:
            params_widget = QWidget()
            params_widget.setVisible(False)
            pl = QVBoxLayout(params_widget)
            pl.setContentsMargins(12, 2, 0, 2)
            pl.setSpacing(2)
            for pname, pinfo in params.items():
                ptype = pinfo.get("type", "string")
                pdesc = pinfo.get("description", "")
                pl.addWidget(QLabel(f"  {pname}: {ptype} — {pdesc}"))
            fl.addWidget(params_widget)

            name_lbl.setCursor(Qt.PointingHandCursor)
            name_lbl.mousePressEvent = lambda e, w=params_widget: w.setVisible(not w.isVisible())

        return frame

    # ── 刷新逻辑 ────────────────────────────────────────

    def _refresh_all(self):
        self._global_log_msg("🔄 开始刷新...", "info")
        self._load_skills()
        self._load_mcp()
        self._load_tools()
        self._update_refresh_time()
        self._rebuild_stats()
        self._on_search("")
        self._global_log_msg("✅ 刷新完成", "ok")

    def _on_refresh(self):
        self._refresh_btn.setText("⏳ 刷新中...")
        self._refresh_btn.setEnabled(False)
        QTimer.singleShot(100, self._do_refresh)

    def _open_browser_debug(self):
        try:
            from gui.browser_debug_dialog import BrowserDebugDialog
            dialog = BrowserDebugDialog(self)
            dialog.setAttribute(Qt.WA_DeleteOnClose, True)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            self._browser_debug_dialog = dialog
        except Exception as exc:
            self._global_log_msg(f"打开浏览器调试面板失败：{exc}", "err")

    def _do_refresh(self):
        try:
            from brain.skill_manager import discover_skills, activate_all_skills
            discover_skills()
            activate_all_skills()
        except Exception as e:
            print(f"[能力中心] Skills 刷新失败: {e}")

        try:
            from brain.mcp.mcp_registry import unload_all, scan_mcp_services
            unload_all()
            scan_mcp_services()
        except Exception as e:
            print(f"[能力中心] MCP 刷新失败: {e}")

        self._last_refresh = datetime.now()
        try:
            from brain.capability_knowledge import invalidate_capability_knowledge_cache
            invalidate_capability_knowledge_cache()
        except Exception:
            pass
        self._refresh_all()
        self._refresh_btn.setText("🔄 刷新")
        self._refresh_btn.setEnabled(True)

    def _update_refresh_time(self):
        ts = self._last_refresh.strftime("%H:%M:%S")
        self._refresh_label.setText(f"上次刷新：{ts}")

    # ── Skills 加载 ─────────────────────────────────────

    def _load_skills(self):
        # 清空旧卡片
        layout = self._skill_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from brain.skill_manager import _skill_registry, _active_skills
        skills = list(_skill_registry.values())

        for skill in skills:
            name = skill["name"]
            active = name in _active_skills
            if name == "浏览器自动化":
                try:
                    from config import get_browser_config
                    active = active and bool(get_browser_config().get("enabled", True))
                except Exception:
                    pass
            tools = skill.get("tool_definitions", [])
            tool_count = len(tools)
            icon = self._skill_icon(name)
            status_text = "✅ 已激活" if active else "⏸ 未激活"
            status_color = "#27AE60" if active else "#AAA"

            # 提取工具信息
            tool_infos = []
            for td in tools:
                fn = td.get("function", {})
                tool_infos.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}).get("properties", {}),
                })

            license_text = skill.get("license", "")
            extra = f"📜 {license_text}" if license_text else ""
            card, badge = self._make_card(
                icon, name, skill.get("description", ""),
                skill.get("version", ""), status_text, status_color,
                tool_count, tool_infos,
                extra_label=extra, extra_color="#888",
            )
            card.setProperty("skill_name", name)
            card.setProperty("card_type", "skill")

            # 操作按钮行
            action_row = QHBoxLayout()
            action_row.setSpacing(8)
            toggle_btn = QPushButton("\u23f8 \u505c\u7528" if active else "\u25b6 \u542f\u7528")
            toggle_btn.setFixedSize(72, 26)
            toggle_btn.setFont(QFont("Microsoft YaHei UI", 9))
            toggle_btn.setCursor(Qt.PointingHandCursor)
            if active:
                toggle_btn.setStyleSheet("QPushButton{background:#FFE0B2;color:#E65100;border-radius:6px;border:1px solid #FFB74D;}QPushButton:hover{background:#FFCC80;}")
            else:
                toggle_btn.setStyleSheet("QPushButton{background:#C8E6C9;color:#2E7D32;border-radius:6px;border:1px solid #A5D6A7;}QPushButton:hover{background:#A5D6A7;}")
            toggle_btn.clicked.connect(lambda checked, n=name: self._on_toggle_skill(n))
            action_row.addWidget(toggle_btn)

            uninstall_btn = QPushButton("\U0001f5d1\ufe0f \u5378\u8f7d")
            uninstall_btn.setFixedSize(60, 26)
            uninstall_btn.setFont(QFont("Microsoft YaHei UI", 9))
            uninstall_btn.setCursor(Qt.PointingHandCursor)
            uninstall_btn.setStyleSheet("QPushButton{background:#FFCDD2;color:#C62828;border-radius:6px;border:1px solid #EF9A9A;}QPushButton:hover{background:#EF9A9A;}")
            uninstall_btn.clicked.connect(lambda checked, n=name: self._on_uninstall_skill(n))
            action_row.addWidget(uninstall_btn)
            action_row.addStretch()
            card.layout().addLayout(action_row)

            layout.insertWidget(layout.count() - 1, card)

        if not skills:
            empty = QLabel("暂无技能。\n将 SKILL.md 放入 skills/ 目录即可自动发现。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #888; padding: 40px;")
            layout.insertWidget(layout.count() - 1, empty)

    def _skill_icon(self, name):
        icons = {
            "日记与备忘": "📔", "浏览器自动化": "🌐", "肩部外设控制": "📷",
            "音乐播放控制": "🎵", "系统信息工具": "💻", "语音合成": "🔊",
            "学习助手": "📚",
        }
        return icons.get(name, "📦")

    # ── MCP 加载 ────────────────────────────────────────

    def _load_mcp(self):
        layout = self._mcp_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from brain.mcp.mcp_registry import MCP_REGISTRY, MANIFEST_CACHE

        for sname, agent in MCP_REGISTRY.items():
            manifest = MANIFEST_CACHE.get(sname, {})
            display = manifest.get("displayName", sname)
            desc = manifest.get("description", agent.description if hasattr(agent, "description") else "")
            agent_type = manifest.get("agentType", "local")
            icon = getattr(agent, "icon", "⚡") if hasattr(agent, "icon") else "⚡"

            # 连接状态
            connected = getattr(agent, "_connected", True)
            if agent_type == "external":
                status_text = "🟢 已连接" if connected else "🔴 未连接"
                status_color = "#27AE60" if connected else "#E74C3C"
            else:
                status_text = "✅ 本地运行"
                status_color = "#27AE60"

            # 工具
            tools = getattr(agent, "_tools", [])
            tool_count = len(tools)
            tool_infos = []
            for t in tools:
                if hasattr(t, "name"):
                    tool_infos.append({
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    })
                elif isinstance(t, dict):
                    tool_infos.append({
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {}).get("properties", t.get("parameters", {})),
                    })

            extra = ""
            extra_color = "#888"
            if agent_type == "external":
                extra = f"类型：外部 MCP（{agent_type}）"
            elif agent_type == "local":
                extra = f"类型：本地 MCP"

            card, badge = self._make_card(
                icon, display, desc, manifest.get("version", ""),
                status_text, status_color, tool_count, tool_infos,
                extra, extra_color,
            )
            card.setProperty("mcp_name", sname)
            card.setProperty("card_type", "mcp")

            # 操作按钮
            action_row = QHBoxLayout()
            action_row.setSpacing(8)
            from brain.mcp.mcp_registry import is_mcp_enabled
            enabled = is_mcp_enabled(sname)
            toggle_btn = QPushButton("⏸ 停用" if enabled else "▶ 启用")
            toggle_btn.setFixedSize(72, 26)
            toggle_btn.setFont(QFont("Microsoft YaHei UI", 9))
            toggle_btn.setCursor(Qt.PointingHandCursor)
            if enabled:
                toggle_btn.setStyleSheet("QPushButton{background:#FFE0B2;color:#E65100;border-radius:6px;border:1px solid #FFB74D;}QPushButton:hover{background:#FFCC80;}")
            else:
                toggle_btn.setStyleSheet("QPushButton{background:#C8E6C9;color:#2E7D32;border-radius:6px;border:1px solid #A5D6A7;}QPushButton:hover{background:#A5D6A7;}")
            toggle_btn.clicked.connect(lambda checked, n=sname: self._on_toggle_mcp(n))
            action_row.addWidget(toggle_btn)

            test_btn = QPushButton("🔌 测试")
            test_btn.setFixedSize(56, 26)
            test_btn.setFont(QFont("Microsoft YaHei UI", 9))
            test_btn.setCursor(Qt.PointingHandCursor)
            test_btn.setStyleSheet("QPushButton{background:#E3F2FD;color:#1565C0;border-radius:6px;border:1px solid #90CAF9;}QPushButton:hover{background:#BBDEFB;}")
            test_btn.clicked.connect(lambda checked, n=sname: self._on_test_mcp(n))
            action_row.addWidget(test_btn)

            uninstall_btn = QPushButton("\U0001f5d1\ufe0f \u5378\u8f7d")
            uninstall_btn.setFixedSize(60, 26)
            uninstall_btn.setFont(QFont("Microsoft YaHei UI", 9))
            uninstall_btn.setCursor(Qt.PointingHandCursor)
            uninstall_btn.setStyleSheet("QPushButton{background:#FFCDD2;color:#C62828;border-radius:6px;border:1px solid #EF9A9A;}QPushButton:hover{background:#EF9A9A;}")
            uninstall_btn.clicked.connect(lambda checked, n=sname: self._on_uninstall_mcp(n))
            action_row.addWidget(uninstall_btn)
            action_row.addStretch()
            card.layout().addLayout(action_row)

            layout.insertWidget(layout.count() - 1, card)

        if not MCP_REGISTRY:
            empty = QLabel("暂无 MCP 服务。\n将 mcp-manifest.json 放入 mcp_servers/ 目录即可自动发现。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #AAA; padding: 40px;")
            layout.insertWidget(layout.count() - 1, empty)

    # ── 内置工具加载 ─────────────────────────────────────

    def _load_tools(self, *_):
        layout = self._tool_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from brain.capability_catalog import CATEGORY_ORDER, list_capabilities
        from brain.tool_usage import get_tool_usage_store

        capabilities = list_capabilities()
        summaries = get_tool_usage_store().summaries(item.name for item in capabilities)
        categories = [cat for cat in CATEGORY_ORDER if any(item.category == cat for item in capabilities)]
        current_category = self._category_filter.currentText()
        if self._category_filter.count() != len(categories) + 1:
            self._category_filter.blockSignals(True)
            self._category_filter.clear()
            self._category_filter.addItem("全部分类")
            self._category_filter.addItems(categories)
            index = self._category_filter.findText(current_category)
            self._category_filter.setCurrentIndex(max(0, index))
            self._category_filter.blockSignals(False)

        category_filter = self._category_filter.currentText()
        source_filter = self._source_filter.currentText()
        if category_filter != "全部分类":
            capabilities = [item for item in capabilities if item.category == category_filter]
        if source_filter == "莲心内置":
            capabilities = [item for item in capabilities if item.source_kind == "builtin"]
        elif source_filter == "Skills":
            capabilities = [item for item in capabilities if item.source_kind == "skill"]
        elif source_filter == "MCP":
            capabilities = [item for item in capabilities if item.source_kind == "mcp"]

        if not capabilities:
            empty = QLabel("当前筛选条件下没有能力。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #AAA; padding: 40px;")
            layout.insertWidget(layout.count() - 1, empty)
            return

        for cat in categories:
            tools = [item for item in capabilities if item.category == cat]
            if not tools:
                continue
            cat_label = QLabel(cat)
            cat_label.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
            cat_label.setStyleSheet("color: #4A4A6A; padding: 8px 4px 2px 4px;")
            cat_label.setProperty("card_type", "capability_category")
            cat_label.setProperty("tool_names", " ".join(
                f"{item.name} {item.display_name} {item.description}" for item in tools
            ))
            layout.insertWidget(layout.count() - 1, cat_label)

            for descriptor in tools:
                card = self._make_tool_card(descriptor, summaries[descriptor.name])
                layout.insertWidget(layout.count() - 1, card)

    def _make_tool_card(self, descriptor, summary):
        """Create a compact capability row with status, usage and chat handoff."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #FAFBFC; border: 1px solid #DDE2E7;
                border-radius: 6px;
            }
        """)
        card.setProperty("card_type", "builtin_tool")
        card.setProperty("tool_name", descriptor.name)
        card.setProperty("search_text", descriptor.searchable_text)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        name_lbl = QLabel(descriptor.display_name)
        name_lbl.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        top.addWidget(name_lbl)
        tech = QLabel(descriptor.name)
        tech.setFont(QFont("Consolas", 8))
        tech.setStyleSheet("color: #737A82;")
        top.addWidget(tech)
        top.addStretch()
        status_color = "#267A55" if descriptor.available and descriptor.enabled else "#A4493D"
        status = QLabel(f"{descriptor.status} · {descriptor.provider_name}")
        status.setStyleSheet(f"color: {status_color};")
        top.addWidget(status)
        layout.addLayout(top)

        desc = QLabel(descriptor.description or "暂无说明")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4D555D;")
        layout.addWidget(desc)

        bottom = QHBoxLayout()
        rate = summary.success_rate * 100
        metrics = QLabel(
            f"调用 {summary.call_count} 次"
            + (f" · 成功率 {rate:.0f}% · 平均 {summary.avg_duration_ms:.0f}ms" if summary.call_count else "")
        )
        metrics.setStyleSheet("color: #737A82;")
        bottom.addWidget(metrics)
        bottom.addStretch()
        favorite = QPushButton("★" if descriptor.favorite else "☆")
        favorite.setFixedSize(30, 26)
        favorite.setToolTip("收藏或取消收藏")
        favorite.clicked.connect(lambda _=False, name=descriptor.name: self._toggle_tool_favorite(name))
        bottom.addWidget(favorite)
        toggle_btn = QPushButton("停用" if descriptor.enabled else "启用")
        toggle_btn.setToolTip(
            "切换此内置工具" if descriptor.source_kind == "builtin" else "切换整个能力来源"
        )
        toggle_btn.clicked.connect(
            lambda _=False, item=descriptor: self._toggle_capability(item)
        )
        bottom.addWidget(toggle_btn)
        use_btn = QPushButton("在对话中使用")
        use_btn.setEnabled(descriptor.available and descriptor.enabled)
        use_btn.clicked.connect(
            lambda _=False, name=descriptor.name: self._request_tool_for_chat(name)
        )
        bottom.addWidget(use_btn)
        layout.addLayout(bottom)

        return card

    def _toggle_tool_favorite(self, name: str):
        from brain.capability_catalog import toggle_favorite
        toggle_favorite(name)
        self._load_tools()
        self._load_usage()

    def _request_tool_for_chat(self, name: str):
        self.tool_requested.emit(name, "preferred")
        self.hide()

    def _toggle_capability(self, descriptor):
        if descriptor.source_kind == "builtin":
            from config import get_builtin_tool_config, save_builtin_tool_config
            config = get_builtin_tool_config()
            config[descriptor.name] = not descriptor.enabled
            save_builtin_tool_config(config)
        elif descriptor.source_kind == "mcp":
            from brain.mcp.mcp_registry import is_mcp_enabled, toggle_mcp_enabled
            if is_mcp_enabled(descriptor.provider_id) == descriptor.enabled:
                toggle_mcp_enabled(descriptor.provider_id)
        elif descriptor.source_kind == "skill":
            from brain.skill_manager import activate_skill, deactivate_skill, save_skill_config
            if descriptor.enabled:
                deactivate_skill(descriptor.provider_id)
            else:
                activate_skill(descriptor.provider_id)
            save_skill_config()
        self._refresh_all()

    def _rebuild_stats(self, mcp_connected=0):
        from brain.skill_manager import _skill_registry, _active_skills
        from brain.mcp.mcp_registry import MCP_REGISTRY, is_mcp_enabled

        skill_count = len(_skill_registry)
        active_count = len(_active_skills)
        mcp_count = len(MCP_REGISTRY)
        mcp_enabled_count = sum(1 for n in MCP_REGISTRY if is_mcp_enabled(n))

        from brain.capability_catalog import list_capabilities
        capabilities = list_capabilities()
        tool_count = len(capabilities)

        if not hasattr(self, "_stat_labels") or not self._stat_labels:
            self._build_stats(skill_count, active_count, mcp_count, mcp_enabled_count, tool_count)
            return

        self._stat_labels["skill"].setText(f"{active_count}/{skill_count}")
        self._stat_labels["mcp"].setText(f"{mcp_enabled_count}/{mcp_count}")
        self._stat_labels["tool"].setText(f"{tool_count}")
        available_count = sum(1 for item in capabilities if item.enabled and item.available)
        self._stat_cards["tool"]["card"].setToolTip(
            f"可用 {available_count}/{tool_count}\n包含莲心内置、Skills 与 MCP 工具"
        )

        active_names = [n for n in _active_skills]
        inactive_names = [n for n in _skill_registry if n not in _active_skills]
        active_str = "、".join(active_names) if active_names else "(无)"
        inactive_str = "、".join(inactive_names) if inactive_names else "(无)"
        self._stat_cards["skill"]["card"].setToolTip(
            f"━━ 已激活 ({active_count}/{skill_count}) ━━\n{active_str}\n\n"
            f"━━ 未激活 ({skill_count - active_count}/{skill_count}) ━━\n{inactive_str}"
        )

        mcp_on = [n for n in MCP_REGISTRY if is_mcp_enabled(n)]
        mcp_off = [n for n in MCP_REGISTRY if not is_mcp_enabled(n)]
        mcp_on_str = "、".join(mcp_on) if mcp_on else "(无)"
        mcp_off_str = "、".join(mcp_off) if mcp_off else "(无)"
        self._stat_cards["mcp"]["card"].setToolTip(
            f"━━ 已连接 ({mcp_enabled_count}/{mcp_count}) ━━\n{mcp_on_str}\n\n"
            f"━━ 未连接 ({mcp_count - mcp_enabled_count}/{mcp_count}) ━━\n{mcp_off_str}"
        )

    # ── 搜索过滤 ────────────────────────────────────────

    # ── 安装插件选项卡 ────────────────────────────────

    def _build_install_tab(self):
        layout = QVBoxLayout(self._install_tab)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 说明文字
        hint = QLabel("将社区 Skills 或 MCP 服务文件夹拖入下方，或点击浏览选择文件夹，系统将自动识别并安装。")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        hint.setStyleSheet("color: #555; padding: 4px 0;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 路径选择行
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self._install_path = QLineEdit()
        self._install_path.setPlaceholderText("选择要安装的插件文件夹...")
        self._install_path.setFont(QFont("Microsoft YaHei UI", 10))
        self._install_path.setStyleSheet("""
            QLineEdit {
                border: 2px dashed #D0D0E0; border-radius: 8px;
                padding: 10px 14px; background: #F6F7F8; color: #2C2C2C;
            }
            QLineEdit:focus { border-color: #6C7BFF; }
        """)
        self._install_path.textChanged.connect(self._on_install_path_changed)
        path_row.addWidget(self._install_path)

        browse_btn = QPushButton("📂 浏览")
        browse_btn.setFixedSize(80, 40)
        browse_btn.setFont(QFont("Microsoft YaHei UI", 10))
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF; color: #FFF;
                border-radius: 8px; border: none;
            }
            QPushButton:hover { background-color: #5A6AE0; }
        """)
        browse_btn.clicked.connect(self._on_browse_folder)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        # 检测结果卡片
        self._detect_card = QFrame()
        self._detect_card.setStyleSheet("""
            QFrame {
                background: #F8F8FF; border: 1px solid #E0E0F0;
                border-radius: 10px;
            }
        """)
        self._detect_card.setVisible(False)
        detect_layout = QVBoxLayout(self._detect_card)
        detect_layout.setContentsMargins(16, 12, 16, 12)
        detect_layout.setSpacing(6)

        self._detect_type = QLabel()
        self._detect_type.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        detect_layout.addWidget(self._detect_type)

        self._detect_name = QLabel()
        self._detect_name.setFont(QFont("Microsoft YaHei UI", 10))
        detect_layout.addWidget(self._detect_name)

        self._detect_desc = QLabel()
        self._detect_desc.setFont(QFont("Microsoft YaHei UI", 9))
        self._detect_desc.setStyleSheet("color: #555;")
        self._detect_desc.setWordWrap(True)
        detect_layout.addWidget(self._detect_desc)

        self._install_btn = QPushButton("⬇️ 一键安装")
        self._install_btn.setFixedHeight(36)
        self._install_btn.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        self._install_btn.setCursor(Qt.PointingHandCursor)
        self._install_btn.setStyleSheet("""
            QPushButton {
                background-color: #27AE60; color: #FFF;
                border-radius: 8px; border: none;
            }
            QPushButton:hover { background-color: #219A52; }
            QPushButton:disabled { background-color: #CCC; }
        """)
        self._install_btn.clicked.connect(self._on_install)
        detect_layout.addWidget(self._install_btn)

        layout.addWidget(self._detect_card)

        # 状态日志
        self._install_log = QTextEdit()
        self._install_log.setReadOnly(True)
        self._install_log.setFont(QFont("Consolas", 9))
        self._install_log.setMaximumHeight(120)
        self._install_log.setStyleSheet("""
            QTextEdit {
                background: #F6F7F8; border: 1px solid #DDE2E7;
                border-radius: 8px; padding: 8px; color: #2C2C2C;
            }
        """)
        self._install_log.setPlaceholderText("安装日志将在此显示...")
        layout.addWidget(self._install_log)

        layout.addStretch()

        self._pending_source = None

    def _build_log_tab(self):
        layout = QVBoxLayout(self._log_tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("📋 调试日志")
        title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        header.addWidget(title)
        header.addStretch()

        clear_btn = QPushButton("🗑️ 清空")
        clear_btn.setFixedSize(60, 24)
        clear_btn.setFont(QFont("Microsoft YaHei UI", 9))
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet("QPushButton{background:#EEE;color:#555;border-radius:4px;border:1px solid #CCC;}QPushButton:hover{background:#DDD;}")
        clear_btn.clicked.connect(lambda: self._global_log.clear())
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self._global_log = QTextEdit()
        self._global_log.setReadOnly(True)
        self._global_log.setFont(QFont("Consolas", 9))
        self._global_log.setStyleSheet("""
            QTextEdit {
                background: #1E1E2E; color: #CDD6F4;
                border: 1px solid #313244; border-radius: 8px;
                padding: 8px;
            }
        """)
        self._global_log.setPlaceholderText("技能和 MCP 的加载日志将在此显示...")
        layout.addWidget(self._global_log)

    def _global_log_msg(self, msg, level="info"):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"info": "#CDD6F4", "ok": "#A6E3A1", "warn": "#F9E2AF", "err": "#F38BA8"}
        color = colors.get(level, "#CDD6F4")
        if hasattr(self, "_global_log"):
            self._global_log.append(f'<span style="color:#89B4FA;">[{ts}]</span> <span style="color:{color};">{msg}</span>')

    def _on_browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择插件文件夹")
        if folder:
            self._install_path.setText(folder)

    def _on_install_path_changed(self, text):
        self._detect_card.setVisible(False)
        self._pending_source = None
        if not text.strip():
            return

        try:
            from brain.plugin_installer import get_plugin_info
            info = get_plugin_info(text.strip())
        except Exception as e:
            self._log(f"❌ 检测失败: {e}")
            return

        ptype = info.get("type", "unknown")
        if ptype == "unknown":
            self._detect_type.setText("❌ 无法识别")
            self._detect_type.setStyleSheet("color: #E74C3C;")
            self._detect_name.setText("未找到 SKILL.md 或 mcp-manifest.json")
            self._detect_desc.setText("")
            self._install_btn.setEnabled(False)
            self._detect_card.setVisible(True)
            return

        if ptype == "skill":
            self._detect_type.setText("📦 检测到技能")
            self._detect_type.setStyleSheet("color: #6C7BFF;")
        else:
            self._detect_type.setText("⚡ 检测到 MCP 服务")
            self._detect_type.setStyleSheet("color: #E67E22;")

        self._detect_name.setText(f"名称: {info.get('name', '?')}")
        desc = info.get("description", "")
        ver = info.get("version", "")
        self._detect_desc.setText(f"{desc}\n版本: {ver}" if ver else desc)
        self._install_btn.setEnabled(True)
        self._detect_card.setVisible(True)
        self._pending_source = info.get("_source_path", text.strip())

    def _on_install(self):
        if not self._pending_source:
            return

        self._install_btn.setEnabled(False)
        self._install_btn.setText("⏳ 安装中...")

        try:
            from brain.plugin_installer import install_plugin
            success, msg = install_plugin(self._pending_source)
        except Exception as e:
            success, msg = False, f"安装异常: {e}"

        self._log(msg)
        self._install_btn.setText("⬇️ 一键安装")
        self._install_btn.setEnabled(True) if not success else None

        if success:
            self._detect_card.setVisible(False)
            self._install_path.clear()
            self._pending_source = None
            self._global_log_msg("⚡ 热加载完成，无需重启莲心即可使用新插件", "ok")
            self._refresh_all()

    def _log(self, msg, level="info"):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._install_log.append(f"[{ts}] {msg}")
        self._global_log_msg(msg, level)

    def _on_toggle_skill(self, name):
        from brain.skill_manager import _active_skills, activate_skill, deactivate_skill, save_skill_config
        browser_enabled = True
        if name == "浏览器自动化":
            try:
                from config import get_browser_config
                browser_enabled = bool(get_browser_config().get("enabled", True))
            except Exception:
                browser_enabled = True
        currently_active = name in _active_skills and browser_enabled
        if currently_active:
            deactivate_skill(name)
            self._log(f"⏸ 技能「{name}」已停用", "warn")
        else:
            activate_skill(name)
            self._log(f"▶ 技能「{name}」已启用", "ok")
        save_skill_config()
        if name == "浏览器自动化":
            try:
                from config import get_browser_config, save_browser_config
                browser_cfg = get_browser_config()
                browser_cfg["enabled"] = name in _active_skills
                save_browser_config(browser_cfg)
                self._log(
                    "🌐 浏览器能力开关已同步：" + ("开启" if browser_cfg["enabled"] else "关闭"),
                    "ok" if browser_cfg["enabled"] else "warn",
                )
            except Exception as exc:
                self._log(f"浏览器能力开关同步失败：{exc}", "err")
        self._refresh_all()

    def _on_uninstall_skill(self, name):
        reply = QMessageBox.question(self, "确认卸载",
            f"确定要卸载技能「{name}」吗？\n\n这将永久删除技能目录，不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        from brain.skill_manager import uninstall_skill
        success, msg = uninstall_skill(name)
        self._log(msg)
        self._refresh_all()

    def _on_uninstall_mcp(self, name):
        reply = QMessageBox.question(self, "确认卸载",
            f"确定要卸载 MCP 服务「{name}」吗？\n\n这将永久删除服务目录，不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        from brain.plugin_installer import uninstall_plugin
        success, msg = uninstall_plugin(name, "mcp")
        self._log(msg)
        self._refresh_all()

    def _on_toggle_mcp(self, name):
        from brain.mcp.mcp_registry import toggle_mcp_enabled
        new_state = toggle_mcp_enabled(name)
        if new_state:
            self._log(f"▶ MCP「{name}」已启用", "ok")
        else:
            self._log(f"⏸ MCP「{name}」已停用，工具定义不再注入 API", "warn")
        self._refresh_all()

    def _on_test_mcp(self, name):
        from brain.mcp.mcp_registry import MCP_REGISTRY
        self._log(f"🔌 正在测试 MCP「{name}」...", "info")
        agent = MCP_REGISTRY.get(name)
        if not agent:
            self._log(f"❌ MCP「{name}」未在注册表中找到", "err")
            return
        try:
            if hasattr(agent, "list_tools"):
                tools = agent.list_tools()
                tool_count = len(tools) if isinstance(tools, list) else len(list(tools))
                self._log(f"✅ MCP「{name}」连接正常，提供 {tool_count} 个工具", "ok")
            elif hasattr(agent, "get_tool_definitions_openai"):
                tools = agent.get_tool_definitions_openai()
                self._log(f"✅ MCP「{name}」连接正常，提供 {len(tools)} 个工具定义", "ok")
            elif hasattr(agent, "_tools"):
                self._log(f"✅ MCP「{name}」已加载，{len(agent._tools)} 个工具就绪", "ok")
            else:
                self._log(f"⚠️ MCP「{name}」已加载但无法获取工具列表", "warn")
        except Exception as e:
            self._log(f"❌ MCP「{name}」测试失败: {e}", "err")

    def _on_search(self, text):
        kw = text.strip().lower()

        for i in range(self._skill_layout.count()):
            item = self._skill_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if w.property("card_type") == "skill":
                    name = w.property("skill_name") or ""
                    w.setVisible(kw in name.lower() if kw else True)

        for i in range(self._mcp_layout.count()):
            item = self._mcp_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if w.property("card_type") == "mcp":
                    name = w.property("mcp_name") or ""
                    w.setVisible(kw in name.lower() if kw else True)

        for i in range(self._tool_layout.count()):
            item = self._tool_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                ct = w.property("card_type") or ""
                if ct == "capability_category":
                    names = w.property("tool_names") or ""
                    visible = kw in names.lower() if kw else True
                    w.setVisible(visible)
                elif ct == "builtin_tool":
                    search_text = w.property("search_text") or w.property("tool_name") or ""
                    w.setVisible(kw in search_text.lower() if kw else True)
