"""
HistoryDialog：历史对话查看器
功能：搜索 / 智能摘要 / 导出 / 删除 / 置顶
"""

import json
import re
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QTextBrowser, QPushButton, QLabel, QSplitter, QWidget,
    QInputDialog, QLineEdit, QMenu, QFileDialog, QMessageBox,
    QDateEdit, QCheckBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QDate
from PyQt5.QtGui import QFont, QColor
from memory.history_manager import HistoryManager
from utils.diary import DiaryWorker, has_diary_for_date

# ── 后台摘要生成线程 ──────────────────────────────────────────────

class SummaryWorker(QThread):
    """调用 DeepSeek API 为一条会话生成摘要（后台线程）。"""
    summary_ready  = pyqtSignal(int, str)   # (session_id, summary_text)
    error_occurred = pyqtSignal(int, str)   # (session_id, error_msg)

    def __init__(self, session_id: int, messages: list, parent=None):
        super().__init__(parent)
        self._session_id = session_id
        self._messages   = messages

    def run(self):
        try:
            from utils.history_summary import generate_session_summary
            summary = generate_session_summary(self._messages)
            self.summary_ready.emit(self._session_id, summary)
        except Exception as e:
            self.error_occurred.emit(self._session_id, str(e))


# ── 主对话框 ─────────────────────────────────────────────────────

class HistoryDialog(QDialog):
    """历史对话查看器（含搜索、摘要、导出、删除、置顶）。"""

    import_memory = pyqtSignal(int)   # 携带 session_id，供主窗口导入记忆
    session_deleted = pyqtSignal(int) # 携带 session_id，通知主窗口会话已删除

    def __init__(self, history_manager: HistoryManager,
                 current_session_id: int = -1, parent=None,
                 first_meet_date: str = ""):
        super().__init__(parent)
        self._mgr                = history_manager
        self._current_session_id = current_session_id   # 主窗口当前会话，删除时判断
        self._sessions: list     = []
        self._summary_workers: list = []   # 防止 QThread 被 GC
        self._diary_worker       = None    # 日记生成线程
        self._first_meet_date    = first_meet_date

        self.setWindowTitle("历史对话记录")
        self.resize(960, 620)
        self.setStyleSheet("""
            QDialog {
                background-color: #1E1E2E;
                border: 2px solid #1ABC9C;
                border-radius: 8px;
            }
        """)
        self._build_ui()
        self._load_sessions()

    # ── 界面构建 ─────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)


        # ── 主体分割器 ────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #D8D8E8; }")

        # ── 左侧：搜索框 + 会话列表 ───────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("搜索标题、内容、摘要…")
        self._search_box.setFont(QFont("Microsoft YaHei UI", 9))
        self._search_box.setStyleSheet("""
            QLineEdit { border: 1px solid #3D3D5A; border-radius: 6px;
                padding: 4px 8px; background: #2D2D3F; color: #E0E0E0;
            }
            QLineEdit:focus { border-color: #1ABC9C; }
        """)
        self._search_box.textChanged.connect(self._on_search_changed)
        left_layout.addWidget(self._search_box)

        # ── 日期筛选 + 为时间胶囊留下右页 ───────────────────
        date_row = QHBoxLayout()
        date_row.setSpacing(4)
        self._date_filter = QDateEdit()
        self._date_filter.setCalendarPopup(True)
        self._date_filter.setDisplayFormat("yyyy-MM-dd")
        self._date_filter.setSpecialValueText("全部日期")
        if self._first_meet_date:
            try:
                y, m, d = map(int, self._first_meet_date.split("-"))
                self._date_filter.setDate(QDate(y, m, d))
            except (ValueError, TypeError):
                self._date_filter.setDate(QDate(2000, 1, 1))
        else:
            self._date_filter.setDate(QDate(2000, 1, 1))

        self._date_filter.setStyleSheet("""
            QDateEdit {
                background: #2D2D3F; color: #E0E0E0;
                border: 1px solid #3D3D5A; border-radius: 4px; padding: 2px 4px;
            }
            QDateEdit:focus { border-color: #1ABC9C; }
            QDateEdit::drop-down { border: none; width: 16px; }
        """)
        self._date_filter.dateChanged.connect(self._on_date_changed)
        date_row.addWidget(self._date_filter)

        clear_date_btn = QPushButton("✕")
        clear_date_btn.setFixedSize(24, 24)
        clear_date_btn.setStyleSheet("""
            QPushButton {
                background: #3D3D5A; color: #AAA; border-radius: 4px; border: none;
            }
            QPushButton:hover {
                background: #5A3A3A; color: #FF8080;
            }
        """)
        clear_date_btn.setToolTip("清除日期筛选")
        clear_date_btn.clicked.connect(lambda: self._date_filter.setDate(QDate(2000, 1, 1)))
        date_row.addWidget(clear_date_btn)

        self._select_all_cb = QCheckBox("全选")

        self._select_all_cb.setStyleSheet("color: #E0E0E0;")
        self._select_all_cb.stateChanged.connect(self._on_select_all)
        self._select_all_cb.hide()
        date_row.addWidget(self._select_all_cb)

        date_row.addStretch()

        self._btn_diary = QPushButton("🌙 留下书页")
        self._btn_diary.setFixedSize(118, 26)
        self._btn_diary.setStyleSheet("""
            QPushButton { background:#6C7BFF; color:white; border-radius:6px; border:none; }
            QPushButton:hover   { background:#5A6AEE; }
            QPushButton:disabled{ background:#3D3D5A; color:#666; }
        """)
        self._btn_diary.clicked.connect(self._on_generate_diary)
        self._btn_diary.setEnabled(False)
        self._btn_diary.hide()
        date_row.addWidget(self._btn_diary)

        left_layout.addLayout(date_row)

        self._session_list = QListWidget()

        self._session_list.setStyleSheet("""
            QListWidget {
                background: #1E1E2E;
                border: 1px solid #3D3D5A;
                border-radius: 6px;
                color: #E0E0E0;
            }
            QListWidget::item {
                padding: 8px !important;
                color: #E0E0E0;
            }
            QListWidget::item:selected {
                background: #1ABC9C;
                color: #1E1E2E;
            }
            QListWidget::item:hover {
                background: #2D2D3F;
                color: #FFFFFF;                         
            }
        """)
        self._session_list.currentRowChanged.connect(self._on_session_selected)
        self._session_list.itemChanged.connect(self._on_item_checked)

        self._session_list.itemDoubleClicked.connect(self._on_rename_session)
        self._session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._session_list.customContextMenuRequested.connect(self._on_context_menu)
        left_layout.addWidget(self._session_list)
        splitter.addWidget(left)

        # ── 右侧：摘要 + 消息内容 ──────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self._content_browser = QTextBrowser()
        self._content_browser.setFont(QFont("Microsoft YaHei UI", 10))
        self._content_browser.setOpenExternalLinks(False)
        self._content_browser.setStyleSheet("""
            QTextBrowser {
                border: 1px solid #3D3D5A;
                border-radius: 6px;
                padding: 8px;
                background: #1E1E2E;
                color: #E0E0E0;
            }
        """)
        right_layout.addWidget(self._content_browser)

        # 摘要显示条（有摘要时显示）
        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setFont(QFont("Microsoft YaHei UI", 9))
        self._summary_label.setStyleSheet("""
            QLabel {
                background: #1A2A3A; border: 1px solid #3D5A7A;
                border-radius: 6px; padding: 6px 10px; color: #A0C0FF;
            }
        """)
        self._summary_label.hide()
        right_layout.addWidget(self._summary_label)

        splitter.addWidget(right)
        splitter.setSizes([250, 690])
        root.addWidget(splitter)

        # ── 底部按钮区 ────────────────────────────────────────
        S_PURPLE = """
            QPushButton { background:#6C7BFF; color:white; border-radius:8px; border:none; }
            QPushButton:hover   { background:#5A6AEE; }
            QPushButton:pressed { background:#4A5ADE; }
            QPushButton:disabled{ background:#3D3D5A; color:#666666; }"""
        S_LIGHT = """
            QPushButton { background:#2D2D3F; color:#A0B0FF; border-radius:8px; border:1px solid #3D3D5A; }
            QPushButton:hover   { background:#3D3D55; }
            QPushButton:pressed { background:#4D4D65; }
            QPushButton:disabled{ background:#2D2D3F; color:#555555; border-color:#2D2D3F; }"""
        S_RED = """
            QPushButton { background:#FF5555; color:white; border-radius:8px; border:none; }
            QPushButton:hover   { background:#EE4444; }
            QPushButton:pressed { background:#DD3333; }
            QPushButton:disabled{ background:#3D3D5A; color:#666666; }"""
        S_PINK = """
            QPushButton { background:#E05080; color:white; border-radius:8px; border:none; }
            QPushButton:hover   { background:#C8406A; }
            QPushButton:pressed { background:#B03060; }
            QPushButton:disabled{ background:#3D3D5A; color:#666666; }"""
        S_GOLD = """
            QPushButton { background:#F0A800; color:white; border-radius:8px; border:none; }
            QPushButton:hover   { background:#E09800; }
            QPushButton:pressed { background:#CC8800; }
            QPushButton:disabled{ background:#3D3D5A; color:#666666; }"""

        def btn(text, w, style, enabled=True):
            b = QPushButton(text)
            b.setFixedSize(w, 32)
            b.setFont(QFont("Microsoft YaHei UI", 9))
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(style)
            b.setEnabled(enabled)
            return b

        # 选中会话才能用的按钮
        self._btn_pin     = btn("☆ 置顶",  80, S_GOLD,   False)
        self._btn_summary = btn("生成摘要", 80, S_LIGHT,  False)
        self._btn_export  = btn("导出 ▼",  72, S_LIGHT,  False)
        self._btn_delete  = btn("删除",    60, S_RED,    False)
        self._btn_import  = btn("导入记忆", 88, S_PINK,   False)

        # 全局按钮
        self._btn_batch   = btn("批量摘要", 80, S_LIGHT)
        self._btn_exp_all = btn("导出全部", 80, S_LIGHT)
        btn_close         = btn("关闭",    64, S_PURPLE)

        self._btn_pin.clicked.connect(self._on_pin_clicked)
        self._btn_summary.clicked.connect(self._on_generate_summary)
        self._btn_export.clicked.connect(self._on_export_clicked)
        self._btn_delete.clicked.connect(self._on_delete_clicked)
        self._btn_import.clicked.connect(self._on_import_memory_clicked)
        self._btn_batch.clicked.connect(self._on_batch_summary)
        self._btn_exp_all.clicked.connect(self._on_export_all)
        btn_close.clicked.connect(self.accept)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        for b in (self._btn_pin, self._btn_summary, self._btn_export, self._btn_delete):
            btn_row.addWidget(b)
        btn_row.addStretch()
        for b in (self._btn_batch, self._btn_exp_all, self._btn_import, btn_close):
            btn_row.addWidget(b)
        root.addLayout(btn_row)

    @staticmethod
    def _make_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont("Microsoft YaHei UI", 9))
        lbl.setStyleSheet("color: #888; padding: 2px 4px;")
        return lbl

    # ── 数据加载 ─────────────────────────────────────────────

    def _load_sessions(self, keyword: str = "", date_str: str = None):
        """加载（或按关键词+日期过滤）会话列表，保持当前选中行。"""
        cur_row = self._session_list.currentRow()
        cur_id  = self._sessions[cur_row]["id"] if 0 <= cur_row < len(self._sessions) else -1

        if date_str:
            self._sessions = self._mgr.get_sessions_by_date(date_str)
        elif keyword.strip():
            self._sessions = self._mgr.search_sessions(keyword.strip())
        else:
            self._sessions = self._mgr.get_sessions()

        if date_str and keyword.strip():
            kw = keyword.strip().lower()
            self._sessions = [
                s for s in self._sessions
                if kw in s["title"].lower() or kw in (s.get("summary") or "").lower()
            ]


        self._session_list.clear()
        if not self._sessions:
            item = QListWidgetItem("（暂无历史记录）")
            item.setFlags(Qt.NoItemFlags)
            self._session_list.addItem(item)
            self._set_sel_buttons(False)
            self._content_browser.clear()
            self._summary_label.hide()
            return

        kw_lower = keyword.strip().lower()
        restore_row = 0
        for i, s in enumerate(self._sessions):
            date_str  = s["created_at"][:10]
            count     = self._mgr.get_message_count(s["id"])
            pin_mark  = "★ " if s.get("is_pinned") else ""
            display   = f"{pin_mark}{date_str}\n{s['title']}  [{count}条]"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, s["id"])
            if date_str:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)

            if s.get("is_pinned"):
                item.setBackground(QColor("#FFFBEE"))
            # 高亮匹配关键词的条目
            if kw_lower and (
                kw_lower in s["title"].lower()
                or kw_lower in (s.get("summary") or "").lower()
            ):
                item.setForeground(QColor("#5060DD"))
            self._session_list.addItem(item)
            if s["id"] == cur_id:
                restore_row = i

        # 尽量恢复原来选中的行
        self._session_list.setCurrentRow(restore_row)

    def refresh(self, current_session_id: int | None = None):
        """Refresh the session list after another window creates a new session."""
        if current_session_id is not None:
            self._current_session_id = current_session_id
        self._load_sessions(
            keyword=self._search_box.text(),
            date_str=self._date_filter.date().toString("yyyy-MM-dd")
            if self._date_filter.date().year() > 2000 else None,
        )
        if current_session_id is not None:
            for row, session in enumerate(self._sessions):
                if session.get("id") == current_session_id:
                    self._session_list.setCurrentRow(row)
                    break

    def _on_session_selected(self, row: int):
        """切换会话时刷新右侧内容、摘要、按钮状态。"""
        has_valid = 0 <= row < len(self._sessions)
        self._set_sel_buttons(has_valid)

        if not has_valid:
            self._content_browser.clear()
            self._summary_label.hide()
            return

        s = self._sessions[row]

        # 置顶按钮文字
        self._btn_pin.setText("★ 取消置顶" if s.get("is_pinned") else "☆ 置顶")

        # 摘要
        summary = (s.get("summary") or "").strip()
        if summary:
            self._summary_label.setText(f"摘要：{summary}")
            self._summary_label.show()
        else:
            self._summary_label.hide()

        # 消息内容
        messages = self._mgr.get_messages(s["id"])
        if not messages:
            self._content_browser.setHtml("<p style='color:#999;'>（该会话暂无消息）</p>")
            return

        kw = self._search_box.text().strip()
        html_parts = []
        for msg in messages:
            raw     = msg["content"]
            escaped = (raw.replace("&", "&amp;")
                          .replace("<", "&lt;")
                          .replace(">", "&gt;"))
            # 高亮搜索词
            if kw:
                escaped = re.sub(
                    f"({re.escape(kw)})",
                    r'<mark style="background:#FFE066;border-radius:3px;">\1</mark>',
                    escaped,
                    flags=re.IGNORECASE,
                )
            content_html = escaped.replace("\n", "<br>")
            time_str = msg["timestamp"][11:16]

            if msg["role"] == "user":
                html_parts.append(
                    f'<div style="margin:8px 0;">'
                    f'<span style="color:#6C7BFF;font-weight:bold;">主人</span>'
                    f'<span style="color:#AAA;font-size:11px;margin-left:8px;">{time_str}</span><br>'
                    f'<div style="background:#2D2D3F;border-radius:8px;'
                    f'padding:8px 12px;margin-top:4px;color:#E0E0E0;">{content_html}</div>'
                    f'</div>'
                )
            else:
                html_parts.append(
                    f'<div style="margin:8px 0;">'
                    f'<span style="color:#E05080;font-weight:bold;">莲心</span>'
                    f'<span style="color:#AAA;font-size:11px;margin-left:8px;">{time_str}</span><br>'
                    f'<div style="background:#2D1A2A;border-radius:8px;'
                    f'padding:8px 12px;margin-top:4px;color:#E0E0E0;">{content_html}</div>'
                    f'</div>'
                )

        self._content_browser.setHtml(
            "<html><body style='font-family:Microsoft YaHei UI;font-size:10pt;'>"
            + "".join(html_parts)
            + "</body></html>"
        )
        self._content_browser.verticalScrollBar().setValue(0)

    def _set_sel_buttons(self, enabled: bool):
        """批量设置"需选中会话"才可用的按钮状态。"""
        for b in (self._btn_pin, self._btn_summary,
                  self._btn_export, self._btn_delete, self._btn_import):
            b.setEnabled(enabled)

    # ── 搜索 ─────────────────────────────────────────────────

    def _on_search_changed(self, text: str):
        date_str = None
        if self._date_filter.date().year() > 2000:
            date_str = self._date_filter.date().toString("yyyy-MM-dd")
        self._load_sessions(keyword=text, date_str=date_str)


    # ── 双击重命名 ───────────────────────────────────────────

    def _on_rename_session(self, item: QListWidgetItem):
        session_id = item.data(Qt.UserRole)
        if session_id is None:
            return
        row = self._session_list.row(item)
        old = self._sessions[row]["title"] if row < len(self._sessions) else ""
        new_title, ok = QInputDialog.getText(
            self, "重命名会话", "输入新标题：", text=old
        )
        if ok and new_title.strip():
            self._mgr.update_session_title(session_id, new_title.strip())
            self._load_sessions(self._search_box.text())

    # ── 置顶 ─────────────────────────────────────────────────

    def _on_pin_clicked(self):
        row = self._session_list.currentRow()
        if not (0 <= row < len(self._sessions)):
            return
        self._mgr.toggle_pin(self._sessions[row]["id"])
        self._load_sessions(self._search_box.text())

    # ── 智能摘要 ─────────────────────────────────────────────

    def _on_generate_summary(self):
        """为当前选中会话生成摘要。"""
        row = self._session_list.currentRow()
        if not (0 <= row < len(self._sessions)):
            return
        s    = self._sessions[row]
        msgs = self._mgr.get_messages(s["id"])
        if not msgs:
            QMessageBox.information(self, "提示", "该会话没有消息内容，无法生成摘要。")
            return
        self._btn_summary.setEnabled(False)
        self._btn_summary.setText("生成中…")
        self._launch_summary_worker(s["id"], msgs,
                                    self._on_summary_done,
                                    self._on_summary_error_single)

    def _on_summary_done(self, session_id: int, summary: str):
        self._mgr.update_summary(session_id, summary)
        self._btn_summary.setEnabled(True)
        self._btn_summary.setText("生成摘要")
        self._load_sessions(self._search_box.text())

    def _on_summary_error_single(self, _sid: int, err: str):
        self._btn_summary.setEnabled(True)
        self._btn_summary.setText("生成摘要")
        self._summary_label.setText(f"⚠️ 摘要生成失败：{err}")
        self._summary_label.setStyleSheet("""
            QLabel {
                background: #3A1A1A; border: 1px solid #7A3D3D;
                border-radius: 6px; padding: 6px 10px; color: #FFA0A0;
            }
        """)
        self._summary_label.show()


    def _on_batch_summary(self):
        """批量为所有消息数 ≥10 条且没有摘要的会话生成摘要。"""
        all_sessions = self._mgr.get_sessions()
        candidates = [
            s for s in all_sessions
            if not (s.get("summary") or "").strip()
            and self._mgr.get_message_count(s["id"]) >= 10
        ]
        if not candidates:
            QMessageBox.information(
                self, "批量摘要",
                "没有需要生成摘要的会话（条件：消息数≥10 且尚无摘要）。"
            )
            return
        reply = QMessageBox.question(
            self, "批量摘要",
            f"找到 {len(candidates)} 个符合条件的会话，\n"
            f"批量生成摘要将消耗一定 API token，是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._batch_queue = list(candidates)
        self._batch_done  = 0
        self._batch_total = len(candidates)
        self._btn_batch.setEnabled(False)
        self._btn_batch.setText(f"0/{self._batch_total}")
        self._next_batch()

        

    def _next_batch(self):
        if not self._batch_queue:
            self._btn_batch.setEnabled(True)
            self._btn_batch.setText("批量摘要")
            self._load_sessions(self._search_box.text())
            QMessageBox.information(
                self, "批量摘要完成",
                f"已完成 {self._batch_done}/{self._batch_total} 个会话的摘要生成。"
            )
            return
        s    = self._batch_queue.pop(0)
        msgs = self._mgr.get_messages(s["id"])
        self._launch_summary_worker(s["id"], msgs,
                                    self._on_batch_item_done,
                                    self._on_batch_item_done_err)

    def _on_batch_item_done(self, session_id: int, summary: str):
        self._mgr.update_summary(session_id, summary)
        self._batch_done += 1
        self._btn_batch.setText(f"{self._batch_done}/{self._batch_total}")
        self._next_batch()

    def _on_batch_item_done_err(self, _sid: int, _err: str):
        self._batch_done += 1
        self._btn_batch.setText(f"{self._batch_done}/{self._batch_total}")
        self._next_batch()

    def _launch_summary_worker(self, session_id: int, msgs: list,
                                on_ready, on_error):
        """创建并启动 SummaryWorker，连接回调后防 GC。"""
        worker = SummaryWorker(session_id, msgs, self)
        worker.summary_ready.connect(on_ready)
        worker.error_occurred.connect(on_error)
        worker.finished.connect(
            lambda w=worker: self._summary_workers.remove(w)
            if w in self._summary_workers else None
        )
        self._summary_workers.append(worker)
        worker.start()

    # ── 导出 ─────────────────────────────────────────────────

    def _on_export_clicked(self):
        """弹出下拉菜单选择导出格式。"""
        menu = QMenu(self)
        menu.addAction("导出为 TXT",  self._export_txt)
        menu.addAction("导出为 JSON", self._export_json)
        menu.exec_(self._btn_export.mapToGlobal(
            self._btn_export.rect().bottomLeft()
        ))

    def _selected_session_and_msgs(self):
        row = self._session_list.currentRow()
        if not (0 <= row < len(self._sessions)):
            return None, None
        s = self._sessions[row]
        return s, self._mgr.get_messages(s["id"])

    def _export_txt(self):
        s, msgs = self._selected_session_and_msgs()
        if s is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 TXT",
            f"{s['title'][:20]}.txt", "文本文件 (*.txt)"
        )
        if not path:
            return
        lines = [f"会话标题：{s['title']}", f"创建时间：{s['created_at']}", ""]
        if (s.get("summary") or "").strip():
            lines += [f"摘要：{s['summary']}", ""]
        lines.append("=" * 40)
        for m in msgs:
            role_name = "主人" if m["role"] == "user" else "莲心"
            lines.append(f"[{m['timestamp'][11:16]}] {role_name}：{m['content']}")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            QMessageBox.information(self, "导出成功", f"已保存到：{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _export_json(self):
        s, msgs = self._selected_session_and_msgs()
        if s is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 JSON",
            f"{s['title'][:20]}.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        data = {
            "session": {
                "id":         s["id"],
                "title":      s["title"],
                "created_at": s["created_at"],
                "summary":    s.get("summary") or "",
                "is_pinned":  bool(s.get("is_pinned")),
            },
            "messages": [
                {"role": m["role"], "content": m["content"],
                 "timestamp": m["timestamp"]}
                for m in msgs
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出成功", f"已保存到：{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _on_export_all(self):
        """将所有会话一次性导出为单个 JSON 文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出全部会话",
            "莲心AI全部对话.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        all_sessions = self._mgr.get_sessions()
        result = []
        for s in all_sessions:
            msgs = self._mgr.get_messages(s["id"])
            result.append({
                "session": {
                    "id":         s["id"],
                    "title":      s["title"],
                    "created_at": s["created_at"],
                    "summary":    s.get("summary") or "",
                    "is_pinned":  bool(s.get("is_pinned")),
                },
                "messages": [
                    {"role": m["role"], "content": m["content"],
                     "timestamp": m["timestamp"]}
                    for m in msgs
                ],
            })
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            QMessageBox.information(
                self, "导出成功",
                f"共导出 {len(result)} 个会话到：{path}"
            )
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ── 删除 ─────────────────────────────────────────────────

    def _on_delete_clicked(self):
        # 日期筛选模式：批量删除勾选的会话
        if self._select_all_cb.isVisible():
            ids_to_delete = []
            for i in range(self._session_list.count()):
                item = self._session_list.item(i)
                if item.checkState() == Qt.Checked:
                    sid = item.data(Qt.UserRole)
                    if sid is not None:
                        ids_to_delete.append(sid)
            if not ids_to_delete:
                return
            reply = QMessageBox.question(
                self, "批量删除",
                f"确定要删除选中的 {len(ids_to_delete)} 个会话吗？\n（删除后不可恢复）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            for sid in ids_to_delete:
                self._mgr.delete_session(sid)
            self._load_sessions(
                keyword=self._search_box.text(),
                date_str=self._date_filter.date().toString("yyyy-MM-dd")
            )
            return

        # 普通模式：删除当前选中
        row = self._session_list.currentRow()
        if not (0 <= row < len(self._sessions)):
            return
        s = self._sessions[row]
        count = self._mgr.get_message_count(s["id"])
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除会话「{s['title']}」吗？\n（共 {count} 条消息，删除后不可恢复）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._mgr.delete_session(s["id"])
        # 如果删除的是当前活跃会话，通知主窗口
        if s["id"] == self._current_session_id:
            self.session_deleted.emit(s["id"])
        self._load_sessions(
            keyword=self._search_box.text(),
            date_str=self._date_filter.date().toString("yyyy-MM-dd")
        )


    # ── 导入记忆 ─────────────────────────────────────────────

    def _on_import_memory_clicked(self):
        row = self._session_list.currentRow()
        if not (0 <= row < len(self._sessions)):
            return
        self.import_memory.emit(self._sessions[row]["id"])
        self.accept()

    # ── 右键菜单 ─────────────────────────────────────────────

    def _on_context_menu(self, pos):
        item = self._session_list.itemAt(pos)
        if item is None:
            return
        row = self._session_list.row(item)
        if not (0 <= row < len(self._sessions)):
            return
        s = self._sessions[row]

        menu = QMenu(self)
        menu.addAction("重命名",
                       lambda: self._on_rename_session(item))
        pin_text = "取消置顶" if s.get("is_pinned") else "置顶"
        menu.addAction(pin_text,
                       lambda: (self._mgr.toggle_pin(s["id"]),
                                self._load_sessions(self._search_box.text())))
        menu.addSeparator()
        menu.addAction("导出为 TXT",  self._export_txt)
        menu.addAction("导出为 JSON", self._export_json)
        menu.addSeparator()
        menu.addAction("删除", self._on_delete_clicked)
        menu.exec_(self._session_list.mapToGlobal(pos))
    # ── 日期筛选 + 日记生成 ─────────────────────────────────

    def _on_date_changed(self, date: QDate):
        if date.year() > 2000:
            date_str = date.toString("yyyy-MM-dd")
            self._load_sessions(keyword=self._search_box.text(), date_str=date_str)
            self._select_all_cb.show()
            self._select_all_cb.setChecked(False)
            self._btn_diary.show()
            self._btn_diary.setText("🌙 留下书页")
            self._btn_diary.setEnabled(False)
        else:
            self._load_sessions(keyword=self._search_box.text())
            self._select_all_cb.hide()
            self._btn_diary.hide()

    def _on_select_all(self, state):
        check = Qt.Checked if state == Qt.Checked else Qt.Unchecked
        for i in range(self._session_list.count()):
            item = self._session_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(check)
        self._update_diary_btn()

    def _on_item_checked(self, _item):
        self._update_diary_btn()

    def _update_diary_btn(self):
        count = 0
        for i in range(self._session_list.count()):
            item = self._session_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable and item.checkState() == Qt.Checked:
                count += 1
        self._btn_diary.setText(f"🌙 留下书页（{count}）")
        self._btn_diary.setEnabled(count > 0)

    def _on_generate_diary(self):
        date_str = self._date_filter.date().toString("yyyy-MM-dd")
        checked_ids = []
        for i in range(self._session_list.count()):
            item = self._session_list.item(i)
            if item.checkState() == Qt.Checked:
                sid = item.data(Qt.UserRole)
                if sid is not None:
                    checked_ids.append(sid)
        if not checked_ids:
            return

        if has_diary_for_date(date_str):
            reply = QMessageBox.question(self, "覆盖确认",
                f"{date_str} 已有莲心留下的书页，是否重写？",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        all_msgs = []
        for sid in checked_ids:
            for m in self._mgr.get_messages(sid):
                all_msgs.append({"role": m["role"], "content": m["content"]})
        if not all_msgs:
            QMessageBox.information(self, "提示", "所选会话没有消息内容。")
            return

        self._btn_diary.setEnabled(False)
        self._btn_diary.setText("正在写下…")
        self._diary_worker = DiaryWorker(date_str, all_msgs)
        self._diary_worker.finished.connect(self._on_diary_finished)
        self._diary_worker.start()

    def _on_diary_finished(self, success: bool, info: str):
        self._btn_diary.setEnabled(True)
        self._btn_diary.setText("🌙 留下书页")
        self._update_diary_btn()
        if success:
            QMessageBox.information(self, "书页已经留下",
                f"莲心已经写下 {info} 的右侧书页，可以到时间胶囊中查看。")
        else:
            QMessageBox.warning(self, "生成失败", f"错误：{info}")
