"""
AccompanyDialog：陪伴统计自定义对话框
显示莲心表情包、陪伴时长、启动次数、初识天数、音乐陪伴统计
"""

import os
from datetime import datetime
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QFont


class AccompanyDialog(QDialog):
    """陪伴统计对话框（含音乐统计）"""

    dialog_closed = pyqtSignal()

    def __init__(self, accompany_stats, music_stats=None, parent=None):
        super().__init__(parent)
        self._stats = accompany_stats
        self._music_stats = music_stats   # 可选
        self.setWindowTitle("📊 陪伴统计")
        self.setMinimumSize(500, 480)
        self.resize(600, 520)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._build_ui()
        self._update_content()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # 主内容区域（水平布局：左侧图片 + 右侧文字）
        content_layout = QHBoxLayout()
        content_layout.setSpacing(20)

        # ── 左侧：莲心表情包 ──
        self._image_label = QLabel()
        self._image_label.setFixedSize(200, 200)
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("""
            QLabel {
                background-color: rgba(240, 242, 250, 200);
                border-radius: 20px;
                border: 2px solid #C8CCEE;
            }
        """)
        content_layout.addWidget(self._image_label)

        # ── 右侧：文字信息区域（垂直滚动） ──
        right_layout = QVBoxLayout()
        right_layout.setSpacing(12)

        self._duration_label = QLabel()
        self._duration_label.setFont(QFont("Microsoft YaHei UI", 11))
        self._duration_label.setWordWrap(True)
        self._duration_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._duration_label.setStyleSheet("color: #1ABC9C;")

        self._session_label = QLabel()
        self._session_label.setFont(QFont("Microsoft YaHei UI", 11))
        self._session_label.setWordWrap(True)
        self._session_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._session_label.setStyleSheet("color: #1ABC9C;")

        self._first_meet_label = QLabel()
        self._first_meet_label.setFont(QFont("Microsoft YaHei UI", 10))
        self._first_meet_label.setWordWrap(True)
        self._first_meet_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._first_meet_label.setStyleSheet("color: #888888;")

        # 新增：音乐陪伴统计标签
        self._music_label = QLabel()
        self._music_label.setFont(QFont("Microsoft YaHei UI", 10))
        self._music_label.setWordWrap(True)
        self._music_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._music_label.setStyleSheet("color: #1ABC9C;")  # 咖啡色
        self._avatar_interaction_label = QLabel()
        self._avatar_interaction_label.setFont(QFont("Microsoft YaHei UI", 10))
        self._avatar_interaction_label.setWordWrap(True)
        self._avatar_interaction_label.setStyleSheet("color: #A98BFF;")
        self._visual_stats_label = QLabel()
        self._visual_stats_label.setFont(QFont("Microsoft YaHei UI", 10))
        self._visual_stats_label.setWordWrap(True)
        self._visual_stats_label.setStyleSheet("color: #5FB3B3;")

        right_layout.addWidget(self._duration_label)
        right_layout.addWidget(self._session_label)
        right_layout.addWidget(self._first_meet_label)
        right_layout.addWidget(self._music_label)
        right_layout.addWidget(self._avatar_interaction_label)
        right_layout.addWidget(self._visual_stats_label)
        right_layout.addStretch()

        content_layout.addLayout(right_layout)
        content_layout.setStretch(0, 1)
        content_layout.setStretch(1, 2)

        layout.addLayout(content_layout)

        # ── 底部按钮 ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._clear_avatar_btn = QPushButton("清空头像互动")
        self._clear_avatar_btn.setFixedHeight(36)
        self._clear_avatar_btn.clicked.connect(self._clear_avatar_events)
        btn_layout.addWidget(self._clear_avatar_btn)

        self._close_btn = QPushButton("好耶！")
        self._close_btn.setFixedSize(100, 36)
        self._close_btn.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border-radius: 18px;
                border: none;
            }
            QPushButton:hover {
                background-color: #5A6AEE;
            }
            QPushButton:pressed {
                background-color: #4A5ADE;
            }
        """)
        self._close_btn.clicked.connect(self._on_close)

        btn_layout.addWidget(self._close_btn)

        layout.addLayout(btn_layout)

    def _update_content(self):
        """更新所有显示内容"""
        # 加载头像
        from utils.resource_path import get_asset_path
        png_path = get_asset_path("头像", "开玩笑.png")
        jpg_path = get_asset_path("头像", "开玩笑.jpg")
        meme_path = png_path if png_path.exists() else jpg_path

        if meme_path.exists():
            pixmap = QPixmap(str(meme_path))
            scaled = pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._image_label.setPixmap(scaled)
        else:
            self._image_label.setText("(图片未找到)")

        # 陪伴时长
        duration_str = self._stats.get_current_formatted_duration()
        self._duration_label.setText(f"🌸 莲心已经陪伴你长达 {duration_str} 了哦~(*´∀ ˋ*)")

        # 启动次数
        session_count = self._stats.get_stats()["session_count"]
        self._session_label.setText(f"📊 你已经累计启动莲心 {session_count} 次了诶！(=´ω`=)")

        # 初识日期
        if self._stats.has_first_meet_date():
            first_date_str = self._stats.get_first_meet_date()
            try:
                first_date = datetime.strptime(first_date_str, "%Y-%m-%d")
                total_days = self._stats.get_total_days_since_first_meet()
                self._first_meet_label.setText(
                    f"📅 你于 {first_date.year}年{first_date.month}月{first_date.day}日 与莲心初识，\n"
                    f"   这是你们一起度过的第 {total_days} 天~"
                )
            except ValueError:
                self._first_meet_label.setText("📅 初识日期格式有误，请在设置中重新设置。")
        else:
            self._first_meet_label.setText("📅 请先在「全局设置」中设置与莲心初识的日期~")

        # 音乐陪伴统计
        if self._music_stats is not None:
            total_hours = self._music_stats.get_total_hours()
            song_name, song_seconds = self._music_stats.get_most_played_song()
            if song_name:
                # 转换秒数为 小时/分钟
                song_hours = song_seconds // 3600
                song_minutes = (song_seconds % 3600) // 60
                song_time_str = f"{song_hours}小时{song_minutes}分钟" if song_hours > 0 else f"{song_minutes}分钟"
                music_text = f"🎵 莲心陪你听歌累计 {total_hours:.1f} 小时啦！\n   你们听最久的音乐是「{song_name}」，共 {song_time_str} ~"
            else:
                music_text = "🎵 还没有听过歌呢～ 点开音乐盒播放一首吧！"
        else:
            music_text = "🎵 音乐统计功能未启用。"
        self._music_label.setText(music_text)
        visual = self._stats.get_visual_stats()
        def fmt(seconds):
            seconds = int(seconds or 0)
            return f"{seconds // 3600}小时{(seconds % 3600) // 60}分钟"
        counts = visual.get("gesture_counts", {})
        self._visual_stats_label.setText(
            f"一屏之隔\n视频陪伴：{fmt(visual.get('video_seconds'))}\n"
            f"语音通话：{fmt(visual.get('voice_call_seconds'))}\n"
            f"视觉互动：{visual.get('gesture_interaction_count', 0)} 次 "
            f"（挥手 {counts.get('wave', 0)} / 大拇指 {counts.get('thumbs_up', 0)} / OK {counts.get('ok', 0)}）"
        )
        avatar_stats = self._stats.get_avatar_interactions()
        summary = self._stats.get_avatar_interaction_summary()
        self._avatar_interaction_label.setText(
            f"头像互动：你拍了拍莲心 {avatar_stats.get('user_tap_count', 0)} 次，"
            f"莲心反拍了你 {avatar_stats.get('assistant_counter_tap_count', 0)} 次\n"
            f"本次累计互动 {summary.get('total', 0)} 次，今日 {summary.get('today', 0)} 次，本周 {summary.get('week', 0)} 次\n"
            f"莲心主动拍你 {summary.get('assistant_taps', 0)} 次，摸头 {summary.get('assistant_headpats', 0)} 次；"
            f"你摸头 {summary.get('user_headpats', 0)} 次，自己拍自己 {summary.get('self_taps', 0)} 次\n"
            f"音效 {summary.get('sound_count', 0)} 次，LLM 动态回复 {summary.get('llm_success', 0)} 次，"
            f"备用回复 {summary.get('fallback', 0)} 次；最长连续 {summary.get('streak_max', 0)} 次"
        )

    def _clear_avatar_events(self):
        from PyQt5.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "清空头像互动", "只清空头像互动统计和事件记录，不影响陪伴时长与初识日期，确定继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._stats.clear_avatar_events()
            self._stats.reset_visual_stats()
            self._update_content()

    def _on_close(self):
        self.dialog_closed.emit()
        self.accept()
