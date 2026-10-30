"""
ToolCallCard：单个工具调用卡片组件
- 折叠态：一行显示工具名 + 状态 + 耗时 + 参数摘要
- 展开态：完整参数 JSON + 完整结果文本
- 颜色编码：running=蓝 / success=绿 / error=红
"""

from PyQt5.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QSizePolicy, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont


_COLORS = {
    "running": {"border": "#5B8DEF", "icon": "🛠️",  "bg": "#252540", "text": "#B0C4F0"},
    "success": {"border": "#27AE60", "icon": "✅",  "bg": "#252540", "text": "#A0D8B0"},
    "error":   {"border": "#E74C3C", "icon": "❌",  "bg": "#2D2020", "text": "#E8A0A0"},
    "unknown": {"border": "#666666", "icon": "⬜",  "bg": "#252540", "text": "#888888"},
}

_MOOYU_COLORS = {
    "running": {"border": "#F39C12", "icon": "🐟",  "bg": "#2D2510", "text": "#F0D8A0"},
    "success": {"border": "#F39C12", "icon": "🐟",  "bg": "#2D2510", "text": "#F0D8A0"},
    "error":   {"border": "#E74C3C", "icon": "🐟",  "bg": "#2D2020", "text": "#E8A0A0"},
    "unknown": {"border": "#666666", "icon": "🐟",  "bg": "#252540", "text": "#888888"},
}


class ToolCallCard(QWidget):
    """单个工具调用卡片 — 可折叠展开"""
    geometry_changed = pyqtSignal()

    def __init__(self, parent=None, variant="tool"):
        super().__init__(parent)
        self._variant = variant
        self._tool_name = ""
        self._args_json = ""
        self._status = "running"
        self._result_text = ""
        self._elapsed_ms = 0.0
        self._expanded = False
        self._build_ui()
        self.setCursor(Qt.PointingHandCursor)

    # ── 颜色分发 ─────────────────────────────────────

    def _get_colors(self):
        return _MOOYU_COLORS if self._variant == "mooyu" else _COLORS

    # ── 公开方法 ───────────────────────────────────────

    def set_running(self, tool_name: str, args_json: str):
        """工具开始执行"""
        self._tool_name = tool_name
        self._args_json = args_json
        self._status = "running"
        self._result_text = ""
        self._elapsed_ms = 0.0
        self._expanded = False
        self._refresh()

    def set_result(self, preview: str, is_error: bool, elapsed_ms: float):
        """工具执行完成"""
        self._status = "error" if is_error else "success"
        self._result_text = preview
        self._elapsed_ms = elapsed_ms
        self._refresh()

    def set_unknown(self):
        """finalize 时未完成卡片置灰"""
        if self._status == "running":
            self._status = "unknown"
            self._refresh()

    # ── UI 构建 ────────────────────────────────────────

    def _build_ui(self):
        c = _COLORS["running"]

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 折叠态头部行 ──
        self._header = QWidget()
        self._header.setCursor(Qt.PointingHandCursor)
        header_row = QHBoxLayout(self._header)
        header_row.setContentsMargins(10, 6, 10, 6)
        header_row.setSpacing(6)

        self._icon = QLabel(c["icon"])
        self._icon.setFont(QFont("Microsoft YaHei UI", 10))
        self._icon.setStyleSheet("background: transparent;")
        header_row.addWidget(self._icon)

        self._name_label = QLabel()
        self._name_label.setFont(QFont("Consolas", 10))
        self._name_label.setStyleSheet(f"color: {c['text']}; background: transparent; font-weight: bold;")
        header_row.addWidget(self._name_label)

        self._status_label = QLabel("执行中…")
        self._status_label.setFont(QFont("Microsoft YaHei UI", 9))
        self._status_label.setStyleSheet(f"color: {c['text']}; background: transparent;")
        header_row.addWidget(self._status_label)

        header_row.addStretch(1)

        self._elapsed_label = QLabel("")
        self._elapsed_label.setFont(QFont("Consolas", 9))
        self._elapsed_label.setStyleSheet("color: #777; background: transparent;")
        header_row.addWidget(self._elapsed_label)

        # 参数一行摘要（折叠时显示在头部下方）
        self._args_summary = QLabel("")
        self._args_summary.setFont(QFont("Microsoft YaHei UI", 8))
        self._args_summary.setStyleSheet("color: #888; background: transparent; padding-left: 28px;")
        self._args_summary.setWordWrap(True)

        root.addWidget(self._header)
        root.addWidget(self._args_summary)

        # ── 展开态详情 ──
        self._detail = QWidget()
        detail_layout = QVBoxLayout(self._detail)
        detail_layout.setContentsMargins(10, 4, 10, 8)
        detail_layout.setSpacing(4)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("border: none; border-top: 1px solid #3D3D5A;")
        detail_layout.addWidget(sep)

        # 参数详情
        self._args_full_label = QLabel("")
        self._args_full_label.setFont(QFont("Consolas", 8))
        self._args_full_label.setStyleSheet("color: #AAA; background: transparent; padding: 4px 8px;")
        self._args_full_label.setWordWrap(True)
        self._args_full_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addWidget(self._args_full_label)

        # 结果详情
        self._result_full_label = QLabel("")
        self._result_full_label.setFont(QFont("Microsoft YaHei UI", 9))
        self._result_full_label.setStyleSheet("color: #CCC; background: transparent; padding: 4px 8px;")
        self._result_full_label.setWordWrap(True)
        self._result_full_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addWidget(self._result_full_label)

        self._detail.hide()
        root.addWidget(self._detail)

    # ── 事件 ──────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_expand()
        super().mousePressEvent(event)

    # ── 内部 ──────────────────────────────────────────

    def _toggle_expand(self):
        self._expanded = not self._expanded
        self._detail.setVisible(self._expanded)
        self.geometry_changed.emit()

    def _refresh(self):
        c = self._get_colors()[self._status]

        # 整体样式
        self.setStyleSheet(f"""
            ToolCallCard {{
                background-color: {c['bg']};
                border-left: 3px solid {c['border']};
                border-radius: 6px;
                margin: 1px 0;
            }}
        """)

        # 图标
        self._icon.setText("🧠" if self._variant == "decision" and self._status == "running" else c["icon"])

        # 名称
        self._name_label.setText(self._tool_name)
        self._name_label.setStyleSheet(f"color: {c['text']}; background: transparent; font-weight: bold;")

        # 状态
        if self._variant == "mooyu":
            status_texts = {
                "running": "获取中…", "success": "已获取", "error": "失败", "unknown": "—"
            }
        elif self._variant == "decision":
            status_texts = {
                "running": "请求中…", "success": "响应已校验", "error": "调用失败", "unknown": "未确认"
            }
        else:
            status_texts = {
                "running": "执行中…", "success": "完成", "error": "失败", "unknown": "—"
            }
        self._status_label.setText(status_texts.get(self._status, ""))
        self._status_label.setStyleSheet(f"color: {c['text']}; background: transparent;")

        # 耗时
        if self._elapsed_ms > 0:
            if self._elapsed_ms >= 1000:
                self._elapsed_label.setText(f"{self._elapsed_ms/1000:.1f}s")
            else:
                self._elapsed_label.setText(f"{self._elapsed_ms:.0f}ms")
        else:
            self._elapsed_label.setText("")

        # 参数一行摘要
        summary = self._make_args_summary()
        self._args_summary.setText(summary)
        self._args_summary.setVisible(bool(summary))

        # 展开态：完整参数
        try:
            import json
            parsed = json.loads(self._args_json) if self._args_json else {}
            pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            pretty = self._args_json
        self._args_full_label.setText(f"参数:\n{pretty}" if pretty else "")

        # 展开态：完整结果
        if self._result_text:
            self._result_full_label.setText(f"结果:\n{self._result_text}")
        else:
            self._result_full_label.setText("")

    def _make_args_summary(self) -> str:
        """生成一行参数摘要"""
        try:
            import json
            args = json.loads(self._args_json) if self._args_json else {}
        except Exception:
            return ""
        if not args:
            return ""

        # 优先显示的 key（按常见程度排序）
        preferred = ["query", "path", "content", "name", "url", "description",
                     "tool_name", "task_name", "message", "text", "command"]
        for key in preferred:
            if key in args:
                val = str(args[key])
                if len(val) > 60:
                    val = val[:57] + "..."
                return f'"{val}"'

        # 兜底：取第一个 key
        first_key = next(iter(args))
        val = str(args[first_key])
        if len(val) > 60:
            val = val[:57] + "..."
        return f"{first_key}: {val}"
