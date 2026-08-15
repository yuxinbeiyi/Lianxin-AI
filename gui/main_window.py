"""
MainWindow：莲心AI 主窗口（Phase 4 — 含语音输入/输出）
"""

import webbrowser
import os
import ctypes
from datetime import datetime
from ctypes import wintypes
from typing import Optional
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QMessageBox, QDialog, QTextEdit, QMenu, QCheckBox, QSizeGrip, QShortcut
)
from PyQt5.QtCore import (
    Qt, QTimer, QAbstractNativeEventFilter, QPoint, QObject,
    QThread, pyqtSignal, QTime, QSettings,
)
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor, QKeySequence
from pathlib import Path
from brain.agent import AgentCore
from utils.emotion_manager import parse_emotion_tag as _strip_emotion_tag
from voice.listener import VoiceListener
from voice.speaker  import VoiceSpeaker
from gui.character_widget import CharacterWidget
from gui.background_widget import BackgroundWidget, FrostedPanel
from gui.chat_widget       import ChatWidget
from gui.input_panel       import InputPanel
from gui.history_dialog    import HistoryDialog
from gui.proactive_dialog  import ProactiveDialog
from gui.settings_dialog   import SettingsDialog
from gui.api_config_dialog import ApiConfigDialog
from gui.alarm_dialog      import AlarmDialog
from gui.qq_settings_dialog import QqSettingsDialog
from gui.wechat_settings_dialog import WeChatSettingsDialog
from gui.network_settings_dialog import NetworkSettingsDialog
from gui.capability_center import CapabilityCenter
from gui.voice_stt_dialog import VoiceSTTDialog
from brain.auto_task_scheduler import AutoTaskScheduler
from brain.auto_task_manager import get_auto_task_manager
from brain.auto_task_executor import execute_auto_task
from config import has_api_key
from brain.decision import decide
from workers.agent_worker      import AgentWorker
from workers.voice_worker      import VoiceWorker
from workers.speaker_worker    import SpeakerWorker
from workers.standby_worker    import StandbyWorker   # 不再需要 contains_end_phrase, strip_end_phrase
from brain.voice_duplex        import VoiceDuplexManager
from utils.accompany_stats  import AccompanyStats
import json
from gui.music_box.bridge import MusicBoxBridge
from gui.music_box.music_box_widget import MusicBoxWidget
from gui.music_box.music_space_window import MusicSpaceWindow

from utils.proactive_chat import ProactiveChatScheduler
from utils.settings import get_settings
from utils.autostart import check_network
from utils.alarm_manager import AlarmManager, REPEAT_LABELS
from utils.todo_manager import TodoManager

from datetime import datetime
import sys
import threading
from utils.diary import init_diary_db, DiaryWorker
from config import get_diary_config
import pygame
import random
from utils.sound import play_sound
import time
from utils.music_stats import MusicStats
from gui.note_dialog import NoteDialog
from utils.reminder_manager import ReminderManager
from gui.reminder_dialog import ReminderDialog
from workers.smart_reminder_worker import SmartReminderWorker
from utils.emotion_manager import parse_emotion_tag, get_random_emotion_image

# ── Galgame 模式 ────────────────────────────────────────────
from gui.galgame.tachie_window import TachieWindow
from gui.galgame.galgame_dialog import GalgameDialog
from gui.galgame.expression_manager import ExpressionManager
from gui.proactive_controller import ProactivePresentationController
from gui.bridge_controller import BridgeController
from gui.avatar_action_router import AvatarActionRouter
from gui.window_experience import WindowExperienceController
from utils.platform_capabilities import get_platform_capabilities
# ── Win32 全局热键 ───────────────────────────────────────────
WM_HOTKEY = 0x0312
_HOTKEY_ID = 1
_PLATFORM_CAPS = get_platform_capabilities()
user32 = ctypes.windll.user32 if _PLATFORM_CAPS.is_windows else None


class _WinHotkeyFilter(QAbstractNativeEventFilter):

    """捕获 WM_HOTKEY 消息，桥接到 Qt 主线程。"""
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, eventType, message):
        if eventType in ('windows_generic_MSG', 'windows_dispatcher_MSG'):
            msg = ctypes.wintypes.MSG.from_address(int(message.__int__()))
            if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                self._callback()
                return True, 0
        return False, 0


_AUTOSTART_WELCOME = "嘿嘿~又睡了一觉，终于等到你开机了，我已经偷偷开机自启动了哦~"
_AUTOSTART_NET_INTERVAL_MS = 30 * 1000   # 每 30 秒检测一次网络
_AUTOSTART_NET_MAX_ATTEMPTS = 30         # 最多等 15 分钟（30 × 30s）


class MainWindow(QMainWindow):
    _route_ready = pyqtSignal(str, bool, object)
    _route_failed = pyqtSignal(str)
    _auto_task_parsed_signal = pyqtSignal(bool, str, object)  # (success, message, task)
    _auto_task_done_signal = pyqtSignal(str, bool, str)       # (task_id, success, message)
    _duplex_voice_start_signal = pyqtSignal()                  # 跨线程：VAD 检测到语音 → UI 更新
    _duplex_transcript_signal = pyqtSignal(str)                # 跨线程：转录结果 → 发送消息
    def __init__(self, autostart_mode: bool = False):
        super().__init__()
        self._autostart_mode = autostart_mode
        # 无边框窗口 + 圆角
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._drag_pos = None
        # 添加：窗口闪烁相关
        self._is_minimized = False  # 记录窗口是否最小化
        self._pending_arms_cross = False   # 是否需要播放抱胸动画
        # ── 核心模块 ──────────────────────────────────────────
        self._agent   = AgentCore()
        self._listener = VoiceListener(model_size="base", language="zh")
        self._speaker  = VoiceSpeaker(voice="zh-CN-XiaoxiaoNeural")

        # ── 陪伴统计模块 ──────────────────────────────────────
        self._accompany_stats = AccompanyStats()
        self._accompany_stats.start_session()   # 记录本次启动时间
        # Achievement Record counts only visible, interactive foreground time.
        self._achievement_presence_started_at = datetime.now().astimezone()
        self._achievement_presence_sequence = 0
        self._achievement_user_turn_active = False
        self._achievement_presence_timer = QTimer(self)
        self._achievement_presence_timer.setInterval(30_000)
        self._achievement_presence_timer.timeout.connect(self._roll_achievement_presence)
        self._achievement_presence_timer.start()
        self._achievement_unlock_check_queued = False
        self._achievement_unlock_toast = None
        self._achievement_unlock_poll = QTimer(self)
        self._achievement_unlock_poll.setInterval(10_000)
        self._achievement_unlock_poll.timeout.connect(self._check_new_achievement_unlocks)
        self._achievement_unlock_poll.start()
        QTimer.singleShot(900, self._schedule_achievement_unlock_check)

        # ── 全局设置 ──────────────────────────────────────────
        self._global_settings = get_settings()
        self._force_quit = False

        # ── 工作线程句柄 ──────────────────────────────────────
        self._agent_worker:      AgentWorker      | None = None
        self._voice_worker:      VoiceWorker      | None = None
        self._speaker_worker:    SpeakerWorker    | None = None
        self._ocr_worker = None
        self._generation = 0                                 # 世代计数器，防止旧回复污染
        self._is_recording = False
    

        # ── 主动聊天调度器 ────────────────────────────────────
        self._proactive_scheduler = ProactiveChatScheduler()


        # ── 自习室模块 ────────────────────────────────────────
        # WebEngine-backed windows are imported only when the feature opens.
        self._study_room_window: object | None = None

        # ── 备忘本模块 ────────────────────────────────────────
        self.note_dialog = NoteDialog(None)

        # ── 非模态对话框（改为 show() 打开，不阻塞主窗口）────
        self._history_dialog = None
        self._accompany_dialog = None
        self._achievement_window = None
        self._api_config_dialog = None
        self._sound_settings_dialog = None
        self._memory_settings_dialog = None
        self._proactive_dialog = None
        self._network_settings_dialog = None
        self._capability_center_dialog = None
        self._constellation_system = None
        self._ripple_constellation_system = None
        self._persona_hub = None
        self._settings_dialog = None
        self._emotion_debug_dialog = None
        self._diary_dialog = None
        self._qq_settings_dialog = None
        self._wechat_settings_dialog = None

        # ── 非模态对话框（改为 show() 打开，不阻塞主窗口）────
        self._network_settings_dialog = None
        self._capability_center_dialog = None
        self._settings_dialog = None
        self._emotion_debug_dialog = None
        self._diary_dialog = None
        self._qq_settings_dialog = None
        # ── 闹钟模块 ──────────────────────────────────────────
        self._alarm_manager = AlarmManager()
        self._alarm_dialog: AlarmDialog | None = None
        self._alarm_timer = QTimer(self)
        self._alarm_timer.timeout.connect(self._check_alarms)
        self._alarm_timer.start(1000)  # 每秒检查一次

        # ── 倒计时模块（主窗口统一管理）───────────────────────
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._check_countdowns)
        self._countdown_timer.start(1000)
        # ── 自动化任务调度器 ────────────────────────────────
        self._auto_task_scheduler = AutoTaskScheduler(self)
        self._auto_task_scheduler.task_due.connect(self._on_auto_task_due)
        self._auto_task_scheduler.task_missed.connect(self._on_auto_task_missed)
        self._auto_task_scheduler.start()
        self._auto_task_parsed_signal.connect(self._on_auto_task_parsed)
        self._auto_task_done_signal.connect(self._on_auto_task_completed)
        # ── 待办清单模块（数据层，UI 无关部分）──────────────
        self._todo_manager = TodoManager()
        # 将同一实例注入工具层，确保 AI 工具和 UI 共享同一个 TodoManager，
        # 避免 tools.py 懒创建独立实例导致观察者无法收到通知。
        import brain.tools as _brain_tools
        _brain_tools._todo_manager = self._todo_manager

        # ── 待机模式（文件中转模式）──────────────────────────────────────────
        self._standby_state = "IDLE"              # IDLE / STANDBY
        self._is_waiting_for_response = False
        self._last_note_content = ""    
        self._note_poll_timer = None
        self._note_timeout_timer = None
        self._note_file = None
        self._voice_duplex: Optional[VoiceDuplexManager] = None
        self._standby_mode = "full_duplex"        # "full_duplex" / "legacy"

        self._build_ui()
        self._resize_grip = QSizeGrip(self)
        self._resize_grip.setFixedSize(18, 18)
        self._resize_grip.setToolTip("")
        self._bridge_controller = BridgeController(
            chat_widget=self._chat_widget,
            qq_button=self._char_widget.get_qq_bridge_button(),
            warning_func=lambda title, text: QMessageBox.warning(self, title, text),
        )
        self._proactive_controller = ProactivePresentationController(
            scheduler=self._proactive_scheduler,
            chat_widget=self._chat_widget,
            history_manager_func=lambda: self._agent.get_history_manager() if self._agent else None,
            session_id_func=lambda: self._agent._session_id if self._agent else 0,
            history_context_func=lambda content: self._agent.history.append(
                {"role": "assistant", "content": content}
            ) if self._agent is not None else None,
            speak_func=self._speak,
            is_minimized_func=self.isMinimized,
            flash_taskbar_func=self.flash_taskbar,
            qq_bridge_func=lambda: self._bridge_controller.qq_bridge,
            dialog_func=lambda: self._proactive_dialog,
            next_track_func=self._next_track,
        )
        import brain.tools as brain_tools
        brain_tools.set_music_control_callback(self._handle_music_control)
        brain_tools.set_music_info_callback(self._handle_music_info)
        brain_tools.set_note_refresh_callback(self.refresh_note_dialog_content)
        brain_tools.set_proactive_toggle_callback(self._proactive_scheduler.reload_settings)
        self._route_ready.connect(self._on_route_ready)
        self._route_failed.connect(self._on_route_failed)
        self._duplex_voice_start_signal.connect(
            lambda: self._input_panel._input.setPlaceholderText("🎤 聆听中...")
        )
        self._duplex_transcript_signal.connect(
            self._handle_duplex_transcript
        )

        # 初始化 pygame 混音器（用于音乐播放）
        # 首次启动时音频驱动可能未就绪，静默降级避免崩溃
        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init()
            except Exception:
                pass
        # 初始化日记数据库
        init_diary_db()

        # 日记定时器
        self._diary_timer = QTimer(self)
        self._diary_timer.timeout.connect(self._on_diary_timer_timeout)
        self._setup_diary_timer()
        # ── 待办提醒定时器（须在 _build_ui 后，_chat_widget 已就绪）──
        self._todo_reminder_timer = QTimer(self)
        self._todo_reminder_timer.timeout.connect(self._check_overdue_todos)
        self._todo_reminder_timer.start(30 * 60 * 1000)  # 30分钟
        self._reminded_todo_ids = set()  # 今日已提醒过的待办ID，防止重复提醒

        # ── 初始化情感系统（涟漪） ─────────────────────────────
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            _get_emotion_mgr()  # 触发加载 + 时间衰减
            # 每 5 分钟更新一次时间衰减（孤独漂移）
            self._emotion_decay_timer = QTimer(self)
            self._emotion_decay_timer.timeout.connect(self._on_emotion_decay_tick)
            self._emotion_decay_timer.start(300_000)  # 5 分钟
        except Exception:
            pass

        # AgentWorker 看门狗：超过 3 分钟无响应则强制终止
        self._agent_watchdog = QTimer(self)
        self._agent_watchdog.setSingleShot(True)
        self._agent_watchdog.timeout.connect(self._on_agent_watchdog_timeout)

        self._show_greeting()
        # 模型不在启动阶段抢占 GUI 线程/GPU。语音和 TTS 均在首次使用时
        # 按需加载，避免窗口刚出现时白屏、卡顿和 Qt 重绘延迟。
        self._prepare_voice_input()


        # ── ReminderManager（供 DutyScheduler 和 reminder_dialog 使用）
        self.reminder_manager = ReminderManager()

        # ── 统一后台职责调度器（替代 3 个独立 QTimer）─────────
        from utils.duty_scheduler import (
            DutyScheduler, ProactiveDuty, HeartbeatDuty, SmartReminderDuty,
            TreeHoleReplyDuty,
            MemoryExtractionDuty, EmbeddingIndexDuty, MemoryMaintenanceDuty, MemoryCueEvaluationDuty,
            MemoryNarrativeDuty, WorkingMemorySummaryDuty, register_duty,
        )
        self._duty_scheduler = DutyScheduler(self)
        self._duty_scheduler.setup(
            proactive_scheduler=self._proactive_scheduler,
            reminder_manager=self.reminder_manager,
            global_settings=self._global_settings,
            session_id_func=lambda: self._agent._session_id if hasattr(self, '_agent') and self._agent else 0,
            history_manager_func=lambda: self._agent.get_history_manager() if hasattr(self, '_agent') and self._agent else None,
            qq_bridge_func=lambda: self._bridge_controller.qq_bridge,
            todo_manager=self._todo_manager,
            agent=lambda: self._agent if hasattr(self, '_agent') else None,
            chat_widget=self._chat_widget,
            speak_func=self._speak,
            is_shoulder_available=self._is_shoulder_available,
            proactive_dialog=None,  # set later when proactive_dialog is created
        )
        register_duty(self._duty_scheduler, ProactiveDuty())
        register_duty(self._duty_scheduler, HeartbeatDuty())
        register_duty(self._duty_scheduler, SmartReminderDuty())
        register_duty(self._duty_scheduler, TreeHoleReplyDuty())
        register_duty(self._duty_scheduler, MemoryExtractionDuty())
        register_duty(self._duty_scheduler, EmbeddingIndexDuty())
        register_duty(self._duty_scheduler, MemoryMaintenanceDuty())
        register_duty(self._duty_scheduler, MemoryCueEvaluationDuty())
        register_duty(self._duty_scheduler, MemoryNarrativeDuty())
        register_duty(self._duty_scheduler, WorkingMemorySummaryDuty())

        self._duty_scheduler.proactive_response.connect(self._on_proactive_response)
        self._duty_scheduler.proactive_error.connect(self._on_proactive_error)
        self._duty_scheduler.proactive_coordination.connect(
            self._on_proactive_coordination
        )
        self._duty_scheduler.proactive_observation_text.connect(self._on_observation_result)
        self._duty_scheduler.proactive_observation_image.connect(self._on_observation_image)
        self._duty_scheduler.proactive_behavior_selected.connect(
            self._proactive_controller.set_behavior
        )
        self._duty_scheduler.slack_response.connect(self._on_slack_response)
        self._duty_scheduler.slack_error.connect(self._on_slack_error)
        self._duty_scheduler.slack_action_selected.connect(
            self._proactive_controller.set_slack_action
        )
        self._duty_scheduler.heartbeat_response.connect(self._on_heartbeat_response)
        self._duty_scheduler.heartbeat_silent.connect(self._on_heartbeat_finished_silent)
        self._duty_scheduler.reminder_response.connect(self._do_reminder)
        self._duty_scheduler.tree_hole_updated.connect(self._on_tree_hole_updated)
        # ── 摸鱼数据源透明信号 ────────────────────────────────
        self._duty_scheduler.mooyu_data_sources.connect(self._on_mooyu_data_sources)
        self._duty_scheduler.mooyu_duty_data_source.connect(self._on_mooyu_duty_data_source)
        self._duty_scheduler.start()
        # 启动时若当前就是从未说过话的新会话，也应允许莲心在等待后主动破冰。
        if self._agent and not self._agent.history:
            self._duty_scheduler.on_session_started()

        # ── 主线程心跳看门狗（后台线程实时监控，卡顿时立即抓堆栈）──
        self._heartbeat_time = time.monotonic()
        self._heartbeat_frozen = False
        # 轻量定时器：仅更新时间戳（5 秒间隔）
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(self._touch_heartbeat)
        self._heartbeat_timer.start(5000)
        # QApplication.activeModalWidget() must only be queried on the Qt
        # main thread.  The watchdog reads this plain boolean from its worker
        # thread instead of touching Qt objects cross-thread.
        self._modal_active = False
        self._modal_state_timer = QTimer(self)
        self._modal_state_timer.timeout.connect(self._sample_modal_state)
        self._modal_state_timer.start(500)
        # 后台监控线程：不依赖 Qt 事件循环，卡顿时能实时捕获堆栈
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

        # ── Galgame 模式 ─────────────────────────────────────
        self._galgame_visible = False
        self._galgame_positioned = False  # 是否首次显示/已拖动过
        self._tachie_win: TachieWindow | None = None
        self._galgame_dialog: GalgameDialog | None = None
        self._expression_mgr = ExpressionManager(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
        )
        # 注册表情切换回调（供 set_expression 工具使用）
        import brain.tools as brain_tools
        brain_tools.set_expression_callback(self._on_galgame_expression)

        # ── 统一角色动作与窗口体验 ────────────────────────────
        self._avatar_actions = AvatarActionRouter(
            self._char_widget,
            expression_callback=self._on_galgame_expression,
            schedule=lambda ms, callback: QTimer.singleShot(ms, callback),
            reduced_motion=self._global_settings.reduced_motion,
        )
        self._window_experience = WindowExperienceController(
            self, self._global_settings,
            mode_callback=self._apply_window_mode,
            companion_callback=self._set_companion_visible,
            quit_callback=self._request_full_quit,
            parent=self,
        )
        self._window_experience.apply_startup_state(autostart=self._autostart_mode)
        # 首次提醒可能触发 TTS，必须等动作路由器与窗口体验完成初始化。
        QTimer.singleShot(0, self._check_overdue_todos)

        # ── 全局热键过滤器 + 注册热键（启动即生效） ──────────
        self._hotkey_filter = None
        self._galgame_shortcut = None
        if _PLATFORM_CAPS.native_global_hotkey:
            self._hotkey_filter = _WinHotkeyFilter(
                lambda: QTimer.singleShot(0, self._toggle_galgame)
            )
            QApplication.instance().installNativeEventFilter(self._hotkey_filter)
        self._setup_galgame_hotkey(register=True)

        # ── 首次运行：未配置 API Key 时自动弹出配置对话框 ───
        if not has_api_key():
            QTimer.singleShot(500, self._show_api_config)

        # ── QQ 桥接：配置为启用且开启自动启动时才自动连接 ────
        if self._bridge_controller.should_auto_start_qq():
            QTimer.singleShot(1000, self._start_qq_bridge)
        if self._bridge_controller.should_auto_start_wechat():
            QTimer.singleShot(1200, self._start_wechat_bridge)

        # ── 开机自启动：启动网络检测轮询 ────────────────────
        self._autostart_net_attempts = 0
        self._autostart_net_timer: QTimer | None = None
        if self._autostart_mode:
            self._start_autostart_net_poll()
        self._stt_process = None   # 阿里云语音识别子进程

        # 音乐播放器变量区域
        self.playlist = []           # 音乐文件路径列表
        self.current_track_index = 0
        self.music_playing = False
        self.loop_mode = "list"          # list / one / random
        self.current_duration = 0        # 当前歌曲总时长（秒）
        self._progress_timer = None      # 用于更新进度的定时器
        self.current_offset = 0          # 当前歌曲播放起始偏移（秒），用于 seek
        self.current_position = 0        # 当前播放进度（秒）
        self._load_music_playlist()
        self._restore_music_state()
        self.music_stats = MusicStats()
        self.current_song_start_time = None
        self._favorite_stems = set()   # 收藏歌曲（stem 集合）
        self._load_favorite_stems()

    def _on_route_ready(self, text: str, is_chat: bool, route_result):
        # IntentRouter 仅用于快速 UI 分类；真正的工具边界由 AgentCore 的
        # RequestRouter 统一决定。否则旧分类漏判会在工具循环前永久关闭能力。
        self._agent_worker = AgentWorker(self._agent, text, self, disable_tools=False)
        self._last_route_result = route_result
        self._agent_worker.response_ready.connect(self._on_ai_response)
        self._agent_worker.progress_update.connect(self._on_progress_update)
        self._agent_worker.activity.connect(self._on_agent_activity)
        self._agent_worker.tool_round_start.connect(self._chat_widget.start_tool_round)
        self._agent_worker.tool_called.connect(self._on_tool_called)
        self._agent_worker.tool_result.connect(self._on_tool_result)
        self._agent_worker.tool_enable_requested.connect(self._on_tool_enable_requested)
        self._agent_worker.browser_confirmation_requested.connect(self._on_browser_confirmation_requested)
        self._agent_worker.observation_image.connect(self._on_observation_image)
        self._agent_worker.checklist_proposed.connect(self._on_checklist_proposed)
        self._agent_worker.error_occurred.connect(self._on_error)
        self._duty_scheduler.set_agent_busy(True)
        self._agent_worker.start()
        self._agent_watchdog.start(180_000)  # 3 分钟看门狗
        self._input_panel.show_interrupt_bar(self._agent_worker)

    def _on_route_failed(self, err: str):
        """显示辅助消息入口的异步路由错误。"""
        self._chat_widget.add_system_tip(f"消息处理失败：{err}")
        if self._galgame_visible and self._galgame_dialog:
            self._galgame_dialog.set_status("处理失败")
        self._set_idle_state()

    def _handle_music_info(self, query_type: str) -> str:
        if query_type == "playlist":
            if not self.playlist:
                return "播放列表为空。"
            names = [p.stem for p in self.playlist]
            return "当前歌单：\n" + "\n".join(f"{i+1}. {name}" for i, name in enumerate(names))
        elif query_type == "status":
            if not self.playlist:
                return "未加载任何音乐。"
            status = "播放中" if self.music_playing else ("暂停" if not self.music_playing and self.current_position > 0 else "停止")
            current_name = self.playlist[self.current_track_index].stem if self.playlist else "无"
            current_pos = self.current_position
            total = self.current_duration
            return f"状态：{status}\n当前歌曲：{current_name}\n进度：{current_pos//60:02d}:{current_pos%60:02d} / {total//60:02d}:{total%60:02d}"
        elif query_type == "stats":
            total_hours = self.music_stats.get_total_hours()
            song_name, seconds = self.music_stats.get_most_played_song()
            if song_name:
                minutes = seconds // 60
                return f"累计听歌 {total_hours:.1f} 小时。\n最常听的歌曲：{song_name}，共 {minutes} 分钟。"
            else:
                return f"累计听歌 {total_hours:.1f} 小时。还没有积累出最常听的歌曲。"
        else:
            return "未知查询。"

    def _handle_music_control(self, action: str) -> str:
        if action == "play":
            self._on_music_play_pause()
            return "已开始播放音乐。"
        elif action == "pause":
            self._on_music_play_pause()
            return "已暂停音乐。"
        elif action == "next":
            self._next_track()
            return "已切换到下一首。"
        elif action == "prev":
            self._prev_track()
            return "已切换到上一首。"
        elif action == "loop":
            self._on_loop_mode_clicked()
            return "已切换循环模式。"
        elif action == "volume_up":
            new_val = min(1.0, self._global_settings.music_volume + 0.1)
            self._set_music_volume(new_val)
            return f"音量增加到 {int(round(new_val * 100))}%"
        elif action == "volume_down":
            new_val = max(0.0, self._global_settings.music_volume - 0.1)
            self._set_music_volume(new_val)
            return f"音量减小到 {int(round(new_val * 100))}%"
        else:
            return "不支持的操作。"

    def flash_taskbar(self, flash_count=3):
        """让任务栏图标闪烁（仅 Windows）"""
        if not sys.platform.startswith('win'):
            return
        
        try:
            # 定义 FLASHWINFO 结构体
            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD),
                ]
            
            # 闪烁标志
            FLASHW_TRAY = 0x00000002      # 闪烁任务栏按钮
            FLASHW_TIMERNOFG = 0x0000000C # 持续闪烁直到窗口被激活
            
            hwnd = int(self.winId())
            info = FLASHWINFO()
            info.cbSize = ctypes.sizeof(FLASHWINFO)
            info.hwnd = hwnd
            info.dwFlags = FLASHW_TRAY | FLASHW_TIMERNOFG
            info.uCount = flash_count     # 闪烁次数（0 表示持续闪烁）
            info.dwTimeout = 0            # 使用默认闪烁速度（约 1 秒一次）
            
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception as e:
            print(f"闪烁任务栏失败: {e}")
    
    def stop_flash(self):
        """停止闪烁"""
        if not sys.platform.startswith('win'):
            return
        
        try:
            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD),
                ]
            
            hwnd = int(self.winId())
            info = FLASHWINFO()
            info.cbSize = ctypes.sizeof(FLASHWINFO)
            info.hwnd = hwnd
            info.dwFlags = 0  # 停止闪烁
            info.uCount = 0
            info.dwTimeout = 0
            
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass
    
    def changeEvent(self, event):
        """监听窗口状态变化（最小化/还原 + 最大化按钮文字）"""
        if event.type() == event.WindowStateChange:
            if self.isMinimized():
                self._is_minimized = True
                self._flush_achievement_presence()
                if hasattr(self, "_window_experience"):
                    QTimer.singleShot(0, self._window_experience.handle_minimize)
            else:
                if self._is_minimized:
                    self._is_minimized = False
                    self.stop_flash()
                    self._start_achievement_presence()
            # 最大化按钮文字同步
            if self.isMaximized():
                self._btn_maximize.setText("❐")
            else:
                self._btn_maximize.setText("□")
        super().changeEvent(event)

    def _start_achievement_presence(self):
        if getattr(self, "_achievement_presence_started_at", None) is None:
            self._achievement_presence_started_at = datetime.now().astimezone()

    def _flush_achievement_presence(self):
        started_at = getattr(self, "_achievement_presence_started_at", None)
        if started_at is None:
            return
        ended_at = datetime.now().astimezone()
        self._achievement_presence_started_at = None
        if (ended_at - started_at).total_seconds() < 1:
            return
        self._achievement_presence_sequence += 1
        try:
            from brain.interaction_events import record_interaction
            record_interaction(
                feature="companion", event_type="presence_segment",
                local_date=started_at.date().isoformat(),
                source_id=f"presence:{self._achievement_presence_sequence}:{started_at.isoformat()}",
                searchable=False,
                metadata={"started_at": started_at.isoformat(), "ended_at": ended_at.isoformat()},
            )
        except Exception as exc:
            print(f"[成就记录] 陪伴片段记录失败: {exc}")

    def _roll_achievement_presence(self):
        if self.isMinimized():
            return
        self._flush_achievement_presence()
        self._start_achievement_presence()

    def _schedule_achievement_unlock_check(self):
        """将成就检查合并到下一轮事件循环，避免阻塞当前交互动画。"""
        if self._achievement_unlock_check_queued:
            return
        self._achievement_unlock_check_queued = True
        QTimer.singleShot(180, self._check_new_achievement_unlocks)

    def _check_new_achievement_unlocks(self):
        """同步新事件并在主窗口右下角展示新解锁成就。"""
        self._achievement_unlock_check_queued = False
        try:
            from gui.achievement.service import AchievementService
            service = AchievementService()
            fresh = service.state().get("new_unlocks", [])
            if not fresh:
                return
            service.mark_unlocks_read([item.get("id", "") for item in fresh])
            from gui.achievement.unlock_toast import AchievementUnlockToast
            if self._achievement_unlock_toast is None:
                self._achievement_unlock_toast = AchievementUnlockToast(self)
            self._achievement_unlock_toast.show_achievements(fresh)
        except Exception as exc:
            print(f"[成就记录] 解锁提示检查失败: {exc}")

    # ── 界面构建 ─────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("莲心AI")
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "应用图标.jpg")
        if os.path.exists(icon_path):
            from PyQt5.QtGui import QIcon
            self.setWindowIcon(QIcon(icon_path))

        self.setMinimumSize(820, 600)
        self.resize(960, 680)

        central = BackgroundWidget()
        central.setObjectName("centralWidget")
        central.setStyleSheet("""
            #centralWidget {
                background-color: transparent;
                border: 2px solid #5B9A8B;
                border-radius: 12px;
            }
        """)
        self.setCentralWidget(central)
        self._set_background_image()

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 顶部栏：标题 + 历史按钮（半透明）
        top_bar = QWidget()
        top_bar.setFixedHeight(36)
        top_bar.setStyleSheet("background: transparent; border-bottom: 1px solid rgba(255,255,255,30);")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(12, 0, 12, 0)

        # 顶部栏空白处支持拖拽窗口
        top_bar._drag_pos = None
        def _top_bar_press(event):
            if event.button() == Qt.LeftButton:
                top_bar._drag_pos = event.globalPos()
        def _top_bar_move(event):
            if event.buttons() == Qt.LeftButton and top_bar._drag_pos is not None:
                delta = event.globalPos() - top_bar._drag_pos
                top_bar._drag_pos = event.globalPos()
                w = top_bar.window()
                w.move(w.x() + delta.x(), w.y() + delta.y())
        def _top_bar_release(event):
            top_bar._drag_pos = None
        top_bar.mousePressEvent = _top_bar_press
        top_bar.mouseMoveEvent = _top_bar_move
        top_bar.mouseReleaseEvent = _top_bar_release

        app_label = QLabel("莲心AI")
        app_label.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        app_label.setStyleSheet("color: #A0B0FF;")
        top_bar_layout.addWidget(app_label)
        top_bar_layout.addStretch()

        # 标题拖拽：按住标题文字可拖动窗口
        app_label._drag_pos = None
        def _title_press(event):
            if event.button() == Qt.LeftButton:
                app_label._drag_pos = event.globalPos()
        def _title_move(event):
            if event.buttons() == Qt.LeftButton and app_label._drag_pos is not None:
                delta = event.globalPos() - app_label._drag_pos
                app_label._drag_pos = event.globalPos()
                w = app_label.window()
                w.move(w.x() + delta.x(), w.y() + delta.y())
        def _title_release(event):
            app_label._drag_pos = None
        app_label.mousePressEvent = _title_press
        app_label.mouseMoveEvent = _title_move
        app_label.mouseReleaseEvent = _title_release
       



        self._btn_history = QPushButton("历史记录")
        self._btn_history.setFixedSize(72, 24)
        self._btn_history.setFont(QFont("Microsoft YaHei UI", 8))
        self._btn_history.setCursor(Qt.PointingHandCursor)
        self._btn_history.setStyleSheet("""
            QPushButton {
                background-color: #6C7BFF;
                color: white;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover  { background-color: #5A6AEE; }
            QPushButton:pressed{ background-color: #4A5ADE; }
        """)
        self._btn_history.clicked.connect(self._on_history_clicked)
        top_bar_layout.addWidget(self._btn_history)

        self._btn_new_chat = QPushButton("新建对话")
        self._btn_new_chat.setFixedSize(72, 24)
        self._btn_new_chat.setFont(QFont("Microsoft YaHei UI", 8))
        self._btn_new_chat.setCursor(Qt.PointingHandCursor)
        self._btn_new_chat.setStyleSheet("""
            QPushButton {
                background-color: #2D2D3F;
                color: #A0B0FF;
                border-radius: 6px;
                border: 1px solid #3D3D5A;
            }
            QPushButton:hover  { background-color: #3D3D55; }
            QPushButton:pressed{ background-color: #4D4D65; }
        """)
        self._btn_new_chat.clicked.connect(self._on_new_chat_clicked)
        top_bar_layout.addWidget(self._btn_new_chat)

        # 备忘本按钮
        self._btn_note = QPushButton("📝 备忘本")
        self._btn_note.setFixedSize(80, 24)
        self._btn_note.setFont(QFont("Microsoft YaHei UI", 8))
        self._btn_note.setCursor(Qt.PointingHandCursor)
        self._btn_note.setStyleSheet("""
            QPushButton {
                background-color: #2D2D3F;
                color: #C8A060;
                border-radius: 6px;
                border: 1px solid #5A4A30;
            }
            QPushButton:hover { background-color: #3D3D55; }
        """)
        self._btn_note.clicked.connect(self._open_note_dialog)
        top_bar_layout.addWidget(self._btn_note)

        # Galgame 窗口按钮
        self._galgame_btn = QPushButton("🎮 Galgame")
        self._galgame_btn.setFixedSize(86, 24)
        self._galgame_btn.setFont(QFont("Microsoft YaHei UI", 8))
        self._galgame_btn.setCursor(Qt.PointingHandCursor)
        self._galgame_btn.setToolTip("打开 Galgame 风格的角色立绘和对话窗口")
        self._galgame_btn.setStyleSheet("""
            QPushButton {
                background-color: #F0F0F8;
                color: #6C4A9A;
                border-radius: 6px;
                border: 1px solid #D0B8E8;
            }
            QPushButton:hover  { background-color: #E8D8F8; }
            QPushButton:pressed{ background-color: #D8C8EE; }
        """)
        self._galgame_btn.clicked.connect(self._toggle_galgame)
        top_bar_layout.addWidget(self._galgame_btn)

        self._btn_standby = QPushButton("🎤 语音聊天")
        self._btn_standby.setFixedSize(88, 24)
        self._btn_standby.setFont(QFont("Microsoft YaHei UI", 8))
        self._btn_standby.setCursor(Qt.PointingHandCursor)
        self._btn_standby.setToolTip(
            "左击：开启/关闭语音聊天\n"
            "右击：使用说明与设置\n\n"
            "🎧 插耳机时：莲心说话中可随时开口打断\n"
            "🔊 用扬声器时：等莲心说完 + 提示音后再说话"
        )
        self._btn_standby.clicked.connect(self._on_standby_clicked)
        self._btn_standby.setContextMenuPolicy(Qt.CustomContextMenu)
        self._btn_standby.customContextMenuRequested.connect(self._show_voice_chat_menu)
        self._update_standby_button()
        top_bar_layout.addWidget(self._btn_standby)

        # 后台职责中心入口
        self._btn_duty = QPushButton("🔧")
        self._btn_duty.setFixedSize(28, 24)
        self._btn_duty.setFont(QFont("Microsoft YaHei UI", 9))
        self._btn_duty.setCursor(Qt.PointingHandCursor)
        self._btn_duty.setToolTip("后台职责中心：查看/管理莲心的主动行为")
        self._btn_duty.setStyleSheet("""
            QPushButton {
                background-color: #2D2D3F; color: #A0A0B0;
                border-radius: 5px; border: 1px solid #3D3D5A;
            }
            QPushButton:hover { background-color: #3D3D55; color: #FFD700; }
        """)
        self._btn_duty.clicked.connect(self._show_duty_center)
        top_bar_layout.addWidget(self._btn_duty)

        top_bar_layout.addStretch()

        # 窗口控制按钮（最右侧）
        self._btn_minimize = QPushButton("—")
        self._btn_minimize.setFixedSize(28, 24)
        self._btn_minimize.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        self._btn_minimize.setCursor(Qt.PointingHandCursor)
        self._btn_minimize.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A0B0FF;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #3D3D5A; }
        """)
        self._btn_minimize.clicked.connect(self.showMinimized)
        top_bar_layout.addWidget(self._btn_minimize)

        self._btn_maximize = QPushButton("□")
        self._btn_maximize.setFixedSize(28, 24)
        self._btn_maximize.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        self._btn_maximize.setCursor(Qt.PointingHandCursor)
        self._btn_maximize.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A0B0FF;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #3D3D5A; }
        """)
        self._btn_maximize.clicked.connect(self._toggle_maximize)
        top_bar_layout.addWidget(self._btn_maximize)

        self._btn_close = QPushButton("✕")
        self._btn_close.setFixedSize(28, 24)
        self._btn_close.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A0B0FF;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #E04040; color: #FFFFFF; }
        """)
        self._btn_close.clicked.connect(self.close)
        top_bar_layout.addWidget(self._btn_close)

        # 统一顶部功能按钮的深色玻璃风格，避免各模块使用互相冲突的配色。
        top_action_style = """
            QPushButton { background-color: rgba(22, 43, 38, 225); color: #DCEFE8;
                border: 1px solid #416B63; border-radius: 7px; padding: 2px 8px; }
            QPushButton:hover { background-color: #2A5148; border-color: #75B8A8; color: #FFFFFF; }
            QPushButton:pressed { background-color: #35685C; border-color: #9AD8C8; }
        """
        for button in (self._btn_history, self._btn_new_chat, self._btn_note,
                       self._galgame_btn, self._btn_standby):
            button.setStyleSheet(top_action_style)

        main_layout.addWidget(top_bar)

        # 上半：左栏（角色）+ 右栏（聊天）
        top_widget = QWidget()
        top_widget.setAttribute(Qt.WA_TranslucentBackground, True)
        top_widget.setAutoFillBackground(False)
        top_widget.setStyleSheet("background: transparent;")
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        self._char_widget = CharacterWidget()
        # 绑定功能区按钮
        self._char_widget.get_accompany_button().clicked.connect(self._on_accompany_clicked)

        self._char_widget.get_settings_button().clicked.connect(self._on_settings_clicked)
        self._char_widget.get_study_room_button().clicked.connect(self._on_study_room_clicked)
        self._char_widget.get_api_config_button().clicked.connect(self._show_api_config)
        self._char_widget.get_alarm_button().clicked.connect(self._on_alarm_clicked)
        self._char_widget.get_camera_button().clicked.connect(self._on_camera_capture)
        self._char_widget.get_emotion_button().clicked.connect(self._show_ripple_constellation)
        self._char_widget.get_sound_button().clicked.connect(self._on_sound_settings)
        self._char_widget.get_memory_button().clicked.connect(self._on_memory_settings)
        self._char_widget.get_workflow_button().clicked.connect(self._show_workflow_center)
        self._char_widget.get_network_button().clicked.connect(self._show_network_settings)
        self._char_widget.get_capability_button().clicked.connect(self._show_capability_center)
        self._char_widget.get_constellation_button().clicked.connect(self._show_constellation_system)
        self._char_widget.get_persona_button().clicked.connect(self._show_persona_hub)
        self._char_widget.get_proactive_button().clicked.connect(self._on_proactive_clicked)
        self._char_widget.get_qq_bridge_button().clicked.connect(self._on_qq_bridge_clicked)
        self._char_widget.get_wechat_bridge_button().clicked.connect(self._on_wechat_settings_clicked)
        self._char_widget.get_diary_button().clicked.connect(self._open_diary_dialog)
        self._char_widget.get_voice_stt_button().clicked.connect(self._show_voice_stt_dialog)

        top_layout.addWidget(self._char_widget)

        # 聊天区（右侧）：进度条 + 滚动消息区
        right_widget = FrostedPanel(opacity=self._global_settings.chat_background_opacity)
        self._chat_background_widget = right_widget
        right_widget.setAttribute(Qt.WA_TranslucentBackground, True)
        right_widget.setAutoFillBackground(False)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        from gui.task_progress_bar import TaskProgressBar
        self._task_progress = TaskProgressBar()
        self._task_progress.hide()
        right_layout.addWidget(self._task_progress)

        self._chat_widget = ChatWidget()
        right_layout.addWidget(self._chat_widget)

        top_layout.addWidget(right_widget)

        # 注册任务追踪观察者 → 进度条实时刷新
        from brain.task_tracker import get_task_tracker
        get_task_tracker().observe(self._refresh_task_progress)

        main_layout.addWidget(top_widget)

        # 下半：输入栏（全宽）
        self._input_panel = InputPanel()
        self._input_panel.message_submitted.connect(self._on_user_message)
        self._input_panel.capability_center_requested.connect(self._show_capability_center)
        self._chat_widget.quote_requested.connect(self._input_panel.set_quote)
        self._chat_widget.speak_requested.connect(self._on_speak_request)
        self._chat_widget.avatar_interaction_requested.connect(self._on_avatar_interaction)
        self._chat_widget.avatar_clicked.connect(self._on_avatar_clicked)
        self._chat_widget.avatar_long_pressed.connect(self._on_avatar_long_pressed)
        self._chat_widget.avatar_context_requested.connect(self._on_avatar_context)
        self._chat_widget.growth_event_requested.connect(self._show_growth_event)
        from gui.avatar_interaction import AvatarInteractionController
        self._avatar_interaction = AvatarInteractionController(self._agent, self._accompany_stats, self)
        self._avatar_interaction.thinking_started.connect(self._on_avatar_interaction_thinking)
        self._avatar_interaction.response_ready.connect(self._on_avatar_interaction_response)
        self._avatar_interaction.interaction_accepted.connect(self._on_avatar_interaction_accepted)
        self._avatar_interaction.interaction_blocked.connect(
            lambda text: self._chat_widget.show_avatar_interaction_notice(text, 1200)
        )
        self._input_panel.voice_clicked.connect(self._on_voice_clicked)


        self._input_panel.clear_clicked.connect(self._on_clear_note)
        self._input_panel.image_submitted.connect(self._on_user_image)
        # 静音 & 重新发送
        self._input_panel.get_mute_button().clicked.connect(self._on_mute)
        self._input_panel.get_resend_button().clicked.connect(self._on_resend)
        main_layout.addWidget(self._input_panel)



        # 音乐盒（Mode A 嵌入式 Web 播放器 + Mode B 沉浸式音乐空间）
        self._music_box_bridge = MusicBoxBridge(
            self._music_box_state, self,
            space_settings_provider=self._music_space_settings,
            space_settings_saver=self._save_music_space_settings,
        )
        self._music_box_widget = MusicBoxWidget(self._music_box_bridge, self)
        self._char_widget.install_music_box_view(self._music_box_widget)
        self._music_space_window = None   # Mode B 懒加载

        _mb = self._music_box_bridge
        _mb.toggle_play_requested.connect(self._on_music_play_pause)
        _mb.play_requested.connect(self._resume_music)
        _mb.pause_requested.connect(self._pause_music)
        _mb.next_requested.connect(self._next_track)
        _mb.previous_requested.connect(self._prev_track)
        _mb.seek_requested.connect(self._seek_to_seconds)
        _mb.volume_requested.connect(self._set_music_volume)
        _mb.play_mode_requested.connect(self._set_loop_mode)
        _mb.track_requested.connect(self._switch_to_track)
        _mb.open_space_requested.connect(self._open_music_space)
        _mb.close_space_requested.connect(self._close_music_space)
        _mb.minimize_space_requested.connect(self._minimize_music_space)
        _mb.maximize_space_requested.connect(self._toggle_max_music_space)
        _mb.toggle_favorite_requested.connect(self._toggle_favorite)

        # 初始化音量与状态推送
        self._global_settings.music_volume = max(0.0, min(1.0, float(self._global_settings.music_volume)))
        self._push_music_state()
        
    def _open_note_dialog(self):
        play_sound("MemoBook.mp3")
        if self.note_dialog is None:
            from gui.note_dialog import NoteDialog
            # 父窗口设为 None，使其独立于主窗口
            self.note_dialog = NoteDialog(None)
            self.note_dialog.destroyed.connect(self._on_note_dialog_destroyed)
            self.note_dialog.show()
        else:
            self.note_dialog.show()
            self.note_dialog.raise_()
            self.note_dialog.activateWindow()

    def _on_note_dialog_destroyed(self):
        self.note_dialog = None

    def refresh_note_dialog_content(self):
        """供工具调用，刷新备忘本显示的內容"""
        if self.note_dialog is not None:
            self.note_dialog.refresh_content()  


    def _set_background_image(self):
        """Apply the persisted desktop background without changing child opacity."""
        settings = self._global_settings
        self._apply_background_config(
            settings.background_source if settings.background_enabled else "",
            settings.background_opacity,
            settings.background_source_type,
            settings.background_fit_mode,
        )

    def _apply_background_config(self, source: str, opacity: float,
                                 source_type: str = "single",
                                 fit_mode: str = "cover"):
        central = self.centralWidget()
        if isinstance(central, BackgroundWidget):
            central.set_background(source, opacity, source_type, fit_mode)

    def _on_background_changed(self, enabled: bool, source: str, opacity: float,
                               source_type: str, fit_mode: str):
        self._apply_background_config(source if enabled else "", opacity, source_type, fit_mode)

    def _set_chat_background_opacity(self, opacity: float):
        """Update only the chat pane's readability mask; wallpaper stays cached."""
        self._chat_background_opacity = max(0.0, min(1.0, float(opacity)))
        widget = getattr(self, "_chat_background_widget", None)
        if widget is not None:
            if hasattr(widget, "set_opacity"):
                widget.set_opacity(self._chat_background_opacity)

    def _on_chat_background_opacity_changed(self, opacity: float):
        self._set_chat_background_opacity(opacity)
        self._global_settings.chat_background_opacity = opacity

    def _apply_window_mode(self, mode: str):
        if mode == "compact":
            self._char_widget.hide()
            self.setMinimumSize(620, 480)
            if not self.isMaximized() and self.width() > 760:
                self.resize(720, max(520, self.height()))
        else:
            self._char_widget.show()
            self.setMinimumSize(820, 600)
            if not self.isMaximized() and self.width() < 820:
                self.resize(960, max(680, self.height()))

    def _set_companion_visible(self, visible: bool):
        if visible:
            self._companion_owned_galgame = not self._galgame_visible
            if not self._galgame_visible:
                self._show_galgame()
        elif getattr(self, "_companion_owned_galgame", False):
            if self._galgame_visible:
                self._hide_galgame()
            self._companion_owned_galgame = False

    def _request_full_quit(self):
        self._force_quit = True
        self.close()

    def _apply_window_settings(self):
        if hasattr(self, "_window_experience"):
            self._window_experience.reload_settings()
            self._window_experience.set_mode(self._global_settings.window_mode, persist=False)
        if hasattr(self, "_avatar_actions"):
            self._avatar_actions.set_reduced_motion(self._global_settings.reduced_motion)

    def resizeEvent(self, event):
        if hasattr(self, "_resize_grip"):
            self._resize_grip.move(self.width() - self._resize_grip.width(),
                                   self.height() - self._resize_grip.height())
            self._resize_grip.raise_()
        if hasattr(self, "_window_experience"):
            self._window_experience.schedule_geometry_save()
        super().resizeEvent(event)

    def moveEvent(self, event):
        if hasattr(self, "_window_experience"):
            self._window_experience.schedule_geometry_save()
        super().moveEvent(event)

    def _show_greeting(self):
        """启动时显示欢迎内容：有历史则回放最近30条，否则显示初次欢迎语。"""
        if self._agent.history:
            msgs = self._agent.get_history_manager().get_messages(self._agent._session_id)
            display = msgs[-30:]
            for m in display:
                if m["role"] == "user":
                    self._chat_widget.add_user_message(m["content"])
                else:
                    clean, _ = _strip_emotion_tag(m["content"])
                    self._chat_widget.add_ai_message(clean or m["content"])
            total = len(msgs)
            shown = len(display)
            self._chat_widget.add_system_tip(
                f"—— 已加载上次对话（显示最近 {shown} 条，共 {total} 条）——"
            )
        else:
            self._chat_widget.add_ai_message("让我看看...现实稳定锚就绪，坐标稳定...这里是莲心，收到请回复~")

    # ── 窗口拖拽 & 边框拉伸（WM_NCHITTEST）─────────────────

    _RESIZE_MARGIN = 8

    def nativeEvent(self, eventType, message):
        """Windows 原生消息：WM_NCHITTEST 实现无边框窗口的拖拽和拉伸。"""
        if eventType == "windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                x = msg.lParam & 0xFFFF
                y = (msg.lParam >> 16) & 0xFFFF
                # 屏幕坐标 → 窗口坐标
                pt = self.mapFromGlobal(QPoint(x, y))
                w, h = self.width(), self.height()

                left = pt.x() < self._RESIZE_MARGIN
                right = pt.x() > w - self._RESIZE_MARGIN
                top = pt.y() < self._RESIZE_MARGIN
                bottom = pt.y() > h - self._RESIZE_MARGIN

                if top and left:
                    return True, 13   # HTTOPLEFT
                if top and right:
                    return True, 14   # HTTOPRIGHT
                if bottom and left:
                    return True, 16   # HTBOTTOMLEFT
                if bottom and right:
                    return True, 17   # HTBOTTOMRIGHT
                if top:
                    return True, 12   # HTTOP
                if bottom:
                    return True, 15   # HTBOTTOM
                if left:
                    return True, 10   # HTLEFT
                if right:
                    return True, 11   # HTRIGHT
                
        return False, 0

    # ── 语音输入准备 ─────────────────────────────────────────

    def _prepare_voice_input(self):
        """只启用语音入口，语音模型在首次录音时由 VoiceWorker 按需加载。"""
        self._input_panel.enable_voice_button()

    def _on_model_loaded(self):
        self._chat_widget.add_system_tip("语音识别模型已就绪，可点击 🎤 开始语音对话")
        self._input_panel.enable_voice_button()

    def _on_model_failed(self, err: str):
        self._chat_widget.add_system_tip(f"语音识别模型加载失败：{err}")

    def _preload_tts(self):
        """后台线程预热 TTS 引擎（GPT-SoVITS worker），缩短首次语音回复延迟。"""
        from config import get_tts_config
        cfg = get_tts_config()
        if not cfg.get("tts_warmup", True):
            return
        def _warmup():
            try:
                from brain.tts_engine import TtsEngine
                engine = TtsEngine()
                engine.warmup()
            except Exception:
                pass
        threading.Thread(target=_warmup, daemon=True).start()

    # ── 文字对话 ─────────────────────────────────────────────
    def _refresh_task_progress(self):
        """进度条刷新（线程安全）。"""
        QTimer.singleShot(0, self._do_refresh_task_progress)

    def _do_refresh_task_progress(self):
        from brain.task_tracker import get_task_tracker
        c, t, label = get_task_tracker().get_progress()
        self._task_progress.refresh(c, t, label)

    def _on_speak_request(self, text: str):
        """右键朗读消息文字。"""
        try:
            if self._speaker_worker and self._speaker_worker.isRunning():
                self._speaker.stop()
            self._speaker_worker = SpeakerWorker(self._speaker, text, self)
            self._speaker_worker.start()
        except Exception as e:
            print(f"[右键朗读] 失败: {e}")


    def _on_user_message(self, text: str, images: list = None):
        from brain.request_context import parse_request_context
        request_context = parse_request_context(text)
        active_text = request_context.active_text
        # 取消语音转录的自动发送定时器（用户手动发送了）
        if hasattr(self, '_voice_auto_send_timer') and self._voice_auto_send_timer:
            self._voice_auto_send_timer.stop()
        # 用户发消息 → 立即停止语音播放、重置语音标记
        try:
            from skills.语音合成.tools import stop_voice_playback
            stop_voice_playback()
        except Exception:
            pass
       
        if images is None:
            images = []
        self._achievement_user_turn_active = bool(str(text or "").strip() or images)
        tool_selection = self._input_panel.get_tool_selection()
        selected_tool = tool_selection.get("name") if tool_selection else None
        selected_mode = tool_selection.get("mode", "auto") if tool_selection else "auto"
        display_text = text  # 气泡显示用原始文本，注入提示不显示
        if selected_tool is None:
            action_keywords = ["打开", "启动", "运行", "执行", "开启"]
            if any(kw in active_text for kw in action_keywords):
                text = "[重要：你必须调用相应工具来执行(比如open_app)，不要直接回复结果。]\n" + text
            diary_keywords = [
                "读日记", "日记里", "回忆一下日记", "看看日记", "日记写了什么",
                "时间胶囊", "共同书页", "回忆某天", "读一下", "最近日记",
            ]
            if any(kw in active_text for kw in diary_keywords):
                text = "[重要：你必须调用 read_diary 工具读取时间胶囊中的真实内容，不要凭空回答。]\n" + text
        if self._speaker_worker and self._speaker_worker.isRunning():
            self._speaker.stop()

        # 新消息接管时，必须撤销旧看门狗的延迟清理。
        # 否则旧任务的超时回调会误把刚创建的新 Worker 当成超时目标并终止。
        self._agent_watchdog.stop()
        self._stop_watchdog_check_timer()
        self._watchdog_resolved = True

        # ── 风暴式打断：终止旧 AgentWorker，防止旧回复污染 ──
        if self._agent_worker and self._agent_worker.isRunning():
            self._agent.cancel_active_request("用户发送了新的消息")
            self._agent_worker.terminate()
            self._agent_worker.wait(1000)
            try:
                self._agent_worker.response_ready.disconnect()
                self._agent_worker.progress_update.disconnect()
                self._agent_worker.tool_called.disconnect()
                self._agent_worker.error_occurred.disconnect()
            except Exception:
                pass
            self._agent_worker = None

        self._generation += 1

        # 分段发送中，用户发新消息 → 取消剩余段落
        if hasattr(self, '_segment_sender') and self._segment_sender is not None:
            if self._segment_sender.is_running:
                self._segment_sender.cancel()
                if hasattr(self, "_avatar_actions"):
                    self._avatar_actions.request("idle", source="segment_cancelled", force=True)
                else:
                    self._char_widget.set_normal()
                self._input_panel.set_mute_visible(False)
                self._segment_sender = None

        self._proactive_scheduler.notify_user_active()
   

        image_bubbles = []
        for img_path in images:
            bubble = self._chat_widget.add_user_image(img_path, ocr_text="分析中...")
            image_bubbles.append((img_path, bubble))

        if text.strip():
            self._chat_widget.add_user_message(display_text)
            avatar_action = self._detect_avatar_command(active_text)
            if avatar_action and hasattr(self, "_avatar_interaction"):
                self._avatar_interaction.trigger_outbound(avatar_action)
                self._input_panel.clear_selection()
                return
        # ── 自动化任务检测 ──────────────────────────────
            if active_text.strip() and self._detect_auto_task_intent(active_text):
                # 剥离【自动化】标签后传给解析器
                clean = active_text.replace("【自动化】", "").strip()
                self._try_parse_auto_task(clean)
                return
            play_sound("ButtonAll.mp3") 

        self._set_thinking_state()

        if images:
            self._staged_image_results = {}
            self._staged_image_errors = {}
            self._staged_image_count = len(images)
            self._staged_text = text
            self._staged_tool_selection = tool_selection
            self._staged_bubbles = image_bubbles

            for i, (img_path, _) in enumerate(image_bubbles):
                worker = _ImageVisionWorker(img_path, self)
                worker.finished.connect(lambda desc, idx=i: self._on_staged_vision_done(idx, desc))
                worker.error.connect(lambda err, idx=i: self._on_staged_vision_error(idx, err))
                worker.start()
        else:
            self._agent_worker = AgentWorker(
                self._agent, self._avatar_contextual_message(text), self,
                forced_tool=selected_tool if selected_mode == "forced" else None,
                preferred_tool=selected_tool if selected_mode == "preferred" else None,
            )
            self._agent_worker.response_ready.connect(self._on_ai_response)
            self._agent_worker.progress_update.connect(self._on_progress_update)
            self._agent_worker.activity.connect(self._on_agent_activity)
            self._agent_worker.tool_round_start.connect(self._chat_widget.start_tool_round)
            self._agent_worker.tool_called.connect(self._on_tool_called)
            self._agent_worker.tool_result.connect(self._on_tool_result)
            self._agent_worker.tool_enable_requested.connect(self._on_tool_enable_requested)
            self._agent_worker.browser_confirmation_requested.connect(self._on_browser_confirmation_requested)
            self._agent_worker.observation_image.connect(self._on_observation_image)
            self._agent_worker.error_occurred.connect(self._on_error)
            self._agent_worker.start()
            self._agent_watchdog.start(180_000)  # 3 分钟看门狗
            self._input_panel.show_interrupt_bar(self._agent_worker)


        self._input_panel.clear_selection()

    def _detect_avatar_command(self, text: str):
        """识别用户明确要求莲心拍击或摸头的短命令。"""
        import re
        value = str(text or "").strip().lower()
        if not value:
            return None
        if re.search(r"(摸摸我|摸我|摸一摸我|摸我的头|摸头)", value):
            return "headpat"
        if re.search(r"(拍我|拍一拍我|拍我的头像|反过来拍|你也拍)", value):
            return "tap"
        return None

    def _avatar_contextual_message(self, text: str):
        context = ""
        try:
            context = self._avatar_interaction.recent_context()
        except Exception:
            pass
        return f"{context}\n\n{text}" if context else text

    def _on_staged_vision_done(self, idx: int, description: str):
        self._staged_image_results[idx] = description
        if len(self._staged_image_results) + len(self._staged_image_errors) >= self._staged_image_count:
            self._finish_staged_vision()

    def _on_staged_vision_error(self, idx: int, err: str):
        self._staged_image_errors[idx] = err
        if len(self._staged_image_results) + len(self._staged_image_errors) >= self._staged_image_count:
            self._finish_staged_vision()

    def _finish_staged_vision(self):
        self._chat_widget._hide_thinking()

        for i, (_, bubble) in enumerate(self._staged_bubbles):
            if i in self._staged_image_results:
                bubble.update_text(self._staged_image_results[i])
                try:
                    from brain.interaction_events import record_interaction
                    record_interaction(
                        feature="vision", event_type="vision_completed",
                        source_id=f"staged-vision:{self._agent._session_id}:{i}:{datetime.now().isoformat(timespec='seconds')}",
                        summary="完成了一次图片识别", searchable=False,
                    )
                except Exception as exc:
                    print(f"[成就记录] 图片事件记录失败: {exc}")
            elif i in self._staged_image_errors:
                bubble.update_text(f"分析失败: {self._staged_image_errors[i]}")

        self._schedule_achievement_unlock_check()
        context_parts = []
        for i, (_, _) in enumerate(self._staged_bubbles):
            if i in self._staged_image_results:
                context_parts.append(f"[用户发了一张图片，视觉分析结果如下]\n{self._staged_image_results[i]}")
            elif i in self._staged_image_errors:
                context_parts.append(f"[图片分析失败] {self._staged_image_errors[i]}")

        if self._staged_text.strip():
            context_parts.append(self._staged_text)

        full_context = "\n\n".join(context_parts)
        if not self._staged_text.strip():
            full_context += "\n\n请根据你看到的内容自然地回应，描述你看到了什么。"

        self._agent_worker = AgentWorker(
            self._agent, self._avatar_contextual_message(full_context), self,
            forced_tool=(self._staged_tool_selection or {}).get("name")
                if (self._staged_tool_selection or {}).get("mode") == "forced" else None,
            preferred_tool=(self._staged_tool_selection or {}).get("name")
                if (self._staged_tool_selection or {}).get("mode") == "preferred" else None,
        )
        self._agent_worker.response_ready.connect(self._on_ai_response)
        self._agent_worker.progress_update.connect(self._on_progress_update)
        self._agent_worker.activity.connect(self._on_agent_activity)
        self._agent_worker.tool_round_start.connect(self._chat_widget.start_tool_round)
        self._agent_worker.tool_called.connect(self._on_tool_called)
        self._agent_worker.tool_result.connect(self._on_tool_result)
        self._agent_worker.tool_enable_requested.connect(self._on_tool_enable_requested)
        self._agent_worker.browser_confirmation_requested.connect(self._on_browser_confirmation_requested)
        self._agent_worker.observation_image.connect(self._on_observation_image)
        self._agent_worker.error_occurred.connect(self._on_error)
        self._duty_scheduler.set_agent_busy(True)
        self._agent_worker.start()
        self._agent_watchdog.start(180_000)  # 3 分钟看门狗
        self._input_panel.show_interrupt_bar(self._agent_worker)

    def _on_tool_called(self, tool_name: str, args_json: str, round_num: int):
        self._chat_widget.add_tool_call_card(tool_name, args_json, round_num)
        if self._galgame_visible and self._galgame_dialog:
            self._galgame_dialog.set_status(f"正在执行：{tool_name}", active=True)
        self._task_progress.set_subtitle(f"🔧 {tool_name} 执行中…")
        self._pending_arms_cross = random.random() < 0.03
        if self._galgame_visible and self._galgame_dialog:
            self._galgame_dialog.show_thinking()

    def _on_tool_result(self, tool_name: str, preview: str, is_error: bool,
                         round_num: int, elapsed_ms: float):
        """工具执行完毕，更新聊天卡片和进度条"""
        self._chat_widget.update_tool_call_result(
            tool_name, preview, is_error, round_num, elapsed_ms)
        status = "❌" if is_error else "✅"
        self._task_progress.set_subtitle(f"{status} {tool_name} → {preview}")
        if not is_error and getattr(self, "_achievement_user_turn_active", False):
            try:
                from brain.interaction_events import record_interaction
                record_interaction(
                    feature="tool", event_type="tool_called",
                    source_id=f"tool:{self._agent._session_id}:{round_num}:{tool_name}:{datetime.now().isoformat(timespec='seconds')}",
                    summary="完成了一次工具探索", searchable=False,
                    metadata={"user_initiated": True, "tool_name": str(tool_name or "")},
                )
            except Exception as exc:
                print(f"[成就记录] 工具事件记录失败: {exc}")
            self._schedule_achievement_unlock_check()

    def _on_tool_enable_requested(self, tool_key: str, display_name: str,
                                  reason: str, request):
        """主线程中的用户确认；工作线程会等待 event 后继续工具循环。"""
        try:
            details = str(reason or "完成当前任务需要该能力。").strip()
            reply = QMessageBox.question(
                self,
                "工具授权",
                f"莲心想要你允许她启用“{display_name}”工具。\n\n"
                f"原因：{details}\n\n"
                "同意后，莲心会继续当前任务。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            request.approved = reply == QMessageBox.Yes
            if request.approved:
                self._chat_widget.add_system_tip(f"已允许莲心启用：{display_name}")
            else:
                self._chat_widget.add_system_tip(f"未允许启用：{display_name}")
        finally:
            request.event.set()

    def _on_browser_confirmation_requested(self, tool_name: str, risk_level: str,
                                           reason: str, request):
        """高风险浏览器动作确认。

        “仅此操作”不会改变任务授权；“本次任务允许”只在当前 Agent 请求内
        放行同等级动作，不写入持久配置。
        """
        try:
            from PyQt5.QtWidgets import QMessageBox
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("浏览器操作确认")
            box.setText(
                f"莲心准备执行浏览器高风险操作：{tool_name}\n\n"
                f"风险等级：{risk_level}\n"
                f"原因：{reason}\n\n"
                "不会显示或保存密码、Cookie 等敏感内容。是否继续？"
            )
            once_btn = box.addButton("仅此操作", QMessageBox.AcceptRole)
            task_btn = box.addButton("本次任务允许", QMessageBox.AcceptRole)
            deny_btn = box.addButton("拒绝", QMessageBox.RejectRole)
            box.setDefaultButton(deny_btn)
            box.exec_()
            clicked = box.clickedButton()
            request.approved = clicked in (once_btn, task_btn)
            request.remember = clicked is task_btn
            if request.approved:
                label = "本次任务已允许" if request.remember else "已允许一次"
                self._chat_widget.add_system_tip(f"浏览器安全确认：{label}（{tool_name}）")
            else:
                self._chat_widget.add_system_tip(f"已拒绝浏览器高风险操作：{tool_name}")
        finally:
            request.event.set()


    def _on_progress_update(self, text: str):
        """显示插话回复；它是当前任务的旁路信息，不冒充最终答案。"""
        self._on_agent_activity("interrupt_progress")
        text = str(text or "").strip()
        if not text:
            return
        self._chat_widget.add_system_tip(f"插话回复：{text}")
        self._task_progress.set_subtitle(f"插话已回复：{text[:80]}")

    def _on_agent_activity(self, stage: str):
        """收到 Worker 的真实执行阶段，延长当前请求的兜底看门狗。"""
        worker = getattr(self, "_agent_worker", None)
        if not worker or not worker.isRunning():
            return
        self._agent_watchdog.start(180_000)
        print(f"[请求进度] {str(stage or 'active')[:120]}", flush=True)


    def _on_error(self, err: str):
        self._achievement_user_turn_active = False
        self._watchdog_resolved = True  # 防止看门狗 cleanup 重复添加系统提示
        self._agent_watchdog.stop()
        self._stop_watchdog_check_timer()
        self._chat_widget.finalize_tool_groups()
        self._duty_scheduler.set_agent_busy(False)
        self._input_panel.hide_interrupt_bar()
        self._char_widget.stop_thinking()
        self._set_idle_state()
        if self._galgame_visible and self._galgame_dialog:
            self._galgame_dialog.set_status("执行失败")
        self._chat_widget.add_system_tip(f"错误：{err}")


    def _on_ai_response(self, text: str):
        self._watchdog_resolved = True  # 防止看门狗 cleanup 重复添加系统提示
        growth_event = getattr(self._agent, "_latest_growth_event", None)
        if growth_event is not None:
            self._chat_widget.add_growth_event(growth_event)
            self._agent._latest_growth_event = None
        self._agent_watchdog.stop()
        self._stop_watchdog_check_timer()
        from brain.task_tracker import get_task_tracker
        get_task_tracker().clear()
        self._input_panel.hide_interrupt_bar()
        self._chat_widget.finalize_tool_groups()
        self._duty_scheduler.set_agent_busy(False)
        if self._galgame_visible and self._galgame_dialog:
            self._galgame_dialog.set_status("正在整理回复", active=True)
        # 回复展示属于业务链路，不能因动画回调缺失而永久丢失。
        # deliver_once 同时防止动画正常完成后与超时兜底重复展示。
        delivery_state = {"done": False}

        def deliver_once():
            if delivery_state["done"]:
                return
            delivery_state["done"] = True
            self._continue_response(text)

        def delivery_fallback():
            if not delivery_state["done"]:
                print("[GUI] 动画完成回调超时，兜底显示 AI 回复", flush=True)
                deliver_once()

        # 正常停止思考约 2 秒，抱胸动画约 10 秒；15 秒只作为异常兜底。
        QTimer.singleShot(15_000, delivery_fallback)
        # 先结束思考（如果是非待机模式，会播放放下手机动画；如果是待机模式，则什么都不做）
        if self._standby_state != "STANDBY":
            # 非待机模式，使用原有的思考结束逻辑（等待打字动画结束）
            def after_thinking():
                if self._pending_arms_cross:
                    self._pending_arms_cross = False
                    self._char_widget.play_arms_cross(on_finished=deliver_once)
                else:
                    deliver_once()
            self._avatar_actions.finish_thinking(on_finished=after_thinking)
        else:
            # 待机模式下，直接触发说话动画（会等待当前倾听动画播放完）
            deliver_once()


    def _continue_response(self, text: str):
        from utils.emotion_manager import get_random_emotion_image
        import random

        # 去除 AI 回复中的 ** 星号
        display_text = text.replace('**', '')

        # Only a factual completion marker is stored for achievements.  The
        # user's message and the reply stay exclusively in chat history.
        try:
            from brain.interaction_events import record_interaction
            record_interaction(
                feature="chat", event_type="chat_turn_completed",
                source_id=f"{self._agent._session_id}:{datetime.now().isoformat(timespec='seconds')}",
                summary="完成了一次对话", searchable=False,
            )
        except Exception as exc:
            print(f"[成就记录] 对话事件记录失败: {exc}")
        self._schedule_achievement_unlock_check()

        play_sound("lianxinSend.mp3")

        # 再次检查：如果在上一个 segment_sender 还没结束就收到了新回复，取消旧段
        if hasattr(self, '_segment_sender') and self._segment_sender is not None:
            if self._segment_sender.is_running:
                self._segment_sender.cancel()
                self._segment_sender = None

        self._segment_sender = SegmentSender(
            display_text, self._chat_widget, self._speaker, self,
            conversational=self._should_send_as_conversation(display_text),
        )

        def on_segment_finished():
            self._segment_sender = None
            self._achievement_user_turn_active = False
            self._set_idle_state()
            if self._galgame_visible and self._galgame_dialog:
                self._galgame_dialog.set_status("就绪")
            if self.isMinimized():
                self.flash_taskbar(flash_count=0)
            self._restart_listening()
            # 刷新B站冲浪数据
            if self._proactive_dialog and self._proactive_dialog.isVisible():
                try:
                    self._proactive_dialog._refresh_bl_tags()
                    self._proactive_dialog._refresh_bl_history()
                except Exception:
                    pass
        self._segment_sender.finished.connect(on_segment_finished)

        segments = self._segment_sender._segments
        first_segment = segments[0] if segments else display_text

        emotion = getattr(self._agent, '_last_emotion', None) if self._agent else None

        if self._galgame_visible and self._galgame_dialog:
            self._galgame_dialog.set_status("正在回复", active=True)
            galgame_emotion = self._expression_mgr.match(first_segment)
            self._galgame_dialog.show_reply(display_text)
            if self._tachie_win:
                img_path = self._expression_mgr.get_image_path(galgame_emotion)
                if img_path:
                    self._tachie_win.set_image(img_path)

        if emotion:
            prob = self._global_settings.emotion_probability
            if random.random() < prob:
                img_path = get_random_emotion_image(emotion)
                if img_path:
                    self._chat_widget.add_ai_image(img_path)

        # 说话状态
        self._avatar_actions.speaking_started("text_response")
        self._input_panel.set_mute_visible(True)
        self._segment_sender.finished.connect(self._avatar_actions.speaking_finished)
        self._segment_sender.finished.connect(lambda: self._input_panel.set_mute_visible(False))

        self._segment_sender.start()

    @staticmethod
    def _should_send_as_conversation(text: str) -> bool:
        """Only casual, compact replies get chat-like multi-bubble pacing."""
        value = str(text or "").strip()
        if not value or len(value) > 220 or "```" in value:
            return False
        structured_markers = ("\n1.", "\n- ", "\n|", "{", "def ", "Traceback")
        return not any(marker in value for marker in structured_markers)



    def _on_error(self, error_msg: str):
        self._pending_arms_cross = False   # 重置标志
        self._chat_widget.add_ai_message(f"（出错了：{error_msg}）")
        self._set_idle_state()
        if hasattr(self, "_avatar_actions"):
            self._avatar_actions.request("error", source="agent_error", force=True)

    # ── Galgame 模式 ──────────────────────────────────────────

    def _toggle_galgame(self):
        """打开/关闭 Galgame 立绘+对话框窗口。"""
        if self._galgame_visible:
            self._hide_galgame()
        else:
            QTimer.singleShot(50, self._show_galgame)

    def _show_galgame(self):
        """显示 Galgame 窗口。"""
        if self._tachie_win is None:
            assets_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"
            )
            self._tachie_win = TachieWindow(assets_dir)
            self._galgame_dialog = GalgameDialog()
            # 连接对话框发送信号
            self._galgame_dialog.message_submitted.connect(self._on_galgame_message)
            self._galgame_dialog.voice_requested.connect(self._on_galgame_voice_requested)
            self._galgame_dialog.mute_toggled.connect(self._on_mute)
            # 连接立绘拖拽 → 对话框跟随移动
            self._tachie_win.position_changed.connect(self._on_tachie_moved)
            # 连接立绘右键 → 切换对话框显示
            self._tachie_win.toggle_dialog_requested.connect(self._toggle_galgame_dialog)

        if not self._galgame_positioned:
            # 仅首次显示时放在桌面右上角附近
            screen = self.screen().availableGeometry()
            tx = screen.width() - self._tachie_win.width() - 40
            ty = screen.height() - self._tachie_win.height() - 80
            self._tachie_win.move(tx, ty)

            # 对话框在立绘左侧
            dx = tx - self._galgame_dialog.width() + 20
            dy = ty + 40
            self._galgame_dialog.move(dx, dy)
            self._galgame_positioned = True

        self._tachie_win.show()
        self._galgame_dialog.show()

        self._galgame_visible = True
        self._galgame_btn.setText("🎮 Galgame ●")
        self._tachie_win.start_breathing()


    def _hide_galgame(self):
        """隐藏 Galgame 窗口。"""
        if self._tachie_win:
            self._tachie_win.hide()
        if self._galgame_dialog:
            self._galgame_dialog.hide()
        self._galgame_visible = False
        self._galgame_btn.setText("🎮 Galgame")
        self._tachie_win.stop_breathing()
        self._tachie_win.stop_talking()



    def _on_tachie_moved(self, tx: int, ty: int):
        """立绘拖拽时，对话框保持相对偏移跟随移动。"""
        self._galgame_positioned = True
        if self._galgame_dialog and self._galgame_dialog.isVisible():
            dx = tx - self._galgame_dialog.width() + 20
            dy = ty + 40
            self._galgame_dialog.move(dx, dy)

    def _toggle_galgame_dialog(self):
        """右键立绘：切换对话框显示/隐藏（不关闭立绘，记住上次位置）。"""
        if self._galgame_dialog:
            if self._galgame_dialog.isVisible():
                self._galgame_dialog.hide()
            else:
                self._galgame_dialog.show()


    def _on_galgame_message(self, text: str):
        """Galgame 对话框发送消息。"""
        if self._galgame_dialog:
            self._galgame_dialog.set_status("莲心正在思考", active=True)
        self._send_user_text_to_agent(text)

    def _on_galgame_voice_requested(self):
        """在 Galgame 输入框复用主界面的本地 VoiceWorker。"""
        if getattr(self, "_galgame_voice_worker", None) and self._galgame_voice_worker.isRunning():
            self._galgame_voice_worker.stop()
            return
        self._galgame_voice_worker = VoiceWorker(self._listener, self)
        self._galgame_voice_worker.recording_stopped.connect(
            lambda: self._galgame_dialog and self._galgame_dialog.set_voice_recording(False))
        self._galgame_voice_worker.text_ready.connect(self._on_galgame_voice_text)
        self._galgame_voice_worker.error_occurred.connect(self._on_galgame_voice_error)
        self._galgame_dialog.set_voice_recording(True)
        self._galgame_dialog.set_status("正在聆听", active=True)
        self._galgame_voice_worker.start()

    def _on_galgame_voice_text(self, text: str):
        if not self._galgame_dialog:
            return
        text = text.strip()
        self._galgame_dialog.set_voice_recording(False)
        if not text:
            self._galgame_dialog.set_status("未识别到语音")
            return
        self._galgame_dialog.set_input_text(text)
        if self._galgame_dialog.auto_send_enabled():
            self._galgame_dialog.set_status("已识别，正在发送", active=True)
            self._on_galgame_message(text)
        else:
            self._galgame_dialog.set_status("已识别，等待发送")

    def _on_galgame_voice_error(self, err: str):
        if self._galgame_dialog:
            self._galgame_dialog.set_voice_recording(False)
            self._galgame_dialog.set_status(f"语音失败：{err}")


    def _on_galgame_expression(self, emotion: str):
        """set_expression 工具回调：切换立绘表情。"""
        if self._galgame_visible and self._tachie_win:
            img_path = self._expression_mgr.get_image_path(emotion)
            if img_path:
                self._tachie_win.set_image(img_path)
    def _on_galgame_speaking_start(self):
        if self._galgame_visible and self._tachie_win:
            self._tachie_win.stop_breathing()
            self._tachie_win.start_talking()

    def _on_galgame_speaking_stop(self):
        if self._galgame_visible and self._tachie_win:
            self._tachie_win.stop_talking()
            self._tachie_win.start_breathing()

    def _setup_galgame_hotkey(self, register: bool = True):
        """Windows 使用全局热键；其他平台降级为窗口内快捷键。"""
        MOD_CONTROL = 0x0002
        MOD_ALT     = 0x0001
        VK_X        = 0x58
        if not _PLATFORM_CAPS.native_global_hotkey:
            if register and self._galgame_shortcut is None:
                self._galgame_shortcut = QShortcut(QKeySequence("Ctrl+Alt+X"), self)
                self._galgame_shortcut.activated.connect(self._toggle_galgame)
            elif not register and self._galgame_shortcut is not None:
                self._galgame_shortcut.setEnabled(False)
                self._galgame_shortcut.deleteLater()
                self._galgame_shortcut = None
            return
        try:
            if register:
                user32.RegisterHotKey(None, _HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_X)
            else:
                user32.UnregisterHotKey(None, _HOTKEY_ID)
        except Exception:
            pass

    # ── 语音输入 ─────────────────────────────────────────────

    def _on_voice_clicked(self):
        if self._is_recording:
            if self._voice_worker:
                self._voice_worker.stop()
            return
        self._is_recording = True
        self._input_panel.set_voice_recording()
        self._chat_widget.add_system_tip("🎤 正在录音，停顿后自动识别…")
        self._input_panel.hide_interrupt_bar()
        self._input_panel.set_enabled(False)
        self._voice_worker = VoiceWorker(self._listener, self)
        self._voice_worker.recording_stopped.connect(self._on_recording_stopped)
        self._voice_worker.text_ready.connect(self._on_voice_text)
        self._voice_worker.error_occurred.connect(self._on_voice_error)
        self._voice_worker.start()

    def _on_recording_stopped(self):
        self._is_recording = False
        self._input_panel.set_voice_idle()
        self._chat_widget.add_system_tip("识别中…")

    def _on_voice_text(self, text: str):
        """麦克风录音完成 → 文字进输入框预览 → 延迟发送"""
        self._input_panel.set_enabled(True)
        text = text.strip()
        if not text:
            self._is_recording = False
            self._input_panel.set_voice_idle()
            self._chat_widget.add_system_tip("未识别到语音内容")
            return

        self._input_panel.set_text(text)
        self._chat_widget.add_system_tip("已识别，0.8秒后自动发送…")
        self._is_recording = False
        self._input_panel.set_voice_idle()

        # 取消之前的定时器
        if hasattr(self, '_voice_auto_send_timer') and self._voice_auto_send_timer:
            self._voice_auto_send_timer.stop()

        # 0.8 秒后自动发送
        self._voice_auto_send_timer = QTimer(self)
        self._voice_auto_send_timer.setSingleShot(True)
        self._voice_auto_send_timer.timeout.connect(self._on_mic_auto_send)
        self._voice_auto_send_timer.start(800)

    def _on_voice_error(self, err: str):
        self._is_recording = False
        self._input_panel.set_voice_idle()
        self._input_panel.set_enabled(True)
        self._chat_widget.add_system_tip(f"语音识别失败：{err}")

    # ── TTS 播放 ─────────────────────────────────────────────

    def _speak(self, text: str):
        if self._global_settings.silent_mode:
            return
        self._speaker_worker = SpeakerWorker(self._speaker, text, self)
        avatar_actions = getattr(self, "_avatar_actions", None)
        if avatar_actions is not None:
            self._speaker_worker.speaking_started.connect(
                lambda: avatar_actions.speaking_started("tts")
            )
        self._speaker_worker.speaking_started.connect(lambda: self._input_panel.set_mute_visible(True))
        self._speaker_worker.speaking_started.connect(self._on_galgame_speaking_start)
        # 全双工模式：TTS 播放时暂停 VAD，防止莲心声音被麦克风拾取→打断循环
        if self._voice_duplex:
            self._speaker_worker.speaking_started.connect(self._voice_duplex.pause_vad)
        self._speaker_worker.speaking_finished.connect(self._on_galgame_speaking_stop)
        if avatar_actions is not None:
            self._speaker_worker.speaking_finished.connect(avatar_actions.speaking_finished)
        self._speaker_worker.speaking_finished.connect(lambda: self._input_panel.set_mute_visible(False))
        if self._voice_duplex:
            self._speaker_worker.speaking_finished.connect(self._voice_duplex.resume_vad)
            # 莲心说完后延迟播提示音（等 VAD cooldown 结束，用户可发言时）
            self._speaker_worker.speaking_finished.connect(
                lambda: QTimer.singleShot(2500, self._play_speak_cue))
        self._speaker_worker.start()

    def _play_speak_cue(self):
        """播放\"轮到用户发言\"提示音。仅在待机模式播。"""
        if self._standby_state != "STANDBY":
            return
        from utils.sound import play_sound
        play_sound("StartSpeak.mp3")


    # ── 状态管理 ─────────────────────────────────────────────

    def _set_thinking_state(self):
        self._input_panel.set_enabled(False)
        # 判断是否处于待机模式
        if self._standby_state == "STANDBY":
            # 待机模式下：不切换动画，只更新状态文字和聊天框提示
            self._char_widget.set_thinking_status()
            self._chat_widget.show_thinking()
        else:
            # 非待机模式：使用思考动画（拿起手机 -> 打字）
            self._avatar_actions.request("thinking", source="conversation", force=True)
            self._chat_widget.show_thinking()
            self._input_panel.set_resend_visible(True)


    def _set_idle_state(self):
        if hasattr(self, "_avatar_actions"):
            self._avatar_actions.request("idle", source="ui_idle", force=True)
        else:
            self._char_widget.set_normal()
        self._input_panel.set_enabled(True)
        self._input_panel.set_resend_visible(False)
        self._input_panel.set_mute_visible(False)

    def _on_mute(self):
        """停止莲心朗读。"""
        try:
            from skills.语音合成.tools import stop_voice_playback
            stop_voice_playback()
        except Exception:
            pass
        if self._speaker_worker and self._speaker_worker.isRunning():
            self._speaker.stop()
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.stop()
        except Exception:
            pass
        self._input_panel.set_mute_visible(False)

    def _on_resend(self):
        """打断思考，回填上一条用户消息到输入框。"""
        self._agent_watchdog.stop()
        if self._agent_worker and self._agent_worker.isRunning():
            self._agent.cancel_active_request("用户手动打断")
            self._agent_worker.terminate()
            self._agent_worker = None
        self._input_panel.hide_interrupt_bar()
        self._char_widget.stop_thinking()
        # 回填最后一条用户消息
        last_text = ""
        for m in reversed(self._agent.history):
            if m["role"] == "user":
                last_text = m["content"]
                break
        self._input_panel.set_text(last_text)
        self._set_idle_state()


    # ── 历史记录 ─────────────────────────────────────────────

    def _on_history_clicked(self):
        play_sound("ButtonAll.mp3")
        if self._history_dialog is None:
            self._history_dialog = HistoryDialog(
                self._agent.get_history_manager(),
                current_session_id=self._agent._session_id,
                parent=self,
                first_meet_date=self._accompany_stats.get_first_meet_date(),
            )
            self._history_dialog.import_memory.connect(self._on_import_memory)
        self._history_dialog.show()
        self._history_dialog.raise_()
        self._history_dialog.activateWindow()
        self._ensure_valid_session()

    def _ensure_valid_session(self):
        mgr = self._agent.get_history_manager()
        sessions = mgr.get_sessions()
        ids = {s["id"] for s in sessions}
        if self._agent._session_id in ids:
            return
        if sessions:
            newest = sessions[0]
            self._agent._session_id = newest["id"]
            self._agent._session_titled = True
            raw = mgr.get_messages(newest["id"])
            self._agent.history = [{"role": m["role"], "content": m["content"]} for m in raw]
            self._chat_widget.clear_messages()
            for m in raw[-30:]:
                if m["role"] == "user":
                    self._chat_widget.add_user_message(m["content"])
                else:
                    clean, _ = _strip_emotion_tag(m["content"])
                    self._chat_widget.add_ai_message(clean or m["content"])
            self._chat_widget.add_system_tip("—— 当前会话已删除，已自动切换到最近的其他会话 ——")
        else:
            from brain.task_tracker import reset_task_tracker
            reset_task_tracker()
            self._agent.new_session()
            self._chat_widget.clear_messages()
            self._chat_widget.add_ai_message("通讯设备正在启动...这里是助手莲心（埋头调试ing...）")

    def _on_new_chat_clicked(self):
        play_sound("ButtonAll.mp3")
        reply = QMessageBox.question(
            self, "新建对话",
            "当前对话已自动保存。\n确定要开启新对话吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        from brain.task_tracker import reset_task_tracker
        reset_task_tracker()
        from brain.task_tracker import reset_task_tracker
        reset_task_tracker()
        self._agent.new_session()
        self._chat_widget.clear_messages()
        self._chat_widget.add_ai_message("这里是助手莲心，现实稳定锚就绪，坐标稳定...收到请回复~")
        self._duty_scheduler.on_session_started()

    def _on_import_memory(self, session_id: int):
        msgs = self._agent.get_history_manager().get_messages(session_id)
        if not msgs:
            QMessageBox.information(self, "提示", "该会话暂无消息内容。")
            return
        total_chars = sum(len(m["content"]) for m in msgs)
        reply = QMessageBox.question(
            self, "导入记忆确认",
            f"即将导入历史会话共 {len(msgs)} 条消息（约 {total_chars} 字符）。\n"
            f"导入后将追加到当前对话上下文，是否确认？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self._chat_widget.add_system_tip(f"—— 导入历史记忆（共 {len(msgs)} 条）——")
        mgr = self._agent.get_history_manager()
        for m in msgs:
            prefixed = f"[回顾] {m['content']}"
            self._agent.history.append({"role": m["role"], "content": prefixed})
            if m["role"] == "user":
                self._chat_widget.add_user_message(prefixed)
            else:
                self._chat_widget.add_ai_message(prefixed)
            mgr.save_message(self._agent._session_id, m["role"], prefixed)

    # ── 陪伴统计 ─────────────────────────────────────────────

    def _on_legacy_accompany_clicked(self):
        play_sound("ButtonAll.mp3")
        from gui.accompany_dialog import AccompanyDialog
        self._accompany_stats.reload()
        if not self._accompany_stats.has_first_meet_date():
            reply = QMessageBox.question(
                self,
                "设置初识日期",
                "检测到你还没有设置与莲心初次见面的日期！\n\n"
                "设置后可以计算「一起度过的第X天」哦~\n\n"
                "是否现在去设置？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self._on_settings_clicked()
            return
        if self._accompany_dialog is None:
            self._accompany_dialog = AccompanyDialog(self._accompany_stats, self.music_stats, self)
            self._accompany_dialog.dialog_closed.connect(self._on_accompany_dialog_closed)
        self._accompany_dialog.show()
        self._accompany_dialog.raise_()
        self._accompany_dialog.activateWindow()

    # The old compact dialog remains in the source for compatibility, but the
    # companion entry now opens the complete four-page achievement record.
    def _on_accompany_clicked(self):
        play_sound("ButtonAll.mp3")
        self._accompany_stats.reload()
        from gui.achievement.web_window import AchievementWindow
        if self._achievement_window is None:
            self._achievement_window = AchievementWindow(self)
            self._achievement_window.closed.connect(self._on_achievement_window_closed)
        self._achievement_window.show()
        self._achievement_window.raise_()
        self._achievement_window.activateWindow()

    def _on_achievement_window_closed(self):
        self._achievement_window = None

    def _on_avatar_interaction(self, role: str):
        """头像双击入口；互动独立于正常 AgentWorker。"""
        if role != "assistant":
            return
        if hasattr(self, "_avatar_interaction"):
            self._avatar_interaction.trigger(role)

    def _on_avatar_interaction_thinking(self, text: str):
        """拍一拍走 LLM 时，同步聊天提示与左侧角色思考状态。"""
        self._chat_widget.show_avatar_thinking(text)
        try:
            self._avatar_actions.request("thinking", source="avatar_interaction", force=True)
        except Exception:
            self._char_widget.start_thinking()

    def _on_avatar_interaction_accepted(self, action: str, target: str, source: str,
                                        counter_action: str = ""):
        """有效互动才写入聊天事件流；冷却/忙碌请求不会留下假事件。"""
        try:
            self._avatar_actions.request("affection", source=f"avatar_{action}", force=True)
        except Exception:
            pass
        if source == "assistant" and self.isMinimized():
            self.flash_taskbar(flash_count=2)
        if action == "tap":
            play_sound("拍一拍.mp3")
            if source == "assistant":
                text = "莲心拍了拍你的头"
            elif target == "assistant":
                text = "你拍了拍莲心的头"
            else:
                text = "你拍了拍自己的头像"
        else:
            text = "莲心轻轻摸了摸你的头" if source == "assistant" else "你摸了摸莲心的头"
        self._chat_widget.add_system_tip(text)
        from gui.avatar_widgets import CircularAvatar
        target_role = "user" if target == "user" else "assistant"
        for avatar in self._chat_widget.findChildren(CircularAvatar):
            if avatar.role == target_role:
                if action == "headpat":
                    avatar.play_headpat_animation()
                elif action == "tap":
                    avatar.play_tap_animation()
        if counter_action:
            def play_counter_action():
                for avatar in self._chat_widget.findChildren(CircularAvatar):
                    if avatar.role != "user":
                        continue
                    if counter_action == "headpat":
                        avatar.play_headpat_animation()
                    else:
                        avatar.play_tap_animation()
                play_sound("拍一拍.mp3")
                counter_text = (
                    "莲心也轻轻摸了摸你的头"
                    if counter_action == "headpat" else "莲心反手拍了拍你的头"
                )
                self._chat_widget.add_system_tip(counter_text)
            QTimer.singleShot(160, play_counter_action)
        self._schedule_achievement_unlock_check()

    def _on_avatar_context(self, role: str):
        if role != "assistant":
            return
        menu = QMenu(self)
        mood_action = menu.addAction("查看当前心情")
        tap_action = menu.addAction("拍一拍")
        pat_action = menu.addAction("摸摸头")
        menu.addSeparator()
        stats_action = menu.addAction("查看数据潮汐")
        chosen = menu.exec_(self.cursor().pos())
        if chosen == mood_action:
            try:
                from brain.emotional import get_manager
                state = get_manager().state
                mood = "开心" if state.valence > 0.25 else ("有点低落" if state.valence < -0.25 else "平静")
                self._chat_widget.add_system_tip(f"莲心现在心情{mood}，正在陪着你")
            except Exception:
                self._chat_widget.add_system_tip("莲心正在这里陪着你")
        elif chosen == tap_action:
            self._avatar_interaction.trigger("assistant")
        elif chosen == pat_action:
            self._avatar_interaction.trigger_headpat("assistant")
        elif chosen == stats_action:
            self._on_accompany_clicked()

    def _on_avatar_clicked(self, role: str):
        # 左键单击按设计不触发任何互动；心情查看请使用右键菜单。
        return

    def _on_avatar_long_pressed(self, role: str):
        if role != "assistant":
            return
        self._avatar_interaction.trigger_headpat("assistant")

    def _on_avatar_interaction_response(self, text: str, counter_tap: bool):
        self._accompany_stats.reload()
        if self._accompany_dialog is not None and self._accompany_dialog.isVisible():
            self._accompany_dialog._update_content()
        from config import get_chat_avatar_config
        if get_chat_avatar_config().get("response_in_chat", True):
            self._chat_widget.add_ai_message(text)
        else:
            self._chat_widget.show_avatar_interaction_notice(text, 3200)
        # 头像互动回复也是莲心的真实发言，进入统一语音链路。
        self._speak(text)
        if self._global_settings.silent_mode:
            self._avatar_actions.request("idle", source="avatar_interaction_silent", force=True)

    def _on_accompany_dialog_closed(self):
        duration_str = self._accompany_stats.get_current_formatted_duration()
        session_count = self._accompany_stats.get_stats()["session_count"]
        total_days = self._accompany_stats.get_total_days_since_first_meet()
        msg = f"原来你这家伙已经待在我身边长达{duration_str}，启动了{session_count}次，相识了{total_days}天了吗？好开心~(*´▽`*)" 
        self._agent.get_history_manager().save_message(
            self._agent._session_id, "assistant", f"[陪伴统计] {msg}"
        )
        self._chat_widget.add_ai_message(msg)
        self._speak(msg)

    # ── 开机自启动网络检测 ────────────────────────────────────

    def _start_autostart_net_poll(self):
        self._autostart_net_attempts = 0
        self._autostart_net_timer = QTimer(self)
        self._autostart_net_timer.timeout.connect(self._on_autostart_net_tick)
        QTimer.singleShot(5000, self._on_autostart_net_tick)
        self._autostart_net_timer.start(_AUTOSTART_NET_INTERVAL_MS)

    def _on_autostart_net_tick(self):
        self._autostart_net_attempts += 1
        if self._autostart_net_attempts > _AUTOSTART_NET_MAX_ATTEMPTS:
            self._stop_autostart_net_poll()
            return
        if not check_network():
            return
        self._stop_autostart_net_poll()
        # 删除日期检查，每次开机都播报
        self._chat_widget.add_ai_message(_AUTOSTART_WELCOME)
        self._speak(_AUTOSTART_WELCOME)
        if self.isMinimized():
            try:
                import ctypes
                hwnd = int(self.winId())
                class FLASHWINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize",    ctypes.c_uint),
                        ("hwnd",      ctypes.c_void_p),
                        ("dwFlags",   ctypes.c_uint),
                        ("uCount",    ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint),
                    ]
                FLASHW_TRAY = 0x00000002
                FLASHW_TIMERNOFG = 0x0000000C
                info = FLASHWINFO(
                    cbSize=ctypes.sizeof(FLASHWINFO),
                    hwnd=hwnd,
                    dwFlags=FLASHW_TRAY | FLASHW_TIMERNOFG,
                    uCount=0,
                    dwTimeout=0,
                )
                ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
            except Exception:
                pass

    def _stop_autostart_net_poll(self):
        if self._autostart_net_timer:
            self._autostart_net_timer.stop()
            self._autostart_net_timer = None

    # ── API Key 配置 ──────────────────────────────────────────

    def _show_api_config(self):
        play_sound("ButtonAll.mp3")
        if self._api_config_dialog is None:
            self._api_config_dialog = ApiConfigDialog(self)
            self._api_config_dialog.config_saved.connect(self._on_api_config_saved)
        self._api_config_dialog.show()
        self._api_config_dialog.raise_()
        self._api_config_dialog.activateWindow()
    
    def _on_sound_settings(self):
        from utils.sound import play_sound
        play_sound("ButtonAll.mp3")
        from gui.sound_settings_dialog import SoundSettingsDialog
        if self._sound_settings_dialog is None:
            self._sound_settings_dialog = SoundSettingsDialog(self)
        self._sound_settings_dialog.show()
        self._sound_settings_dialog.raise_()
        self._sound_settings_dialog.activateWindow()

    def _on_memory_settings(self):
        from utils.sound import play_sound
        play_sound("ButtonAll.mp3")
        from gui.memory_settings_dialog import MemorySettingsDialog
        if self._memory_settings_dialog is None:
            self._memory_settings_dialog = MemorySettingsDialog(self)
        self._memory_settings_dialog.show()
        self._memory_settings_dialog.raise_()
        self._memory_settings_dialog.activateWindow()

    def _show_network_settings(self):
        from utils.sound import play_sound
        play_sound("ButtonAll.mp3")
        if self._network_settings_dialog is None:
            self._network_settings_dialog = NetworkSettingsDialog(self)
            self._network_settings_dialog.config_saved.connect(self._on_api_config_saved)
        self._network_settings_dialog.show()
        self._network_settings_dialog.raise_()
        self._network_settings_dialog.activateWindow()

    def _show_capability_center(self):
        from utils.sound import play_sound
        play_sound("ButtonAll.mp3")
        if self._capability_center_dialog is None:
            self._capability_center_dialog = CapabilityCenter(self)
            self._capability_center_dialog.tool_requested.connect(self._on_capability_tool_requested)
        self._capability_center_dialog.show()
        self._capability_center_dialog.raise_()
        self._capability_center_dialog.activateWindow()

    def _on_capability_tool_requested(self, tool_name: str, mode: str):
        self._input_panel.select_tool(tool_name, mode)
        self._input_panel._input.setFocus()

    def _show_constellation_system(self):
        """Open the Canvas-based Memory Constellations comparison view."""
        from utils.sound import play_sound
        from gui.memory_constellation_web import MemoryConstellationWebWindow
        play_sound("ButtonAll.mp3")
        if self._constellation_system is None:
            self._constellation_system = MemoryConstellationWebWindow(self)
        self._constellation_system.show()
        self._constellation_system.raise_()
        self._constellation_system.activateWindow()

    def _show_ripple_constellation(self):
        """Open the unified emotion and memory star map."""
        from utils.sound import play_sound
        from gui.ripple_constellation_web import RippleConstellationWebWindow
        play_sound("ButtonAll.mp3")
        if self._ripple_constellation_system is None:
            self._ripple_constellation_system = RippleConstellationWebWindow(self)
            self._ripple_constellation_system.destroyed.connect(
                lambda: setattr(self, "_ripple_constellation_system", None)
            )
        self._ripple_constellation_system.show()
        self._ripple_constellation_system.raise_()
        self._ripple_constellation_system.activateWindow()

    def _show_persona_hub(self):
        """打开可窗口化、最大化和全屏的人格枢控。"""
        from utils.sound import play_sound
        from gui.persona_hub import PersonaHub
        play_sound("ButtonAll.mp3")
        if self._persona_hub is None:
            self._persona_hub = PersonaHub(self)
            self._persona_hub.persona_activated.connect(self._on_persona_activated)
            self._persona_hub.growth_event_applied.connect(self._on_growth_event_applied)
        if self._persona_hub.isMinimized():
            self._persona_hub.showNormal()
        else:
            self._persona_hub.show()
        self._persona_hub.raise_()
        self._persona_hub.activateWindow()

    def _show_growth_event(self, event_id: int):
        self._show_persona_hub()
        if self._persona_hub is not None:
            self._persona_hub.show_growth_event(event_id)

    def _on_growth_event_applied(self, event):
        self._chat_widget.add_growth_event(event)

    def _on_persona_activated(self, profile_name: str, start_new_conversation: bool):
        """人格从下一条请求生效；可选创建干净的新会话。"""
        if start_new_conversation:
            from brain.task_tracker import reset_task_tracker
            reset_task_tracker()
            self._agent.new_session()
            self._chat_widget.clear_messages()
            self._chat_widget.add_system_tip(
                f"—— 已激活人格“{profile_name}”，并开启全新对话 ——"
            )
            if self._history_dialog is not None:
                self._history_dialog.refresh(self._agent._session_id)
            self._duty_scheduler.on_session_started()
        else:
            self._chat_widget.add_system_tip(
                f"—— 已激活人格“{profile_name}”，将从下一条消息生效 ——"
            )

    def _on_api_config_saved(self):
        self._agent = AgentCore()
        self._chat_widget.add_system_tip("✅ 配置已更新，莲心已重新连接。")

        # QQ 桥接热重载（如果正在运行）
        self._bridge_controller.reload_qq_bridge_config()

    # ── 全局设置 ─────────────────────────────────────────────

    def _on_settings_clicked(self):
        play_sound("ButtonAll.mp3")
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self)
            self._settings_dialog.date_saved.connect(self._on_first_meet_date_saved)
            self._settings_dialog.background_changed.connect(self._on_background_changed)
            self._settings_dialog.avatars_changed.connect(self._chat_widget.refresh_avatars)
            self._settings_dialog.window_settings_changed.connect(self._apply_window_settings)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _on_open_emotion_debug(self):
        """打开涟漪情感系统调试面板。"""
        play_sound("ButtonAll.mp3")
        from gui.emotional_debug_dialog import EmotionalDebugDialog
        if self._emotion_debug_dialog is None:
            self._emotion_debug_dialog = EmotionalDebugDialog(self)
            self._emotion_debug_dialog.destroyed.connect(
                lambda: setattr(self, '_emotion_debug_dialog', None))
        self._emotion_debug_dialog.show()
        self._emotion_debug_dialog.raise_()
        self._emotion_debug_dialog.activateWindow()

    def _on_first_meet_date_saved(self):
        self._accompany_stats.reload()
        self._chat_widget.add_system_tip("📅 初识日期已更新，陪伴统计已同步。")

    # ── 莲心自习室 ──────────────────────────────────────────

    def _on_study_room_clicked(self):
        play_sound("ButtonAll.mp3")
        if self._study_room_window is None:
            from gui.study_room import StudyRoomWebWindow
            self._study_room_window = StudyRoomWebWindow(self)
            self._study_room_window.focus_completed.connect(self._on_study_focus_completed)
            self._study_room_window.closed.connect(self._on_study_room_closed)
        self._study_room_window.show()
        self._study_room_window.raise_()
        self._study_room_window.activateWindow()

    def _on_study_focus_completed(self, task_name: str, duration: int):
        minutes = max(1, int(duration) // 60)
        text = f"📚 专注完成啦，这次坚持了 {minutes} 分钟，辛苦了。"
        if task_name:
            text = f"📚 你完成了「{task_name}」的 {minutes} 分钟专注，辛苦啦。"
        self._chat_widget.add_system_tip(text)
        if hasattr(self, "_avatar_actions"):
            self._avatar_actions.request("celebrate", source="study_focus", force=True)

    def _on_study_room_closed(self):
        self._study_room_window = None

    # ── 闹钟功能 ─────────────────────────────────────────────

    def _on_alarm_clicked(self):
        play_sound("ButtonAll.mp3")
        if self._alarm_dialog is None:
            self._alarm_dialog = AlarmDialog(self._alarm_manager, self, todo_manager=self._todo_manager, reminder_manager=self.reminder_manager)
            self._alarm_dialog.alarms_changed.connect(self._on_alarms_changed)
        self._alarm_dialog.show()
        self._alarm_dialog.raise_()
        self._alarm_dialog.activateWindow()

    def _check_alarms(self):
        due_alarms = self._alarm_manager.get_due_alarms()
        if due_alarms:
            print(f"[闹钟调试] 发现 {len(due_alarms)} 个闹钟: {[a.name for a in due_alarms]}")
        for alarm in due_alarms:
            self._on_alarm_triggered(alarm)

    def _on_alarm_triggered(self, alarm):
        self._alarm_manager.mark_fired(alarm.id)
        msg = f"⏰ {alarm.time_str} 了哦~「{alarm.name}」时间到啦！记得关闹钟！"
        self._agent.get_history_manager().save_message(
            self._agent._session_id, "assistant", f"[闹钟] {msg}"
        )
        self._chat_widget.add_ai_message(msg)
        self._speak(msg)
        repeat_text = REPEAT_LABELS.get(alarm.repeat, "仅一次")
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("⏰ 闹钟")
        msg_box.setText(f"「{alarm.name}」时间到啦！\n\n时间：{alarm.time_str}\n重复：{repeat_text}")
        msg_box.setIcon(QMessageBox.Information)
        snooze_btn = msg_box.addButton("再响5分钟", QMessageBox.AcceptRole)
        msg_box.addButton("关闭", QMessageBox.RejectRole)
        def on_button_clicked(btn):
            if btn == snooze_btn:
                from datetime import datetime, timedelta
                snooze_time = (datetime.now() + timedelta(minutes=5)).strftime("%H:%M")
                self._alarm_manager.add_alarm(
                    name=f"{alarm.name}(贪睡)",
                    time_str=snooze_time,
                    repeat="once"
                )
                self._chat_widget.add_system_tip(f"⏰ {alarm.name} 已推迟5分钟")
        msg_box.buttonClicked.connect(on_button_clicked)
        msg_box.show()

    def _on_alarms_changed(self):
        pass

    # ── 倒计时管理（主窗口统一处理）────────────────────────────

    def _check_countdowns(self):
        finished = self._alarm_manager.update_countdowns()
        for cd in finished:
            self._on_countdown_finished(cd.name, cd.total_seconds)

    def _on_countdown_finished(self, name: str, total_seconds: int):
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        duration_parts = []
        if h > 0:
            duration_parts.append(f"{h}小时")
        if m > 0 or h > 0:
            duration_parts.append(f"{m}分钟")
        duration_parts.append(f"{s}秒")
        duration_str = "".join(duration_parts)
        msg = f"⏰ 倒计时结束啦！「{name}」的{duration_str}已经过去啦~"
        self._agent.get_history_manager().save_message(
            self._agent._session_id, "assistant", f"[倒计时] {msg}"
        )
        self._chat_widget.add_ai_message(msg)
        self._speak(msg)
    # ── 自动化任务处理 ──────────────────────────────────────

    def _on_auto_task_due(self, task):
        execute_auto_task(
            task,
            on_complete=lambda tid, ok, msg: self._auto_task_done_signal.emit(tid, ok, msg)
        )

    def _on_auto_task_missed(self, task):
        msg = f"主人，有个名为「{task.name}」的任务现在还没做，需要我补上吗？"
        self._chat_widget.add_ai_message(msg)
        self._speak(msg)

    def _on_auto_task_completed(self, task_id, success, message):
        manager = get_auto_task_manager()
        task = manager.get_task(task_id)
        if not task:
            return
        if success:
            msg = f"🤖 自动化任务「{task.name}」执行完成"
            self._chat_widget.add_system_tip(msg)
            self._speak(f"已完成自动化任务：{task.name}")
            if hasattr(self, "_avatar_actions"):
                self._avatar_actions.request("celebrate", source="auto_task", force=True)
            if hasattr(self, "_window_experience") and (self.isHidden() or self.isMinimized()):
                self._window_experience.notify("自动化任务完成", task.name)
        else:
            # P2: 失败时发送醒目的 AI 消息 + 语音播报，而非仅在终端打印
            fail_msg = (
                f"⚠️ 自动化任务「{task.name}」执行失败了 😢\n\n"
                f"原因：{message[:200]}\n\n"
                f"💡 可以在「闹钟&提醒 → 自动化」标签页查看详细日志。"
            )
            self._chat_widget.add_ai_message(fail_msg)
            self._speak(f"主人，自动化任务「{task.name}」执行失败了，请查看详情")
            if hasattr(self, "_avatar_actions"):
                self._avatar_actions.request("concerned", source="auto_task_error", force=True)
            if hasattr(self, "_window_experience") and (self.isHidden() or self.isMinimized()):
                self._window_experience.notify("自动化任务执行失败", task.name)
    # ── 自动化任务自然语言解析 ──────────────────────────────

    def _detect_auto_task_intent(self, text: str) -> bool:
        """仅在消息开头包含【自动化】标签时才触发任务解析。零误触。"""
        return "【自动化】" in text

    def _try_parse_auto_task(self, text: str):
        from brain.auto_task_parser import parse_auto_task, generate_confirm_message
        from brain.auto_task_manager import get_auto_task_manager

        self._set_thinking_state()

        def _parse():
            try:
                task = parse_auto_task(text)
                confirm_msg = generate_confirm_message(task)
                if task.schedule_type == "once" and "提醒" in task.name:
                    confirm_msg += "\n\n💡 这是一个一次性提醒，将在指定时间通知你。"

                manager = get_auto_task_manager()
                manager.add_task(task)

                # 通过信号回主线程更新 UI
                self._auto_task_parsed_signal.emit(True, confirm_msg, task)
            except Exception as e:
                self._auto_task_parsed_signal.emit(
                    False, f"任务解析失败，请手动配置: {str(e)[:100]}", None)

        from threading import Thread
        Thread(target=_parse, daemon=True).start()

    def _set_normal_state(self):
        if hasattr(self, "_avatar_actions"):
            self._avatar_actions.request("idle", source="normal_state", force=True)
        else:
            self._char_widget.set_normal()
        self._input_panel.set_mute_visible(False)
        self._is_waiting_for_response = False

    def _on_auto_task_parsed(self, success: bool, message: str, task):
        """自动化任务解析结果回调（主线程）"""
        if success:
            self._chat_widget.add_ai_message(message)
            self._speak("好的，我记下了这个自动化任务 ✨")
            if hasattr(self, '_auto_task_scheduler'):
                self._auto_task_scheduler.status_changed.emit()
        else:
            self._chat_widget.add_system_tip(message)
        self._set_normal_state()
    def _on_diary_finished(self, success: bool, result: str):
        if self._diary_dialog is not None:
            bridge = getattr(self._diary_dialog, "_bridge", None)
            if bridge is not None:
                bridge.generation_completed.emit(str(result), bool(success), "")
                if success:
                    bridge.emit_state(str(result))
                    bridge.emit_page_state("corridor")
        if success:
            self._chat_widget.add_system_tip(f"🌙 莲心已在 {result} 的时间胶囊里留下了她的书页")
            # 播放写完成音效
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                from utils.resource_path import get_asset_path
                sound_path = get_asset_path("sound", "write.mp3")
                if sound_path.exists():
                    pygame.mixer.Sound(str(sound_path)).play()
                else:
                    print(f"[写日记音效] 文件不存在: {sound_path}")
            except Exception as e:
                print(f"[写日记音效] 播放失败: {e}")
            # 刷新时间胶囊
            if self._diary_dialog is not None and self._diary_dialog.isVisible():
                self._diary_dialog.refresh(result)
        else:
            self._chat_widget.add_system_tip(f"🌙 莲心的书页暂时没有写好：{result}")


    def _check_overdue_todos(self):
        """检查过期待办并主动提醒（同一待办每天只提醒一次）"""
        # 跨天自动清零
        today = datetime.now().strftime("%Y-%m-%d")
        if getattr(self, "_reminded_todo_date", "") != today:
            self._reminded_todo_ids.clear()
            self._reminded_todo_date = today

        overdue = self._todo_manager.get_overdue_todos()
        if not overdue:
            return
        for todo in overdue[:3]:
            if todo.id in self._reminded_todo_ids:
                continue
            self._reminded_todo_ids.add(todo.id)
            due_str = ""
            if todo.due_time:
                try:
                    dt = datetime.fromisoformat(todo.due_time)
                    due_str = f"（原定于{dt.strftime('%Y-%m-%d %H:%M')}）"
                except:
                    pass
            msg = f"⚠️ 过期待办提醒：{todo.title}{due_str}"
            self._agent.get_history_manager().save_message(
                self._agent._session_id, "assistant", f"[提醒] {msg}"
            )
            self._chat_widget.add_ai_message(msg)
            self._speak(msg)

    # ── 主动聊天 ─────────────────────────────────────────────

    def _on_proactive_clicked(self):
        play_sound("ButtonAll.mp3")
        if self._proactive_dialog is None:
            self._proactive_dialog = ProactiveDialog(self._proactive_scheduler, self)
            self._proactive_dialog.debug_trigger.connect(self._on_proactive_debug)
            self._proactive_dialog.debug_observe_signal.connect(self._on_proactive_debug_observe)
            self._proactive_dialog.finished.connect(self._on_proactive_dialog_finished)
        self._proactive_dialog.show()
        self._proactive_dialog.raise_()
        self._proactive_dialog.activateWindow()

    def _on_proactive_dialog_finished(self):
        self._update_proactive_button()


    def _update_proactive_button(self):
        btn = self._char_widget.get_proactive_button()
        if self._proactive_scheduler.desktop_enabled:
            btn.setText("💬 主动聊天")
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #27AE60;
                    color: white;
                    border-radius: 16px;
                    border: none;
                    padding: 6px 12px;
                }
                QPushButton:hover {
                    background-color: #229954;
                }
                QPushButton:pressed {
                    background-color: #1E8449;
                }
            """)
        else:
            btn.setText("✋ 主动聊天")
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #7F8C8D;
                    color: white;
                    border-radius: 16px;
                    border: none;
                    padding: 6px 12px;
                }
                QPushButton:hover {
                    background-color: #707B7C;
                }
                QPushButton:pressed {
                    background-color: #616A6B;
                }
            """)

    def _on_proactive_debug(self):
        """调试触发主动聊天（通过 DutyScheduler）。"""
        try:
            statuses = self._duty_scheduler.get_all_statuses()
            for s in statuses:
                if s.name == "proactive" and s.is_running:
                    self._chat_widget.add_system_tip("主动消息正在生成中，请稍候…")
                    return
        except Exception:
            pass
        if not self._proactive_scheduler.debug_fire():
            self._chat_widget.add_system_tip("请先开启桌面或QQ主动聊天功能再使用调试。")
            return
        self._duty_scheduler.manual_trigger("proactive")

    def _on_proactive_debug_observe(self, mode: str):
        """调试观察：强制走截图/摄像头/B站冲浪模式，或摸鱼调试。"""
        if mode.startswith("slack:"):
            self._duty_scheduler.manual_trigger(
                "proactive", force_behavior="slack", force_action=mode[6:]
            )
            return
        try:
            statuses = self._duty_scheduler.get_all_statuses()
            for s in statuses:
                if s.name == "proactive" and s.is_running:
                    self._chat_widget.add_system_tip("主动消息正在生成中，请稍候…")
                    return
        except Exception:
            pass
        if mode == "bilibili":
            self._proactive_controller.set_observation_tip(
                self._chat_widget.add_system_tip("莲心正在B站冲浪…")
            )
            self._duty_scheduler.manual_trigger("proactive", force_observe="bilibili")
            return
        if not self._proactive_scheduler.observe_enabled:
            self._chat_widget.add_system_tip("请先启用调皮观察功能再使用调试。")
            return
        self._proactive_controller.set_observation_tip(
            self._chat_widget.add_system_tip(f"正在{mode}观察中…")
        )
        self._duty_scheduler.manual_trigger("proactive", force_observe=mode)

    def _on_observation_result(self, desc: str):
        """观察完成，保存描述用于短期记忆。"""
        self._proactive_controller.handle_observation_result(desc)

    def _on_observation_image(self, img_path: str, desc: str):
        """收到观察图片和视觉描述，保存并显示在聊天界面。"""
        self._proactive_controller.handle_observation_image(img_path, desc)

    def _on_proactive_response(self, text: str):
        """主动聊天回复"""
        self._proactive_controller.handle_proactive_response(text)
        if text and hasattr(self, "_avatar_actions"):
            warm_markers = ("开心", "想你", "陪你", "抱抱", "喜欢", "真好")
            action = "happy" if any(marker in text for marker in warm_markers) else "wave"
            self._avatar_actions.request(action, source="proactive_chat")
        if text and hasattr(self, "_window_experience") and (self.isHidden() or self.isMinimized()):
            self._window_experience.notify("莲心来陪你啦", text[:80])
        # 主动聊天完成后，保留少量自然的头像陪伴互动概率。
        if text and getattr(self._proactive_scheduler, "desktop_enabled", False):
            if random.random() < 0.20 and hasattr(self, "_avatar_interaction"):
                action = "headpat" if random.random() < 0.40 else "tap"
                QTimer.singleShot(700, lambda a=action: self._trigger_proactive_avatar(a))

    def _trigger_proactive_avatar(self, action="tap"):
        if not hasattr(self, "_avatar_interaction"):
            return
        self._avatar_interaction.trigger_outbound(action)

    def _on_proactive_error(self, err: str):
        self._proactive_controller.handle_proactive_error(err)

    def _on_proactive_coordination(self, message: str):
        """展示情绪与主动调度的协作状态，避免误判为主动聊天故障。"""
        self._chat_widget.add_system_tip(message)
        try:
            self._proactive_controller.set_observation_tip(message)
        except Exception:
            pass

    def _on_mooyu_data_sources(self, action_name: str, sources: list):
        self._proactive_controller.handle_mooyu_data_sources(action_name, sources)

    def _on_mooyu_duty_data_source(self, name: str, preview: str, is_error: bool, elapsed_ms: float):
        self._proactive_controller.handle_mooyu_duty_data_source(
            name, preview, is_error, elapsed_ms
        )

    def _on_checklist_proposed(self, items: list):
        """莲心从对话中提取到待办，弹窗确认。"""
        from config import get_todo_auto_confirm, save_todo_auto_confirm
        if not get_todo_auto_confirm():
            # 自动模式：直接添加
            for item in items:
                self._todo_manager.add_todo(
                    item["title"], item.get("due_time"),
                    item.get("priority", "medium")
                )
            return

        from gui.todo_confirm_dialog import TodoConfirmDialog
        dlg = TodoConfirmDialog(items, self)
        dlg.exec_()

        if dlg.was_accepted():
            for item in items:
                self._todo_manager.add_todo(
                    item["title"], item.get("due_time"),
                    item.get("priority", "medium")
                )

        if dlg.is_auto_mode():
            save_todo_auto_confirm(False)
            self._chat_widget.add_system_tip("已开启待办自动添加，可在待办选项卡中恢复询问")

    # ── 摸鱼模块 ─────────────────────────────────────────────

    def _on_slack_response(self, text: str):
        """摸鱼消息回复"""
        self._proactive_controller.handle_slack_response(text)

    def _on_slack_error(self, err: str):
        self._proactive_controller.handle_slack_error(err)

    # ── 心跳自检 ─────────────────────────────────────────────

    def _on_emotion_decay_tick(self):
        """情感衰减计时器触发，更新孤独漂移等时间衰减。"""
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            _get_emotion_mgr().update_decay_only()
        except Exception:
            pass

    def _on_agent_watchdog_timeout(self):
        """AgentWorker 看门狗超时：优雅取消 → 等待完工 → 兜底强杀 + 清理残留。"""
        if not self._agent_worker or not self._agent_worker.isRunning():
            return
        print("[看门狗] AgentWorker 超过 3 分钟无响应，发起优雅取消", flush=True)
        self._watchdog_worker = self._agent_worker
        # 记录当前 history 末尾索引，用于后续清理残留的未展示回复
        self._watchdog_history_snapshot = len(self._agent.history) if self._agent else 0
        self._watchdog_resolved = False  # 防止 response_ready 和 cleanup 重复处理
        # 协作取消
        self._agent._cancel_event.set()
        # 等待最多 8 秒让 worker 自然结束（API 超时 120s，重试时也能检查取消事件）
        self._watchdog_retry_count = 0
        self._watchdog_check_timer = QTimer(self)
        self._watchdog_check_timer.timeout.connect(self._on_watchdog_check)
        self._watchdog_check_timer.start(2000)  # 每 2 秒检查一次

    def _on_watchdog_check(self):
        target_worker = getattr(self, "_watchdog_worker", None)
        # 新消息已经替换 Worker 时，旧看门狗只需自行失效，绝不能碰新任务。
        if target_worker is None or target_worker is not self._agent_worker:
            self._stop_watchdog_check_timer()
            return
        self._watchdog_retry_count += 1
        if target_worker.isRunning():
            if self._watchdog_retry_count >= 5:  # 10 秒后仍运行
                print("[看门狗] 优雅取消失败，强制终止", flush=True)
                target_worker.terminate()
                target_worker.wait(500)
                if self._agent_worker is target_worker:
                    self._agent_worker = None
                self._finish_watchdog_cleanup(force=True)
        else:
            # worker 已自然结束（_on_ai_response 可能已处理）
            if not getattr(self, '_watchdog_resolved', False):
                self._finish_watchdog_cleanup(force=False)

    def _stop_watchdog_check_timer(self):
        timer = getattr(self, "_watchdog_check_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            del self._watchdog_check_timer
        for name in ("_watchdog_retry_count", "_watchdog_worker"):
            if hasattr(self, name):
                delattr(self, name)

    def _finish_watchdog_cleanup(self, force: bool):
        if getattr(self, '_watchdog_resolved', False):
            return
        self._watchdog_resolved = True
        self._stop_watchdog_check_timer()

        # 清理 history 中残留的未展示 assistant 回复
        if force and self._agent:
            snapshot = getattr(self, '_watchdog_history_snapshot', 0)
            current_len = len(self._agent.history)
            if current_len > snapshot:
                # history 末端新增了消息，检查是否是未展示的 assistant 回复
                new_msgs = self._agent.history[snapshot:]
                for i, msg in enumerate(new_msgs):
                    if msg.get("role") == "assistant":
                        # 清理这条未展示回复，同时清理对应位置的 user 消息后的 assistant
                        idx = snapshot + i
                        if idx < len(self._agent.history):
                            print(f"[看门狗] 清理残留回复: {msg.get('content', '')[:50]}...", flush=True)
                            self._agent.history.pop(idx)
                        break
        if hasattr(self, '_watchdog_history_snapshot'):
            del self._watchdog_history_snapshot

        self._agent._cancel_event.clear()
        self._duty_scheduler.set_agent_busy(False)
        self._input_panel.hide_interrupt_bar()
        self._char_widget.stop_thinking()
        self._set_idle_state()
        self._chat_widget.add_system_tip(
            "⚠️ 莲心响应超时（3分钟），已自动终止。请重试或检查网络。" if force
            else "⚠️ 莲心响应较慢，已自动结束当前任务。"
        )

    def _on_heartbeat_response(self, text: str):
        """心跳自检有提醒内容，显示给用户。"""
        self._agent.get_history_manager().save_message(
            self._agent._session_id, "assistant", f"[心跳提醒] {text}"
        )
        self._chat_widget.add_ai_message(text)
        if self.isMinimized():
            self.flash_taskbar(flash_count=0)
        self._speak(text)

    def _on_heartbeat_finished_silent(self):
        """心跳自检静默完成（无需提醒或失败）。"""



    def _is_shoulder_available(self) -> bool:
        """检查肩载设备（ESP32-CAM）是否在线（通过 socket 探测）。"""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            result = s.connect_ex(("192.168.43.251", 81))
            s.close()
            return result == 0
        except Exception:
            return False

    # ── 语音聊天 ──────────────────────────────────────────

    def _show_duty_center(self):
        """打开后台职责中心"""
        from gui.duty_center import DutyCenter
        dlg = DutyCenter(self._duty_scheduler, self)
        dlg.exec_()

    def _show_workflow_center(self):
        from gui.workflow_center import WorkflowCenter

        WorkflowCenter(
            self,
            retry_callback=self._retry_workflow_run,
            cancel_callback=self._cancel_workflow_run,
        ).exec_()

    def _cancel_workflow_run(self, run: dict):
        if (run.get("kind") == "conversation"
                and getattr(self, "_agent", None) is not None
                and int(getattr(self._agent, "_active_workflow_run_id", 0) or 0) == int(run["id"])):
            self._agent._cancel_event.set()

    def _retry_workflow_run(self, run: dict):
        metadata = run.get("metadata", {}) if isinstance(run.get("metadata"), dict) else {}
        if run.get("kind") == "auto_task":
            from brain.auto_task_executor import execute_auto_task
            from brain.auto_task_manager import get_auto_task_manager

            task = get_auto_task_manager().get_task(str(metadata.get("task_id", "")))
            if task is None:
                self._chat_widget.add_system_tip("原自动任务已不存在，无法重试。")
                return False
            task._workflow_retry_of_run_id = int(run["id"])
            execute_auto_task(task)
            return True
        if run.get("kind") != "conversation":
            self._chat_widget.add_system_tip("当前运行类型没有可用的重试执行器。")
            return False
        user_message = str(metadata.get("user_message", "") or "").strip()
        if not user_message:
            self._chat_widget.add_system_tip("这条运行记录缺少原始请求，无法自动重试。")
            return False
        self._agent._workflow_retry_of_run_id = int(run["id"])
        QTimer.singleShot(0, lambda: self._on_user_message(user_message, []))
        return True

    def _show_voice_chat_menu(self, pos):
        """右击语音聊天按钮 → 弹出菜单"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #2D2D3F; color: #E0E0E0; border: 1px solid #5B5B7A;
                    border-radius: 6px; padding: 4px; }
            QMenu::item { padding: 6px 24px; border-radius: 4px; }
            QMenu::item:selected { background: #4A4A6A; }
            QMenu::separator { height: 1px; background: #3D3D5A; margin: 4px 8px; }
        """)

        if self._standby_state == "STANDBY":
            menu.addAction("⏹ 关闭语音聊天", self._on_standby_clicked)
        else:
            menu.addAction("▶ 开启语音聊天", self._on_standby_clicked)
        menu.addSeparator()
        menu.addAction("⚙ 使用说明与设置", self._show_voice_chat_settings)
        menu.exec_(self._btn_standby.mapToGlobal(pos))

    def _show_voice_chat_settings(self):
        """语音聊天设置面板"""
        dlg = QDialog(self)
        dlg.setWindowTitle("🎤 语音聊天 — 说明与设置")
        dlg.setFixedSize(440, 360)
        dlg.setStyleSheet("""
            QDialog { background: #2D2D3F; color: #E0E0E0; }
            QLabel { color: #E0E0E0; background: transparent; }
            QCheckBox { color: #E0E0E0; }
            QCheckBox::indicator { width: 16px; height: 16px; }
        """)

        root = QVBoxLayout(dlg)
        root.setSpacing(10)
        root.setContentsMargins(16, 14, 16, 14)

        # ── 当前模式 ──
        is_headphone = bool(self._voice_duplex and self._voice_duplex._headphone_mode)
        mode_text = "🎧 耳机模式 — 可随时打断莲心说话" if is_headphone else "🔊 扬声器模式 — 莲心说话时请等她说完"
        mode_label = QLabel(mode_text)
        mode_label.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        mode_label.setStyleSheet(f"color: {'#27AE60' if is_headphone else '#E67E22'}; padding: 8px;")
        root.addWidget(mode_label)

        # ── 说明 ──
        guide = QLabel(
            "<b>使用说明</b><br><br>"
            "<table>"
            "<tr><td>🎧 <b>插耳机时</b></td><td>真正全双工——莲心说话中随时开口打断，"
            "像打电话一样自然</td></tr>"
            "<tr><td>🔊 <b>用扬声器时</b></td><td>半双工——等莲心说完 + 🔔提示音后再说话，"
            "防止麦克风录到扬声器回声</td></tr>"
            "<tr><td>🔔 <b>提示音</b></td><td>响起时表示麦克风在听，可以说话了</td></tr>"
            "</table>"
        )
        guide.setWordWrap(True)
        guide.setFont(QFont("Microsoft YaHei UI", 9))
        guide.setStyleSheet("color: #B0B0C0; padding: 8px; line-height: 1.6;")
        root.addWidget(guide)

        # ── 手动切换 ──
        cb = QCheckBox("强制耳机模式（插耳机但未自动识别时手动开启）")
        cb.setChecked(is_headphone)
        cb.setFont(QFont("Microsoft YaHei UI", 9))
        cb.toggled.connect(lambda on: self._voice_duplex.set_headphone_mode(on) if self._voice_duplex else None)
        root.addWidget(cb)

        root.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setFont(QFont("Microsoft YaHei UI", 10))
        close_btn.setStyleSheet("""
            QPushButton { background: #4A4A6A; color: #E0E0E0; border-radius: 6px; padding: 6px 24px; }
            QPushButton:hover { background: #5B5B7A; }
        """)
        close_btn.clicked.connect(dlg.accept)
        root.addWidget(close_btn, alignment=Qt.AlignCenter)

        dlg.exec_()

    def _on_standby_clicked(self):
        from utils.sound import play_sound
        play_sound("DaiJiMoShi.mp3")
        if self._standby_state == "IDLE":
            self._enter_standby()
        else:
            self._exit_standby()

    def _enter_standby(self):
        """开启待机模式。
        full_duplex: 全双工语音（Silero VAD + 本地 Whisper，随时插话，无需结束词）
        legacy: 旧模式（阿里云 + 文件轮询，需要结束词）
        """
        if self._standby_state != "IDLE":
            return
        self._standby_state = "STANDBY"
        self._char_widget.enter_standby()
        self._is_waiting_for_response = False

        if self._standby_mode == "full_duplex":
            self._voice_duplex = VoiceDuplexManager(
                on_transcript=self._on_duplex_transcript,
                on_voice_start_ui=self._on_duplex_voice_start,
                on_state_change=self._on_duplex_state_change,
                on_interrupt_tts=self._on_duplex_interrupt_tts,
            )
            # 自动检测耳机：耳机/耳麦 → 允许TTS期间语音打断
            detected = self._voice_duplex.auto_detect_headphone()
            if detected:
                self._chat_widget.add_system_tip(
                    "🎧 检测到耳机 — 莲心说话时你可以直接开口打断~")
            if not self._voice_duplex.start():
                self._voice_duplex = None
                self._standby_state = "IDLE"
                self._char_widget.exit_standby()
                self._update_standby_button()
                self._chat_widget.add_system_tip(
                    "⚠️ 语音聊天启动失败：WebRTC VAD 不可用。"
                    "请查看终端中的具体依赖错误。"
                )
                return
            self._update_standby_button()
            # 提示音：麦克风就绪，可以说话了
            QTimer.singleShot(500, self._play_speak_cue)
            self._chat_widget.add_system_tip(
                '—— 全双工待机已开启，**随时开口说话即可**，莲心说话时随时打断——')
        else:
            # 旧模式：阿里云 + 文件轮询
            from utils.settings import get_settings
            settings = get_settings()
            self._note_file = Path(settings.note_file_path)
            self._note_file.parent.mkdir(parents=True, exist_ok=True)
            self._note_file.write_text("", encoding="utf-8")

            import subprocess
            self._stt_process = subprocess.Popen(
                ["python", "aliyun_stt.py"],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            )

            self._note_poll_timer = QTimer(self)
            self._note_poll_timer.timeout.connect(self._check_note_file)
            self._note_poll_timer.start(2000)

            self._note_timeout_timer = QTimer(self)
            self._note_timeout_timer.setSingleShot(True)
            self._note_timeout_timer.timeout.connect(self._on_note_timeout)

            self._update_standby_button()
            if self._global_settings.standby_auto_send:
                self._chat_widget.add_system_tip('—— 待机模式已开启，直接说话，说完稍等即可——')
            else:
                end_word = self._global_settings.standby_end_word or "完毕"
                self._chat_widget.add_system_tip(f'—— 待机模式已开启，直接说话，说完请说「{end_word}」——')


    def _exit_standby(self):
        """关闭待机模式"""
        self._standby_state = "IDLE"
        self._char_widget.exit_standby()
        
        # 全双工模式
        if self._voice_duplex:
            self._voice_duplex.stop()
            self._voice_duplex = None
        
        # 旧模式：终止子进程
        if self._stt_process:
            self._stt_process.terminate()
            self._stt_process = None
        
        # 停止定时器
        if self._note_poll_timer:
            self._note_poll_timer.stop()
        if self._note_timeout_timer:
            self._note_timeout_timer.stop()
  
        self._update_standby_button()
        self._chat_widget.add_system_tip("—— 待机模式已关闭 ——")

    def _update_standby_button(self):
        """根据当前待机状态更新顶部栏待机按钮样式"""
        is_active = self._standby_state == "STANDBY"
        if is_active:
            self._btn_standby.setText("🎤 语音聊天 ●")
            self._btn_standby.setStyleSheet("""
                QPushButton {
                    background-color: #2D2D3F;
                    color: #A0A0B0;
                    border-radius: 6px;
                    border: 1px solid #3D3D5A;
                }
                QPushButton:hover  { background-color: #3D3D55; }
                QPushButton:pressed{ background-color: #4D4D65; }
            """)
        else:
            self._btn_standby.setText("🎤 语音聊天")
            self._btn_standby.setStyleSheet("""
                QPushButton {
                    background-color: #2D2D3F;
                    color: #A0A0B0;
                    border-radius: 6px;
                    border: 1px solid #3D3D5A;
                }
                QPushButton:hover  { background-color: #3D3D55; }
                QPushButton:pressed{ background-color: #4D4D65; }
            """)
        self._btn_standby.setStyleSheet("""
            QPushButton { background-color: #254D43; color: #E7FFF6; border: 1px solid #83CDB8;
                border-radius: 7px; padding: 2px 8px; }
            QPushButton:hover { background-color: #347261; color: #FFFFFF; }
            QPushButton:pressed { background-color: #3E8A73; }
        """ if is_active else """
            QPushButton { background-color: rgba(22, 43, 38, 225); color: #DCEFE8;
                border: 1px solid #416B63; border-radius: 7px; padding: 2px 8px; }
            QPushButton:hover { background-color: #2A5148; border-color: #75B8A8; color: #FFFFFF; }
            QPushButton:pressed { background-color: #35685C; border-color: #9AD8C8; }
        """)

    def _on_duplex_voice_start(self):
        """全双工：检测到用户开始说话 → 输入框显示聆听中（线程安全）"""
        self._duplex_voice_start_signal.emit()

    def _on_duplex_interrupt_tts(self):
        """全双工：用户在 TTS 播放时开口说话 → 立刻停止 TTS + 取消后续分段"""
        # 停止当前播放
        if self._speaker_worker and self._speaker_worker.isRunning():
            self._speaker_worker.stop()
            self._speaker_worker = None
        # 取消分段发送器（防止继续播下一段）
        if hasattr(self, '_segment_sender') and self._segment_sender:
            self._segment_sender.cancel()
            self._segment_sender = None
        # 恢复角色动画
        if hasattr(self, "_avatar_actions"):
            self._avatar_actions.request("listening", source="voice_interrupt", force=True)
        elif self._char_widget:
            self._char_widget.set_normal()
        self._chat_widget.add_system_tip("🗣️ 莲心被打断了，你说吧~")

    def _on_duplex_transcript(self, text: str):
        """全双工模式：收到用户语音转录文本（VAD 线程调用 → 转发到主线程）"""
        if self._standby_state != "STANDBY":
            return
        if not text or not text.strip():
            return
        self._duplex_transcript_signal.emit(text.strip())

    def _handle_duplex_transcript(self, text: str):
        """全双工模式：转录文字 → 输入框预览 → 延迟自动发送"""
        if not text or not text.strip():
            return
        text = text.strip()

        # 取消上一次的自动发送定时器（用户连续说话）
        if hasattr(self, '_voice_auto_send_timer') and self._voice_auto_send_timer:
            self._voice_auto_send_timer.stop()

        # 文字显示在输入框，用户可以预览/修改
        existing = self._input_panel.get_text().strip()
        if existing:
            # 如果用户正在编辑上一次的转录 → 不覆盖
            self._input_panel.set_text(text)
        else:
            self._input_panel.set_text(text)

        # 1.5 秒后自动发送（期间新语音到达会取消）
        self._voice_auto_send_timer = QTimer(self)
        self._voice_auto_send_timer.setSingleShot(True)
        self._voice_auto_send_timer.timeout.connect(self._on_voice_auto_send)
        self._voice_auto_send_timer.start(1500)

    def _on_voice_auto_send(self):
        """全双工：定时器到期 → 自动发送输入框中的转录文字"""
        text = self._input_panel.get_text().strip()
        if not text:
            return
        # 先清空输入框再发送（避免重复）
        self._input_panel.set_text("")
        self._is_waiting_for_response = True
        self._on_user_message(text)

    def _on_mic_auto_send(self):
        """麦克风按钮：定时器到期 → 自动发送转录文字"""
        text = self._input_panel.get_text().strip()
        if not text:
            return
        self._input_panel.set_text("")
        self._on_user_message(text)

    def _on_duplex_state_change(self, state: str):
        """全双工状态变化回调（可选：显示在状态栏）"""
        from brain.voice_duplex import STATE_LABELS
        label = STATE_LABELS.get(state, state)
        print(f"[全双工] {label}")
        # 待机时恢复输入框占位提示
        if state == "LISTENING":
            self._input_panel._input.setPlaceholderText(
                "🎤 随时开口说话…（按 Enter 发送文字，Shift+Enter 换行）")

    def _check_note_file(self):
        """轮询检查小纸条.txt。内容有变化时重置倒计时，
        超时自动发送，或检测到结束词立即发送。"""
        if self._is_waiting_for_response:
            return

        if not self._note_file or not self._note_file.exists():
            return

        content = self._note_file.read_text(encoding="utf-8").strip()
        if not content:
            self._last_note_content = ""
            return

        # 内容没变化 → 不重置计时器，等它自然到期
        if content == self._last_note_content:
            return

        # 内容有变化 → 记录新内容，重置倒计时
        self._last_note_content = content

        end_word = self._global_settings.standby_end_word or "完毕"

        # 检测结束词 → 立即发送
        if end_word in content:
            last_idx = content.rfind(end_word)
            query = content[:last_idx].replace(end_word, "")

            lines = query.split("\n")
            deduped_lines = list(dict.fromkeys(lines))
            query = "\n".join(deduped_lines).strip()

            if query:
                self._is_waiting_for_response = True
                if self._note_timeout_timer:
                    self._note_timeout_timer.stop()
                self._note_file.write_text("", encoding="utf-8")
                self._last_note_content = ""
                self._on_user_message(query)
            else:
                self._note_file.write_text("", encoding="utf-8")
                self._is_waiting_for_response = False
                self._last_note_content = ""
                if self._note_timeout_timer:
                    self._note_timeout_timer.stop()
                self._chat_widget.add_system_tip("没有识别到内容，请重新说话")
        else:
            # 无结束词 → 按配置决定是否启动自动发送计时器
            if self._global_settings.standby_auto_send:
                delay_ms = self._global_settings.standby_auto_send_delay * 1000
                if self._note_timeout_timer:
                    self._note_timeout_timer.stop()
                    self._note_timeout_timer.start(delay_ms)


    def _on_note_timeout(self):
        """5 秒无新内容，自动发送当前累积的消息"""
        if self._standby_state != "STANDBY":
            return

        if not self._note_file or not self._note_file.exists():
            return

        content = self._note_file.read_text(encoding="utf-8").strip()
        self._note_file.write_text("", encoding="utf-8")
        self._last_note_content = ""

        if content:
            self._is_waiting_for_response = True
            self._on_user_message(content)

        
    def _restart_listening(self):
        """回复完成后，重新启动监听"""
        if self._standby_state != "STANDBY":
            return
        QTimer.singleShot(1000, self._actually_restart_listening)


    def _actually_restart_listening(self):
        if self._standby_state != "STANDBY":
            return
        if self._note_file:
            self._note_file.write_text("", encoding="utf-8")
        self._last_note_content = ""
        # 3 秒后再次清空，兜底阿里云延迟回调
        QTimer.singleShot(3000, self._second_clear_note)

    def _second_clear_note(self):
        if self._standby_state != "STANDBY":
            return
        if self._note_file:
            self._note_file.write_text("", encoding="utf-8")
        self._last_note_content = ""
        self._is_waiting_for_response = False
        self._chat_widget.add_system_tip("🎤 继续监听中...")



    def _on_clear_note(self):
        """手动清空小纸条"""
        if self._note_file and self._note_file.exists():
            self._note_file.write_text("", encoding="utf-8")
            self._chat_widget.add_system_tip("🗑️ 已手动清空小纸条")


    # ── 图片视觉理解处理 ───────────────────────────────────────

    def _on_user_image(self, image_path: str):
        """处理用户粘贴或拖拽的图片 — 复制到托管目录后自动调用视觉理解 API"""
        # 复制到托管目录，确保聊天气泡不会因原图被删而失效
        managed = self._save_managed_image(image_path)
        display_path = managed or image_path
        self._vision_pending_bubble = self._chat_widget.add_user_image(
            display_path, ocr_text="🔍 分析中...", temporary=True
        )
        self._vision_image_worker = _ImageVisionWorker(display_path, self)
        self._vision_image_worker.finished.connect(lambda desc: self._on_vision_finished(display_path, desc))
        self._vision_image_worker.error.connect(self._on_vision_error)
        self._vision_image_worker.start()

    def _save_managed_image(self, src_path: str) -> str | None:
        """将图片复制到托管目录 ~/.lianxin/images/，保留最近 100 张。返回新路径，失败返回 None。"""
        try:
            img_dir = Path.home() / ".lianxin" / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.monotonic() * 1000)
            ext = Path(src_path).suffix or ".png"
            dst = img_dir / f"img_{ts}{ext}"
            import shutil
            shutil.copy2(src_path, dst)
            # 清理旧图：保留最近 100 张
            try:
                files = sorted(img_dir.glob("img_*"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in files[100:]:
                    old.unlink()
            except Exception:
                pass
            return str(dst)
        except Exception:
            return None

    def _on_vision_finished(self, image_path: str, description: str):
        """视觉分析完成：更新图片气泡 + 将描述注入对话"""
        self._chat_widget._hide_thinking()
        # 只移除本次视觉分析创建的临时气泡，不依赖布局索引。
        pending = getattr(self, "_vision_pending_bubble", None)
        self._chat_widget.remove_widget(pending)
        self._vision_pending_bubble = None
        summary = description[:100] + "..." if len(description) > 100 else description
        self._chat_widget.add_user_image(image_path, ocr_text=summary, full_text=description)
        try:
            from brain.interaction_events import record_interaction
            record_interaction(
                feature="vision", event_type="vision_completed",
                source_id=f"vision:{self._agent._session_id}:{datetime.now().isoformat(timespec='seconds')}",
                summary="完成了一次图片识别", searchable=False,
            )
        except Exception as exc:
            print(f"[成就记录] 图片事件记录失败: {exc}")
        self._schedule_achievement_unlock_check()

        context = f"[用户发了一张图片，视觉分析结果如下]\n{description}\n\n请根据你看到的内容自然地回应，描述你看到了什么。"
        self._send_user_text_to_agent(context, skip_bubble=True)

    def _on_vision_error(self, err: str):
        """视觉分析失败处理"""
        pending = getattr(self, "_vision_pending_bubble", None)
        self._chat_widget.remove_widget(pending)
        self._vision_pending_bubble = None
        self._chat_widget.add_system_tip(f"图片分析失败：{err}")
        self._send_user_text_to_agent(f"[图片分析失败] {err}，请告知用户。", skip_bubble=True)





    def _send_user_text_to_agent(self, text: str, skip_bubble: bool = False):
        try:
            from skills.语音合成.tools import stop_voice_playback
            stop_voice_playback()
        except Exception:
            pass

        from brain.tools import set_diary_message_source
        set_diary_message_source(self._get_today_messages)
        
        if not skip_bubble and text.strip():
            self._chat_widget.add_user_message(text)
            play_sound("ButtonAll.mp3")
        
        self._proactive_scheduler.notify_user_active()
        self._duty_scheduler.on_user_message()
        self._set_thinking_state()

        # 辅助入口（图片、Galgame）不经过 _on_user_message，必须在本地
        # 解析请求上下文，避免引用内容参与路由。
        from brain.request_context import parse_request_context
        request_context = parse_request_context(text)
        routing_text = request_context.routing_text
        
        from threading import Thread
        def route_and_start(route_text: str, display_text: str):
            try:
                from brain.intent_router import get_router
                route_result = get_router().route(route_text)
                is_chat = route_result.route == "chat"
            except Exception as exc:
                self._route_failed.emit(str(exc))
                return
            self._route_ready.emit(display_text, is_chat, route_result)
        
        Thread(target=route_and_start, args=(routing_text, text), daemon=True).start()




    # ── 窗口关闭 ─────────────────────────────────────────────

    def closeEvent(self, event):
        if (not self._force_quit and hasattr(self, "_window_experience")
                and self._window_experience.should_close_to_tray()):
            if self._window_experience.hide_to_tray():
                event.ignore()
                return
        # 检查是否启用退出确认
        if (not self._force_quit and self._global_settings.close_behavior == "ask"
                and self._global_settings.show_exit_confirmation):
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("退出")
            msg_box.setText("诶！？不打算继续陪我一会儿了吗？(´ﾟдﾟ`)！")
            btn_yes = msg_box.addButton("待会儿见哦~", QMessageBox.YesRole)
            btn_no = msg_box.addButton("抱歉我手滑了！", QMessageBox.NoRole)
            msg_box.setDefaultButton(btn_no)
            msg_box.exec_()
            if msg_box.clickedButton() != btn_yes:
                msg = "噢耶！我就知道你想继续陪我！ヾ(*´∀ ˋ*)" 
                self._agent.get_history_manager().save_message(
                    self._agent._session_id, "assistant", f"[互动] {msg}"
                )
                self._chat_widget.add_ai_message(msg)
                self._speak(msg)
                event.ignore()
                return

        # ----- 新增：关闭前停止音乐并记录最后一次播放时长 -----
        if self.music_playing:
            self._stop_music()       # 该方法会统计当前歌曲播放时长并停止
        # -------------------------------------------------

        # 以下是原有关闭逻辑（确认退出时执行）
        self._flush_achievement_presence()
        self._achievement_presence_timer.stop()
        self._achievement_unlock_poll.stop()
        self._accompany_stats.end_session()
        self._duty_scheduler.stop()
        self._alarm_timer.stop()
        if hasattr(self, '_auto_task_scheduler'):
            self._auto_task_scheduler.stop()
        self._countdown_timer.stop()
        self._todo_reminder_timer.stop()
        self._stop_autostart_net_poll()
        self._save_music_state()
        if self._study_room_window:
            self._study_room_window.shutdown()
        if getattr(self, '_music_box_widget', None) is not None:
            self._music_box_widget.shutdown()
        if getattr(self, '_music_space_window', None) is not None:
            self._music_space_window.shutdown()
        
        # ── 停止待机模式相关线程（新版）──
        if hasattr(self, '_note_poll_timer') and self._note_poll_timer:
            self._note_poll_timer.stop()
        if hasattr(self, '_note_timeout_timer') and self._note_timeout_timer:
            self._note_timeout_timer.stop()
        # 全双工语音
        if hasattr(self, '_voice_duplex') and self._voice_duplex:
            self._voice_duplex.stop()
            self._voice_duplex = None
        
        from utils.shutdown import stop_qthreads
        stop_qthreads((
            getattr(self, "_auto_task_scheduler", None),
            self._agent_worker, self._voice_worker, self._speaker_worker,
        ), total_timeout_ms=250)

        self._speaker.stop()

        self._bridge_controller.shutdown()

        if hasattr(self, "_window_experience"):
            self._window_experience.shutdown()

        # ── 关闭 Galgame 窗口 ──────────────────────────────
        self._setup_galgame_hotkey(register=False)
        if self._tachie_win:
            self._tachie_win.close()
            self._tachie_win = None
        if self._galgame_dialog:
            self._galgame_dialog.close()
            self._galgame_dialog = None

        event.accept()



    def _on_camera_capture(self):
        play_sound("ButtonAll.mp3")
        """弹出摄像头预览对话框，拍照后直接进入 OCR 流程"""
        from gui.camera_dialog import CameraDialog
        dlg = CameraDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            img_path = dlg.get_photo_path()
            if img_path and os.path.exists(img_path):
                self._on_user_image(img_path)
            else:
                self._chat_widget.add_system_tip("拍照失败，未获取到图片")
        # 如果用户取消，什么都不做

    def _on_camera_photo_taken(self, image_path: str):
        if not image_path:
            self._chat_widget.add_system_tip("❌ 拍照失败，请检查摄像头是否已连接。")
            return
        # 直接复用现有的图片处理流程
        self._on_user_image(image_path)

    def _open_diary_dialog(self):
        play_sound("OpenDiary.mp3")
        if self._diary_dialog is None:
            from gui.time_capsule.web_window import TimeCapsuleWindow
            self._diary_dialog = TimeCapsuleWindow(
                None, generation_callback=self.regenerate_diary_by_date,
                settings_callback=self._setup_diary_timer,
            )
            self._diary_dialog.closed.connect(self._on_time_capsule_closed)
            self._diary_dialog.tree_reply_requested.connect(self._on_tree_reply_requested)
        self._diary_dialog.refresh()
        self._diary_dialog.show()
        self._diary_dialog.raise_()
        self._diary_dialog.activateWindow()

    def _on_time_capsule_closed(self):
        self._diary_dialog = None

    def _on_tree_hole_updated(self, note_id: int, payload):
        """Refresh an open capsule without making the WebEngine own the worker."""
        if self._diary_dialog is not None:
            try:
                self._diary_dialog.notify_tree_hole_changed(int(note_id), payload or {})
            except Exception as exc:
                print(f"[树洞] 界面刷新失败: {exc}")

    def _on_tree_reply_requested(self, note_id: int):
        try:
            self._duty_scheduler.manual_trigger("tree_hole_reply", note_id=int(note_id))
        except Exception as exc:
            print(f"[树洞] 手动回复触发失败: {exc}")

    def _show_voice_stt_dialog(self):
        """显示语音转录设置独立弹窗（非模态，不阻塞主界面）"""
        play_sound("OpenDiary.mp3")
        
        # 复用或创建对话框实例（避免重复打开多个窗口）
        if not hasattr(self, '_voice_stt_dialog') or self._voice_stt_dialog is None:
            self._voice_stt_dialog = VoiceSTTDialog(self)
            self._voice_stt_dialog.config_saved.connect(self._on_voice_stt_config_saved)
        
        # 使用 show() 而非 exec_()：非模态显示，不阻塞主界面
        self._voice_stt_dialog.show()
        self._voice_stt_dialog.raise_()
        self._voice_stt_dialog.activateWindow()

    def _on_voice_stt_config_saved(self):
        """语音转录配置保存后的处理"""
        self._chat_widget.add_system_tip("✅ 语音转录配置已保存，下次启动语音聊天时生效。")
        
        # 如果正在运行语音聊天，提示需要重启
        if hasattr(self, '_voice_duplex') and self._voice_duplex:
            reply = QMessageBox.question(
                self,
                "重启语音",
                "语音转录配置已更新。\n\n"
                "是否立即重启语音聊天以应用新配置？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self._restart_voice_chat()

    def regenerate_diary_by_date(self, date_str: str):
        """从设置对话框调用：手动重新生成指定日期的日记"""
        mgr = self._agent.get_history_manager()
        all_messages = mgr.get_messages_by_date(date_str, owner_only=True)
        date_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in all_messages
        ]
        try:
            from brain.interaction_events import InteractionEventStore
            events = InteractionEventStore().list_for_date(date_str, limit=80)
            cfg = get_diary_config()
            enabled_features = {
                "desktop": cfg.get("reference_chat", True),
                "tree_hole": cfg.get("reference_tree_hole", True),
                "study_room": cfg.get("reference_study_room", True),
                "time_capsule": cfg.get("reference_time_capsule", True),
            }
            events = [event for event in events if enabled_features.get(event.get("feature", ""), True)]
            events = [event for event in events if event.get("importance") != "noise"]
            events.sort(key=lambda event: (event.get("importance") != "important", event.get("occurred_at", "")))
            date_messages.extend(
                {"role": "system", "source_event_id": event["id"], "content": f"[{event['importance']}][{event['feature']}] {event['summary'] or event['content']}"}
                for event in events if event.get("event_type") != "diary_saved"
            )
        except Exception as exc:
            print(f"[日记] 读取跨功能互动失败: {exc}")
        
        if not date_messages:
            self._chat_widget.add_system_tip(f"未找到 {date_str} 的共同对话，莲心暂时无法为这一天留下书页。")
            return
        
        cfg = get_diary_config()
        max_messages = cfg.get("max_messages", 30)
        direction = cfg.get("direction", "latest")
        
        if direction == "earliest":
            selected = date_messages[:max_messages]
        else:
            selected = date_messages[-max_messages:] if len(date_messages) > max_messages else date_messages
        
        self._diary_worker = DiaryWorker(date_str, selected)
        self._diary_worker.finished.connect(self._on_diary_finished)
        self._diary_worker.start()

    def _has_today_diary(self) -> bool:
        from utils.diary import has_diary_for_date
        today_str = datetime.now().strftime("%Y-%m-%d")
        return has_diary_for_date(today_str)

    def _get_today_messages(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        mgr = self._agent.get_history_manager()
        all_messages = mgr.get_messages_by_date(today_str, owner_only=True)
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in all_messages
        ]
        try:
            from brain.interaction_events import InteractionEventStore
            events = InteractionEventStore().list_for_date(today_str, limit=80)
            cfg = get_diary_config()
            enabled_features = {
                "desktop": cfg.get("reference_chat", True),
                "tree_hole": cfg.get("reference_tree_hole", True),
                "study_room": cfg.get("reference_study_room", True),
                "time_capsule": cfg.get("reference_time_capsule", True),
            }
            events = [event for event in events if enabled_features.get(event.get("feature", ""), True)]
            events = [event for event in events if event.get("importance") != "noise"]
            events.sort(key=lambda event: (event.get("importance") != "important", event.get("occurred_at", "")))
            messages.extend(
                {"role": "system", "source_event_id": event["id"], "content": f"[{event['importance']}][{event['feature']}] {event['summary'] or event['content']}"}
                for event in events if event.get("event_type") != "diary_saved"
            )
        except Exception as exc:
            print(f"[日记] 读取跨功能互动失败: {exc}")
        return messages

    def _write_diary_if_needed(self, force=False):
        """如果当天还没有莲心的书页，则根据共同对话写下右页。"""
        if not force and self._has_today_diary():
            return
        
        today_messages = self._get_today_messages()
        if not today_messages:
            self._chat_widget.add_system_tip("今天还没有共同对话，莲心暂时没有可写进右页的故事。")
            return
        
        cfg = get_diary_config()
        max_messages = cfg.get("max_messages", 30)
        direction = cfg.get("direction", "latest")
        
        if direction == "earliest":
            selected = today_messages[:max_messages]
        else:
            selected = today_messages[-max_messages:] if len(today_messages) > max_messages else today_messages
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        self._diary_worker = DiaryWorker(today_str, selected)
        self._diary_worker.finished.connect(self._on_diary_finished)
        self._diary_worker.start()


    def _write_diary_now(self):
        play_sound("ButtonAll.mp3")
        if self._has_today_diary():
            reply = QMessageBox.question(
                self, "日记已存在",
                "今天已经有一篇日记了，是否重新生成？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        self._write_diary_if_needed(force=True)


    def _setup_diary_timer(self):
        """根据配置的定时时间，设置日记定时器（每天一次）"""
        cfg = get_diary_config()
        if not cfg.get("scheduled_enabled", True):
            self._diary_timer.stop()
            return
        target_time_str = cfg.get("scheduled_time", "23:55")
        target_time = QTime.fromString(target_time_str, "HH:mm")
        now = QTime.currentTime()
        # 计算距离目标时间还有多少毫秒
        msecs = now.msecsTo(target_time)
        if msecs < 0:  # 今天的时间已过，则改为明天
            msecs = 24 * 60 * 60 * 1000 + msecs
        
        self._diary_timer.start(msecs)
        # 注意：QTimer 单次触发后，我们在 _on_diary_timer_timeout 中会重新启动


    def _on_diary_timer_timeout(self):
        """定时时间到，尝试写日记（如果当天无日记）"""
        if not self._has_today_diary():
            self._write_diary_if_needed(force=False)
        # 重新设置第二天的定时
        self._setup_diary_timer()

    def _load_music_playlist(self):
        """扫描 assets/music/ 目录下的 mp3 文件"""
        from utils.resource_path import get_asset_path
        music_dir = get_asset_path("music")
        if music_dir.exists():
            self.playlist = sorted(music_dir.glob("*.mp3"))
        else:
            self.playlist = []
        self._update_music_ui()

    def _update_music_ui(self):
        """更新音乐盒界面（状态由前端渲染）"""
        self._push_music_state()
    def _play_music(self, start_sec=0):
        if not self.playlist:
            return
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(str(self.playlist[self.current_track_index]))
        pygame.mixer.music.set_volume(self._global_settings.music_volume)
        pygame.mixer.music.play(start=start_sec)
        self.music_playing = True
        self.current_offset = start_sec
        self.current_position = start_sec
        self.current_song_start_time = time.time() - start_sec   # 记录开始时间（考虑偏移）
        # 获取总时长
        try:
            from mutagen.mp3 import MP3 # type: ignore
            audio = MP3(str(self.playlist[self.current_track_index]))
            self.current_duration = int(audio.info.length)
        except:
            self.current_duration = 0
        self._update_time_display(start_sec)
        # 启动进度更新定时器
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._update_progress)
        self._progress_timer.start(500)
        # 监听播放结束
        self._music_check_timer = QTimer(self)
        self._music_check_timer.timeout.connect(self._on_music_check)
        self._music_check_timer.start(500)
        self._push_music_state()

    def _stop_music(self):
    # 更新当前歌曲的播放时长（如果正在播放且已记录开始时间）
        if self.music_playing and self.current_song_start_time is not None:
            elapsed = int(time.time() - self.current_song_start_time)
            if elapsed > 0:
                self.music_stats.update_song(str(self.playlist[self.current_track_index]), elapsed)
        pygame.mixer.music.stop()
        self.music_playing = False
        self._push_music_state()
        if self._progress_timer:
            self._progress_timer.stop()
        if hasattr(self, '_music_check_timer'):
            self._music_check_timer.stop()

    def _on_music_check(self):
        if not pygame.mixer.music.get_busy() and self.music_playing:
            # 播放结束，计算本次播放时长
            if self.current_song_start_time is not None:
                elapsed = int(time.time() - self.current_song_start_time)
                if elapsed > 0:
                    self.music_stats.update_song(str(self.playlist[self.current_track_index]), elapsed)
            self._next_track()

    def _prev_track(self):
        play_sound("ButtonMusic.mp3")
        self._stop_music()
        if self.loop_mode == "random":
            import random
            self.current_track_index = random.randint(0, len(self.playlist)-1)
        else:
            self.current_track_index = (self.current_track_index - 1) % len(self.playlist) if self.playlist else 0
        self._update_music_ui()
        self._play_music()

    def _next_track(self):
        play_sound("ButtonMusic.mp3")
        self._stop_music()
        if self.loop_mode == "one":
            # 单曲循环：索引不变
            pass
        elif self.loop_mode == "random":
            # 随机播放
            import random
            self.current_track_index = random.randint(0, len(self.playlist)-1)
        else:
            # 列表循环
            self.current_track_index = (self.current_track_index + 1) % len(self.playlist) if self.playlist else 0
        self._update_music_ui()
        self._play_music()

    def _on_music_play_pause(self):
        play_sound("ButtonMusic.mp3")
        if not self.playlist:
            return
        if self.music_playing:
            self._pause_music()
        else:
            self._resume_music()

    def _resume_music(self):
        if not self.music_playing and self.playlist:
            pygame.mixer.music.unpause()
            self.music_playing = True
            if self._progress_timer:
                self._progress_timer.start(500)
            self._push_music_state()

    def _pause_music(self):
        if self.music_playing:
            pygame.mixer.music.pause()
            self.music_playing = False
            self._push_music_state()
            if self._progress_timer:
                self._progress_timer.stop()


    def _on_music_volume_changed(self, value):
        vol = value / 100.0
        self._global_settings.music_volume = vol
        self._push_music_state()
        # 确保 mixer 已初始化
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        # 只有当前有音乐播放或 mixer 已加载音乐时才设置音量，否则只保存数值
        try:
            pygame.mixer.music.set_volume(vol)
        except pygame.error:
            pass  # 如果还没有加载音乐，忽略错误

    def _restore_music_state(self):
        self.current_track_index = self._global_settings.music_playlist_index
        self._update_music_ui()
        if self._global_settings.music_is_playing and self.playlist:
            start_pos = self._global_settings.music_position
            # 确保位置不超过总时长（避免出错）
            if start_pos >= self.current_duration:
                start_pos = 0
            self._play_music(start_sec=start_pos)
        else:
            self.music_playing = False
            self._push_music_state()

    def _save_music_state(self):
        self._global_settings.music_playlist_index = self.current_track_index
        self._global_settings.music_is_playing = self.music_playing
        self._global_settings.music_position = self.current_position  # 保存进度

    def _music_box_state(self) -> dict:
        """收集当前音乐状态，供 HTML 前端渲染

        注意：Web 前端加载完成后会异步调用 bridge.getState()，而它可能在
        MainWindow.__init__ 的“音乐播放器变量区域”初始化之前被触发，
        因此这里对所有音乐状态字段做防御式读取，避免启动期
        'MainWindow' object has no attribute 'playlist' 之类的序列化崩溃。
        """
        playlist = getattr(self, "playlist", None) or []
        current_index = getattr(self, "current_track_index", 0)
        current_duration = getattr(self, "current_duration", 0)
        current_position = getattr(self, "current_position", 0)
        music_playing = bool(getattr(self, "music_playing", False))
        loop_mode = getattr(self, "loop_mode", "list")
        try:
            volume = float(getattr(self._global_settings, "music_volume", 0.5))
        except Exception:
            volume = 0.5
        playlist_items = []
        for idx, path in enumerate(playlist):
            dur = current_duration if idx == current_index and current_duration else 0
            try:
                name = path.stem
            except Exception:
                name = str(path)
            playlist_items.append({"title": name, "duration": dur, "index": idx, "favorite": (name in self._favorite_stems)})
        title = ""
        if playlist and 0 <= current_index < len(playlist):
            try:
                title = playlist[current_index].stem
            except Exception:
                title = str(playlist[current_index])
        return {
            "playing": music_playing and bool(playlist),
            "current_index": current_index if playlist else 0,
            "title": title,
            "artist": "",
            "album": "",
            "duration": current_duration,
            "position": current_position,
            "playlist": playlist_items,
            "loop_mode": loop_mode,
            "volume": volume,
            "has_playlist": bool(playlist),
            "error": "",
            "favorite": (title in self._favorite_stems),
            "space_background": self._music_space_background(),
            "wallpaper": self._music_box_wallpaper(),
            "space_settings": self._music_space_settings(),
        }

    def _music_box_wallpaper(self) -> str:
        """解析当前壁纸路径，供 Mode B 背景使用（file:// URI）"""
        try:
            central = self.centralWidget()
            if isinstance(central, BackgroundWidget):
                resolved = central.resolved_path
                if resolved:
                    path = Path(resolved).resolve()
                    if path.is_file():
                        return path.as_uri()
        except Exception:
            pass
        return ""

    def _music_space_background(self) -> str:
        """返回音乐空间背景图（默认猫与咖啡桌.png，或用户选择的壁纸）的 file:// URI"""
        try:
            from utils.resource_path import get_asset_path
            settings = QSettings("Lianxin", "MusicBox")
            chosen = str(settings.value("space_wallpaper", "default"))
            if chosen != "default":
                p = Path(chosen)
                if p.is_file():
                    return p.resolve().as_uri()
            p = get_asset_path("主界面背景图", "猫与咖啡桌.png")
            if p.exists():
                return p.resolve().as_uri()
        except Exception:
            pass
        return ""

    def _music_space_wallpapers(self) -> list:
        """扫描主界面背景图目录，返回壁纸列表 [{id, name, url}]。"""
        try:
            from utils.resource_path import get_asset_path
            directory = get_asset_path("主界面背景图")
        except Exception:
            directory = None
        items = [{"id": "default", "name": "默认壁纸", "url": ""}]
        if directory is not None and directory.is_dir():
            allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
            try:
                files = sorted(directory.iterdir(), key=lambda p: p.name.lower())
            except Exception:
                files = []
            for image in files:
                try:
                    if image.is_file() and image.suffix.lower() in allowed:
                        resolved = image.resolve()
                        items.append({"id": str(resolved), "name": image.stem,
                                      "url": resolved.as_uri()})
                except Exception:
                    continue
        return items

    def _music_space_settings(self) -> dict:
        """返回音乐空间设置载荷：壁纸列表 + 当前配置。"""
        settings = QSettings("Lianxin", "MusicBox")
        current = str(settings.value("space_wallpaper", "default"))
        wallpapers = self._music_space_wallpapers()
        known = {item["id"] for item in wallpapers}
        if current != "default" and current not in known:
            p = Path(current)
            if p.is_file():
                wallpapers.append({"id": str(p.resolve()), "name": f"自定义：{p.stem}",
                                   "url": p.resolve().as_uri()})
        try:
            opacity = float(settings.value("space_wallpaper_opacity", 0.7))
        except (TypeError, ValueError):
            opacity = 0.75
        try:
            mask = float(settings.value("space_content_mask_opacity", 0.5))
        except (TypeError, ValueError):
            mask = 0.82
        return {
            "wallpapers": wallpapers,
            "settings": {
                "wallpaper": current,
                "wallpaper_opacity": opacity,
                "content_mask_opacity": mask,
                "fit": str(settings.value("space_wallpaper_fit", "cover")),
            },
        }

    def _save_music_space_settings(self, wallpaper, wallpaper_opacity,
                                   content_mask_opacity, fit) -> dict:
        """持久化音乐空间设置并推送最新状态，返回更新后的设置载荷。"""
        settings = QSettings("Lianxin", "MusicBox")
        path = str(wallpaper or "default")
        if path != "default":
            p = Path(path)
            if not p.is_file():
                path = "default"
        settings.setValue("space_wallpaper", path)
        settings.setValue("space_wallpaper_opacity",
                          max(0.0, min(1.0, float(wallpaper_opacity))))
        settings.setValue("space_content_mask_opacity",
                          max(0.0, min(1.0, float(content_mask_opacity))))
        settings.setValue("space_wallpaper_fit",
                          "contain" if str(fit) == "contain" else "cover")
        settings.sync()
        self._push_music_state()
        return self._music_space_settings()

    def _load_favorite_stems(self):
        """加载收藏歌曲（stem 集合）"""
        try:
            from utils.paths import get_user_data_dir
            f = get_user_data_dir() / "music_favorites.json"
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                self._favorite_stems = set(data if isinstance(data, list) else [])
            else:
                self._favorite_stems = set()
        except Exception:
            self._favorite_stems = set()

    def _save_favorite_stems(self):
        """持久化收藏歌曲到 JSON"""
        try:
            from utils.paths import get_user_data_dir
            f = get_user_data_dir() / "music_favorites.json"
            f.write_text(json.dumps(sorted(self._favorite_stems), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"[音乐盒] 收藏保存失败: {exc}")

    def _toggle_favorite(self):
        """切换当前歌曲收藏状态"""
        if not self.playlist or not (0 <= self.current_track_index < len(self.playlist)):
            self._push_music_state()
            return
        stem = self.playlist[self.current_track_index].stem
        if stem in self._favorite_stems:
            self._favorite_stems.discard(stem)
        else:
            self._favorite_stems.add(stem)
        self._save_favorite_stems()
        self._push_music_state()

    def _push_music_state(self):
        """推送最新音乐状态到 Mode A / Mode B 前端"""
        try:
            payload = json.dumps(self._music_box_state(), ensure_ascii=False)
        except Exception as exc:
            print(f"[音乐盒] 状态序列化失败: {exc}")
            return
        widget = getattr(self, "_music_box_widget", None)
        if widget is not None:
            try:
                widget.push_state(payload)
            except Exception as exc:
                print(f"[音乐盒] Mode A 推送失败: {exc}")
        space = getattr(self, "_music_space_window", None)
        if space is not None:
            try:
                space.push_state(payload)
            except Exception as exc:
                print(f"[音乐盒] Mode B 推送失败: {exc}")

    def _push_music_position(self):
        """轻量级推送：仅推送播放位置，不序列化播放列表/壁纸/设置等重数据"""
        try:
            payload = json.dumps({
                "playing": bool(getattr(self, "music_playing", False)) and bool(getattr(self, "playlist", None)),
                "position": getattr(self, "current_position", 0),
                "duration": getattr(self, "current_duration", 0),
            }, ensure_ascii=False)
        except Exception:
            return
        widget = getattr(self, "_music_box_widget", None)
        if widget is not None:
            try:
                widget.push_state(payload)
            except Exception:
                pass
        space = getattr(self, "_music_space_window", None)
        if space is not None:
            try:
                space.push_state(payload)
            except Exception:
                pass

    def _set_music_volume(self, volume: float):
        """设置音量（0~1），来自前端音量滑块"""
        try:
            vol = max(0.0, min(1.0, float(volume)))
        except (TypeError, ValueError):
            return
        self._global_settings.music_volume = vol
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        try:
            pygame.mixer.music.set_volume(vol)
        except pygame.error:
            pass
        self._push_music_state()

    def _set_loop_mode(self, mode: str):
        """切换播放模式：list / one / random"""
        if mode in ("list", "one", "random"):
            self.loop_mode = mode
            self._push_music_state()


    def _open_music_space(self):
        """打开沉浸式音乐空间（Mode B，懒加载）"""
        if self._music_space_window is None:
            self._music_space_window = MusicSpaceWindow(self._music_box_bridge, self, self)
            self._music_space_window.set_anchor(self)
            self._music_space_window.space_visibility_changed.connect(
                self._on_music_space_visibility_changed)
        self._music_space_window.show_space()
        # 全屏音乐空间已覆盖主窗口，隐藏主界面嵌入式音乐盒
        # （QWebEngineView 为原生子窗口，不隐藏会射穿到最上层）
        if getattr(self, "_music_box_widget", None) is not None:
            self._music_box_widget.hide()
        self._push_music_state()

    def _close_music_space(self):
        """关闭沉浸式音乐空间（Mode B）"""
        if self._music_space_window is not None:
            self._music_space_window.close_space()

    def _minimize_music_space(self):
        """最小化音乐空间窗口"""
        space = getattr(self, "_music_space_window", None)
        if space is not None:
            space.minimize_space()

    def _toggle_max_music_space(self):
        """最大化 / 还原音乐空间窗口"""
        space = getattr(self, "_music_space_window", None)
        if space is not None:
            space.toggle_maximize()
        widget = getattr(self, "_music_box_widget", None)
        if widget is None:
            return
        if space is not None and space.isMaximized():
            widget.hide()
        else:
            try:
                if self._char_widget.is_function_expanded():
                    widget.hide()
                    return
            except Exception:
                pass
            widget.show()

    def _on_music_space_visibility_changed(self, visible: bool):
        """音乐空间可见性变化时，统一控制嵌入式音乐盒的显隐

        - 音乐空间可见（正常/最大化/从任务栏恢复）→ 隐藏音乐盒
        - 音乐空间不可见（最小化/关闭）→ 显示音乐盒
        """
        widget = getattr(self, "_music_box_widget", None)
        if widget is None:
            return
        if visible:
            widget.hide()
        else:
            try:
                if self._char_widget.is_function_expanded():
                    widget.hide()
                    return
            except Exception:
                pass
            widget.show()



    def _reorder_playlist(self, new_order):
        """当用户在音乐列表中拖拽排序后，更新主窗口的播放列表"""
        if not new_order:
            return
        # 保存当前正在播放的歌曲路径
        current_path = self.playlist[self.current_track_index] if self.playlist else None
        self.playlist = new_order
        # 更新索引
        if current_path in self.playlist:
            self.current_track_index = self.playlist.index(current_path)
        else:
            self.current_track_index = 0
        self._update_music_ui()
        # 如果正在播放，重新加载当前歌曲（保持播放状态）
        if self.music_playing:
            current_pos = self.current_position
            self._stop_music()
            self._play_music(start_sec=current_pos)
        else:
            self._push_music_state()

    def _switch_to_track(self, index):
        """切换到指定索引的歌曲"""
        if index == self.current_track_index:
            return
        self._stop_music()
        self.current_track_index = index
        self._update_music_ui()
        self._play_music()
        
    def _update_progress(self):
        if self.music_playing and pygame.mixer.music.get_busy():
            pos = self.current_offset + (pygame.mixer.music.get_pos() // 1000)
            if pos < 0:
                pos = 0
            self.current_position = pos   # 保存位置
            self._update_time_display(pos)

    def _update_time_display(self, current_sec):
        """时间标签已由前端渲染，仅轻量推送位置（避免每500ms全量重渲染）"""
        self._push_music_position()
    def _seek_to_seconds(self, seconds: float):
        """按秒跳转（来自前端 seek 指令）"""
        try:
            target_sec = max(0, int(float(seconds or 0)))
        except (TypeError, ValueError):
            return
        if self.current_duration > 0:
            target_sec = min(target_sec, self.current_duration)
        was_playing = self.music_playing
        if not self.playlist:
            return
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.play(start=target_sec)
        except pygame.error:
            return
        self.current_offset = target_sec
        self.current_position = target_sec
        if not was_playing:
            pygame.mixer.music.pause()
        self.music_playing = was_playing
        self._update_time_display(target_sec)
        self._push_music_state()

    def _seek_to(self):
        """保留旧接口（无滑块后为空操作，兜底跳转到当前位置）"""
        if self.playlist and self.current_duration > 0:
            self._seek_to_seconds(self.current_position)

    def _on_loop_mode_clicked(self):
        play_sound("ButtonMusic.mp3")
        if self.loop_mode == "list":
            self.loop_mode = "one"
        elif self.loop_mode == "one":
            self.loop_mode = "random"
        else:
            self.loop_mode = "list"
        self._push_music_state()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self._on_music_play_pause()
        elif event.key() == Qt.Key_Left:
            self._prev_track()
        elif event.key() == Qt.Key_Right:
            self._next_track()
        else:
            super().keyPressEvent(event)

    def _open_reminder_dialog(self):
        play_sound("ButtonMusic.mp3")
        from gui.reminder_dialog import ReminderDialog
        dlg = ReminderDialog(self, manager=self.reminder_manager)
        dlg.show()

    def _touch_heartbeat(self):
        """轻量心跳：由 Qt 定时器每 5 秒调用一次，仅更新时间戳。"""
        self._heartbeat_time = time.monotonic()
        if self._heartbeat_frozen:
            self._heartbeat_frozen = False
            print(f"[看门狗] 主线程已恢复")

    def _watchdog_loop(self):
        """后台线程：轮询心跳时间戳，卡顿时实时抓取主线程调用堆栈。"""
        import traceback
        while True:
            time.sleep(1.0)
            elapsed = time.monotonic() - self._heartbeat_time
            if elapsed <= 7.0:
                continue
            # 如果当前有模态对话框打开，不误报冻结
            if self._modal_active:
                self._heartbeat_frozen = False
                self._heartbeat_time = time.monotonic()
                continue
            if not self._heartbeat_frozen:
                # 首次检测到卡顿，立即抓堆栈
                self._heartbeat_frozen = True
                for t in threading.enumerate():
                    if t.name == 'MainThread':
                        frame = sys._current_frames().get(t.ident)
                        if frame:
                            stacks = "".join(traceback.format_stack(frame))
                            print(f"[看门狗] WARN 主线程已卡住 {elapsed:.1f} 秒！调用堆栈：\n{stacks}")
                        else:
                            print(f"[看门狗] WARN 主线程已卡住 {elapsed:.1f} 秒（无法获取堆栈）")
                        break

            elif round(elapsed) % 30 == 0:
                # 长时间卡顿，每 30 秒再抓一次堆栈看有没有变化
                for t in threading.enumerate():
                    if t.name == 'MainThread':
                        frame = sys._current_frames().get(t.ident)
                        if frame:
                            stacks = "".join(traceback.format_stack(frame))
                            print(f"[看门狗] 仍在卡顿中 ({elapsed:.0f}s) 堆栈：\n{stacks}")
                        break

    def _sample_modal_state(self):
        """Sample modal-dialog state on the Qt GUI thread for the watchdog."""
        self._modal_active = QApplication.activeModalWidget() is not None

    def _check_reminders(self):
        due = self.reminder_manager.get_due_reminders()

        if not due:
            return

        # 提取所有提醒名称（最多5条，避免过长）
        reminder_names = [r["name"] for r in due[:5]]
        reminder_times = [r["time"] for r in due[:5]]

        if self._global_settings.global_smart_reminder:
            # 智能模式：将多个提醒名称用"、"连接，传递给合并版 Worker
            combined_names = "、".join(reminder_names)
            worker = SmartReminderWorker(combined_names, is_combined=True)   # 注意参数
            worker.finished.connect(self._on_smart_reminder_ready)
            worker.start()
        else:
            # 非智能模式：固定句式拼接
            if len(reminder_names) == 1:
                msg = f"⏰ 提醒：{reminder_names[0]}（{reminder_times[0]}）"
            else:
                items = [f"{name}（{time}）" for name, time in zip(reminder_names, reminder_times)]
                msg = f"⏰ 有几个提醒：{', '.join(items)}"
            self._chat_widget.add_ai_message(msg)
            self._speak(msg)

        # 将所有到期的提醒标记为已触发
        for r in due:
            self.reminder_manager.mark_triggered(r["id"])

    def _on_smart_reminder_ready(self, text: str):
        if self._agent_worker and self._agent_worker.isRunning():
            QTimer.singleShot(5000, lambda: self._do_reminder(text))
        else:
            self._do_reminder(text)

    def _do_reminder(self, text: str):
        self._chat_widget.add_ai_message(text)
        self._speak(text)

    # ── QQ 桥接 ─────────────────────────────────────────────

    def _on_qq_bridge_clicked(self):
        """点击 QQ聊天 按钮：打开 QQ 聊天面板（含桥接开关和参数设置）。"""
        play_sound("ButtonAll.mp3")
        self._heartbeat_time = time.monotonic()
        if self._qq_settings_dialog is None:
            self._qq_settings_dialog = QqSettingsDialog(self)
            self._qq_settings_dialog.finished.connect(self._on_qq_settings_finished)
        self._qq_settings_dialog.show()
        self._qq_settings_dialog.raise_()
        self._qq_settings_dialog.activateWindow()

    def _on_qq_settings_finished(self, result: int):
        if result == QDialog.Accepted:
            if self._bridge_controller.reload_qq_timing_config():
                self._chat_widget.add_system_tip("✅ QQ 聊天参数已更新（即时生效）")
            self._bridge_controller.reload_qq_bridge_config()

       # ── 微信桥接 ─────────────────────────────────────────────

    def _on_wechat_settings_clicked(self):
        """打开微信聊天设置对话框。"""
        if self._wechat_settings_dialog is None:
            self._wechat_settings_dialog = WeChatSettingsDialog(self)
        self._wechat_settings_dialog.show()
        self._wechat_settings_dialog.raise_()
        self._wechat_settings_dialog.activateWindow()
    def _start_wechat_bridge(self):
        return self._bridge_controller.start_wechat()

    def _stop_wechat_bridge(self):
        self._bridge_controller.stop_wechat()
    # ── QQ 桥接方法 ──────────────────────────────────────────

    def _start_qq_bridge(self):
        return self._bridge_controller.start_qq()

    def _stop_qq_bridge(self):
        self._bridge_controller.stop_qq()
        
    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self._btn_maximize.setText("□")
        else:
            self.showMaximized()
            self._btn_maximize.setText("❐")

    def _update_qq_bridge_button(self):
        self._bridge_controller.update_qq_button()


class _ImageVisionWorker(QThread):
    """后台线程：调用视觉API理解图片内容。"""
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.image_path = image_path

    def run(self):
        try:
            from brain.vision import describe_image
            result = describe_image(self.image_path)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class CameraCaptureThread(QThread):
    finished = pyqtSignal(str)  # 返回图片路径或空字符串

    def run(self):
        from utils.camera import capture_from_camera
        path = capture_from_camera()
        self.finished.emit(path)
class SegmentSender(QObject):
    """分段发送控制器，逐段朗读，支持中断"""
    finished = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, full_text: str, chat_widget, speaker, parent=None,
                 conversational: bool = False):
        super().__init__(parent)
        self._full_text = full_text.strip()
        self._chat_widget = chat_widget
        self._speaker = speaker
        self._conversational = bool(conversational)
        self._segments = self._split_text()
        self._index = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._send_next)
        self._bubbles = []
        self._speaker_worker = None
        self._cancelled = False

    def _split_text(self):
        from utils.text_segmentation import split_conversation_text, split_semantic_text
        if self._conversational:
            return split_conversation_text(self._full_text)
        return split_semantic_text(self._full_text)

    def start(self):
        if not self._segments:
            self.finished.emit()
            return
        self._send_next()

    def _send_next(self):
        if self._cancelled or self._index >= len(self._segments):
            if not self._cancelled:
                self.finished.emit()
            return

        seg = self._segments[self._index]
        bubble = self._chat_widget.add_ai_message(seg)
        self._bubbles.append(bubble)
        self._index += 1

        # 朗读当前段
        p = self.parent()
        if p and hasattr(p, '_global_settings') and not p._global_settings.silent_mode:
            self._speaker_worker = SpeakerWorker(self._speaker, seg, self)
            # TTS 播放 → 暂停 VAD（防止回声循环）
            if hasattr(p, '_voice_duplex') and p._voice_duplex:
                self._speaker_worker.speaking_started.connect(p._voice_duplex.pause_vad)
                self._speaker_worker.speaking_finished.connect(p._voice_duplex.resume_vad)
                # 莲心说完后延迟播提示音
                self._speaker_worker.speaking_finished.connect(
                    lambda: QTimer.singleShot(2500, p._play_speak_cue))
            self._speaker_worker.speaking_finished.connect(self._on_tts_finished)
            self._speaker_worker.start()
        else:
            self._on_tts_finished()


    def _on_tts_finished(self):
        if self._cancelled or self._index >= len(self._segments):
            self.finished.emit()
            return
        p = self.parent()
        gs = getattr(p, "_global_settings", None) if p is not None else None
        if self._conversational:
            lo_ms, hi_ms = 450, 1100
            if gs is not None:
                lo_ms = int(gs.segment_pause_chat_min * 1000)
                hi_ms = int(gs.segment_pause_chat_max * 1000)
        else:
            lo_ms, hi_ms = 3000, 7000
            if gs is not None:
                lo_ms = int(gs.segment_pause_semantic_min * 1000)
                hi_ms = int(gs.segment_pause_semantic_max * 1000)
        if lo_ms > hi_ms:
            lo_ms, hi_ms = hi_ms, lo_ms
        self._timer.start(random.randint(lo_ms, hi_ms))

    def cancel(self):
        self._cancelled = True
        self._timer.stop()
        if self._speaker_worker and self._speaker_worker.isRunning():
            self._speaker.stop()
        self.cancelled.emit()

    @property
    def is_running(self):
        if self._cancelled:
            return False
        if self._index < len(self._segments):
            return True
        if self._speaker_worker and self._speaker_worker.isRunning():
            return True
        return False