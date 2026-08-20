"""
InputPanel：底部悬浮输入舱
上下两层：上方消息输入区 + 下方功能工具栏
工具栏严格顺序：工具箱 → 附件 → 语音输入 → 自动发送 → 发送
支持拖拽/粘贴图片（发送图片给莲心进行OCR识别）
"""

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QTextEdit, QCheckBox,
    QSizePolicy, QApplication, QDialog, QListWidget, QListWidgetItem,
    QLineEdit, QMenu, QAction, QTreeWidget, QTreeWidgetItem, QStackedWidget,
    QLabel, QButtonGroup, QComboBox, QFrame, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QPoint, QSize, QRectF, QPointF
from PyQt5.QtGui import (QFont, QKeyEvent, QDragEnterEvent, QDropEvent, QColor,
                         QPixmap, QPalette, QIcon, QPainter, QPainterPath, QPen)
import tempfile
import os
import json
from pathlib import Path
from brain.capability_catalog import (
    list_capabilities, load_favorites as _load_catalog_favorites,
    save_favorites as _save_catalog_favorites, toggle_favorite,
)
from utils.paths import get_user_data_dir




try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# 常用工具配置文件路径
FAVORITES_FILE = get_user_data_dir() / "favorite_tools.json"


def load_favorites():
    """加载收藏的工具列表"""
    return _load_catalog_favorites()


def save_favorites(favorites):
    """保存收藏的工具列表"""
    _save_catalog_favorites(favorites)


class _InputBox(QTextEdit):
    """支持 Enter 发送、Shift+Enter 换行的输入框。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)

    enter_pressed = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.enter_pressed.emit()
        else:
            super().keyPressEvent(event)


# ==================== 工具分组映射 ====================
TOOL_GROUP_MAP = {
    # 文件操作类
    "read_file": "📁 文件操作",
    "read_file_chunk": "📁 文件操作",
    "list_directory": "📁 文件操作",
    "search_files": "📁 文件操作",
    "read_excel": "📁 文件操作",
    "write_file": "📁 文件操作",
    "write_docx": "📁 文件操作",
    "format_document": "📁 文件操作",
    "write_excel": "📁 文件操作",
    "copy_excel_content": "📁 文件操作",
    "glob_files": "📁 文件操作",
    "grep_file": "📁 文件操作",
    "read_file_lines": "📁 文件操作",
    "edit_file": "📁 文件操作",
    "search_code": "🔍 代码搜索",
    "diff_files": "📁 文件操作",
    "run_shell": "💻 系统命令",
    "git_status": "🔧 开发工具",
    "code_structure": "🔍 代码搜索",
    "plan_tasks": "🧩 任务分解",
    "delegate_task": "🧩 任务分解",
    "track_tasks": "📋 任务追踪",
    "code_goto_def": "🔍 代码搜索",
    "code_find_refs": "🔍 代码搜索",
    "code_diagnostics": "🔍 代码搜索",

    "describe_image": "🔍 视觉理解",
    # 系统命令类
    "open_app": "💻 系统命令",
    "run_command": "💻 系统命令",
    "get_clipboard": "💻 系统命令",
    "run_python_code": "💻 系统命令",
    # 联网搜索类
    "web_search": "🌐 联网搜索",
    "fetch_webpage": "🌐 联网搜索",
    "fetch_webpage_browser": "🌐 联网搜索",
    "fetch_webpage_via_api": "🌐 联网搜索",
    # 信息查询类
    "get_current_time": "📅 信息查询",
    "get_balance": "📅 信息查询",
    # 记忆与任务类
    "save_memory": "🧠 记忆与任务",
    "add_todo": "🧠 记忆与任务",
    "list_todos": "🧠 记忆与任务",
    "complete_todo": "🧠 记忆与任务",
    

}

# ==================== 工具别名映射（用于搜索） ====================
TOOL_ALIASES = {
    # 文件操作别名
    "read_file": ["读文件", "打开文件", "查看文件", "读取文件", "文件内容"],
    "list_directory": ["列目录", "查看文件夹", "目录内容", "列出文件"],
    "search_files": ["搜索文件", "查找文件", "找文件"],
    "write_file": ["写文件", "保存文件", "写入文件"],
    # 系统命令别名
    "open_app": ["打开程序", "启动应用", "运行软件", "打开软件"],
    "run_command": ["执行命令", "运行命令", "cmd命令"],
    "get_clipboard": ["剪贴板", "粘贴板", "复制内容"],
    # 联网搜索别名
    "web_search": ["搜索", "查一下", "百度", "谷歌", "联网查", "网上搜"],
    "fetch_webpage": ["获取网页", "抓取网页", "网页内容"],
    # 信息查询别名
    "get_current_time": ["现在时间", "几点了", "今天日期", "农历", "节假日"],
    "get_balance": ["余额", "查余额", "账户余额"],
    # 记忆与任务
    "save_memory": ["记住", "记一下", "保存记忆"],
    "add_todo": ["添加待办", "记任务", "提醒我", "设置提醒"],
    "list_todos": ["待办列表", "查看待办", "有什么任务"],
    "complete_todo": ["完成待办", "任务完成", "做完了"],
    # 编程工具别名
    "edit_file": ["修改文件", "替换", "编辑代码", "改代码"],
    "search_code": ["搜索代码", "查找代码", "代码搜索", "正则搜索"],
    "diff_files": ["对比文件", "文件差异", "diff"],
    "run_shell": ["执行shell", "命令行", "终端"],
    "git_status": ["git状态", "查看git", "版本控制"],
    "code_structure": ["代码结构", "函数列表", "类定义"],
    "plan_tasks": ["分解任务", "任务规划", "拆分任务"],
    "delegate_task": ["委派任务", "子代理", "并行执行"],
    "track_tasks": ["任务进度", "追踪任务", "更新任务", "任务清单"],
    "code_goto_def": ["跳转定义", "查看定义", "在哪定义", "函数定义"],
    "code_find_refs": ["查找引用", "谁调用了", "哪用了", "引用位置"],
    "code_diagnostics": ["检查代码", "代码诊断", "语法检查", "错误检查"],



}


DEFAULT_GROUP = "🔧 其他（音乐盒+时间胶囊+备忘本+OCR相机）"


def get_tool_group(tool_name: str) -> str:
    return TOOL_GROUP_MAP.get(tool_name, DEFAULT_GROUP)


class ToolSelectionDialog(QDialog):
    """工具选择弹窗（支持分组显示、滚动、搜索、收藏）"""
    tool_selected = pyqtSignal(object)
    manage_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_tool = None
        self.selected_mode = "preferred"
        self.favorites = load_favorites()
        self.expanded_groups = set()  # 记录展开的分组名称
        self.setWindowTitle("递给莲心")
        self.setModal(True)
        self.setMinimumWidth(450)
        self.setMinimumHeight(500)
        self.resize(480, 550)

        # 样式表（包含QMenu样式，解决右键菜单文字消失）
        self.setStyleSheet("""
            QDialog {
                background-color: #12201E;
                border-radius: 8px;
            }
            QListWidget, QTreeWidget {
                background-color: #142421;
                color: #DCEFE8;
                border: 1px solid #416B63;
                border-radius: 6px;
                padding: 4px;
                outline: none;
                font-size: 10pt;
                font-family: "Microsoft YaHei UI";
            }
            QListWidget::item, QTreeWidget::item {
                padding: 8px 12px;
                border-bottom: 1px solid rgba(117, 184, 168, 45);
            }
            QListWidget::item:selected, QTreeWidget::item:selected {
                background-color: #347767;
                color: white;
            }
            QTreeWidget::item:hover, QListWidget::item:hover {
                background-color: #2A5148;
                color: #FFFFFF;
            }
            QTreeWidget::item:selected:hover, QListWidget::item:selected:hover {
                background-color: #3E8A73;
                color: white;
            }
            QMenu {
                background-color: #142421;
                border: 1px solid #416B63;
                border-radius: 4px;
                padding: 4px;
            }
            QMenu::item {
                background-color: transparent;
                color: #DCEFE8;
                padding: 6px 24px 6px 12px;
                margin: 2px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #347767;
                color: #FFFFFF;
            }
            QPushButton {
                background-color: #254D43;
                color: #E7FFF6;
                border: 1px solid #5C9D8B;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: #347261;
            }
            QPushButton:checked {
                background-color: #3E8A73;
                border: 2px solid #9AD8C7;
                color: #FFFFFF;
            }
            QPushButton#cancel_btn {
                background-color: #3D3D5A;
                color: #E0E0E0;
            }
            QPushButton#cancel_btn:hover {
                background-color: #4D4D6A;
            }
            QLineEdit {
                background-color: #0F1B1A;
                color: #E7FFF6;
                border: 1px solid #416B63;
                border-radius: 6px;
                padding: 8px 10px;
                font-size: 10pt;
            }
            QLineEdit:focus {
                border: 1px solid #83CDB8;
            }
            QCheckBox {
                color: #C8DED7;
                font-size: 10pt;
                spacing: 6px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # 搜索框
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 搜索工具...")
        self.search_edit.textChanged.connect(self._refresh_ui)
        layout.addWidget(self.search_edit)

        # 选项区域
        opts_layout = QHBoxLayout()
        self.quick_view = QComboBox()
        self.quick_view.addItems(["推荐", "最近", "收藏", "全部"])
        self.quick_view.currentTextChanged.connect(self._refresh_ui)
        opts_layout.addWidget(self.quick_view)
        self.fav_only_cb = QCheckBox("⭐ 仅显示收藏（右键收藏工具）")
        self.fav_only_cb.stateChanged.connect(self._refresh_ui)
        self.group_cb = QCheckBox("📁 分组显示")
        self.group_cb.setChecked(True)
        self.group_cb.stateChanged.connect(self._refresh_ui)
        opts_layout.addWidget(self.fav_only_cb)
        opts_layout.addWidget(self.group_cb)
        opts_layout.addStretch()
        layout.addLayout(opts_layout)

        # 堆叠视图：0 = 分组树形视图，1 = 扁平列表视图
        self.stacked = QStackedWidget()
        self.tool_tree = QTreeWidget()
        self.tool_tree.setHeaderHidden(True)
        self.tool_tree.setIndentation(20)
        self.tool_tree.itemDoubleClicked.connect(self._on_tree_item_activated)
        self.tool_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tool_tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tool_tree.setIndentation(20)

        self.tool_list_flat = QListWidget()
        self.tool_list_flat.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.tool_list_flat.setUniformItemSizes(True)
        self.tool_list_flat.itemDoubleClicked.connect(self._on_flat_item_activated)
        self.tool_list_flat.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tool_list_flat.customContextMenuRequested.connect(self._show_flat_context_menu)

        self.stacked.addWidget(self.tool_tree)      # index 0
        self.stacked.addWidget(self.tool_list_flat) # index 1
        layout.addWidget(self.stacked)

        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        for mode, label in (("preferred", "建议使用"), ("forced", "强制使用")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(mode == "preferred")
            button.clicked.connect(lambda checked, value=mode: self._set_mode(value))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            btn_layout.addWidget(button)
        self.auto_btn = QPushButton("⚙️ 无工具（自动）")
        self.auto_btn.clicked.connect(lambda: self._select_tool(None, "auto"))
        btn_layout.addWidget(self.auto_btn)
        manage_btn = QPushButton("能力中枢")
        manage_btn.clicked.connect(self._open_capability_center)
        btn_layout.addWidget(manage_btn)
        btn_layout.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        # 加载工具数据
        self._load_tools()

        # 初始刷新
        self._refresh_ui()

    def _load_tools(self):
        """Load the unified built-in, Skill and MCP capability catalog."""
        self.all_tools = [{
            "name": item.name,
            "display_name": item.display_name,
            "description": item.description,
            "category": item.category,
            "source": item.provider_name,
            "source_kind": item.source_kind,
            "status": item.status,
            "available": item.available and item.enabled,
            "search_text": item.searchable_text,
            "is_favorite": item.favorite,
        } for item in list_capabilities()]
        from brain.tool_usage import get_tool_usage_store
        self.recent_tool_names = set(get_tool_usage_store().recent_tool_names(16))
        self.recommended_tool_names = set()
        user_text = self.parent()._input.toPlainText() if self.parent() and hasattr(self.parent(), "_input") else ""
        if user_text.strip():
            try:
                from brain.tool_router import CATEGORY_TOOLS, match_categories
                for category in match_categories(user_text):
                    self.recommended_tool_names.update(CATEGORY_TOOLS.get(category, set()))
            except Exception:
                pass
        if not self.recommended_tool_names:
            self.recommended_tool_names = set(self.favorites) | set(self.recent_tool_names)
        if not self.recommended_tool_names:
            self.recommended_tool_names = {
                "web_search", "read_file", "search_files_everything", "get_current_time",
                "get_weather", "add_todo", "describe_image", "open_app",
            }
        # 排序：收藏的在前，然后按名称排序（用于扁平列表）
        self.all_tools.sort(key=lambda x: (not x["is_favorite"], x["name"]))

    def _included_in_quick_view(self, tool: dict) -> bool:
        view = self.quick_view.currentText()
        if view == "收藏":
            return tool["is_favorite"]
        if view == "最近":
            return tool["name"] in self.recent_tool_names
        if view == "推荐":
            return tool["name"] in self.recommended_tool_names
        return True

        # ---------- 分组树形视图 ----------
    def _build_group_tree(self):
        """构建分组树形视图（两列布局：工具名绿色加粗，描述灰色）"""
        self.tool_tree.clear()
        self.tool_tree.setHeaderLabels(["工具名称", "描述"])
        self.tool_tree.header().setVisible(False)
        self.tool_tree.setColumnWidth(0, 350)
        self.tool_tree.setIndentation(20)

        keyword = self.search_edit.text().strip().lower()
        show_fav_only = self.fav_only_cb.isChecked()

        def matches_keyword(tool_name, tool_desc, kw):
            if kw in tool_name.lower() or kw in tool_desc.lower():
                return True
            aliases = TOOL_ALIASES.get(tool_name, [])
            for alias in aliases:
                if kw in alias.lower():
                    return True
            return False

        groups = {}
        for tool in self.all_tools:
            if not self._included_in_quick_view(tool):
                continue
            if show_fav_only and not tool["is_favorite"]:
                continue
            if (keyword and keyword not in tool["search_text"]
                    and not matches_keyword(tool["name"], tool["description"], keyword)):
                continue
            group_name = tool["category"]
            groups.setdefault(group_name, []).append(tool)

        for group_name in sorted(groups.keys()):
            group_item = QTreeWidgetItem([group_name, ""])
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsSelectable)
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            group_item.setForeground(0, Qt.gray)
            self.tool_tree.addTopLevelItem(group_item)

            for tool in groups[group_name]:
                star = "⭐ " if tool["is_favorite"] else "   "
                state = "" if tool["available"] else f" · {tool['status']}"
                tool_name_display = f"{star}{tool['display_name']}{state}"
                desc = f"{tool['source']} · {tool['description']}"[:80]
                if len(tool["description"]) > 80:
                    desc += "..."

                child = QTreeWidgetItem([tool_name_display, desc])
                child.setData(0, Qt.UserRole, tool["name"])
                child.setData(0, Qt.UserRole + 1, tool["is_favorite"])
                child.setToolTip(0, tool["description"])
                child.setToolTip(1, tool["description"])
                child.setForeground(0, QColor(46, 125, 50))
                font = child.font(0)
                font.setBold(True)
                child.setFont(0, font)
                child.setForeground(1, QColor(85, 85, 85))
                group_item.addChild(child)
            
            # 恢复展开状态
            is_expanded = group_name in self.expanded_groups
            group_item.setExpanded(is_expanded)

        # 保存当前展开状态
        self.expanded_groups.clear()
        for i in range(self.tool_tree.topLevelItemCount()):
            item = self.tool_tree.topLevelItem(i)
            if item.isExpanded():
                self.expanded_groups.add(item.text(0))

    # ---------- 扁平列表视图 ----------
    def _build_flat_list(self):
        """构建扁平列表（根据搜索和收藏过滤）"""
        self.tool_list_flat.clear()
        keyword = self.search_edit.text().strip().lower()
        show_fav_only = self.fav_only_cb.isChecked()

        def matches_keyword(tool_name, tool_desc, kw):
            if kw in tool_name.lower() or kw in tool_desc.lower():
                return True
            aliases = TOOL_ALIASES.get(tool_name, [])
            for alias in aliases:
                if kw in alias.lower():
                    return True
            return False

        self.tool_list_flat.setUpdatesEnabled(False)
        for tool in self.all_tools:
            if not self._included_in_quick_view(tool):
                continue
            if show_fav_only and not tool["is_favorite"]:
                continue
            if (keyword and keyword not in tool["search_text"]
                    and not matches_keyword(tool["name"], tool["description"], keyword)):
                continue

            star = "⭐ " if tool["is_favorite"] else "   "
            display_text = (
                f"{star}{tool['display_name']}  ·  {tool['source']}  ·  {tool['status']}\n"
                f"   {tool['description'][:80]}"
            )
            if len(tool['description']) > 80:
                display_text += "..."

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, tool["name"])
            item.setData(Qt.UserRole + 1, tool["is_favorite"])
            item.setToolTip(tool["description"])
            self.tool_list_flat.addItem(item)
        self.tool_list_flat.setUpdatesEnabled(True)

    # ---------- 统一刷新入口 ----------
    def _refresh_ui(self):
        """根据当前选项刷新视图"""
        group_mode = self.group_cb.isChecked()
        if group_mode:
            self._build_group_tree()
            self.stacked.setCurrentIndex(0)
        else:
            self._build_flat_list()
            self.stacked.setCurrentIndex(1)

    # ---------- 右键菜单（树形视图） ----------
    def _show_tree_context_menu(self, position: QPoint):
        item = self.tool_tree.itemAt(position)
        if not item or item.parent() is None:  # 忽略分组项
            return
        tool_name = item.data(0, Qt.UserRole)
        if not tool_name:
            return
        is_fav = tool_name in self.favorites
        full_description = item.toolTip(0) or "无描述"

        menu = QMenu(self)
        # 收藏/取消收藏
        if is_fav:
            fav_action = QAction("❌ 取消收藏", menu)
            fav_action.triggered.connect(lambda: self._toggle_favorite(tool_name))
        else:
            fav_action = QAction("⭐ 收藏此工具", menu)
            fav_action.triggered.connect(lambda: self._toggle_favorite(tool_name))
        menu.addAction(fav_action)
        
        # 查看完整描述
        desc_action = QAction("📄 查看完整描述", menu)
        desc_action.triggered.connect(lambda: self._show_full_description(tool_name, full_description))
        menu.addAction(desc_action)
        
        menu.exec_(self.tool_tree.mapToGlobal(position))


    def _show_full_description(self, tool_name: str, description: str):
        """弹出对话框显示完整描述"""
        from PyQt5.QtWidgets import QMessageBox, QTextEdit, QDialogButtonBox, QVBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle(f"工具描述 - {tool_name}")
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(300)
        
        layout = QVBoxLayout(dialog)
        
        text_edit = QTextEdit()
        text_edit.setPlainText(description)
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet("""
            QTextEdit {
                font-size: 12pt;
                font-family: "Microsoft YaHei UI";
                border: 1px solid #DDDDDD;
                border-radius: 6px;
                padding: 8px;
            }
        """)
        layout.addWidget(text_edit)
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(button_box)
        
        dialog.exec_()

    # ---------- 右键菜单（扁平视图） ----------
    def _show_flat_context_menu(self, position: QPoint):
        item = self.tool_list_flat.itemAt(position)
        if not item:
            return
        tool_name = item.data(Qt.UserRole)
        if not tool_name:
            return
        is_fav = tool_name in self.favorites
        full_description = item.toolTip() or "无描述"

        menu = QMenu(self)
        if is_fav:
            fav_action = QAction("❌ 取消收藏", menu)
            fav_action.triggered.connect(lambda: self._toggle_favorite(tool_name))
        else:
            fav_action = QAction("⭐ 收藏此工具", menu)
            fav_action.triggered.connect(lambda: self._toggle_favorite(tool_name))
        menu.addAction(fav_action)
        
        desc_action = QAction("📄 查看完整描述", menu)
        desc_action.triggered.connect(lambda: self._show_full_description(tool_name, full_description))
        menu.addAction(desc_action)
        
        menu.exec_(self.tool_list_flat.mapToGlobal(position))
    # ---------- 双击选择 ----------
    def _on_tree_item_activated(self, item, column):
        if item.parent() is None:
            return
        tool_name = item.data(0, Qt.UserRole)  # 从第0列获取数据
        if tool_name:
            self._select_tool(tool_name)

    def _on_flat_item_activated(self, item):
        tool_name = item.data(Qt.UserRole)
        if tool_name:
            self._select_tool(tool_name)

    # ---------- 切换收藏 ----------
    def _toggle_favorite(self, tool_name: str):
        """切换收藏状态（供右键菜单调用）"""
        is_favorite = toggle_favorite(tool_name)
        if is_favorite:
            self.favorites.add(tool_name)
        else:
            self.favorites.discard(tool_name)

        # 更新 all_tools 中的收藏标记
        for tool in self.all_tools:
            if tool["name"] == tool_name:
                tool["is_favorite"] = tool_name in self.favorites
                break

        # 刷新界面
        self._refresh_ui()

    def _set_mode(self, mode: str):
        self.selected_mode = mode

    def _open_capability_center(self):
        self.manage_requested.emit()
        self.reject()

    def _select_tool(self, tool_name, mode=None):
        if tool_name:
            item = next((entry for entry in self.all_tools if entry["name"] == tool_name), None)
            if item and not item["available"]:
                return
        self.selected_tool = tool_name
        if mode:
            self.selected_mode = mode
        self.accept()


# ==================== 输入面板主题常量 ====================
def _make_input_styles(font_size: int) -> dict:
    """根据全局聊天字号生成输入框样式（与聊天字体一致，pt 单位）。
    聊天气泡使用 QFont("Microsoft YaHei UI", font_size)，第二个参数为磅值，
    因此这里也用 pt 而非 px，否则输入框会比聊天框明显偏小。"""
    fs = max(8, int(font_size))
    return {
        "normal": f"""
            QTextEdit {{
                background: transparent;
                border: none;
                color: #E9EDF2;
                font-size: {fs}pt;
                font-family: "Microsoft YaHei UI";
            }}
            QTextEdit:focus {{ background: transparent; }}
        """,
        "disabled": f"""
            QTextEdit {{
                background: transparent;
                border: none;
                color: #6E7A78;
                font-size: {fs}pt;
                font-family: "Microsoft YaHei UI";
            }}
            QTextEdit:focus {{ background: transparent; }}
        """,
        "highlight": f"""
            QTextEdit {{
                background: rgba(46, 96, 80, 70);
                border: none;
                color: #FFFFFF;
                font-size: {fs}pt;
                font-family: "Microsoft YaHei UI";
            }}
            QTextEdit:focus {{ background: rgba(46, 96, 80, 70); }}
        """,
    }


def _wrench_icon(color: QColor, size: int = 20) -> QIcon:
    """绘制组合扳手图标（上端开口钳口 + 手柄 + 下端圆环），替代 emoji/字体图标。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color)
    pen.setWidthF(max(1.6, size * 0.10))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    cx = size / 2
    # 下端圆环
    p.drawEllipse(QPointF(cx, size * 0.82), size * 0.12, size * 0.12)
    # 手柄
    p.drawLine(QPointF(cx, size * 0.70), QPointF(cx, size * 0.34))
    # 开口钳口（凵形朝上）
    jaw = size * 0.20
    y_base = size * 0.34
    y_tip = size * 0.16
    p.drawLine(QPointF(cx - jaw, y_base), QPointF(cx - jaw, y_tip))
    p.drawLine(QPointF(cx + jaw, y_base), QPointF(cx + jaw, y_tip))
    p.drawLine(QPointF(cx - jaw, y_base), QPointF(cx + jaw, y_base))
    p.end()
    return QIcon(pm)


def _paperclip_icon(color: QColor, size: int = 20) -> QIcon:
    """绘制回形针图标（45° 嵌套双层胶囊环，内圈略向下偏移），替代 emoji/字体图标。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color)
    pen.setWidthF(max(1.5, size * 0.085))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.save()
    p.translate(size / 2, size / 2)
    p.rotate(45)
    # 外层胶囊环
    outer = QPainterPath()
    outer.addRoundedRect(QRectF(-size * 0.20, -size * 0.37, size * 0.40, size * 0.74),
                         size * 0.37, size * 0.37)
    p.drawPath(outer)
    # 内层胶囊环
    p.translate(0, size * 0.02)
    inner = QPainterPath()
    inner.addRoundedRect(QRectF(-size * 0.13, -size * 0.21, size * 0.26, size * 0.42),
                         size * 0.21, size * 0.21)
    p.drawPath(inner)
    p.restore()
    p.end()
    return QIcon(pm)


def _microphone_icon(color: QColor, size: int = 20) -> QIcon:
    """绘制麦克风图标（胶囊话筒头 + 支架底座），替代 emoji/字体图标。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color)
    pen.setWidthF(max(1.6, size * 0.10))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = size
    # 话筒头（胶囊形）
    p.drawRoundedRect(QRectF(s * 0.39, s * 0.12, s * 0.22, s * 0.50), s * 0.11, s * 0.11)
    # 支架两腿
    p.drawLine(QPointF(s * 0.43, s * 0.66), QPointF(s * 0.30, s * 0.82))
    p.drawLine(QPointF(s * 0.57, s * 0.66), QPointF(s * 0.70, s * 0.82))
    # 底座横线
    p.drawLine(QPointF(s * 0.24, s * 0.88), QPointF(s * 0.76, s * 0.88))
    p.end()
    return QIcon(pm)


class InputPanel(QWidget):
    message_submitted = pyqtSignal(str, list)   # (text, image_paths)
    voice_clicked = pyqtSignal()
    clear_clicked = pyqtSignal()
    image_submitted = pyqtSignal(str)           # 旧接口保留，供外部使用
    capability_center_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._voice_connected = False
        self._selected_tool = None
        self._selected_tool_mode = "auto"
        self._pending_images: list[str] = []    # 暂存的图片路径
        self._image_preview_widgets: list[QWidget] = []
        self._quote_text = ""
        self._quote_sender = ""
        self._build_ui()
        self.setAcceptDrops(True)
        # 下边栏空白处支持拖拽窗口
        self._drag_pos = None
        def _panel_press(event):
            if event.button() == Qt.LeftButton:
                self._drag_pos = event.globalPos()
        def _panel_move(event):
            if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
                delta = event.globalPos() - self._drag_pos
                self._drag_pos = event.globalPos()
                w = self.window()
                w.move(w.x() + delta.x(), w.y() + delta.y())
        def _panel_release(event):
            self._drag_pos = None
        self.mousePressEvent = _panel_press
        self.mouseMoveEvent = _panel_move
        self.mouseReleaseEvent = _panel_release

        self._input.installEventFilter(self)

    def _build_ui(self):
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        palette = QPalette(self.palette())
        palette.setColor(QPalette.Window, QColor(0, 0, 0, 0))
        palette.setColor(QPalette.Base, QColor(0, 0, 0, 0))
        self.setPalette(palette)
        self.setStyleSheet("QWidget { background: transparent; }")

        # ── 悬浮输入舱：外边距 + 圆角面板 ──
        pod_margin = QVBoxLayout(self)
        pod_margin.setContentsMargins(20, 4, 20, 16)
        pod_margin.setSpacing(0)

        self._pod = QFrame()
        self._pod.setObjectName("inputPod")
        self._pod.setStyleSheet("""
            QFrame#inputPod {
                background-color: rgba(15, 25, 33, 232);
                border: 1px solid rgba(91, 154, 139, 150);
                border-radius: 22px;
            }
        """)
        _glow = QGraphicsDropShadowEffect(self._pod)
        _glow.setBlurRadius(30)
        _glow.setOffset(0, 4)
        _glow.setColor(QColor(58, 138, 115, 80))
        self._pod.setGraphicsEffect(_glow)

        self._pod_layout = QVBoxLayout(self._pod)
        self._pod_layout.setContentsMargins(18, 12, 18, 12)
        self._pod_layout.setSpacing(6)

        # ── 引用预览条 ──
        self._quote_bar = QWidget()
        self._quote_bar.setVisible(False)
        self._quote_bar.setObjectName("quoteBar")
        self._quote_bar.setStyleSheet("""
            QWidget#quoteBar {
                background-color: rgba(45, 45, 63, 180);
                border-left: 3px solid #8A98F0;
                border-radius: 6px;
            }
            QWidget#quoteBar QLabel { color: #B0B0C0; background: transparent; font-size: 10pt; }
            QWidget#quoteBar QPushButton { background: transparent; color: #888; border: none; font-size: 10pt; }
            QWidget#quoteBar QPushButton:hover { color: #FFF; }
        """)
        quote_layout = QHBoxLayout(self._quote_bar)
        quote_layout.setContentsMargins(12, 6, 8, 6)
        self._quote_label = QLabel()
        self._quote_label.setWordWrap(False)
        quote_layout.addWidget(self._quote_label, 1)
        self._quote_close = QPushButton("✕")
        self._quote_close.setFixedSize(20, 20)
        self._quote_close.setCursor(Qt.PointingHandCursor)
        self._quote_close.setToolTip("取消引用")
        self._quote_close.clicked.connect(self.clear_quote)
        quote_layout.addWidget(self._quote_close)
        self._pod_layout.addWidget(self._quote_bar)

        # ── 工具选择 chip ──
        self._tool_chip = QWidget()
        self._tool_chip.setObjectName("toolSelectionChip")
        self._tool_chip.setVisible(False)
        self._tool_chip.setStyleSheet("""
            QWidget#toolSelectionChip {
                background-color: rgba(40, 82, 72, 210);
                border: 1px solid #75B8A8;
                border-radius: 8px;
            }
            QWidget#toolSelectionChip QLabel { color: #E7FFF6; border: none; background: transparent; }
            QWidget#toolSelectionChip QPushButton { color: #CFEDE4; background: transparent; border: none; }
            QWidget#toolSelectionChip QPushButton:hover { color: #FFFFFF; }
        """)
        chip_layout = QHBoxLayout(self._tool_chip)
        chip_layout.setContentsMargins(12, 6, 8, 6)
        self._tool_chip_label = QLabel()
        chip_layout.addWidget(self._tool_chip_label, 1)
        chip_close = QPushButton("×")
        chip_close.setFixedSize(22, 22)
        chip_close.setToolTip("移除本轮工具")
        chip_close.clicked.connect(self.clear_selection)
        chip_layout.addWidget(chip_close)
        self._pod_layout.addWidget(self._tool_chip)

        # ── 图片预览栏 ──
        self._image_preview = QWidget()
        self._image_preview.setVisible(False)
        self._image_preview.setStyleSheet("background-color: transparent; border-bottom: 1px solid rgba(93, 124, 116, 60);")
        self._image_preview.setMaximumHeight(72)
        self._image_preview_layout = QHBoxLayout(self._image_preview)
        self._image_preview_layout.setContentsMargins(2, 6, 2, 6)
        self._image_preview_layout.setSpacing(8)
        self._image_preview_layout.setAlignment(Qt.AlignLeft)
        tip = QLabel("📷")
        tip.setFont(QFont("Segoe UI Emoji", 14))
        tip.setToolTip("待发送的图片，输入文字后按 Enter 发送")
        self._image_preview_layout.addWidget(tip)
        self._pod_layout.addWidget(self._image_preview)

        # ── 第一层：消息输入区 ──
        input_row = QHBoxLayout()
        input_row.setContentsMargins(6, 0, 6, 0)
        input_row.setSpacing(8)

        from utils.settings import get_settings
        chat_font_size = get_settings().font_size
        self._input_styles = _make_input_styles(chat_font_size)

        self._input = _InputBox()
        self._input.setFont(QFont("Microsoft YaHei UI", chat_font_size))
        self._input.setPlaceholderText("输入消息，按 Enter 发送，Shift+Enter 换行... (可粘贴图片到此)")
        self._input.setMinimumHeight(44)
        self._input.setMaximumHeight(120)
        self._input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._input.setStyleSheet(self._input_styles["normal"])
        _pal = self._input.palette()
        _pal.setColor(QPalette.PlaceholderText, QColor("#6B7A90"))
        self._input.setPalette(_pal)
        self._input.enter_pressed.connect(self._on_send)
        input_row.addWidget(self._input, 1)

        # 右上角瞬态工具簇：清空小纸条 / 重发 / 静音（默认隐藏，保持工具栏纯净）
        util_col = QVBoxLayout()
        util_col.setSpacing(6)
        util_col.setAlignment(Qt.AlignTop | Qt.AlignRight)

        self._btn_clear = QPushButton("🗑")
        self._btn_clear.setFixedSize(30, 30)
        self._btn_clear.setFont(QFont("Segoe UI Emoji", 11))
        self._btn_clear.setCursor(Qt.PointingHandCursor)
        self._btn_clear.setToolTip("清空小纸条")
        self._btn_clear.setStyleSheet(self._icon_button_style())
        self._btn_clear.clicked.connect(self._on_clear)
        util_col.addWidget(self._btn_clear)

        self._btn_resend = QPushButton("✏️")
        self._btn_resend.setFixedSize(30, 30)
        self._btn_resend.setFont(QFont("Segoe UI Emoji", 11))
        self._btn_resend.setCursor(Qt.PointingHandCursor)
        self._btn_resend.setToolTip("打断思考，回填上一条消息")
        self._btn_resend.setVisible(False)
        self._btn_resend.setStyleSheet(self._icon_button_style("#7FA9FF"))
        util_col.addWidget(self._btn_resend)

        self._btn_mute = QPushButton("🔇")
        self._btn_mute.setFixedSize(30, 30)
        self._btn_mute.setFont(QFont("Segoe UI Emoji", 11))
        self._btn_mute.setCursor(Qt.PointingHandCursor)
        self._btn_mute.setToolTip("停止朗读")
        self._btn_mute.setVisible(False)
        self._btn_mute.setStyleSheet(self._icon_button_style("#F0B429"))
        util_col.addWidget(self._btn_mute)

        input_row.addLayout(util_col)
        self._pod_layout.addLayout(input_row)

        # ── 第二层：底部功能工具栏（严格顺序：工具箱→附件→语音输入→自动发送→发送）──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 0, 6, 0)
        toolbar.setSpacing(10)

        self._tool_btn = QPushButton(" 工具箱")
        self._tool_btn.setIcon(_wrench_icon(QColor("#DCEFE8")))
        self._tool_btn.setIconSize(QSize(20, 20))
        self._tool_btn.setFixedHeight(40)
        self._tool_btn.setFont(QFont("Microsoft YaHei UI", 11))
        self._tool_btn.setCursor(Qt.PointingHandCursor)
        self._tool_btn.setToolTip("选择工具（强制让莲心使用某工具）")
        self._tool_btn.setStyleSheet(self._toolbar_button_style())
        self._tool_btn.clicked.connect(self._show_tool_dialog)
        toolbar.addWidget(self._tool_btn)

        self._attach_btn = QPushButton(" 附件")
        self._attach_btn.setIcon(_paperclip_icon(QColor("#DCEFE8")))
        self._attach_btn.setIconSize(QSize(20, 20))
        self._attach_btn.setFixedHeight(40)
        self._attach_btn.setFont(QFont("Microsoft YaHei UI", 11))
        self._attach_btn.setCursor(Qt.PointingHandCursor)
        self._attach_btn.setToolTip("发送图片 / 文件给莲心")
        self._attach_btn.setStyleSheet(self._toolbar_button_style())
        self._attach_btn.clicked.connect(self._show_attach_menu)
        toolbar.addWidget(self._attach_btn)

        self._btn_voice = QPushButton(" 语音输入")
        self._btn_voice.setIcon(_microphone_icon(QColor("#DCEFE8")))
        self._btn_voice.setIconSize(QSize(20, 20))
        self._btn_voice.setFixedHeight(40)
        self._btn_voice.setFont(QFont("Microsoft YaHei UI", 11))
        self._btn_voice.setCursor(Qt.PointingHandCursor)
        self._btn_voice.setToolTip("语音输入")
        self._btn_voice.setEnabled(False)
        self._btn_voice.setStyleSheet(self._toolbar_button_style())
        toolbar.addWidget(self._btn_voice)

        self._auto_send_cb = QCheckBox("自动发送")
        self._auto_send_cb.setFixedHeight(40)
        self._auto_send_cb.setChecked(True)
        self._auto_send_cb.setCursor(Qt.PointingHandCursor)
        self._auto_send_cb.setStyleSheet(self._auto_send_style())
        toolbar.addWidget(self._auto_send_cb)

        toolbar.addStretch(1)

        self._btn_send = QPushButton("↑  发送 (Enter)")
        self._btn_send.setFixedHeight(40)
        self._btn_send.setMinimumWidth(180)
        self._btn_send.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        self._btn_send.setCursor(Qt.PointingHandCursor)
        self._btn_send.setStyleSheet(self._send_button_style())
        self._btn_send.clicked.connect(self._on_send)
        toolbar.addWidget(self._btn_send)

        self._pod_layout.addLayout(toolbar)

        pod_margin.addWidget(self._pod)

        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(self._clear_highlight)

    # ── 工具栏按钮样式 ─────────────────────────────────────

    @staticmethod
    def _toolbar_button_style():
        return """
            QPushButton {
                background-color: rgba(22, 43, 38, 205);
                color: #DCEFE8;
                border: 1px solid #416B63;
                border-radius: 10px;
                padding: 0px 16px;
                font-size: 11pt;
                font-family: "Microsoft YaHei UI";
            }
            QPushButton:hover {
                background-color: #2A5148;
                border-color: #75B8A8;
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: #35685C;
                padding-top: 2px;
            }
            QPushButton:disabled {
                background-color: #1C2D29;
                color: #78918A;
                border: 1px solid #30463F;
            }
        """

    @staticmethod
    def _icon_button_style(color="#DCEFE8"):
        return f"""
            QPushButton {{
                background-color: rgba(22, 43, 38, 180);
                color: {color};
                border: 1px solid #416B63;
                border-radius: 9px;
                font-size: 11pt;
            }}
            QPushButton:hover {{ background-color: #2A5148; border-color: #75B8A8; color: #FFFFFF; }}
            QPushButton:pressed {{ background-color: #35685C; }}
        """

    @staticmethod
    def _auto_send_style():
        return """
            QCheckBox {
                background-color: rgba(22, 43, 38, 205);
                border: 1px solid #416B63;
                border-radius: 10px;
                padding: 0px 14px;
                color: #DCEFE8;
                font-size: 11pt;
                font-family: "Microsoft YaHei UI";
                spacing: 8px;
            }
            QCheckBox:hover { border-color: #75B8A8; color: #FFFFFF; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QCheckBox::indicator:unchecked {
                border: 1px solid #416B63;
                border-radius: 4px;
                background: transparent;
            }
            QCheckBox::indicator:checked {
                border: 1px solid #9AD8C7;
                border-radius: 4px;
                background: #3E8A73;
            }
        """

    @staticmethod
    def _send_button_style():
        return """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2C7C60, stop:1 #3E8A73);
                color: #FFFFFF;
                border: 1px solid #83CDB8;
                border-radius: 12px;
                padding: 0px 22px;
                font-size: 12pt;
                font-family: "Microsoft YaHei UI";
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #38916F, stop:1 #52A98C);
                border-color: #9AD8C7;
            }
            QPushButton:pressed {
                background: #2A6350;
                padding-top: 2px;
            }
            QPushButton:disabled {
                background: #2A3B36;
                color: #8DA69E;
                border-color: #416B63;
            }
        """

    # ── 工具选择相关方法 ─────────────────────────────────────

    def _show_tool_dialog(self):
        from utils.sound import play_sound
        play_sound("ToolBox1.mp3")
        dialog = ToolSelectionDialog(self)
        dialog.manage_requested.connect(self.capability_center_requested.emit)
        if dialog.exec_() == QDialog.Accepted:
            self._selected_tool = dialog.selected_tool
            self._selected_tool_mode = dialog.selected_mode if dialog.selected_tool else "auto"
            self._update_tool_button_style()

    def _update_tool_button_style(self):
        if self._selected_tool:
            from brain.capability_catalog import get_capability
            descriptor = get_capability(self._selected_tool)
            display_name = descriptor.display_name if descriptor else self._selected_tool
            mode_label = "强制使用" if self._selected_tool_mode == "forced" else "建议使用"
            self._tool_chip_label.setText(f"{mode_label}：{display_name}")
            self._tool_chip.setVisible(True)
            self._tool_btn.setStyleSheet("""
                QPushButton {
                    background-color: #347767;
                    color: #FFFFFF;
                    border-radius: 10px;
                    border: 1px solid #83CDB8;
                    padding: 0px 16px;
                    font-size: 11pt;
                    font-family: "Microsoft YaHei UI";
                }
                QPushButton:hover {
                    background-color: #3E8A73;
                }
                QPushButton:pressed {
                    background-color: #2A5148;
                }
            """)
            self._tool_btn.setToolTip(f"已绑定工具：{self._selected_tool}")
        else:
            self._tool_chip.setVisible(False)
            self._tool_btn.setStyleSheet(self._toolbar_button_style())
            self._tool_btn.setToolTip("选择工具（强制让莲心使用某工具）")

    # ── 附件按钮 ───────────────────────────────────────────

    def _show_attach_menu(self):
        from utils.sound import play_sound
        play_sound("ToolBox1.mp3")
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #142421;
                border: 1px solid #416B63;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                background-color: transparent;
                color: #DCEFE8;
                padding: 8px 28px 8px 14px;
                margin: 2px;
                border-radius: 6px;
                font-size: 10pt;
                font-family: "Microsoft YaHei UI";
            }
            QMenu::item:selected { background-color: #347767; color: #FFFFFF; }
        """)
        img_action = QAction("📷 图片（发送给莲心识别）", menu)
        img_action.triggered.connect(self._pick_image)
        file_action = QAction("📄 文件（填入路径，让莲心读取）", menu)
        file_action.triggered.connect(self._pick_file)
        menu.addAction(img_action)
        menu.addAction(file_action)
        menu.exec_(self._attach_btn.mapToGlobal(QPoint(0, self._attach_btn.height())))

    def _pick_image(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.tiff *.webp)")
        if path:
            self._process_image(path)

    def _pick_file(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "")
        if path:
            self.set_text(path)

    def get_selected_tool(self):
        return self._selected_tool

    def get_tool_selection(self):
        if not self._selected_tool:
            return None
        return {"name": self._selected_tool, "mode": self._selected_tool_mode}

    def select_tool(self, tool_name: str, mode: str = "preferred"):
        self._selected_tool = str(tool_name or "") or None
        self._selected_tool_mode = mode if mode in ("preferred", "forced") else "preferred"
        self._update_tool_button_style()
        if self._selected_tool:
            self._input.setFocus()

    def clear_selection(self):
        self._selected_tool = None
        self._selected_tool_mode = "auto"
        self._update_tool_button_style()

    # ── 公开接口 ─────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        self._btn_send.setEnabled(enabled)
        self._auto_send_cb.setEnabled(enabled)
        if enabled:
            self._input.setFocus()
            self._input.setStyleSheet(self._input_styles["normal"])
        else:
            self._input.setStyleSheet(self._input_styles["disabled"])

    def enable_voice_button(self):
        self._btn_voice.setEnabled(True)
        self._btn_voice.setToolTip("点击开始语音输入，检测到停顿后自动识别")
        if not self._voice_connected:
            self._btn_voice.clicked.connect(self.voice_clicked)
            self._voice_connected = True
        self._set_voice_idle()

    def disable_voice_button(self):
        self._btn_voice.setEnabled(False)
        self._btn_voice.setToolTip("待机模式运行中，麦克风被占用")
        self._btn_voice.setStyleSheet(self._toolbar_button_style())

    def set_voice_recording(self):
        self._btn_voice.setStyleSheet("""
            QPushButton {
                background-color: #B85C5C;
                color: #FFFFFF;
                border-radius: 10px;
                border: 1px solid #E49A9A;
                padding: 0px 16px;
                font-size: 11pt;
                font-family: "Microsoft YaHei UI";
            }
            QPushButton:hover { background-color: #D06A6A; }
        """)
        self._btn_voice.setToolTip("录音中…（检测到停顿后自动停止）")

    def set_voice_idle(self):
        self._set_voice_idle()

    def _set_voice_idle(self):
        self._btn_voice.setStyleSheet(self._toolbar_button_style())

    def set_text(self, text: str):
        self._input.setText(text)
        self._input.setFocus()
        cursor = self._input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._input.setTextCursor(cursor)
        self._highlight_input()

    def _highlight_input(self):
        self._input.setStyleSheet(self._input_styles["highlight"])
        self._highlight_timer.start(3000)

    def _clear_highlight(self):
        self._input.setStyleSheet(self._input_styles["normal"])

    def get_text(self) -> str:
        """获取当前输入框中的文本（不清空）。"""
        return self._input.toPlainText()

    def is_auto_send_enabled(self) -> bool:
        return self._auto_send_cb.isChecked()

    @property
    def voice_button(self):
        return self._btn_voice

    def enable_clear_button(self):
        self._btn_clear.setEnabled(True)

    def disable_clear_button(self):
        self._btn_clear.setEnabled(False)

    # ── 内部发送/清空 ──────────────────────────────────────

    def _on_send(self):
        from utils.sound import play_sound
        play_sound("Send message.mp3")

        text = self._input.toPlainText().strip()
        has_images = bool(self._pending_images)
        if not text and not has_images:
            return

        # 构建引用消息
        if self._quote_text:
            quoted = self._quote_text[:200]
            text = f"[引用回复] {self._quote_sender}说：\"{quoted}\"\n---\n我的回复：{text}"
            self.clear_quote()

        if not text and has_images:
            text = "看看这张图"
        images = list(self._pending_images)
        self._pending_images.clear()
        self._clear_image_preview()
        self._input.clear()
        self._highlight_timer.stop()
        self._clear_highlight()
        self.message_submitted.emit(text, images)

    def _on_clear(self):
        self.clear_clicked.emit()

    # ── 图片处理 ───────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj == self._input and event.type() == event.KeyPress:
            if event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
                if self._paste_image_from_clipboard():
                    return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(('.png','.jpg','.jpeg','.bmp','.tiff')):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(('.png','.jpg','.jpeg','.bmp','.tiff')):
                self._process_image(path)
                break
        event.acceptProposedAction()

    def _paste_image_from_clipboard(self) -> bool:
        clipboard = QApplication.clipboard()
        pixmap = clipboard.pixmap()
        if not pixmap.isNull():
            if not HAS_PIL:
                self._show_image_error("缺少 Pillow 库，无法处理剪贴板图片")
                return False
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                tmp_path = f.name
            pixmap.save(tmp_path, 'PNG')
            self._process_image(tmp_path)
            return True
        mime = clipboard.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if path.lower().endswith(('.png','.jpg','.jpeg','.bmp','.tiff')):
                    self._process_image(path)
                    return True
        return False

    def _process_image(self, img_path: str):
        self._pending_images.append(img_path)
        self._update_image_preview()

    def _remove_pending_image(self, index: int):
        if 0 <= index < len(self._pending_images):
            # 清理临时文件（粘贴产生的 tmp 文件）
            path = self._pending_images.pop(index)
            try:
                if "tmp" in Path(path).stem.lower() and Path(path).exists():
                    Path(path).unlink()
            except Exception:
                pass
            self._update_image_preview()

    def _update_image_preview(self):
        # 清除旧的预览缩略图
        while self._image_preview_layout.count() > 1:  # 保留 "📷" 提示
            item = self._image_preview_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        if not self._pending_images:
            self._image_preview.setVisible(False)
            return

        self._image_preview.setVisible(True)
        for i, img_path in enumerate(self._pending_images):
            thumb = QLabel()
            pixmap = QPixmap(img_path)
            if not pixmap.isNull():
                thumb.setPixmap(pixmap.scaled(50, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            thumb.setFixedSize(54, 54)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setStyleSheet("border: 2px solid #CCCCCC; border-radius: 6px; background: white;")
            self._image_preview_layout.addWidget(thumb)

            btn_x = QPushButton("×")
            btn_x.setFixedSize(16, 16)
            btn_x.setFont(QFont("Arial", 10, QFont.Bold))
            btn_x.setStyleSheet("QPushButton { background: #CC3333; color: white; border-radius: 8px; border: none; } QPushButton:hover { background: #FF4444; }")
            btn_x.setCursor(Qt.PointingHandCursor)
            idx = i
            btn_x.clicked.connect(lambda checked, idx=idx: self._remove_pending_image(idx))
            self._image_preview_layout.addWidget(btn_x)

    def _clear_image_preview(self):
        self._pending_images.clear()
        while self._image_preview_layout.count() > 1:
            item = self._image_preview_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._image_preview.setVisible(False)

    def _show_image_error(self, msg: str):
        self._input.setPlainText(msg)
        QTimer.singleShot(2000, lambda: self._input.clear() if self._input.toPlainText() == msg else None)
    # ── 中途插话条 ───────────────────
    def show_interrupt_bar(self, agent_worker):
        """显示插话输入条，绑定到 AgentWorker 的 interrupt_queue。"""
        from PyQt5.QtWidgets import QLineEdit, QPushButton, QHBoxLayout, QFrame, QLabel
        from PyQt5.QtCore import Qt

        if hasattr(self, '_interrupt_bar') and self._interrupt_bar is not None:
            self._interrupt_bar.show()
            self._interrupt_input.setText("")
            self._interrupt_worker = agent_worker
            if hasattr(self, "_interrupt_status"):
                self._interrupt_status.setText("就绪")
            self._interrupt_input.setFocus()
            return

        bar = QFrame(self)
        bar.setObjectName("interruptBar")
        bar.setStyleSheet("""
            QFrame#interruptBar {
                background: rgba(60, 60, 80, 200);
                border: 1px solid #666;
                border-radius: 8px;
                margin: 4px 8px;
            }
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        lbl = QLineEdit()
        lbl.setPlaceholderText("插话问问进度…（Enter 发送）")
        lbl.setStyleSheet("""
            QLineEdit {
                background: transparent; border: none; color: #ddd;
                font-size: 13px; padding: 3px 6px;
            }
        """)
        lbl.setEnabled(True)

        btn = QPushButton("发送")
        btn.setStyleSheet("""
            QPushButton {
                background: #4a6fa5; color: white; border-radius: 4px;
                padding: 3px 10px; font-size: 12px; min-width: 40px;
            }
            QPushButton:hover { background: #5a8fc5; }
        """)

        def _do_send():
            txt = lbl.text().strip()
            if txt and agent_worker and agent_worker.isRunning():
                agent_worker.send_interrupt(txt)
            lbl.setText("")

        lbl.returnPressed.connect(_do_send)
        btn.clicked.connect(_do_send)

        layout.addWidget(lbl)
        layout.addWidget(btn)
        status = QLabel("就绪")
        status.setStyleSheet("color: #9aa4bd; font-size: 11px; padding: 0 4px;")
        layout.addWidget(status)

        # 插入到输入舱内部顶部（在输入框上方展示）
        self._pod_layout.insertWidget(0, bar)

        self._interrupt_bar = bar
        self._interrupt_input = lbl
        self._interrupt_status = status
        self._interrupt_worker = agent_worker
        bar.show()
        lbl.setFocus()

        def send_with_status():
            txt = lbl.text().strip()
            if not txt:
                return
            worker = getattr(self, "_interrupt_worker", None)
            accepted = worker.send_interrupt(txt) if worker else False
            if accepted:
                status.setText("已排队，当前步骤结束后回复")
                lbl.clear()
            else:
                status.setText("当前任务已结束或队列已满")

        # 替换前面绑定的通用发送逻辑，保留按钮和 Enter 两种入口。
        try:
            lbl.returnPressed.disconnect(_do_send)
            btn.clicked.disconnect(_do_send)
        except Exception:
            pass
        lbl.returnPressed.connect(send_with_status)
        btn.clicked.connect(send_with_status)

    def hide_interrupt_bar(self):
        """隐藏插话输入条。"""
        if hasattr(self, '_interrupt_bar') and self._interrupt_bar is not None:
            self._interrupt_bar.hide()
            self._interrupt_worker = None
    def get_mute_button(self):
        return self._btn_mute

    def get_resend_button(self):
        return self._btn_resend

    def set_mute_visible(self, visible: bool):
        self._btn_mute.setVisible(visible)

    def set_resend_visible(self, visible: bool):
        self._btn_resend.setVisible(visible)

    def set_quote(self, text: str, sender: str):
        """设置引用消息。"""
        self._quote_text = text
        self._quote_sender = sender
        preview = text[:60] + ("..." if len(text) > 60 else "")
        self._quote_label.setText(f"引用 {sender}: \"{preview}\"")
        # 根据发送者改左边框颜色
        color = "#7EC8A4" if sender == "你" else "#8A98F0"
        self._quote_bar.setStyleSheet(f"""
            QWidget#quoteBar {{
                background-color: rgba(45, 45, 63, 180);
                border-left: 3px solid {color};
                border-radius: 6px;
            }}
        """)
        self._quote_bar.setVisible(True)
        self._input.setFocus()

    def clear_quote(self):
        """清除引用消息。"""
        self._quote_text = ""
        self._quote_sender = ""
        self._quote_bar.setVisible(False)
