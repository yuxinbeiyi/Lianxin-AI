"""
CharacterWidget：莲心角色图像显示区域
支持状态机驱动的动画切换（待机/思考/说话/待机模式/自定义表情）
使用 GIF 动图循环播放，支持序列动画和事件触发。
"""

import os
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QScrollArea, QSlider,
    QLineEdit, QMessageBox, QFileDialog, QRadioButton,
    QButtonGroup, QDialog, QDialogButtonBox, QGraphicsOpacityEffect, QStackedWidget,
)
from PyQt5.QtCore import Qt, QTimer, QPoint, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFont, QIcon, QPixmap

from gui.animation_state_machine import AnimationStateMachine


# 状态定义（用于兼容旧接口）
STATE_NORMAL   = "normal"
STATE_THINKING = "thinking"
STATE_TALKING  = "talking"

# 各状态对应的显示文字和颜色
STATE_CONFIG = {
    STATE_NORMAL:   ("● 待机中",  "#6C7BFF"),
    STATE_THINKING: ("● 思考中",  "#FF9500"),
    STATE_TALKING:  ("● 说话中",  "#34C759"),
}


class CharacterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(400)
        from utils.resource_path import get_base_dir
        self._assets_dir = get_base_dir() / "assets"
        self._gif_dir = self._assets_dir / "GIF"
        self._current_state = STATE_NORMAL
        self._previous_mode = "normal"
        self._playing_arms_cross = False
        self._arms_cross_speech_pending = False
        self._function_expanded = False
        self._music_box_view = None


        self._build_ui()

        # 提前读取头像配置，静态模式下跳过 GIF 预加载，节省资源
        from config import get_avatar_config
        avatar_cfg = get_avatar_config()
        self._avatar_mode = avatar_cfg.get("mode", "animated")
        self._static_image_path = avatar_cfg.get("static_image_path", "")
        _is_static = (self._avatar_mode == "static" and self._static_image_path
                      and os.path.exists(self._static_image_path))

        config_path = self._assets_dir / "animation_config.json"
        self.anim_machine = AnimationStateMachine(
            label=self._gif_label,
            config_path=str(config_path),
            assets_dir=str(self._gif_dir),
            skip_initial=_is_static,
        )
        self.anim_machine.state_changed.connect(self._on_state_changed)

        if _is_static:
            self._apply_static_avatar(self._static_image_path)
        else:
            self.anim_machine.set_mode("normal")
        self._update_status_label(STATE_NORMAL)


    def _build_ui(self):
        self.setStyleSheet("background: transparent; border-right: 1px solid rgba(255,255,255,30);")

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignCenter)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 12, 16, 24)

        # GIF 动画区：85% 缩放居中，外层容器保持边框样式
        gif_wrapper = QWidget()
        gif_wrapper.setFixedSize(270, 430)
        gif_wrapper.setStyleSheet("""
            QWidget {
                background-color: rgba(30, 30, 45, 200);
                border-radius: 16px;
                border: 2px solid rgba(80, 80, 110, 150);
            }
        """)
        wrapper_layout = QVBoxLayout(gif_wrapper)
        wrapper_layout.setAlignment(Qt.AlignCenter)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        self._gif_label = QLabel()
        self._gif_label.setFixedSize(270, 430)
        self._gif_label.setAlignment(Qt.AlignCenter)
        self._gif_label.setStyleSheet("background: transparent; border: none;")
        wrapper_layout.addWidget(self._gif_label)

        gif_container = QHBoxLayout()
        gif_container.addSpacing(50)
        gif_container.addWidget(gif_wrapper)
        main_layout.addLayout(gif_container)

        # 状态作为头像框内的悬浮层，不再挤在头像与音乐盒之间。
        self._state_label = QLabel("● 待机中", gif_wrapper)
        self._state_label.setAlignment(Qt.AlignCenter)
        self._state_label.setFont(QFont("Microsoft YaHei UI", 9))
        self._state_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._state_label.setStyleSheet(
            "color: #BFC8FF; background: rgba(20, 24, 40, 185); "
            "border: 1px solid rgba(127, 142, 232, 170); border-radius: 11px; "
            "padding: 3px 9px;"
        )
        self._state_label.adjustSize()
        self._state_label.move(12, 12)
        self._state_label.raise_()

        # 音乐盒与功能区整体下移
        main_layout.addSpacing(8)

        # ========== 音乐盒控件（由 MusicBoxWidget 提供） ==========
        self._music_bar = QWidget()
        self._music_bar.setStyleSheet("background: transparent; border: none;")
        music_main_layout = QVBoxLayout(self._music_bar)
        music_main_layout.setContentsMargins(6, 0, 6, 0)
        music_main_layout.setSpacing(0)

        from gui.jiwen_status_widget import JiwenStatusWidget
        self._view_stack = QStackedWidget(self)
        self._view_stack.setStyleSheet("QStackedWidget { background: transparent; border: none; }")
        self._jiwen_status_widget = JiwenStatusWidget(self)
        self._view_stack.addWidget(self._music_bar)
        self._view_stack.addWidget(self._jiwen_status_widget)
        self._view_stack.setCurrentWidget(self._music_bar)
        main_layout.addWidget(self._view_stack)

        self._view_switch = QWidget(self)
        switch_layout = QHBoxLayout(self._view_switch)
        switch_layout.setContentsMargins(8, 0, 8, 0)
        switch_layout.setSpacing(4)
        self._music_view_button = QPushButton("音乐盒", self._view_switch)
        self._jiwen_view_button = QPushButton("五轴意识", self._view_switch)
        for button in (self._music_view_button, self._jiwen_view_button):
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(24)
            button.setStyleSheet("QPushButton { color:#AEB7D4; background:#252B45; border:1px solid #3D4668; border-radius:6px; font-size:8pt; } QPushButton:checked { color:#FFFFFF; background:#4A5ADE; border-color:#8F9BFF; }")
            switch_layout.addWidget(button)
        self._music_view_button.setChecked(True)
        self._music_view_button.clicked.connect(lambda: self._switch_sidebar_view(0))
        self._jiwen_view_button.clicked.connect(lambda: self._switch_sidebar_view(1))
        main_layout.addWidget(self._view_switch)

        # ========== 功能区弹出触发按钮 ==========
        self._btn_function_toggle = QPushButton("▲ 功能中心")
        self._btn_function_toggle.setFixedHeight(32)
        self._btn_function_toggle.setFont(QFont("Microsoft YaHei UI", 9))
        self._btn_function_toggle.setCursor(Qt.PointingHandCursor)
        self._btn_function_toggle.setStyleSheet("""
            QPushButton {
                background-color: #252538;
                color: #C9CCDA;
                border: 1px solid #3D3D5A;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #303049;
                color: #F0F1F8;
                border-color: #6C7BFF;
            }
            QPushButton:pressed { background-color: #383854; }
        """)
        self._btn_function_toggle.clicked.connect(self._toggle_function_panel)
        main_layout.addWidget(self._btn_function_toggle)

        # ========== 功能区弹出面板（overlay，初始隐藏） ==========
        self._function_popup = QWidget(self)
        self._function_popup.setObjectName("function_popup")
        self._function_popup.setStyleSheet("""
            QWidget#function_popup {
                background-color: rgba(24, 24, 42, 248);
                border: 1px solid #3D3D5A;
                border-radius: 14px;
            }
        """)
        popup_layout = QVBoxLayout(self._function_popup)
        popup_layout.setSpacing(12)
        popup_layout.setContentsMargins(16, 14, 16, 16)

        # 顶部标题栏
        popup_header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        popup_title = QLabel("功能中心")
        popup_title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        popup_title.setStyleSheet("color: #F0F1F8; background: transparent; border: none;")
        popup_subtitle = QLabel("莲心的工具与设置")
        popup_subtitle.setFont(QFont("Microsoft YaHei UI", 8))
        popup_subtitle.setStyleSheet("color: #8F96B2; background: transparent; border: none;")
        title_box.addWidget(popup_title)
        title_box.addWidget(popup_subtitle)
        popup_header.addLayout(title_box)
        popup_header.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setToolTip("收起功能中心")
        close_btn.setFixedSize(30, 30)
        close_btn.setFont(QFont("Microsoft YaHei UI", 10))
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #252538;
                color: #AEB2C5;
                border-radius: 8px;
                border: 1px solid #3D3D5A;
            }
            QPushButton:hover {
                background-color: #303049;
                color: #F0F1F8;
                border-color: #6C7BFF;
            }
        """)
        close_btn.clicked.connect(self._toggle_function_panel)
        popup_header.addWidget(close_btn)
        popup_layout.addLayout(popup_header)

        # 统一的功能卡片。语义色仅作为左侧细线，避免高饱和色块破坏主界面的一致性。
        self._btn_accompany = self._create_button("🌊  数据潮汐", "#6C7BFF")
        self._btn_settings = self._create_button("⚙️  全局设置", "#8F96B2")
        self._btn_study_room = self._create_button("📚  莲心自习室", "#C94B55")
        self._btn_api_config = self._create_button("🔑  API Key", "#F0A84B")
        self._btn_alarm = self._create_button("⏰  闹钟与提醒", "#E2647C")
        self._btn_camera = self._create_button("👁️  视觉理解", "#5B9A8B")
        self._btn_emotion = self._create_button("🧪  涟漪情感系统", "#9B72CF")
        self._btn_sound = self._create_button("🔊  声音设置", "#48A999")
        self._btn_memory = self._create_button("🧠  棱镜记忆系统", "#77839A")
        self._btn_workflow = self._create_button("🧭  任务运行中心", "#5F83C7")
        self._btn_network = self._create_button("🌐  网络设置", "#4C95D9")
        self._btn_capability = self._create_button("🧩  能力中枢", "#9670C9")
        self._btn_persona = self._create_button("🎭  人格枢控", "#7C72D8")
        self._btn_constellation = self._create_button("🌌  星图系统", "#4C9ED9")
        self._btn_proactive = self._create_button("💬  主动聊天", "#52B788")
        self._btn_qq_bridge = self._create_button("🐧  QQ 聊天", "#4B8FD1")
        self._btn_wechat_bridge = self._create_button("💬  微信聊天", "#36A66D")
        self._btn_diary = self._create_button("🌙  时间胶囊", "#D98A45")
        self._btn_voice_stt = self._create_button("🎙️  语音转录", "#52B788")

        scroll_area = QScrollArea(self._function_popup)
        scroll_area.setObjectName("function_scroll_area")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
            QScrollBar::handle:vertical {
                background: #4B4B68;
                min-height: 30px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover { background: #626284; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        scroll_content = QWidget()
        scroll_content.setObjectName("function_scroll_content")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 4, 0)
        scroll_layout.setSpacing(12)
        self._add_function_group(scroll_layout, "常用", (
            self._btn_study_room,
            self._btn_alarm,
            self._btn_diary, self._btn_accompany,
        ))
        self._add_function_group(scroll_layout, "莲心", (
            self._btn_emotion, self._btn_memory,
            self._btn_workflow, self._btn_proactive, self._btn_capability,
            self._btn_persona, self._btn_constellation,
        ))
        self._add_function_group(scroll_layout, "感知与声音", (
            self._btn_camera, self._btn_sound,
            self._btn_voice_stt, self._btn_settings,
        ))
        self._add_function_group(scroll_layout, "连接与服务", (
            self._btn_api_config, self._btn_network,
            self._btn_qq_bridge, self._btn_wechat_bridge,
        ))
        scroll_layout.addStretch()
        scroll_area.setWidget(scroll_content)
        popup_layout.addWidget(scroll_area, 1)

        self._function_opacity = QGraphicsOpacityEffect(self._function_popup)
        self._function_popup.setGraphicsEffect(self._function_opacity)
        self._function_animation = QPropertyAnimation(
            self._function_opacity, b"opacity", self
        )
        self._function_animation.setDuration(180)
        self._function_animation.setEasingCurve(QEasingCurve.OutCubic)

        self._function_popup.hide()
        self._function_expanded = False

    def _create_button(self, text: str, color: str = "#6C7BFF") -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("function_card")
        btn.setFixedHeight(46)
        btn.setFont(QFont("Microsoft YaHei UI", 9))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(text.replace("  ", " "))
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #252538;
                color: #E8EAF2;
                border: 1px solid #34344D;
                border-left: 3px solid {color};
                border-radius: 9px;
                padding: 6px 10px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: #303049;
                color: #FFFFFF;
                border-color: #4A4A69;
                border-left: 3px solid {color};
            }}
            QPushButton:pressed {{
                background-color: #383854;
                padding-left: 11px;
            }}
        """)
        return btn

    @staticmethod
    def _add_function_group(layout: QVBoxLayout, title: str, buttons) -> None:
        """向功能中心添加一个两列分组，并保持各业务按钮对象不变。"""
        label = QLabel(title)
        label.setObjectName("function_group_title")
        label.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
        label.setStyleSheet(
            "color: #8F96B2; background: transparent; border: none; padding-left: 2px;"
        )
        layout.addWidget(label)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, button in enumerate(buttons):
            grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(grid)

    # ========== 音乐盒控件安装 / 获取方法 ==========
    def install_music_box_view(self, view):
        self._music_box_view = view
        layout = self._music_bar.layout()
        if layout is not None:
            layout.addWidget(view)

    def get_music_box_view(self):
        return self._music_box_view

    def is_function_expanded(self) -> bool:
        """功能区覆盖面板当前是否处于展开状态"""
        return bool(getattr(self, "_function_expanded", False))

    def _set_music_box_visible(self, visible: bool):
        """控制音乐盒 Web 视图的显隐。

        QWebEngineView 是原生子窗口（HWND），会穿透普通 Qt 控件的 Z 序，
        因此功能区覆盖面板打开时必须隐藏它，否则音乐盒会显示在覆盖面板上层。
        关闭面板后，仅当音乐盒仍是当前侧栏视图时才恢复显示。
        """
        view = getattr(self, "_music_box_view", None)
        if view is None:
            return
        if visible:
            if self._view_stack.currentWidget() is self._music_bar:
                view.show()
        else:
            view.hide()

    def _sync_music_box_visibility(self):
        """按当前侧栏视图 + 功能区状态同步音乐盒 Web 视图显隐。"""
        view = getattr(self, "_music_box_view", None)
        if view is None:
            return
        if self._view_stack.currentWidget() is self._music_bar and not self._function_expanded:
            view.show()
        else:
            view.hide()

    def _switch_sidebar_view(self, index: int):
        self._view_stack.setCurrentIndex(int(index))
        self._music_view_button.setChecked(index == 0)
        self._jiwen_view_button.setChecked(index == 1)
        if index == 1:
            self._jiwen_status_widget.refresh()
        # 修复：切换侧栏视图后同步音乐盒 Web 视图显隐。
        self._sync_music_box_visibility()

    def _toggle_function_panel(self):
        """弹出/收起功能区覆盖面板"""
        self._function_expanded = not self._function_expanded
        self._function_animation.stop()
        try:
            self._function_animation.finished.disconnect()
        except TypeError:
            pass

        if self._function_expanded:
            self._position_function_popup()
            self._function_opacity.setOpacity(0.0)
            self._function_popup.show()
            self._function_popup.raise_()
            # QWebEngineView 是原生子窗口（HWND），会穿透普通 Qt 控件的 Z 序，
            # 打开功能区覆盖面板时必须隐藏音乐盒 Web 视图，避免它显示在最上层。
            self._set_music_box_visible(False)
            self._btn_function_toggle.setText("▼ 收起")
            self._function_animation.setStartValue(0.0)
            self._function_animation.setEndValue(1.0)
            self._function_animation.start()
        else:
            self._btn_function_toggle.setText("▲ 功能中心")
            self._set_music_box_visible(True)
            self._function_animation.setStartValue(self._function_opacity.opacity())
            self._function_animation.setEndValue(0.0)
            self._function_animation.finished.connect(self._finish_function_popup_hide)
            self._function_animation.start()

    def _finish_function_popup_hide(self):
        """仅在收起状态结束淡出，避免快速连点造成面板误隐藏。"""
        if not self._function_expanded:
            self._function_popup.hide()

    def _position_function_popup(self):
        """将功能区面板定位到覆盖 GIF + 音乐盒区域"""
        toggle_y = self._btn_function_toggle.mapTo(self, QPoint(0, 0)).y()
        self._function_popup.setGeometry(0, 0, self.width(), toggle_y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._function_expanded:
            self._position_function_popup()

    # ========== 动画控制方法（保持不变） ==========
    def set_talking(self):
        if self._avatar_mode == "static":
            self._update_status_label(STATE_TALKING)
            return
        if self._playing_arms_cross:
            self._arms_cross_speech_pending = True
            return
        self.anim_machine.trigger_event("speak_start")
        self._update_status_label(STATE_TALKING)


    def set_normal(self):
        if self._avatar_mode == "static":
            self._update_status_label(STATE_NORMAL)
            return
        if self._playing_arms_cross:
            return
        self.anim_machine.trigger_event("speak_end")
        self.anim_machine.trigger_event("think_end")
        self._update_status_label(STATE_NORMAL)


    def set_thinking(self):
        self.start_thinking()

    def start_thinking(self):
        if self._avatar_mode == "static":
            self._update_status_label(STATE_THINKING)
            return
        if self._playing_arms_cross:
            return
        self._previous_mode = self.anim_machine.current_mode
        self.anim_machine.set_mode("thinking")
        self._update_status_label(STATE_THINKING)


    def stop_thinking(self, on_finished=None):
        if self._avatar_mode == "static":
            self._update_status_label(STATE_NORMAL)
            if on_finished:
                on_finished()
            return
        if self._playing_arms_cross:
            # 动画不能阻断业务回调。回复展示会自行切换说话状态。
            if on_finished:
                on_finished()
            return
        if self.anim_machine.current_mode == "thinking":
            self.anim_machine.trigger_event("stop_thinking")
            QTimer.singleShot(2000, lambda: self._restore_after_thinking(on_finished))
        else:
            self._restore_after_thinking(on_finished)


    def _restore_after_thinking(self, on_finished=None):
        if self._avatar_mode == "static":
            self._update_status_label(STATE_NORMAL)
            if on_finished:
                on_finished()
            return
        prev = getattr(self, '_previous_mode', 'normal')
        self.anim_machine.set_mode(prev)
        self._update_status_label(STATE_NORMAL)
        if on_finished:
            on_finished()


    def set_thinking_status(self):
        if self._avatar_mode == "static" or self._playing_arms_cross:
            self._update_status_label(STATE_THINKING)
            return
        self._update_status_label(STATE_THINKING)


    def set_normal_status(self):
        if self._avatar_mode == "static" or self._playing_arms_cross:
            self._update_status_label(STATE_NORMAL)
            return
        self._update_status_label(STATE_NORMAL)


    def _on_state_changed(self, state_name: str):
        if self._avatar_mode == "static":
            return
        if self.anim_machine.current_mode == "standby" and state_name == "normal_idle":
            self.anim_machine.set_mode("normal")
            self._update_status_label(STATE_NORMAL)


    def play_arms_cross(self, on_finished=None):
        if self._avatar_mode == "static":
            if on_finished:
                on_finished()
            return
        if self._playing_arms_cross:
            if on_finished:
                on_finished()
            return
        self._playing_arms_cross = True
        self._arms_cross_speech_pending = False
        self._previous_arms_cross_mode = self.anim_machine.current_mode
        self.anim_machine.set_mode("arms_cross")
        try:
            cfg = self.anim_machine.config["modes"]["arms_cross"]["states"]
            dur1 = cfg["cross_start"]["duration"]
            dur2 = cfg["cross_end"]["duration"]
            total_duration = int((dur1 + dur2) * 1000)
        except:
            total_duration = 10000
        QTimer.singleShot(total_duration, lambda: self._restore_after_arms_cross(on_finished))

    def _restore_after_arms_cross(self, on_finished=None):
        if self._avatar_mode == "static":
            self._playing_arms_cross = False
            if on_finished:
                on_finished()
            return
        prev = getattr(self, '_previous_arms_cross_mode', 'normal')
        self.anim_machine.set_mode(prev)
        self._playing_arms_cross = False
        if self._arms_cross_speech_pending:
            self._arms_cross_speech_pending = False
            self.anim_machine.trigger_event("speak_start")
            self._update_status_label(STATE_TALKING)
        else:
            if prev == "normal":
                self._update_status_label(STATE_NORMAL)
            elif prev == "standby":
                self._update_status_label(STATE_NORMAL)
        if on_finished:
            on_finished()

    def enter_standby(self):
        if self._avatar_mode == "static" or self._playing_arms_cross:
            return
        self.anim_machine.set_mode("standby")


    def exit_standby(self):
        if self._avatar_mode == "static" or self._playing_arms_cross:
            return
        self.anim_machine.trigger_event("standby_end")

    def set_arms_cross(self):
        if self._avatar_mode != "static":
            self.play_arms_cross()


    def set_normal_mode(self):
        if self._avatar_mode == "static" or self._playing_arms_cross:
            return
        self.anim_machine.set_mode("normal")


    def wave_seen(self):
        if self._avatar_mode != "static":
            self.anim_machine.trigger_event("wave_seen")

    def smile_seen(self):
        if self._avatar_mode != "static":
            self.anim_machine.trigger_event("smile_seen")

    def get_accompany_button(self):
        return self._btn_accompany

    def get_settings_button(self):
        return self._btn_settings

    def get_study_room_button(self):
        return self._btn_study_room

    def get_api_config_button(self):
        return self._btn_api_config

    def get_alarm_button(self):
        return self._btn_alarm

    def get_camera_button(self):
        return self._btn_camera

    def get_emotion_button(self):
        return self._btn_emotion

    def get_sound_button(self):
        return self._btn_sound
    def get_memory_button(self):
        return self._btn_memory
    def get_workflow_button(self):
        return self._btn_workflow

    def get_network_button(self):
        return self._btn_network

    def get_capability_button(self):
        return self._btn_capability
    def get_constellation_button(self):
        return self._btn_constellation
    def get_persona_button(self):
        return self._btn_persona
    def get_proactive_button(self):
        return self._btn_proactive
    def get_qq_bridge_button(self):
        return self._btn_qq_bridge
    def get_wechat_bridge_button(self):
        return self._btn_wechat_bridge
    def get_diary_button(self):
        return self._btn_diary

    def get_voice_stt_button(self):
        return self._btn_voice_stt

    def _update_status_label(self, state: str):
        text, color = STATE_CONFIG.get(state, ("● 待机中", "#6C7BFF"))
        self._state_label.setText(text)
        self._state_label.setStyleSheet(
            f"color: {color}; background: rgba(20, 24, 40, 185); "
            "border: 1px solid rgba(127, 142, 232, 170); border-radius: 11px; "
            "padding: 3px 9px;"
        )
        self._state_label.adjustSize()
        self._state_label.move(12, 12)
        self._state_label.raise_()

    def _apply_static_avatar(self, image_path: str):
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self._gif_label.width(), self._gif_label.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.anim_machine.set_static_pixmap(scaled)
        self._static_image_path = image_path

    def _switch_to_animated(self):
        self._avatar_mode = "animated"
        self.anim_machine.restore_animation()

    def _show_avatar_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("头像显示设置")
        dlg.setFixedSize(420, 340)
        dlg.setStyleSheet("background-color: #F5F5FA;")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("🎨 头像显示设置")
        title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        title.setStyleSheet("color: #3A3A5C;")
        layout.addWidget(title)

        group = QButtonGroup(dlg)
        radio_animated = QRadioButton("动画状态机（动态GIF）")
        radio_static = QRadioButton("静态头像（本地图片）")
        radio_animated.setFont(QFont("Microsoft YaHei UI", 10))
        radio_static.setFont(QFont("Microsoft YaHei UI", 10))
        radio_animated.setStyleSheet("color: #333;")
        radio_static.setStyleSheet("color: #333;")
        group.addButton(radio_animated, 0)
        group.addButton(radio_static, 1)
        layout.addWidget(radio_animated)
        layout.addWidget(radio_static)

        if self._avatar_mode == "static":
            radio_static.setChecked(True)
        else:
            radio_animated.setChecked(True)

        path_row = QHBoxLayout()
        path_edit = QLineEdit()
        path_edit.setPlaceholderText("选择本地图片...")
        path_edit.setFont(QFont("Microsoft YaHei UI", 9))
        path_edit.setReadOnly(True)
        path_edit.setStyleSheet("background: white; border: 1px solid #D0D0E0; border-radius: 6px; padding: 4px 8px;")
        if self._static_image_path:
            path_edit.setText(self._static_image_path)
        path_edit.setEnabled(radio_static.isChecked())
        path_row.addWidget(path_edit)

        browse_btn = QPushButton("浏览")
        browse_btn.setFixedWidth(60)
        browse_btn.setFont(QFont("Microsoft YaHei UI", 9))
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.setStyleSheet("background: #6C7BFF; color: white; border-radius: 6px; border: none; padding: 4px;")
        browse_btn.setEnabled(radio_static.isChecked())
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        radio_static.toggled.connect(lambda checked: [
            path_edit.setEnabled(checked),
            browse_btn.setEnabled(checked)
        ])

        browse_btn.clicked.connect(lambda: self._browse_avatar_image(path_edit))

        tip = QLabel("💡 选择静态头像可避免动画卡顿，节省 CPU 资源。图片将自动缩放适应。")
        tip.setFont(QFont("Microsoft YaHei UI", 8))
        tip.setStyleSheet("color: #888; background: #F0F0F8; padding: 8px; border-radius: 6px;")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch()

        btn_box = QDialogButtonBox()
        save_btn = btn_box.addButton("保存", QDialogButtonBox.AcceptRole)
        cancel_btn = btn_box.addButton("取消", QDialogButtonBox.RejectRole)
        save_btn.setStyleSheet("background: #6C7BFF; color: white; border-radius: 8px; padding: 6px 20px; border: none;")
        cancel_btn.setStyleSheet("background: #E0E0F0; color: #555; border-radius: 8px; padding: 6px 20px; border: none;")
        layout.addWidget(btn_box)

        btn_box.accepted.connect(lambda: self._on_avatar_dialog_save(
            radio_static.isChecked(), path_edit.text().strip(), dlg
        ))
        btn_box.rejected.connect(dlg.reject)

        dlg.exec_()

    def _browse_avatar_image(self, path_edit):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择莲心头像", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if not file_path:
            return
        from gui.avatar_widgets import AvatarCropDialog
        from utils.paths import get_user_data_dir
        crop_dialog = AvatarCropDialog(
            file_path,
            self,
            crop_ratio=270 / 430,
            output_size=(810, 1290),
            title="调整静态头像构图",
        )
        if crop_dialog.exec_() != QDialog.Accepted:
            return
        output_dir = get_user_data_dir() / "avatars"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "character_static.png"
        if not crop_dialog.cropped_pixmap().save(str(output_path), "PNG"):
            QMessageBox.warning(self, "头像设置", "裁剪后的头像保存失败，请重试。")
            return
        path_edit.setText(str(output_path))

    def _on_avatar_dialog_save(self, is_static: bool, image_path: str, dlg: QDialog):
        from config import save_avatar_config

        if is_static:
            if not image_path or not os.path.exists(image_path):
                QMessageBox.warning(dlg, "提示", "请先选择一张图片作为静态头像。")
                return
            self._avatar_mode = "static"
            self._static_image_path = image_path
            self._apply_static_avatar(image_path)
            save_avatar_config({
                "mode": "static",
                "static_image_path": image_path,
                "static_source_path": image_path,
            })
        else:
            self._avatar_mode = "animated"
            self._switch_to_animated()
            save_avatar_config({
                "mode": "animated",
                "static_image_path": self._static_image_path,
                "static_source_path": self._static_image_path,
            })

        dlg.accept()
