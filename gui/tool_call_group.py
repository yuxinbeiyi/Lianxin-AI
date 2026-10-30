"""
ToolCallGroup：单轮工具调用容器
- 包含可选的轮次标题（多轮时显示）
- 包含 ToolCallCard 列表
- 支持增量添加 + 结果更新 + 完结标记
"""

from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

from gui.tool_call_card import ToolCallCard


class ToolCallGroup(QWidget):
    """单轮 ReAct 工具调用的容器"""
    geometry_changed = pyqtSignal()

    def __init__(self, round_num: int, parent=None):
        super().__init__(parent)
        self.round_num = round_num
        self.total_rounds = 1
        self._finalized = False
        self._cards: list[ToolCallCard] = []
        self._build_ui()

    @property
    def finalized(self) -> bool:
        return self._finalized

    # ── 公开方法 ───────────────────────────────────────

    def add_card(self, tool_name: str, args_json: str) -> ToolCallCard:
        """添加一个 running 状态的工具卡片"""
        card = ToolCallCard(variant="decision" if tool_name.startswith("决策模型 · ") else "tool")
        card.set_running(tool_name, args_json)
        card.geometry_changed.connect(self.geometry_changed.emit)
        self._cards.append(card)
        # 插入到标题之下、已有卡片之后
        self._card_layout.addWidget(card)
        return card

    def update_card(self, tool_name: str, preview: str,
                    is_error: bool, elapsed_ms: float):
        """找到匹配的 running 卡片并更新状态"""
        # 从后向前找同名 + running 状态的卡片（处理同名工具并行场景）
        target = None
        for card in reversed(self._cards):
            if card._tool_name == tool_name and card._status == "running":
                target = card
                break
        if target is None:
            # 防御：如果没找到 running 卡片，创建一个新的（正常流程不应出现）
            target = self.add_card(tool_name, "{}")
        target.set_result(preview, is_error, elapsed_ms)

    def finalize(self, total_rounds: int):
        """完结此轮：设置轮次标题，未完成卡片置灰"""
        self.total_rounds = total_rounds
        self._finalized = True

        # 未完成卡片置灰
        for card in self._cards:
            card.set_unknown()

        # 更新轮次标题
        if total_rounds > 1:
            self._round_header.setText(
                f"🔄 第 {self.round_num}/{total_rounds} 轮工具调用"
            )
            self._round_header.show()
        else:
            self._round_header.hide()

    # ── UI 构建 ────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 2, 16, 2)
        root.setSpacing(2)

        # 轮次标题（默认隐藏，多轮时显示）
        self._round_header = QLabel()
        self._round_header.setFont(QFont("Microsoft YaHei UI", 9))
        self._round_header.setStyleSheet("color: #888; background: transparent; padding: 4px 0;")
        self._round_header.setAlignment(Qt.AlignCenter)
        self._round_header.hide()
        root.addWidget(self._round_header)

        # 卡片容器
        self._card_layout = QVBoxLayout()
        self._card_layout.setSpacing(3)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self._card_layout)

        self.setStyleSheet("background: transparent;")
