"""
DutyScheduler：统一后台职责调度器
- 用单一 60s master QTimer 替代 3 个独立 QTimer
- 每个 Duty 封装自己的设置读取、门控逻辑、Worker 构造
- 保持现有 Worker 类完全不变
"""

import time
import uuid
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable

from PyQt5.QtCore import QObject, QTimer, pyqtSignal


# ═══════════════════════════════════════════════════════════════
# Data
# ═══════════════════════════════════════════════════════════════

@dataclass
class DutyStatus:
    name: str
    display_name: str
    enabled: bool = True
    is_running: bool = False
    last_fire_time: float = 0.0
    last_result: str = ""       # "success" | "skipped" | "failed" | ""
    last_result_text: str = ""
    run_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    user_paused: bool = False
    pending_count: int = 0
    next_retry_at: float = 0.0
    paused_until: float = 0.0
    detail_text: str = ""


@dataclass
class SchedulerState:
    now: float = 0.0
    paused: bool = False
    agent_busy: bool = False
    last_user_message_time: float = 0.0
    # references set by MainWindow
    proactive_scheduler: object = None
    reminder_manager: object = None
    global_settings: object = None
    heartbeat_config: dict = field(default_factory=dict)
    session_id: int = 0
    history_manager: object = None
    qq_bridge: object = None
    todo_manager: object = None
    agent: object = None
    chat_widget: object = None
    speak_func: Callable = None
    # observation helpers
    is_shoulder_available: Callable = None
    # proactive dialog reference for bilibili refresh
    proactive_dialog: object = None


# ═══════════════════════════════════════════════════════════════
# Abstract Base
# ═══════════════════════════════════════════════════════════════

class Duty(ABC):
    """单个后台职责的抽象基类。"""

    def __init__(self, name: str, display_name: str, tick_interval_seconds: int):
        self.name = name
        self.display_name = display_name
        self.tick_interval_seconds = tick_interval_seconds
        self._last_tick_time: float = 0.0
        self._worker = None
        self.status = DutyStatus(name=name, display_name=display_name)

    def on_tick(self, state: SchedulerState) -> bool:
        """主定时器回调。返回 True 表示本次有触发。"""
        self.status.enabled = self._check_enabled(state) and not self.status.user_paused
        if not self.status.enabled:
            return False
        if state.paused:
            return False
        elapsed = state.now - self._last_tick_time
        if elapsed < self.tick_interval_seconds:
            return False
        self._last_tick_time = state.now
        if self._should_fire(state):
            self._execute(state)
            return True
        return False

    def on_user_message(self, state: SchedulerState):
        """用户发送消息后的钩子（默认无操作）。"""

    def manual_trigger(self, state: SchedulerState, **kwargs):
        """调试触发，绕过所有门控。"""
        self._execute(state, **kwargs)

    # ── 子类必须实现 ──

    @abstractmethod
    def _check_enabled(self, state: SchedulerState) -> bool:
        """读取实时设置，判断是否启用。"""

    @abstractmethod
    def _should_fire(self, state: SchedulerState) -> bool:
        """判断此刻是否应执行。"""

    @abstractmethod
    def _create_worker(self, state: SchedulerState, **kwargs):
        """构造 QThread Worker 并返回。子类负责信号连接。"""

    # ── 内部 ──

    def _execute(self, state: SchedulerState, **kwargs):
        """执行职责：创建 Worker → 连接信号 → 启动。"""
        if self.status.is_running:
            return
        try:
            worker = self._create_worker(state, **kwargs)
            if worker is None:
                return
            self.status.is_running = True
            self.status.run_count += 1
            self.status.last_fire_time = time.time()
            self._worker = worker
            self._wire_worker(worker)
            scheduler = getattr(self, "_scheduler", None)
            if scheduler is not None:
                scheduler._begin_duty_workflow(self, state, kwargs)
                try:
                    worker.finished.connect(lambda n=self.name: scheduler._finish_duty_workflow(n))
                except Exception:
                    pass
                scheduler.duty_started.emit(self.name)
            worker.start()
        except Exception:
            self.status.is_running = False

    @abstractmethod
    def _wire_worker(self, worker):
        """连接 Worker 信号到 Duty 的内部处理。"""


# ═══════════════════════════════════════════════════════════════
# DutyScheduler
# ═══════════════════════════════════════════════════════════════

class DutyScheduler(QObject):
    """统一后台职责调度器。"""

    # 结果信号（MainWindow 连接一次）
    proactive_response = pyqtSignal(str)              # message text
    proactive_error = pyqtSignal(str)                 # error text
    proactive_observation_text = pyqtSignal(str)      # observation description
    proactive_observation_image = pyqtSignal(str, str) # image_path, description
    proactive_behavior_selected = pyqtSignal(str)     # normal | observe | bilibili | slack
    proactive_coordination = pyqtSignal(str)          # 情绪协调导致主动联系延后

    slack_response = pyqtSignal(str)                  # message text
    slack_error = pyqtSignal(str)                     # error text
    slack_action_selected = pyqtSignal(str)           # 摸鱼动作名

    heartbeat_response = pyqtSignal(str)              # reminder text
    heartbeat_silent = pyqtSignal()                   # nothing to report

    reminder_response = pyqtSignal(str)               # reminder text
    memory_maintenance_completed = pyqtSignal(object) # maintenance result dict
    memory_maintenance_failed = pyqtSignal(str)
    tree_hole_updated = pyqtSignal(int, object)

    # 摸鱼数据源透明信号
    mooyu_data_sources = pyqtSignal(str, object)      # action_name, list[MooyuDataSource]
    mooyu_duty_data_source = pyqtSignal(str, str, bool, float)  # name, preview, is_error, elapsed_ms

    # 状态变化信号
    duty_started = pyqtSignal(str)                    # duty name
    duty_completed = pyqtSignal(str, str)             # duty name, result
    duty_failed = pyqtSignal(str, str)                # duty name, error
    status_changed = pyqtSignal(str, object)          # duty name, DutyStatus

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duties: dict[str, Duty] = {}
        self._paused = False
        self._agent_busy = False
        self._last_user_message_time = 0.0
        self._last_emotion_gate_notice = 0.0
        self._workflow_duty_runs: dict[str, int] = {}

        self._master_timer = QTimer(self)
        self._master_timer.timeout.connect(self._tick)
        self._master_timer.setInterval(60_000)  # 60s
        self.duty_completed.connect(self._on_duty_workflow_completed)
        self.duty_failed.connect(self._on_duty_workflow_failed)

        # 状态引用（由 setup() 设置）
        self._proactive_scheduler = None
        self._reminder_manager = None
        self._global_settings = None
        self._session_id = 0
        self._history_manager = None
        self._qq_bridge = None
        self._todo_manager = None
        self._agent = None
        self._chat_widget = None
        self._speak_func = None
        self._is_shoulder_available = lambda: False
        self._proactive_dialog = None

    # ── 公开 API ──

    def setup(self, *, proactive_scheduler, reminder_manager, global_settings,
              session_id_func, history_manager_func, qq_bridge_func,
              todo_manager, agent, chat_widget, speak_func,
              is_shoulder_available=None, proactive_dialog=None):
        """设置调度器所需的外部引用。"""
        self._proactive_scheduler = proactive_scheduler
        self._reminder_manager = reminder_manager
        self._global_settings = global_settings
        self._session_id_func = session_id_func
        self._history_manager_func = history_manager_func
        self._qq_bridge_func = qq_bridge_func
        self._todo_manager = todo_manager
        self._agent = agent
        self._chat_widget = chat_widget
        self._speak_func = speak_func
        self._is_shoulder_available = is_shoulder_available or (lambda: False)
        self._proactive_dialog = proactive_dialog

    def register(self, duty: Duty):
        duty._scheduler = self
        self._duties[duty.name] = duty

    def start(self):
        self._master_timer.start()
        # Reconcile durable queues immediately after startup instead of leaving
        # crash recovery waiting for the first 60-second master tick.
        QTimer.singleShot(0, self._tick)

    def stop(self):
        """在共享的短超时内停止职责线程，避免退出时按职责累计卡顿。"""
        self._master_timer.stop()
        self._paused = True
        workers = [duty._worker for duty in self._duties.values() if duty._worker]
        from utils.shutdown import stop_qthreads
        survivors = stop_qthreads(workers, total_timeout_ms=250)
        survivor_ids = {id(worker) for worker in survivors}
        for duty in self._duties.values():
            if duty._worker and id(duty._worker) in survivor_ids:
                duty.status.detail_text = "应用退出中：后台调用将在完成后自行释放"
            else:
                duty.status.is_running = False
        return survivors

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def set_agent_busy(self, busy: bool):
        self._agent_busy = busy

    def on_user_message(self):
        self._last_user_message_time = time.monotonic()
        state = self._build_state()
        for duty in self._duties.values():
            duty.on_user_message(state)

    def on_session_started(self):
        """新建空白会话时重置空闲基线，并启动主动聊天的首次破冰计时。"""
        self._last_user_message_time = time.monotonic()
        if self._proactive_scheduler is not None:
            self._proactive_scheduler.notify_session_started()

    def get_all_statuses(self) -> list[DutyStatus]:
        return [d.status for d in self._duties.values()]

    def set_duty_paused(self, name: str, paused: bool):
        duty = self._duties.get(name)
        if duty:
            duty.status.user_paused = bool(paused)
            try:
                configured = duty._check_enabled(self._build_state())
            except Exception:
                configured = True
            duty.status.enabled = bool(configured) and not duty.status.user_paused

    def manual_trigger(self, name: str, **kwargs):
        duty = self._duties.get(name)
        if duty:
            state = self._build_state()
            duty.manual_trigger(state, **kwargs)

    def _begin_duty_workflow(self, duty: Duty, state: SchedulerState, kwargs: dict):
        try:
            from brain.workflow import get_workflow_store

            run = get_workflow_store().begin_run(
                kind="duty", title=duty.display_name, session_id=state.session_id or None,
                channel="background", metadata={"duty_name": duty.name, "arguments": kwargs},
            )
            self._workflow_duty_runs[duty.name] = int(run["id"])
        except Exception:
            pass

    def _finish_duty_workflow(self, name: str, *, status: str = "success", detail: str = ""):
        run_id = self._workflow_duty_runs.pop(name, 0)
        if not run_id:
            return
        duty = self._duties.get(name)
        if status == "success" and duty is not None and duty.status.last_result == "failed":
            status = "failed"
            detail = detail or duty.status.last_result_text
        elif not detail and duty is not None:
            detail = duty.status.last_result_text
        try:
            from brain.workflow import get_workflow_store

            get_workflow_store().finish_run(
                run_id, status=status,
                result_summary=detail if status == "success" else "",
                error=detail if status != "success" else "",
            )
        except Exception:
            pass

    def _on_duty_workflow_completed(self, name: str, detail: str):
        self._finish_duty_workflow(name, status="success", detail=detail)

    def _on_duty_workflow_failed(self, name: str, error: str):
        self._finish_duty_workflow(name, status="failed", detail=error)

    # ── 内部 ──

    def _build_state(self) -> SchedulerState:
        return SchedulerState(
            now=time.monotonic(),
            paused=self._paused,
            agent_busy=self._agent_busy,
            last_user_message_time=self._last_user_message_time,
            proactive_scheduler=self._proactive_scheduler,
            reminder_manager=self._reminder_manager,
            global_settings=self._global_settings,
            heartbeat_config=self._get_heartbeat_config(),
            session_id=self._session_id_func() if self._session_id_func else 0,
            history_manager=self._history_manager_func() if self._history_manager_func else None,
            qq_bridge=self._qq_bridge_func() if self._qq_bridge_func else None,
            todo_manager=self._todo_manager,
            agent=self._agent() if callable(self._agent) else self._agent,
            chat_widget=self._chat_widget,
            speak_func=self._speak_func,
            is_shoulder_available=self._is_shoulder_available,
            proactive_dialog=self._proactive_dialog,
        )

    @staticmethod
    def _get_heartbeat_config() -> dict:
        try:
            from config import get_heartbeat_config
            return get_heartbeat_config()
        except Exception:
            return {"enabled": True, "delay_minutes": 5,
                    "active_hours_start": "08:00", "active_hours_end": "23:00"}

    def _check_emotional_gate(self) -> bool:
        try:
            from brain.emotional import get_manager
            mgr = get_manager()
            if mgr.enabled and not mgr.proactive_allowed_nonblocking(timeout=0.05):
                now = time.monotonic()
                if now - self._last_emotion_gate_notice >= 300:
                    self._last_emotion_gate_notice = now
                    self.proactive_coordination.emit(
                        "情绪协调中：当前关系张力较高，主动聊天暂缓以避免打扰；这不是故障。"
                    )
                return False
            return True
        except Exception:
            return True

    def _tick(self):
        state = self._build_state()
        for duty in self._duties.values():
            duty.on_tick(state)


class TreeHoleReplyDuty(Duty):
    """Durable tree-hole reply queue, independent of the capsule window."""

    def __init__(self):
        super().__init__("tree_hole_reply", "树洞回应", tick_interval_seconds=60)
        self._job: dict = {}
        self._owner = f"tree-hole-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _database():
        from gui.time_capsule.database import TimeCapsuleDatabase
        return TimeCapsuleDatabase(migrate_legacy=False)

    def _check_enabled(self, state: SchedulerState) -> bool:
        return True

    def _should_fire(self, state: SchedulerState) -> bool:
        self._job = {}
        db = self._database()
        try:
            db.remove_invalid_empty_days()
            db.reconcile_tree_reply_jobs()
            db.recover_expired_tree_reply_jobs()
            pending = db.pending_tree_reply_jobs()
            self.status.pending_count = sum(
                1 for item in pending if item.get("status") in {"waiting", "retrying"}
            )
            if state.agent_busy or not state.agent:
                self.status.detail_text = "等待莲心空闲后回复树洞"
                return False
            job = db.claim_due_tree_reply_job(self._owner, lease_seconds=300)
            if not job:
                self.status.detail_text = "暂无到期的树洞纸条"
                return False
            self._job = job
            self.status.pending_count = max(0, self.status.pending_count - 1)
            self.status.detail_text = f"正在回复纸条 #{job.get('note_id')}"
            return True
        except Exception as exc:
            self.status.detail_text = f"树洞队列读取失败：{exc}"
            return False

    def manual_trigger(self, state: SchedulerState, **kwargs):
        note_id = int(kwargs.get("note_id", 0) or 0)
        if not note_id or self.status.is_running or not state.agent:
            return
        db = self._database()
        db.force_tree_reply(note_id)
        self._job = db.claim_due_tree_reply_job(self._owner, lease_seconds=300)
        if self._job:
            self._execute(state)

    def _create_worker(self, state: SchedulerState, **kwargs):
        if not self._job:
            return None
        from workers.tree_hole_reply_worker import TreeHoleReplyWorker
        return TreeHoleReplyWorker(
            database_path=self._database().path,
            job=self._job,
            agent=state.agent,
        )

    def _wire_worker(self, worker):
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)

    def _on_completed(self, result):
        note_id = int((result or {}).get("note_id") or self._job.get("note_id", 0) or 0)
        db = self._database()
        status = str((result or {}).get("status") or "success")
        if note_id:
            db.mark_tree_reply_result(note_id, True)
            if status == "success":
                try:
                    reply = (result or {}).get("reply") or {}
                    from brain.interaction_events import record_interaction
                    record_interaction(
                        feature="tree_hole", event_type="tree_reply_finished",
                        source_id=note_id, content=str(reply.get("content") or ""),
                        summary=str(reply.get("content") or "")[:240],
                        metadata={"reply_id": reply.get("id"), "echo_of": note_id},
                    )
                except Exception as exc:
                    print(f"[互动事件] 树洞回复事件记录失败: {exc}")
        self.status.is_running = False
        self.status.last_result = "success"
        self.status.success_count += 1
        self.status.last_result_text = "树洞回应已写入"
        if note_id:
            self._scheduler.tree_hole_updated.emit(note_id, result or {})
        self._scheduler.duty_completed.emit(self.name, self.status.last_result_text)
        self._job = {}

    def _on_failed(self, result):
        result = result or {}
        note_id = int(result.get("note_id") or self._job.get("note_id", 0) or 0)
        error = str(result.get("error") or "树洞回应失败")
        db = self._database()
        attempts = int(self._job.get("attempts", 1) or 1)
        if note_id and attempts < 5:
            retry_at = time.time() + min(300, 30 * (2 ** max(0, attempts - 1)))
            db.reschedule_tree_reply(note_id, retry_at, error)
        elif note_id:
            db.mark_tree_reply_result(note_id, False, error)
        self.status.is_running = False
        self.status.last_result = "failed"
        self.status.fail_count += 1
        self.status.last_result_text = error
        self._scheduler.duty_failed.emit(self.name, error)
        self._job = {}


# ═══════════════════════════════════════════════════════════════
# ProactiveDuty
# ═══════════════════════════════════════════════════════════════

class ProactiveDuty(Duty):
    """主动聊天、观察、B站冲浪和摸鱼的统一抽签入口。"""

    def __init__(self):
        super().__init__("proactive", "主动聊天", tick_interval_seconds=300)
        # 强制调试行为（尤其是 B 站）失败时不应回退到其他主动行为。
        self._fallback_allowed = True

    def _check_enabled(self, state: SchedulerState) -> bool:
        ps = state.proactive_scheduler
        if ps is None:
            return False
        return ps.desktop_enabled or ps.qq_enabled

    def _should_fire(self, state: SchedulerState) -> bool:
        if state.agent_busy:
            return False
        ps = state.proactive_scheduler
        if ps is None:
            return False
        # 情感门控
        if not self._scheduler._check_emotional_gate():
            return False
        try:
            from brain.memory_proactive import get_active_suppression, get_due_cue
            if ps.memory_link_enabled:
                if get_active_suppression():
                    return False
                cue = get_due_cue()
                if cue and ps.can_deliver_memory_cue():
                    self._pending_memory_cue = cue
                    return True
        except Exception:
            pass
        self._pending_memory_cue = None
        self._pending_emotion_motive = None
        self._pending_emotion_persona = None
        try:
            from brain.emotional import get_manager
            from brain.persona.runtime import capture_persona_snapshot
            snapshot = capture_persona_snapshot()
            motive = get_manager().get_proactive_motive(persona_snapshot=snapshot)
            if motive.get("should_contact") and ps.can_deliver_emotional_motive():
                self._pending_emotion_motive = motive
                self._pending_emotion_persona = snapshot
                return True
        except Exception:
            pass
        return ps.should_fire()

    def manual_trigger(self, state: SchedulerState, **kwargs):
        """手动调试绕过概率和冷却，但仍只执行一种行为。"""
        kwargs["ignore_cooldowns"] = True
        self._execute(state, **kwargs)

    def _create_worker(self, state: SchedulerState, **kwargs):
        from workers.proactive_worker import ProactiveWorker

        force_observe = kwargs.get("force_observe", "")
        force_behavior = kwargs.get("force_behavior", "")
        forced_request = bool(force_observe or force_behavior)
        self._fallback_allowed = bool(kwargs.get("fallback_on_failure", True)) and not forced_request
        if forced_request:
            print(f"[主动调度] 强制行为={force_observe or force_behavior}，禁用普通行为回退")
        ps = state.proactive_scheduler
        hm = state.history_manager
        if force_observe:
            force_behavior = "bilibili" if force_observe == "bilibili" else "observe"

        memory_cue = None if force_behavior else getattr(self, "_pending_memory_cue", None)
        emotion_motive = None if force_behavior else getattr(self, "_pending_emotion_motive", None)
        emotion_persona = None if force_behavior else getattr(self, "_pending_emotion_persona", None)
        self._pending_memory_cue = None
        self._pending_emotion_motive = None
        self._pending_emotion_persona = None
        candidates = kwargs.get("candidates") or self._eligible_behaviors(
            state, ignore_cooldowns=kwargs.get("ignore_cooldowns", False)
        )
        behavior = force_behavior or (
            "memory" if memory_cue else
            "normal" if emotion_motive and "normal" in candidates else
            ps.choose_behavior(candidates)
        )
        if not behavior:
            return None

        self._current_state = state
        self._current_behavior = behavior
        self._remaining_behaviors = [item for item in candidates if item != behavior]
        self._observation_succeeded = False
        self._scheduler.proactive_behavior_selected.emit(behavior)

        self._current_memory_cue = memory_cue if behavior == "memory" else None
        if behavior == "memory":
            worker = ProactiveWorker(
                hm, memory_cue=memory_cue, emotional_motive=emotion_motive,
                persona_snapshot=emotion_persona,
            )
        elif behavior == "bilibili":
            worker = ProactiveWorker(
                hm,
                bilibili_mode=True,
                bilibili_ignore_cooldown=bool(kwargs.get("ignore_cooldowns", False)),
            )
        elif behavior == "slack":
            from workers.slack_worker import SlackWorker
            action = kwargs.get("force_action", "") or ps.should_slack()
            if not action:
                return None
            context, sources = SlackDuty()._build_context(action, state)
            self._current_action = action
            self._scheduler.slack_action_selected.emit(action)
            if sources:
                self._scheduler.mooyu_data_sources.emit(action, sources)
            worker = SlackWorker(action, context)
        else:
            observe_mode = (force_observe or ps.should_observe()) if behavior == "observe" else ""
            if behavior == "observe" and not observe_mode and not state.is_shoulder_available():
                return None
            if observe_mode and state.is_shoulder_available():
                observe_mode = "shoulder_explore"
            last_obs = ps.get_last_observation()
            growth_request = None
            if behavior == "normal":
                try:
                    from brain.persona.growth import get_persona_growth_service
                    from brain.persona.runtime import capture_persona_snapshot
                    current_persona = capture_persona_snapshot()
                    if current_persona and current_persona.enabled:
                        growth_request = get_persona_growth_service().next_proactive_request(
                            current_persona.profile.id
                        )
                        if growth_request:
                            get_persona_growth_service().record_proactive_result(
                                growth_request.get("kind", "curiosity"), "sent"
                            )
                            get_persona_growth_service().save_settings({
                                "last_proactive_reason": {
                                    "kind": growth_request.get("kind", "curiosity"),
                                    "source": growth_request.get("reason_source", ""),
                                    "summary": growth_request.get("reason_summary", ""),
                                }
                            })
                except Exception:
                    pass
            worker = ProactiveWorker(
                hm,
                observation_mode=observe_mode,
                last_observation=last_obs,
                camera_index=ps.camera_index,
                camera_wait=ps.camera_wait,
                emotional_motive=emotion_motive,
                growth_request=growth_request,
                persona_snapshot=emotion_persona,
            )
        return worker

    @staticmethod
    def _bilibili_available() -> bool:
        try:
            from utils.bilibili_history import get_bilibili_history
            return get_bilibili_history().can_search()
        except Exception:
            return False

    def _eligible_behaviors(self, state: SchedulerState,
                            ignore_cooldowns: bool = False) -> list[str]:
        ps = state.proactive_scheduler
        candidates = []
        ready = lambda name: ignore_cooldowns or ps.is_behavior_ready(name)
        if ps.normal_enabled and ready("normal"):
            candidates.append("normal")
        if (ps.observe_enabled and ps.desktop_enabled
                and (ps.screenshot_prob > 0 or ps.camera_prob > 0 or state.is_shoulder_available())
                and ready("observe")):
            candidates.append("observe")
        if (ps.bilibili_enabled and ready("bilibili")
                and (ignore_cooldowns or self._bilibili_available())):
            candidates.append("bilibili")
        slack_idle_ready = (not state.last_user_message_time or
                            state.now - state.last_user_message_time >= ps.slack_idle_minutes * 60)
        if (ps.slack_enabled and slack_idle_ready and ps.get_enabled_slack_actions()
                and ready("slack")):
            candidates.append("slack")
        return candidates

    def _wire_worker(self, worker):
        worker.response_ready.connect(self._on_response)
        worker.error_occurred.connect(self._on_error)
        if hasattr(worker, "observation_text"):
            worker.observation_text.connect(self._on_obs_text)
        if hasattr(worker, "observation_image"):
            worker.observation_image.connect(self._on_obs_image)
        # 摸鱼数据源透明信号（跨线程，Qt 自动队列）
        if hasattr(worker, "data_source_called"):
            worker.data_source_called.connect(self._on_data_source)

    def _on_response(self, text: str):
        self.status.is_running = False
        behavior = getattr(self, "_current_behavior", "normal")
        if behavior == "observe" and not self._observation_succeeded:
            text = ""
        if not text:
            self.status.last_result = "skipped"
            if self._try_fallback():
                return
            return
        self.status.last_result = "success"
        self.status.success_count += 1
        self._scheduler._proactive_scheduler.record_behavior_success(behavior)
        try:
            from brain.emotional import get_manager
            get_manager().record_proactive_action(
                behavior,
                persona_snapshot=getattr(self._worker, "_persona_snapshot", None),
            )
        except Exception:
            pass
        cue = getattr(self, "_current_memory_cue", None)
        if cue:
            try:
                from brain.memory_proactive import mark_cue_delivered
                mark_cue_delivered(cue["id"], text)
            except Exception:
                pass
        if behavior == "slack":
            self._scheduler.slack_response.emit(text)
        else:
            self._scheduler.proactive_response.emit(text)

    def _on_error(self, err: str):
        self.status.is_running = False
        self.status.last_result = "failed"
        self.status.fail_count += 1
        cue = getattr(self, "_current_memory_cue", None)
        if cue:
            try:
                from brain.memory_proactive import mark_cue_failed
                mark_cue_failed(cue["id"], err)
            except Exception:
                pass
        if self._try_fallback():
            return
        self._scheduler.proactive_error.emit(err)

    def _try_fallback(self) -> bool:
        if not getattr(self, "_fallback_allowed", True):
            print("[主动调度] 当前为强制行为，跳过普通行为回退")
            return False
        ps = self._scheduler._proactive_scheduler
        remaining = getattr(self, "_remaining_behaviors", [])
        state = getattr(self, "_current_state", None)
        if not ps or not ps.fallback_on_failure or not remaining or state is None:
            return False
        # 每轮最多回退一次，避免接口或设备异常造成连续请求。
        old_worker = self._worker
        if old_worker is not None:
            retired = getattr(self, "_retired_workers", [])
            retired.append(old_worker)
            self._retired_workers = retired
            old_worker.finished.connect(lambda: self._release_worker(old_worker))
        self._execute(state, candidates=remaining, fallback_on_failure=False)
        self._remaining_behaviors = []
        return self.status.is_running

    def _release_worker(self, worker):
        retired = getattr(self, "_retired_workers", [])
        if worker in retired:
            retired.remove(worker)

    def _on_obs_text(self, desc: str):
        self._observation_succeeded = bool(desc)
        self._scheduler.proactive_observation_text.emit(desc)

    def _on_obs_image(self, img_path: str, desc: str):
        self._scheduler.proactive_observation_image.emit(img_path, desc)

    def _on_data_source(self, name: str, preview: str, is_error: bool, elapsed_ms: float):
        """中继 ProactiveWorker 的数据源信号到 DutyScheduler。"""
        self._scheduler.mooyu_duty_data_source.emit(name, preview, is_error, elapsed_ms)

    # 子类需要访问 DutyScheduler —— 在 register 时注入
    @property
    def _scheduler(self):
        return self.__scheduler

    @_scheduler.setter
    def _scheduler(self, s):
        self.__scheduler = s


# ═══════════════════════════════════════════════════════════════
# SlackDuty
# ═══════════════════════════════════════════════════════════════

class SlackDuty(Duty):
    """摸鱼上下文构建兼容类；自动调度已并入 ProactiveDuty。"""

    def __init__(self):
        super().__init__("slack", "摸鱼消息", tick_interval_seconds=300)

    def _check_enabled(self, state: SchedulerState) -> bool:
        ps = state.proactive_scheduler
        if ps is None:
            return False
        return ps._settings.get("slack_enabled", False)

    def _should_fire(self, state: SchedulerState) -> bool:
        return False

    def _create_worker(self, state: SchedulerState, **kwargs):
        from workers.slack_worker import SlackWorker
        force_action = kwargs.get("force_action", "")
        ps = state.proactive_scheduler
        action = force_action or ps.should_slack()
        if not action:
            return None
        context, sources = self._build_context(action, state)
        self._current_action = action
        # 发射摸鱼数据源信号（主线程 → 直接同步）
        if sources:
            self._scheduler.mooyu_data_sources.emit(action, sources)
        return SlackWorker(action, context)

    def _wire_worker(self, worker):
        worker.response_ready.connect(self._on_response)
        worker.error_occurred.connect(self._on_error)

    def _on_response(self, text: str):
        self.status.is_running = False
        self.status.last_result = "success"
        self.status.success_count += 1
        self._scheduler.slack_response.emit(text)

    def _on_error(self, err: str):
        self.status.is_running = False
        self.status.last_result = "failed"
        self.status.fail_count += 1
        self._scheduler.slack_error.emit(err)

    def _build_context(self, action: str, state: SchedulerState) -> tuple[str, list]:
        """为摸鱼动作构建上下文（同步返回 context 文本 + 数据源记录列表）。"""
        from datetime import datetime
        from utils.mooyu_data import MooyuDataSource, MOOYU_SOURCE_FRIENDLY

        parts: list[str] = []
        sources: list[MooyuDataSource] = []
        today = datetime.now().strftime("%Y-%m-%d")

        # ── 补充日记 ──────────────────────────────────────────────
        if action == "supplement_diary":
            t0 = time.monotonic()
            try:
                from utils.diary import get_diary_by_date
                diary = get_diary_by_date(today)
                elapsed = (time.monotonic() - t0) * 1000
                if diary:
                    parts.append(f"【今天的日记】\n{diary['content'][:500]}")
                    sources.append(MooyuDataSource(
                        source_name="get_diary_today",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_diary_today", "翻日记"),
                        preview=f"找到今天的日记，{len(diary['content'])} 字",
                        elapsed_ms=elapsed,
                        detail=diary['content'][:500],
                    ))
                else:
                    sources.append(MooyuDataSource(
                        source_name="get_diary_today",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_diary_today", "翻日记"),
                        preview="今天还没有日记",
                        is_error=True,
                        elapsed_ms=elapsed,
                    ))
            except Exception as e:
                elapsed = (time.monotonic() - t0) * 1000
                sources.append(MooyuDataSource(
                    source_name="get_diary_today",
                    friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_diary_today", "翻日记"),
                    preview=f"查询失败: {e}",
                    is_error=True,
                    elapsed_ms=elapsed,
                ))

        # ── 翻旧日记 ──────────────────────────────────────────────
        elif action == "review_old_diary":
            t0 = time.monotonic()
            try:
                import random as _random
                from utils.diary import get_all_diaries
                diaries = get_all_diaries()
                elapsed = (time.monotonic() - t0) * 1000
                if diaries:
                    old = _random.choice(diaries)
                    parts.append(f"【旧日记 - {old['date']}】\n{old['content'][:500]}")
                    sources.append(MooyuDataSource(
                        source_name="get_diary_old",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_diary_old", "翻旧日记"),
                        preview=f"找到 {old['date']} 的日记",
                        elapsed_ms=elapsed,
                        detail=old['content'][:500],
                    ))
                else:
                    sources.append(MooyuDataSource(
                        source_name="get_diary_old",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_diary_old", "翻旧日记"),
                        preview="没有旧日记",
                        is_error=True,
                        elapsed_ms=elapsed,
                    ))
            except Exception as e:
                elapsed = (time.monotonic() - t0) * 1000
                sources.append(MooyuDataSource(
                    source_name="get_diary_old",
                    friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_diary_old", "翻旧日记"),
                    preview=f"查询失败: {e}",
                    is_error=True,
                    elapsed_ms=elapsed,
                ))

        # ── 翻旧话题 / 随机提问 ──────────────────────────────────
        elif action in ("search_old_topic", "random_question"):
            t0 = time.monotonic()
            try:
                hm = state.history_manager
                if hm:
                    sessions = hm.get_sessions()
                    if sessions:
                        msgs = hm.get_messages(sessions[0]["id"])
                        recent = msgs[-30:] if len(msgs) > 30 else msgs
                        elapsed = (time.monotonic() - t0) * 1000
                        user_name = self._get_user_name()
                        lines = [f"{user_name if m['role'] == 'user' else '莲心'}：{m['content'][:200]}"
                                 for m in recent]
                        parts.append("【最近的对话】\n" + "\n".join(lines))
                        sources.append(MooyuDataSource(
                            source_name="get_chat_history",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_chat_history", "翻聊天记录"),
                            preview=f"获取最近 {len(recent)} 条对话",
                            elapsed_ms=elapsed,
                            detail="\n".join(lines),
                        ))
                    else:
                        elapsed = (time.monotonic() - t0) * 1000
                        sources.append(MooyuDataSource(
                            source_name="get_chat_history",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_chat_history", "翻聊天记录"),
                            preview="没有对话记录",
                            is_error=True,
                            elapsed_ms=elapsed,
                        ))
                else:
                    elapsed = (time.monotonic() - t0) * 1000
                    sources.append(MooyuDataSource(
                        source_name="get_chat_history",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_chat_history", "翻聊天记录"),
                        preview="历史管理器不可用",
                        is_error=True,
                        elapsed_ms=elapsed,
                    ))
            except Exception as e:
                elapsed = (time.monotonic() - t0) * 1000
                sources.append(MooyuDataSource(
                    source_name="get_chat_history",
                    friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_chat_history", "翻聊天记录"),
                    preview=f"查询失败: {e}",
                    is_error=True,
                    elapsed_ms=elapsed,
                ))

        # ── 提醒待办 ──────────────────────────────────────────────
        elif action == "remind_todo":
            t0 = time.monotonic()
            try:
                tm = state.todo_manager
                if tm:
                    todos = tm.get_todos(completed=False)
                    elapsed = (time.monotonic() - t0) * 1000
                    if todos:
                        todo_lines = []
                        for t in todos[:5]:
                            if hasattr(t, 'due_date') and t.due_date:
                                todo_lines.append(f"- {t.title}（截止日期：{t.due_date}）")
                            else:
                                todo_lines.append(f"- {t.title}")
                        parts.append(f"【当前日期】{today}\n【未完成的待办】\n" + "\n".join(todo_lines))
                        sources.append(MooyuDataSource(
                            source_name="get_todos",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_todos", "查看待办"),
                            preview=f"找到 {len(todos)} 个未完成待办",
                            elapsed_ms=elapsed,
                            detail="\n".join(todo_lines),
                        ))
                    else:
                        sources.append(MooyuDataSource(
                            source_name="get_todos",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_todos", "查看待办"),
                            preview="没有未完成待办",
                            elapsed_ms=elapsed,
                        ))
                else:
                    elapsed = (time.monotonic() - t0) * 1000
                    sources.append(MooyuDataSource(
                        source_name="get_todos",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_todos", "查看待办"),
                        preview="待办管理器不可用",
                        is_error=True,
                        elapsed_ms=elapsed,
                    ))
            except Exception as e:
                elapsed = (time.monotonic() - t0) * 1000
                sources.append(MooyuDataSource(
                    source_name="get_todos",
                    friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_todos", "查看待办"),
                    preview=f"查询失败: {e}",
                    is_error=True,
                    elapsed_ms=elapsed,
                ))

        # ── 天气闲聊 ──────────────────────────────────────────────
        elif action == "weather_chitchat":
            t0 = time.monotonic()
            try:
                from config import get_qweather_config
                from brain.weather import get_user_city_from_memory, get_full_weather
                qw_cfg = get_qweather_config()
                api_key = qw_cfg.get("api_key", "").strip()
                if api_key:
                    city = get_user_city_from_memory()
                    if city:
                        weather_text = get_full_weather(city, api_key=api_key)
                        elapsed = (time.monotonic() - t0) * 1000
                        if weather_text and "错误" not in weather_text:
                            parts.append(f"【当前天气】\n{weather_text}")
                            sources.append(MooyuDataSource(
                                source_name="get_weather",
                                friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_weather", "查天气"),
                                preview=f"获取到 {city} 的天气数据",
                                elapsed_ms=elapsed,
                                detail=weather_text,
                            ))
                        else:
                            sources.append(MooyuDataSource(
                                source_name="get_weather",
                                friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_weather", "查天气"),
                                preview=f"天气获取失败: {weather_text}",
                                is_error=True,
                                elapsed_ms=elapsed,
                            ))
                    else:
                        elapsed = (time.monotonic() - t0) * 1000
                        sources.append(MooyuDataSource(
                            source_name="get_weather",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_weather", "查天气"),
                            preview="未设置城市",
                            is_error=True,
                            elapsed_ms=elapsed,
                        ))
                else:
                    elapsed = (time.monotonic() - t0) * 1000
                    sources.append(MooyuDataSource(
                        source_name="get_weather",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_weather", "查天气"),
                        preview="未配置 API Key",
                        is_error=True,
                        elapsed_ms=elapsed,
                    ))
            except Exception as e:
                elapsed = (time.monotonic() - t0) * 1000
                sources.append(MooyuDataSource(
                    source_name="get_weather",
                    friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_weather", "查天气"),
                    preview=f"查询异常: {e}",
                    is_error=True,
                    elapsed_ms=elapsed,
                ))

        # ── 翻本地文件 ────────────────────────────────────────────
        elif action == "read_local_files":
            t0 = time.monotonic()
            try:
                from utils.slack_utils import get_random_document
                doc = get_random_document()
                elapsed = (time.monotonic() - t0) * 1000
                if doc:
                    parts.append(
                        f"【翻到的文件】\n文件名：{doc['name']}\n"
                        f"所在文件夹：{doc['folder']}\n大小：{doc['size_kb']} KB\n"
                        f"\n内容摘要：\n{doc['snippet']}"
                    )
                    sources.append(MooyuDataSource(
                        source_name="get_random_document",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_random_document", "翻文件"),
                        preview=f"找到文件: {doc['name']} ({doc['size_kb']} KB)",
                        elapsed_ms=elapsed,
                        detail=doc['snippet'],
                    ))
                else:
                    sources.append(MooyuDataSource(
                        source_name="get_random_document",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_random_document", "翻文件"),
                        preview="没有找到可读文件",
                        is_error=True,
                        elapsed_ms=elapsed,
                    ))
            except Exception as e:
                elapsed = (time.monotonic() - t0) * 1000
                sources.append(MooyuDataSource(
                    source_name="get_random_document",
                    friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_random_document", "翻文件"),
                    preview=f"查询失败: {e}",
                    is_error=True,
                    elapsed_ms=elapsed,
                ))

        # ── 浏览器历史 ────────────────────────────────────────────
        elif action == "browser_history":
            t0 = time.monotonic()
            try:
                from utils.slack_utils import get_browser_history_snippet
                history = get_browser_history_snippet()
                elapsed = (time.monotonic() - t0) * 1000
                if history:
                    parts.append(f"【浏览器最近访问记录】\n{history}")
                    sources.append(MooyuDataSource(
                        source_name="get_browser_history",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_browser_history", "看浏览记录"),
                        preview="获取到浏览器历史记录",
                        elapsed_ms=elapsed,
                        detail=history,
                    ))
                else:
                    sources.append(MooyuDataSource(
                        source_name="get_browser_history",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_browser_history", "看浏览记录"),
                        preview="浏览器历史为空",
                        is_error=True,
                        elapsed_ms=elapsed,
                    ))
            except Exception as e:
                elapsed = (time.monotonic() - t0) * 1000
                sources.append(MooyuDataSource(
                    source_name="get_browser_history",
                    friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_browser_history", "看浏览记录"),
                    preview=f"查询失败: {e}",
                    is_error=True,
                    elapsed_ms=elapsed,
                ))

        # ── 批量获取：CPU状态/回收站/休息提醒/喝水提醒/纪念日/切歌 ──
        elif action in ("check_cpu_disk", "check_recycle_bin", "remind_rest",
                        "remind_water", "anniversary_remind", "next_song"):
            try:
                if action == "check_cpu_disk":
                    t0 = time.monotonic()
                    try:
                        from utils.slack_utils import get_system_status
                        status = get_system_status()
                        elapsed = (time.monotonic() - t0) * 1000
                        lines = []
                        if status.get("cpu_percent") is not None:
                            lines.append(f"CPU: {status['cpu_percent']}%")
                        if status.get("memory_percent") is not None:
                            lines.append(f"内存: {status['memory_percent']}%")
                        if status.get("top_processes"):
                            lines.append("高占用进程: " + ", ".join(status["top_processes"]))
                        if status.get("disk_info"):
                            lines.append(f"磁盘: {status['disk_info']}")
                        if lines:
                            ctx = "【电脑系统状态】\n" + "\n".join(lines)
                            parts.append(ctx)
                            sources.append(MooyuDataSource(
                                source_name="get_system_status",
                                friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_system_status", "查看系统状态"),
                                preview=f"CPU {status.get('cpu_percent', '?')}% | 内存 {status.get('memory_percent', '?')}%",
                                elapsed_ms=elapsed,
                                detail=ctx,
                            ))
                        else:
                            sources.append(MooyuDataSource(
                                source_name="get_system_status",
                                friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_system_status", "查看系统状态"),
                                preview="无系统状态数据",
                                is_error=True,
                                elapsed_ms=elapsed,
                            ))
                    except Exception as e:
                        elapsed = (time.monotonic() - t0) * 1000
                        sources.append(MooyuDataSource(
                            source_name="get_system_status",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_system_status", "查看系统状态"),
                            preview=f"查询失败: {e}",
                            is_error=True,
                            elapsed_ms=elapsed,
                        ))

                elif action == "check_recycle_bin":
                    t0 = time.monotonic()
                    try:
                        from utils.slack_utils import get_recycle_bin_info
                        info = get_recycle_bin_info()
                        elapsed = (time.monotonic() - t0) * 1000
                        if info:
                            parts.append(f"【回收站信息】\n{info}")
                            sources.append(MooyuDataSource(
                                source_name="get_recycle_bin",
                                friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_recycle_bin", "查看回收站"),
                                preview=info[:80].replace("\n", " "),
                                elapsed_ms=elapsed,
                                detail=info,
                            ))
                        else:
                            sources.append(MooyuDataSource(
                                source_name="get_recycle_bin",
                                friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_recycle_bin", "查看回收站"),
                                preview="无回收站数据",
                                is_error=True,
                                elapsed_ms=elapsed,
                            ))
                    except Exception as e:
                        elapsed = (time.monotonic() - t0) * 1000
                        sources.append(MooyuDataSource(
                            source_name="get_recycle_bin",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_recycle_bin", "查看回收站"),
                            preview=f"查询失败: {e}",
                            is_error=True,
                            elapsed_ms=elapsed,
                        ))

                elif action == "remind_rest":
                    t0 = time.monotonic()
                    try:
                        import psutil
                        from datetime import datetime as _dt
                        boot = _dt.fromtimestamp(psutil.boot_time())
                        uptime = _dt.now() - boot
                        hours = int(uptime.total_seconds() / 3600)
                        elapsed = (time.monotonic() - t0) * 1000
                        ctx = f"【电脑开机时长】约 {hours} 小时"
                        parts.append(ctx)
                        sources.append(MooyuDataSource(
                            source_name="get_uptime",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_uptime", "查看开机时长"),
                            preview=f"开机 {hours} 小时",
                            elapsed_ms=elapsed,
                            detail=ctx,
                        ))
                    except Exception as e:
                        elapsed = (time.monotonic() - t0) * 1000
                        sources.append(MooyuDataSource(
                            source_name="get_uptime",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_uptime", "查看开机时长"),
                            preview=f"查询失败: {e}",
                            is_error=True,
                            elapsed_ms=elapsed,
                        ))

                elif action == "remind_water":
                    ctx = f"【提醒喝水】现在是时候提醒{self._get_user_name()}喝口水了"
                    parts.append(ctx)
                    sources.append(MooyuDataSource(
                        source_name="remind_water",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("remind_water", "提醒喝水"),
                        preview="提醒喝水",
                        detail=ctx,
                    ))

                elif action == "anniversary_remind":
                    t0 = time.monotonic()
                    try:
                        from utils.accompany_stats import AccompanyStats
                        stats = AccompanyStats()
                        stats.reload()
                        first_meet = stats.get_first_meet_date()
                        elapsed = (time.monotonic() - t0) * 1000
                        if first_meet:
                            days = stats.get_total_days_since_first_meet()
                            ctx = f"【相识纪念日】相识日期: {first_meet}，已相伴 {days} 天"
                            parts.append(ctx)
                            sources.append(MooyuDataSource(
                                source_name="get_anniversary",
                                friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_anniversary", "查看纪念日"),
                                preview=f"相识 {days} 天",
                                elapsed_ms=elapsed,
                                detail=ctx,
                            ))
                        else:
                            sources.append(MooyuDataSource(
                                source_name="get_anniversary",
                                friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_anniversary", "查看纪念日"),
                                preview="未设置相识日期",
                                is_error=True,
                                elapsed_ms=elapsed,
                            ))
                    except Exception as e:
                        elapsed = (time.monotonic() - t0) * 1000
                        sources.append(MooyuDataSource(
                            source_name="get_anniversary",
                            friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_anniversary", "查看纪念日"),
                            preview=f"查询失败: {e}",
                            is_error=True,
                            elapsed_ms=elapsed,
                        ))

                elif action == "next_song":
                    sources.append(MooyuDataSource(
                        source_name="get_current_song",
                        friendly_name=MOOYU_SOURCE_FRIENDLY.get("get_current_song", "查看播放歌曲"),
                        preview="（需要在主窗口上下文才能获取播放列表）",
                        is_error=True,
                        elapsed_ms=0,
                    ))

            except Exception:
                pass

        return ("\n\n".join(parts) if parts else "", sources)

    @staticmethod
    def _get_user_name() -> str:
        try:
            from utils.settings import get_settings
            return get_settings().user_name
        except Exception:
            return "主人"

    @property
    def _scheduler(self):
        return self.__scheduler

    @_scheduler.setter
    def _scheduler(self, s):
        self.__scheduler = s


# ═══════════════════════════════════════════════════════════════
# MemoryExtractionDuty
# ═══════════════════════════════════════════════════════════════

class MemoryExtractionDuty(Duty):
    """Extract persisted owner-session messages when idle or backlogged."""

    def __init__(self):
        super().__init__("memory_extraction", "记忆自动提取", tick_interval_seconds=60)
        self._candidate: dict | None = None
        self._active_store = None
        self._active_session_id = 0
        self._probe_thread: threading.Thread | None = None
        self._probe_result: list[dict] | None = None
        self._probe_error: Exception | None = None
        self._probe_lock = threading.Lock()

    @staticmethod
    def _config() -> dict:
        try:
            from config import get_memory_config
            return get_memory_config()
        except Exception:
            return {
                "auto_extract": True,
                "extract_message_count": 20,
                "extraction_min_messages": 3,
                "extraction_idle_seconds": 120,
                "extraction_backlog_messages": 20,
                "extraction_backlog_quiet_seconds": 15,
                "extraction_retry_base_minutes": 5,
                "extraction_retry_max_minutes": 60,
                "extraction_failure_pause_threshold": 5,
                "extraction_pause_minutes": 360,
            }

    @staticmethod
    def _message_age_seconds(value: str) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return max(0.0, time.time() - parsed.timestamp())
            return max(0.0, time.time() - parsed.timestamp())
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _store(state: SchedulerState):
        if not state.history_manager:
            return None
        from brain.memory_extraction_pipeline import MemoryExtractionStore

        return MemoryExtractionStore(state.history_manager.db_path)

    def _check_enabled(self, state: SchedulerState) -> bool:
        return bool(self._config().get("auto_extract", True))

    def _should_fire(self, state: SchedulerState) -> bool:
        self._candidate = None
        if state.agent_busy or not state.history_manager or not state.agent:
            return False
        if getattr(state.agent, "_use_local", False):
            self.status.detail_text = "本地模型模式下暂停自动提取"
            return False
        # The Qt timer owns this method. SQLite probing belongs on a daemon
        # thread so a locked database cannot freeze the application's UI.
        with self._probe_lock:
            probe = self._probe_thread
            if probe is None:
                self._probe_result = None
                self._probe_error = None
                db_path = state.history_manager.db_path
                self._probe_thread = threading.Thread(
                    target=self._probe_pending_sessions,
                    args=(db_path,),
                    name="memory-extraction-probe",
                    daemon=True,
                )
                self._probe_thread.start()
                self.status.detail_text = "正在后台检查待提取消息"
                return False
            if probe.is_alive():
                self.status.detail_text = "正在后台检查待提取消息"
                return False
            candidates = self._probe_result or []
            probe_error = self._probe_error
            self._probe_thread = None
        if probe_error:
            self.status.detail_text = f"状态读取失败：{probe_error}"
            return False

        self.status.pending_count = sum(int(item.get("pending_count", 0)) for item in candidates)
        self.status.next_retry_at = 0.0
        self.status.paused_until = 0.0
        if not candidates:
            self.status.detail_text = "暂无待提取消息"
            return False

        cfg = self._config()
        minimum = max(1, int(cfg.get("extraction_min_messages", 3)))
        idle_seconds = max(30, int(cfg.get("extraction_idle_seconds", 120)))
        backlog = max(minimum, int(cfg.get("extraction_backlog_messages", 20)))
        quiet_seconds = max(0, int(cfg.get("extraction_backlog_quiet_seconds", 15)))
        now_epoch = time.time()
        waiting_reason = ""

        for candidate in candidates:
            count = int(candidate.get("pending_count", 0))
            if count < minimum:
                continue
            paused_until = float(candidate.get("paused_until", 0) or 0)
            next_retry_at = float(candidate.get("next_retry_at", 0) or 0)
            lease_expires_at = float(candidate.get("lease_expires_at", 0) or 0)
            if candidate.get("active_run_id") and lease_expires_at > now_epoch:
                waiting_reason = "已有提取任务正在运行"
                continue
            if paused_until > now_epoch:
                self.status.paused_until = max(self.status.paused_until, paused_until)
                waiting_reason = candidate.get("pause_reason") or "连续失败后自动暂停"
                continue
            if next_retry_at > now_epoch:
                self.status.next_retry_at = max(self.status.next_retry_at, next_retry_at)
                waiting_reason = "失败后退避等待"
                continue

            age = self._message_age_seconds(candidate.get("last_message_at", ""))
            if count >= backlog and age >= quiet_seconds:
                self._candidate = candidate
                self._candidate["trigger_reason"] = "backlog"
                self.status.detail_text = f"积压 {count} 条，准备优先提取"
                return True
            if age >= idle_seconds:
                self._candidate = candidate
                self._candidate["trigger_reason"] = "idle"
                self.status.detail_text = f"已空闲 {int(age)} 秒，准备提取 {count} 条"
                return True

        if self.status.paused_until > now_epoch:
            self.status.detail_text = waiting_reason or "自动提取已暂停"
        elif self.status.next_retry_at > now_epoch:
            self.status.detail_text = waiting_reason or "等待失败重试"
        else:
            self.status.detail_text = waiting_reason or f"积压 {self.status.pending_count} 条，等待空闲"
        return False

    def _probe_pending_sessions(self, db_path):
        try:
            from brain.memory_extraction_pipeline import MemoryExtractionStore
            store = MemoryExtractionStore(db_path)
            result = store.list_pending_sessions(owner_only=True)
        except Exception as exc:
            with self._probe_lock:
                self._probe_error = exc
            return
        with self._probe_lock:
            self._probe_result = result

    def manual_trigger(self, state: SchedulerState, **kwargs):
        kwargs.setdefault("trigger", "manual")
        kwargs.setdefault("force", True)
        kwargs.setdefault("session_id", state.session_id)
        self._execute(state, **kwargs)

    def _create_worker(self, state: SchedulerState, **kwargs):
        from brain.memory_extraction_pipeline import (
            LLMExtractionProcessor,
            MemoryExtractionPipeline,
        )
        from workers.memory_extraction_worker import MemoryExtractionWorker

        agent = state.agent
        if not agent or getattr(agent, "_use_local", False) or not state.history_manager:
            self.status.detail_text = "当前没有可用的云端记忆提取模型"
            return None

        force = bool(kwargs.get("force", False))
        session_id = int(
            kwargs.get("session_id")
            or (self._candidate or {}).get("session_id")
            or state.session_id
        )
        if session_id <= 0:
            self.status.detail_text = "当前没有可提取的会话"
            return None

        store = self._store(state)
        summary = store.get_pending_summary(session_id)
        source_channel = str(
            summary.get("channel") or getattr(agent, "_source_channel", "desktop") or "desktop"
        )
        try:
            from brain.persona.runtime import capture_persona_snapshot

            persona_id = capture_persona_snapshot().profile.id
        except Exception:
            persona_id = ""
        try:
            from config import get_graph_config

            graph_cfg = get_graph_config()
        except Exception:
            graph_cfg = {"graph_enabled": True, "auto_extract_quintuples": True}

        cfg = self._config()
        processor = LLMExtractionProcessor(
            model=agent._model,
            api_key=agent._api_key,
            api_base=agent._api_base,
            source_channel=source_channel,
            persona_id=persona_id,
            graph_enabled=graph_cfg.get("graph_enabled", True),
            extract_quintuples=graph_cfg.get("auto_extract_quintuples", True),
        )
        pipeline = MemoryExtractionPipeline(
            store=store,
            processor=processor,
            max_messages=int(cfg.get("extract_message_count", 20)),
            min_messages=1 if force else int(cfg.get("extraction_min_messages", 3)),
            retry_base_seconds=int(cfg.get("extraction_retry_base_minutes", 5)) * 60,
            retry_max_seconds=int(cfg.get("extraction_retry_max_minutes", 60)) * 60,
            pause_threshold=int(cfg.get("extraction_failure_pause_threshold", 5)),
            pause_seconds=int(cfg.get("extraction_pause_minutes", 360)) * 60,
        )
        self._active_store = store
        self._active_session_id = session_id
        trigger = str(
            kwargs.get("trigger")
            or (self._candidate or {}).get("trigger_reason")
            or "scheduled"
        )
        return MemoryExtractionWorker(
            pipeline=pipeline,
            session_id=session_id,
            trigger=trigger,
            force=force,
        )

    def _wire_worker(self, worker):
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)

    def _sync_gate_status(self):
        if not self._active_store or not self._active_session_id:
            return {}
        summary = self._active_store.get_pending_summary(self._active_session_id)
        self.status.pending_count = int(summary.get("pending_count", 0) or 0)
        self.status.next_retry_at = float(summary.get("next_retry_at", 0) or 0)
        self.status.paused_until = float(summary.get("paused_until", 0) or 0)
        return summary

    def _on_completed(self, result: dict):
        self.status.is_running = False
        status = result.get("status", "success")
        self.status.last_result = status
        if status == "success":
            self.status.success_count += 1
            run = result.get("run", {})
            self.status.last_result_text = (
                f"消息 #{run.get('from_message_id', 0)}–#{run.get('to_message_id', 0)} "
                f"共 {run.get('message_count', 0)} 条，写入 {run.get('fragments_created', 0)} 条，"
                f"Token {int(run.get('input_tokens', 0)) + int(run.get('output_tokens', 0))}，"
                f"耗时 {float(run.get('duration_ms', 0)) / 1000:.1f} 秒"
            )
        else:
            self.status.last_result_text = "当前会话暂无可提取消息"
        self._sync_gate_status()
        self.status.detail_text = self.status.last_result_text
        self._scheduler.duty_completed.emit(self.name, self.status.last_result_text)

    def _on_failed(self, result: dict):
        self.status.is_running = False
        self.status.last_result = "failed"
        self.status.fail_count += 1
        summary = self._sync_gate_status()
        error = str(result.get("error", "未知提取错误"))
        if float(summary.get("paused_until", 0) or 0) > time.time():
            self.status.last_result_text = f"{error}；已自动暂停"
        else:
            self.status.last_result_text = f"{error}；将按退避策略重试"
        self.status.detail_text = self.status.last_result_text
        self._scheduler.duty_failed.emit(self.name, self.status.last_result_text)

    @property
    def _scheduler(self):
        return self.__scheduler

    @_scheduler.setter
    def _scheduler(self, scheduler):
        self.__scheduler = scheduler


class EmbeddingIndexDuty(Duty):
    """Build a small batch of memory embeddings only after chat becomes idle."""

    def __init__(self):
        super().__init__("embedding_index", "记忆语义索引", tick_interval_seconds=60)

    @staticmethod
    def _config() -> dict:
        try:
            from config import get_memory_config
            return get_memory_config()
        except Exception:
            return {
                "embedding_indexing_mode": "idle",
                "embedding_idle_seconds": 180,
                "embedding_idle_batch_size": 20,
            }

    def _check_enabled(self, state: SchedulerState) -> bool:
        return self._config().get("embedding_indexing_mode", "idle") == "idle"

    def _should_fire(self, state: SchedulerState) -> bool:
        if state.agent_busy:
            self.status.detail_text = "正在处理对话，稍后索引"
            return False
        if not state.last_user_message_time:
            self.status.detail_text = "等待一次聊天后的空闲窗口"
            return False
        cfg = self._config()
        idle_seconds = max(30, int(cfg.get("embedding_idle_seconds", 180)))
        if state.now - state.last_user_message_time < idle_seconds:
            self.status.detail_text = "等待聊天空闲"
            return False
        try:
            from utils.model_resource_manager import get_model_resource_manager
            if get_model_resource_manager().has_active_gpu_lease():
                self.status.detail_text = "语音模型正在使用 GPU，延后索引"
                return False
            from brain.graph_memory import _get_conn
            self.status.pending_count = int(_get_conn().execute(
                "SELECT COUNT(*) FROM memory_facts WHERE embedding IS NULL AND status='active'"
            ).fetchone()[0])
            ann_needs_build = False
            try:
                from config import get_rag_config
                from utils.rag_ann_index import get_rag_ann_index
                ann_needs_build = (
                    bool(get_rag_config().get("rag_ann_enabled", True))
                    and get_rag_ann_index().needs_build()
                )
            except Exception:
                ann_needs_build = False
        except Exception as exc:
            self.status.detail_text = f"待索引记忆检查失败: {exc}"
            return False
        if not self.status.pending_count and not ann_needs_build:
            self.status.detail_text = "没有待索引记忆"
            return False
        if ann_needs_build and not self.status.pending_count:
            self.status.detail_text = "空闲中，准备构建 RAG ANN 索引"
        else:
            self.status.detail_text = f"空闲中，准备索引 {self.status.pending_count} 条记忆"
        return True

    def _create_worker(self, state: SchedulerState, **kwargs):
        from workers.embedding_index_worker import EmbeddingIndexWorker

        size = max(1, int(self._config().get("embedding_idle_batch_size", 20)))
        return EmbeddingIndexWorker(max_items=size)

    def _wire_worker(self, worker):
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)

    def _on_completed(self, result: dict):
        self.status.is_running = False
        status = str(result.get("status", "success"))
        self.status.last_result = status
        self.status.pending_count = int(result.get("remaining", 0) or 0)
        if status == "success":
            self.status.success_count += 1
            self.status.last_result_text = (
                f"已索引 {int(result.get('indexed', 0))} 条，剩余 {self.status.pending_count} 条"
            )
        elif status == "busy":
            self.status.last_result_text = "已有索引任务在运行"
        else:
            self.status.last_result_text = "嵌入模型当前不可用，保留关键词检索"
        self.status.detail_text = self.status.last_result_text
        self._scheduler.duty_completed.emit(self.name, self.status.last_result_text)

    def _on_failed(self, error: str):
        self.status.is_running = False
        self.status.last_result = "failed"
        self.status.fail_count += 1
        self.status.last_result_text = str(error)
        self.status.detail_text = self.status.last_result_text
        self._scheduler.duty_failed.emit(self.name, self.status.last_result_text)

    @property
    def _scheduler(self):
        return self.__scheduler

    @_scheduler.setter
    def _scheduler(self, scheduler):
        self.__scheduler = scheduler


# ═══════════════════════════════════════════════════════════════
# MemoryCueEvaluationDuty
# ═══════════════════════════════════════════════════════════════

class MemoryCueEvaluationDuty(Duty):
    """Low-frequency model review of semantic proactive opportunities."""

    def __init__(self):
        super().__init__("memory_cue_evaluation", "记忆主动联动", tick_interval_seconds=300)

    def _check_enabled(self, state: SchedulerState) -> bool:
        ps = state.proactive_scheduler
        return bool(ps and ps.memory_link_enabled and (ps.desktop_enabled or ps.qq_enabled))

    def _should_fire(self, state: SchedulerState) -> bool:
        if state.agent_busy:
            return False
        if state.last_user_message_time and state.now-state.last_user_message_time < 120:
            return False
        try:
            from brain.memory_proactive import should_evaluate
            return should_evaluate(state.proactive_scheduler.memory_evaluation_interval_minutes)
        except Exception:
            return False

    def _create_worker(self, state: SchedulerState, **kwargs):
        from workers.memory_cue_worker import MemoryCueEvaluationWorker
        return MemoryCueEvaluationWorker(max_candidates=state.proactive_scheduler.memory_max_candidates)

    def _wire_worker(self, worker):
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)

    def _on_completed(self, result):
        self.status.is_running=False; self.status.last_result="success"; self.status.success_count+=1
        self.status.last_result_text=f"评估 {result.get('evaluated',0)} 条，批准 {result.get('approved',0)} 条"
        self._scheduler.duty_completed.emit(self.name,self.status.last_result_text)

    def _on_failed(self, error):
        self.status.is_running=False; self.status.last_result="failed"; self.status.fail_count+=1
        self.status.last_result_text=str(error); self._scheduler.duty_failed.emit(self.name,str(error))

    @property
    def _scheduler(self): return self.__scheduler
    @_scheduler.setter
    def _scheduler(self, value): self.__scheduler=value


# ═══════════════════════════════════════════════════════════════
# MemoryNarrativeDuty
# ═══════════════════════════════════════════════════════════════

class MemoryNarrativeDuty(Duty):
    """Low-frequency semantic consolidation into entities, episodes and sagas."""

    def __init__(self):
        super().__init__("memory_narrative", "叙事记忆整合", tick_interval_seconds=300)

    @staticmethod
    def _config():
        try:
            from config import get_memory_config
            return get_memory_config()
        except Exception:
            return {"narrative_enabled": True, "narrative_interval_hours": 12,
                    "narrative_candidate_batch": 36}

    def _check_enabled(self, state):
        return bool(self._config().get("narrative_enabled", True))

    def _should_fire(self, state):
        if state.agent_busy:
            return False
        try:
            from brain.memory_narrative import should_run_narrative
            return should_run_narrative(self._config().get("narrative_interval_hours", 12))
        except Exception:
            return False

    def _create_worker(self, state, **kwargs):
        from workers.memory_narrative_worker import MemoryNarrativeWorker
        return MemoryNarrativeWorker(
            max_candidates=int(self._config().get("narrative_candidate_batch", 36))
        )

    def _wire_worker(self, worker):
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)

    def _on_completed(self, result):
        self.status.is_running = False
        self.status.last_result = "success"
        self.status.success_count += 1
        self.status.last_result_text = (
            f"Episode {result.get('episodes_created', 0)} 条，"
            f"实体更新 {result.get('entities_updated', 0)} 个"
        )
        self._scheduler.duty_completed.emit(self.name, self.status.last_result_text)

    def _on_failed(self, error):
        self.status.is_running = False
        self.status.last_result = "failed"
        self.status.fail_count += 1
        self.status.last_result_text = str(error)
        self._scheduler.duty_failed.emit(self.name, str(error))

    @property
    def _scheduler(self): return self.__scheduler
    @_scheduler.setter
    def _scheduler(self, value): self.__scheduler = value


# ═══════════════════════════════════════════════════════════════
# WorkingMemorySummaryDuty
# ═══════════════════════════════════════════════════════════════

class WorkingMemorySummaryDuty(Duty):
    """Model-assisted topic summary; code summary remains the fallback."""

    def __init__(self):
        super().__init__("working_memory_summary", "工作记忆摘要", tick_interval_seconds=300)

    @staticmethod
    def _config():
        try:
            from config import get_memory_config
            return get_memory_config()
        except Exception:
            return {"working_memory_model_summary_enabled": True, "working_memory_summary_interval_minutes": 10}

    def _check_enabled(self, state):
        return bool(self._config().get("working_memory_model_summary_enabled", True))

    def _should_fire(self, state):
        if state.agent_busy or not state.history_manager:
            return False
        try:
            from brain.working_memory import get_working_topic, should_refresh_model_summary
            topic = get_working_topic(session_id=state.session_id)
            if state.last_user_message_time and state.now - state.last_user_message_time < 120:
                return False
            return should_refresh_model_summary(topic, self._config().get("working_memory_summary_interval_minutes", 10))
        except Exception:
            return False

    def _create_worker(self, state, **kwargs):
        from workers.working_memory_summary_worker import WorkingMemorySummaryWorker
        return WorkingMemorySummaryWorker(state.history_manager, state.session_id)

    def _wire_worker(self, worker):
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)

    def _on_completed(self, result):
        self.status.is_running = False
        self.status.last_result = result.get("status", "success")
        self.status.success_count += 1
        self.status.last_result_text = "工作记忆摘要已更新" if result.get("status") == "success" else "暂无可更新工作记忆"

    def _on_failed(self, error):
        self.status.is_running = False
        self.status.last_result = "failed"
        self.status.fail_count += 1
        self.status.last_result_text = str(error)

    @property
    def _scheduler(self): return self.__scheduler
    @_scheduler.setter
    def _scheduler(self, value): self.__scheduler = value


# ═══════════════════════════════════════════════════════════════
# MemoryMaintenanceDuty
# ═══════════════════════════════════════════════════════════════

class MemoryMaintenanceDuty(Duty):
    """Low-frequency deterministic maintenance; semantic review stays elsewhere."""

    def __init__(self):
        super().__init__("memory_maintenance", "记忆维护", tick_interval_seconds=300)

    @staticmethod
    def _config() -> dict:
        try:
            from config import get_memory_config
            return get_memory_config()
        except Exception:
            return {
                "maintenance_enabled": True,
                "maintenance_interval_hours": 6,
                "maintenance_conflict_scan_batch": 10,
            }

    def _check_enabled(self, state: SchedulerState) -> bool:
        return bool(self._config().get("maintenance_enabled", True))

    def _should_fire(self, state: SchedulerState) -> bool:
        if state.agent_busy:
            return False
        # Avoid competing with a just-started user turn even if agent_busy has
        # not propagated yet.
        if state.last_user_message_time and state.now - state.last_user_message_time < 120:
            return False
        try:
            from brain.memory_maintenance import should_run_maintenance
            interval = self._config().get("maintenance_interval_hours", 6)
            return should_run_maintenance(interval)
        except Exception:
            return False

    def _create_worker(self, state: SchedulerState, **kwargs):
        from workers.memory_maintenance_worker import MemoryMaintenanceWorker
        cfg = self._config()
        return MemoryMaintenanceWorker(
            trigger=str(kwargs.get("trigger", "scheduled")),
            conflict_scan_batch=int(
                kwargs.get(
                    "conflict_scan_batch",
                    cfg.get("maintenance_conflict_scan_batch", 10),
                )
            ),
        )

    def _wire_worker(self, worker):
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)

    def _on_completed(self, result: dict):
        self.status.is_running = False
        status = result.get("status", "success")
        self.status.last_result = status
        self.status.last_result_text = (
            f"质量更新 {result.get('quality', {}).get('updated', 0)} 条，"
            f"过期状态 {result.get('current_states_expired', 0)} 条"
        )
        if status == "success":
            self.status.success_count += 1
        self._scheduler.memory_maintenance_completed.emit(result)
        self._scheduler.duty_completed.emit(self.name, self.status.last_result_text)

    def _on_failed(self, error: str):
        self.status.is_running = False
        self.status.last_result = "failed"
        self.status.last_result_text = str(error)
        self.status.fail_count += 1
        self._scheduler.memory_maintenance_failed.emit(str(error))
        self._scheduler.duty_failed.emit(self.name, str(error))

    @property
    def _scheduler(self):
        return self.__scheduler

    @_scheduler.setter
    def _scheduler(self, scheduler):
        self.__scheduler = scheduler


# ═══════════════════════════════════════════════════════════════
# HeartbeatDuty
# ═══════════════════════════════════════════════════════════════

class HeartbeatDuty(Duty):
    """心跳自检：用户沉默 N 分钟后触发。"""

    def __init__(self):
        super().__init__("heartbeat", "心跳自检", tick_interval_seconds=60)
        self._reset_time: float = 0.0
        self._fired_since_reset = False

    def on_user_message(self, state: SchedulerState):
        self._reset_time = state.now
        self._fired_since_reset = False

    def _check_enabled(self, state: SchedulerState) -> bool:
        cfg = state.heartbeat_config
        return cfg.get("enabled", True)

    def _should_fire(self, state: SchedulerState) -> bool:
        if self._fired_since_reset:
            return False
        if self._reset_time == 0.0:
            return False
        cfg = state.heartbeat_config
        delay_minutes = cfg.get("delay_minutes", 5)
        elapsed_minutes = (state.now - self._reset_time) / 60.0
        if elapsed_minutes < delay_minutes:
            return False
        # 活跃时段检查
        from datetime import datetime
        now_str = datetime.now().strftime("%H:%M")
        start = cfg.get("active_hours_start", "08:00")
        end = cfg.get("active_hours_end", "23:00")
        if not (start <= now_str <= end):
            return False
        return True

    def _create_worker(self, state: SchedulerState, **kwargs):
        from workers.heartbeat_worker import HeartbeatWorker
        self._fired_since_reset = True
        return HeartbeatWorker(state.session_id)

    def _wire_worker(self, worker):
        worker.response_ready.connect(self._on_response)
        worker.finished_silent.connect(self._on_silent)

    def _on_response(self, text: str):
        self.status.is_running = False
        self.status.last_result = "success"
        self.status.success_count += 1
        self._scheduler.heartbeat_response.emit(text)

    def _on_silent(self):
        self.status.is_running = False
        self.status.last_result = "skipped"
        self._scheduler.heartbeat_silent.emit()

    @property
    def _scheduler(self):
        return self.__scheduler

    @_scheduler.setter
    def _scheduler(self, s):
        self.__scheduler = s


# ═══════════════════════════════════════════════════════════════
# SmartReminderDuty
# ═══════════════════════════════════════════════════════════════

class SmartReminderDuty(Duty):
    """智能提醒：1 分钟轮询到期的提醒。"""

    def __init__(self):
        super().__init__("smart_reminder", "智能提醒", tick_interval_seconds=60)
        self._deferred_reminders = None
        self._defer_timer: Optional[QTimer] = None

    def _check_enabled(self, state: SchedulerState) -> bool:
        return True  # 始终轮询，_should_fire 检查是否有到期提醒

    def _should_fire(self, state: SchedulerState) -> bool:
        if state.agent_busy:
            # 推迟到 agent 空闲时
            return False
        rm = state.reminder_manager
        if rm is None:
            return False
        due = rm.get_due_reminders()
        if not due:
            return False
        # 标记已触发（持久化到文件，避免重复提醒）
        for d in due:
            rm.mark_triggered(d["id"])
        # 构建提醒文本
        names = [d.get("name", d.get("title", "")) for d in due]
        self._pending_names = names
        return True

    def _create_worker(self, state: SchedulerState, **kwargs):
        from workers.smart_reminder_worker import SmartReminderWorker
        names = self._pending_names
        self._pending_names = []
        gs = state.global_settings
        use_smart = getattr(gs, 'global_smart_reminder', False) if gs else False
        if use_smart:
            combined = "、".join(names)
            return SmartReminderWorker(combined, is_combined=(len(names) > 1))
        else:
            # 不使用智能提醒，直接用模板
            return None  # 不需要 worker, _on_no_worker_needed 处理

    def _on_no_worker_needed(self):
        """不使用 LLM 生成时直接用模板文本。"""
        names = self._pending_names
        self._pending_names = []
        if len(names) == 1:
            text = f"⏰ 莲心提醒：{names[0]}（记得抽空完成哦）"
        else:
            text = f"⏰ 莲心提醒：有{len(names)}个事情需要你注意哦～"
        self.status.is_running = False
        self.status.last_result = "success"
        self.status.success_count += 1
        self._scheduler.reminder_response.emit(text)

    def _wire_worker(self, worker):
        worker.finished.connect(self._on_finished)

    def _on_finished(self, text: str):
        self.status.is_running = False
        self.status.last_result = "success"
        self.status.success_count += 1
        self._scheduler.reminder_response.emit(text)

    @property
    def _scheduler(self):
        return self.__scheduler

    @_scheduler.setter
    def _scheduler(self, s):
        self.__scheduler = s


# ═══════════════════════════════════════════════════════════════
# Registration helper
# ═══════════════════════════════════════════════════════════════

def register_duty(scheduler: DutyScheduler, duty: Duty):
    duty._scheduler = scheduler
    scheduler.register(duty)
