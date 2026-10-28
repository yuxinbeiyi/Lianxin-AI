"""Bounded native-goal adapter with protocol-backed acceptance evidence."""

from __future__ import annotations

import copy
import os
import ntpath
import shlex
import threading
import time
from dataclasses import replace

from .backend import ControlResult, DECISION_DECLINE, SessionHandle, TaskSpec
from .codex_backend import (CodexExecutionBackend, CodexOutcomeUnknown,
                            _APPROVAL_REQUEST_METHODS, _checked_result)
from .goals import GoalContract, GoalContractError, GoalProgress, VerificationEvidence


GOAL_UPDATED = "goal_updated"
_NATIVE_TERMINAL = {"complete", "blocked", "budgetLimited", "usageLimited"}


class NativeGoalBackend(CodexExecutionBackend):
    """Only thread/goal/set activates work; never starts a continuation turn."""

    def __init__(self, *args, checkpoint=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._goal_progress = None
        self._goal_native = {}
        self._goal_halt = threading.Event()
        self._goal_monitor = None
        self._goal_thread = ""
        self._goal_clock = 0.0
        self._goal_prior_seconds = 0.0
        self._goal_checkpoint = checkpoint
        self._goal_stop_reason = ""
        self._goal_stop_confirmed = False
        self._goal_last_checkpoint = None
        self._goal_checkpoint_lock = threading.Lock()
        self._goal_turn_results = {}
        self._goal_overflow_turns = []
        self._goal_created_at = None
        self._goal_start_lock = threading.Lock()
        self._goal_seen_items = set()
        self._goal_seen_notifications = set()
        self._goal_checkpoint_failed = False

    def start(self, spec: TaskSpec, *, resume_progress=None) -> SessionHandle:
        with self._goal_start_lock:
            return self._start_goal(spec, resume_progress=resume_progress)

    def set_checkpoint(self, checkpoint):
        monitor = self._goal_monitor
        if monitor is not None and monitor.is_alive() and self._goal_halt.is_set():
            monitor.join(timeout=15)
        with self._control_lock:
            if self._goal_monitor is not None and self._goal_monitor.is_alive():
                raise RuntimeError("原生目标监督尚未结束，不能替换持久化回调")
            self._goal_checkpoint = checkpoint

    def _start_goal(self, spec: TaskSpec, *, resume_progress=None) -> SessionHandle:
        if not isinstance(spec.goal_contract, GoalContract):
            raise GoalContractError("原生目标执行必须提供已校验的目标契约")
        if bool(spec.resume_thread_id) != (resume_progress is not None):
            raise GoalContractError("恢复目标必须同时提供原 thread 和累计进度")
        progress = (GoalProgress.restore_for_resume(spec.goal_contract, resume_progress)
                    if resume_progress is not None else GoalProgress(spec.goal_contract))
        if resume_progress is not None and resume_progress.get("threadId") != spec.resume_thread_id:
            raise GoalContractError("累计进度不属于待恢复的 thread")
        if resume_progress is not None and (resume_progress.get("nativeGoal") or {}).get("createdAt") is None:
            raise GoalContractError("缺少原生目标创建身份，不能恢复")
        with self._control_lock:
            if self._active_turn or (self._goal_progress is not None and
                                     not self._goal_halt.is_set() and
                                     self._goal_native.get("status") == "active"):
                raise RuntimeError("已有原生目标在执行，不能并行启动")
            previous_monitor = self._goal_monitor
            self._goal_halt.set()
        if previous_monitor is not None and previous_monitor is not threading.current_thread():
            previous_monitor.join(timeout=15)
            if previous_monitor.is_alive():
                raise RuntimeError("上个目标尚未安全收尾")
        with self._control_lock:
            self._goal_progress = progress
            self._goal_native = {}
            self._goal_thread = ""
            self._goal_halt = threading.Event()
            self._goal_stop_reason = ""
            self._goal_stop_confirmed = False
            self._goal_last_checkpoint = None
            self._goal_turn_results = copy.deepcopy(resume_progress.get("turnResults") or {}) if resume_progress else {}
            self._goal_overflow_turns = list(resume_progress.get("overflowTurnIds") or []) if resume_progress else []
            self._goal_created_at = ((resume_progress.get("nativeGoal") or {}).get("createdAt")
                                     if resume_progress is not None else None)
            self._goal_seen_items = set()
            self._goal_seen_notifications = set()
            self._goal_checkpoint_failed = False
            self._goal_prior_seconds = progress.elapsed_seconds
            self._goal_clock = time.monotonic()
            self._native_spec = spec
            handle = super().start(replace(spec, goal_contract=None))
            monitor = threading.Thread(target=self._watch_goal, args=(self._generation,),
                                       name="lianxin-native-goal-budget", daemon=True)
            self._goal_monitor = monitor
            monitor.start()
            return handle

    def _rpc(self, method, params):
        if self._client is None:
            raise CodexOutcomeUnknown("原生目标连接不可用")
        return _checked_result(method, self._client.request(method, params, timeout=15))

    def _start(self, unused_spec: TaskSpec) -> SessionHandle:
        spec = self._native_spec
        self.set_context(spec.session_id, spec.turn)
        client, error = self._ensure_initialized()
        if error:
            raise RuntimeError(error)
        config = {**(spec.config or {}), "features.goals": True, "features.multi_agent": False}
        if spec.resume_thread_id:
            self.unarchive(spec.resume_thread_id)
            existing = self._rpc("thread/goal/get", {"threadId": spec.resume_thread_id}).get("goal")
            if not existing or existing.get("status") not in ("active", "paused"):
                raise GoalContractError("原生目标已结束或预算受限，不能恢复")
            if (existing.get("objective") != self._objective(spec) or
                    existing.get("tokenBudget") != spec.goal_contract.max_tokens):
                raise GoalContractError("原生目标与持久化契约不一致，拒绝覆盖")
            self._assert_inactive(self._rpc("thread/goal/set", {
                "threadId": spec.resume_thread_id, "status": "paused"}).get("goal"))
            result = self._rpc("thread/resume", {
                "threadId": spec.resume_thread_id, "cwd": spec.cwd,
                "sandbox": spec.sandbox, "approvalPolicy": spec.approval_policy,
                "excludeTurns": False, "config": config})
        else:
            result = self._rpc("thread/start", {
                "cwd": spec.cwd, "sandbox": spec.sandbox, "approvalPolicy": spec.approval_policy,
                "ephemeral": False, "config": config})
        thread_id = str((result.get("thread") or {}).get("id") or "")
        if not thread_id or (spec.resume_thread_id and thread_id != spec.resume_thread_id):
            raise CodexOutcomeUnknown("原生目标 thread 身份未确认")
        with self._scope_lock:
            self._goal_thread = self._scope_thread = thread_id
            self._starting_turn = True
            if spec.resume_thread_id:
                self._finished_turns.update(self._goal_progress.to_dict()["startedTurnIds"])
                for turn in (result.get("thread") or {}).get("turns", []):
                    if turn.get("id"):
                        self._finished_turns.add(turn["id"])
        if spec.resume_thread_id:
            self._stop_live_turn(thread_id)
        paused = self._rpc("thread/goal/set", {
            "threadId": thread_id, "objective": self._objective(spec), "status": "paused",
            "tokenBudget": spec.goal_contract.max_tokens}).get("goal")
        self._assert_inactive(paused)
        self._observe_goal(paused)
        if self._goal_progress.limit_reason():
            raise GoalContractError("累计预算已耗尽，不能恢复")
        self._save_checkpoint()
        goal = self._rpc("thread/goal/set", {"threadId": thread_id, "status": "active"}).get("goal")
        if not goal or goal.get("status") not in {"active", *_NATIVE_TERMINAL}:
            raise CodexOutcomeUnknown("原生目标激活未被确认")
        self._observe_goal(goal)
        self._save_checkpoint()
        return SessionHandle(thread_id, self._scope_turn, self.name, self._generation)

    @staticmethod
    def _objective(spec):
        criteria = "\n".join("%d. %s" % (index + 1, text)
                             for index, text in enumerate(spec.goal_contract.acceptance_criteria))
        checks = "\n".join("条件 %d [%s]：%s" % (check.criterion_index + 1, check.kind, check.command)
                           for check in spec.goal_contract.verification_checks)
        return (spec.goal_contract.objective + "\n\n任务上下文：\n" + spec.goal +
                "\n\n验收条件：\n" + criteria +
                ("\n\n用户指定验收命令（在当前工作目录逐项原样运行）：\n" + checks if checks else "") +
                "\n必须实际检查交付；验证失败先修复再验证。没有证据不要宣称已验收。"
                "每条验收命令必须单独调用命令工具，原样作为完整命令执行；"
                "不得与读取文件、git、echo、目录切换等操作合并，也不得追加其它命令或覆盖退出码。"
                "任何修改后重新运行全部验收命令，不得修改测试或验收标准来伪造通过。"
                "观察命令超时不等于命令终止；先检查原进程，不要重复启动。"
                "保持同一个目标；不能并行启动代理或自行增加预算。遇到真实阻塞时如实说明。")

    @staticmethod
    def _assert_inactive(goal):
        if not isinstance(goal, dict) or goal.get("status") not in {"paused", *_NATIVE_TERMINAL}:
            raise CodexOutcomeUnknown("未确认原生目标停止自动续跑")

    def _observe_goal(self, goal):
        if not isinstance(goal, dict) or goal.get("threadId") != self._goal_thread:
            raise CodexOutcomeUnknown("原生目标快照身份不匹配")
        if goal.get("status") not in {"active", "paused", *_NATIVE_TERMINAL}:
            raise CodexOutcomeUnknown("未知原生目标状态")
        spec = getattr(self, "_native_spec", None)
        if spec is not None and (goal.get("objective") != self._objective(spec) or
                                 goal.get("tokenBudget") != spec.goal_contract.max_tokens):
            raise CodexOutcomeUnknown("原生目标或预算已被替换，拒绝继续")
        created_at = goal.get("createdAt")
        if self._goal_created_at is not None and created_at != self._goal_created_at:
            raise CodexOutcomeUnknown("原生目标身份已改变，拒绝复用累计计账")
        with self._scope_lock:
            self._goal_created_at = created_at
            self._goal_progress.observe_usage(goal.get("tokensUsed", 0), goal.get("timeUsedSeconds", 0))
            previous = self._goal_native
            regressed = (goal.get("tokensUsed", 0) < previous.get("tokensUsed", 0) or
                         (previous.get("status") in _NATIVE_TERMINAL and goal.get("status") == "active"))
            if not regressed:
                self._goal_native = copy.deepcopy(goal)

    def progress_snapshot(self):
        with self._scope_lock:
            if self._goal_progress is None:
                return None
            data = self._goal_progress.to_dict()
            data.update(threadId=self._goal_thread, generation=self._generation,
                        nativeGoal=copy.deepcopy(self._goal_native),
                        stopConfirmed=self._goal_stop_confirmed,
                        turnResults=copy.deepcopy(self._goal_turn_results),
                        overflowTurnIds=list(self._goal_overflow_turns))
            if self._goal_checkpoint_failed:
                data.update(state="failed", endReason="checkpoint_failed", checkpointFailed=True)
            return data

    def _save_checkpoint(self):
        with self._goal_checkpoint_lock:
            snapshot = self.progress_snapshot()
            if self._goal_checkpoint is not None and snapshot != self._goal_last_checkpoint:
                try:
                    if self._goal_checkpoint(copy.deepcopy(snapshot)) is not True:
                        raise RuntimeError("目标进度持久化未确认，停止执行")
                except Exception:
                    self._goal_checkpoint_failed = True
                    self._goal_stop_reason = "checkpoint_failed"
                    raise
                self._goal_last_checkpoint = snapshot

    def _accept_scope(self, params, method=""):
        if self._goal_progress is None:
            return super()._accept_scope(params, method)
        if params.get("threadId") != self._goal_thread or not self._goal_thread:
            return False
        turn_id = str(params.get("turnId") or (params.get("turn") or {}).get("id") or "")
        if method == "thread/goal/updated":
            if turn_id and turn_id in self._finished_turns:
                return False
            return not turn_id or turn_id == self._scope_turn
        if method in ("item/started", "item/completed"):
            identity = (turn_id, str((params.get("item") or {}).get("id") or ""), method)
            if identity in self._goal_seen_notifications:
                return False
        if method == "turn/started" and turn_id and turn_id != self._scope_turn:
            if turn_id in self._finished_turns or self._active_turn:
                return False
            self._scope_turn = ""
            self._starting_turn = True
        accepted = super()._accept_scope(params, method)
        if accepted and method == "turn/started":
            try:
                self._goal_progress.begin_turn(turn_id)
            except GoalContractError:
                if turn_id not in self._goal_overflow_turns:
                    self._goal_overflow_turns.append(turn_id)
                self._goal_stop_reason = self._goal_progress.limit_reason() or "unexpected_turn"
        elif accepted and method == "turn/completed":
            self._goal_turn_results[turn_id] = str((params.get("turn") or {}).get("status") or "")
            if self._goal_progress.current_turn_id == turn_id:
                self._goal_progress.finish_turn(turn_id)
            if (params.get("turn") or {}).get("status") != "completed":
                self._goal_stop_reason = "turn_not_completed"
        if accepted and method in ("item/started", "item/completed"):
            self._goal_seen_notifications.add(identity)
            self._observe_verification(params, method)
        return accepted

    def _observe_verification(self, params, method):
        spec = getattr(self, "_native_spec", None)
        if spec is None or not spec.goal_contract.verification_checks:
            return
        item = params.get("item") or {}
        item_type = item.get("type")
        if item_type in ("userMessage", "agentMessage", "reasoning"):
            return
        item_id = str(item.get("id") or "")
        turn_id = str(params.get("turnId") or "")
        if not item_id or turn_id != self._goal_progress.current_turn_id:
            return
        identity = (turn_id, item_id, method)
        if identity in self._goal_seen_items:
            return
        self._goal_seen_items.add(identity)
        command = self._verification_command(str(item.get("command") or "").strip())
        cwd = str(item.get("cwd") or "")
        local = bool(cwd) and os.path.normcase(os.path.abspath(cwd)) == os.path.normcase(os.path.abspath(spec.cwd))
        checks = [check for check in spec.goal_contract.verification_checks
                  if local and command == check.command]
        if item_type != "commandExecution" or not checks:
            self._goal_progress.invalidate_evidence()
            return
        if method != "item/completed":
            for check in checks:
                self._goal_progress.add_evidence(VerificationEvidence(
                    check.criterion_index, check.kind, "inconclusive", "验收命令运行中",
                    "codex:%s:%s" % (item_id[:150], command[:700]), turn_id))
            return
        exit_code = item.get("exitCode")
        state = ("passed" if item.get("status") == "completed" and type(exit_code) is int and exit_code == 0
                 else "failed" if type(exit_code) is int or item.get("status") in ("failed", "declined")
                 else "inconclusive")
        for check in checks:
            self._goal_progress.add_evidence(VerificationEvidence(
                check.criterion_index, check.kind, state,
                "命令状态=%s，退出码=%s；%s" % (item.get("status"), exit_code,
                    str(item.get("aggregatedOutput") or "")[-700:]),
                "codex:%s:%s" % (item_id[:150], command[:700]), turn_id))

    @staticmethod
    def _verification_command(command):
        try:
            arguments = shlex.split(command)
        except ValueError:
            return command
        if len(arguments) < 3:
            return command
        shell = ntpath.basename(arguments[0]).lower()
        flags = [argument.lower() for argument in arguments[1:-1]]
        if shell in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
            if flags[-1:] == ["-command"] and all(
                    flag in ("-noprofile", "-noninteractive", "-nologo") for flag in flags[:-1]):
                return arguments[-1].strip()
        if shell in ("bash", "sh", "bash.exe", "sh.exe") and flags in (["-c"], ["-lc"]):
            return arguments[-1].strip()
        return command

    def _on_scoped_notification(self, message):
        if message.get("method") == "thread/goal/updated":
            params = message.get("params") or {}
            if self._accept_scope(params, "thread/goal/updated"):
                try:
                    self._observe_goal(params.get("goal"))
                    self._emit(GOAL_UPDATED, self._goal_thread, params.get("turnId"),
                               goal=self.progress_snapshot())
                except Exception:
                    self._goal_stop_reason = "invalid_goal_notification"
            return
        super()._on_scoped_notification(message)
        params = message.get("params") or {}
        if (message.get("method") in ("turn/started", "turn/completed", "item/completed") and
                params.get("threadId") == self._goal_thread and
                str(params.get("turnId") or (params.get("turn") or {}).get("id") or "") == self._scope_turn):
            self._emit(GOAL_UPDATED, self._goal_thread, self._scope_turn,
                       goal=self.progress_snapshot())

    def _on_scoped_server_request(self, message):
        method = message.get("method")
        if self._goal_halt.is_set() and method in _APPROVAL_REQUEST_METHODS:
            return self._decision_payload(method, DECISION_DECLINE), None
        return super()._on_scoped_server_request(message)

    def _watch_goal(self, generation):
        try:
            while not self._goal_halt.wait(0.1):
                with self._scope_lock:
                    if generation != self._generation:
                        return
                    progress = self._goal_progress
                    progress.observe_usage(progress.tokens_used, self._goal_prior_seconds +
                                           time.monotonic() - self._goal_clock)
                    reason = self._goal_stop_reason or progress.limit_reason()
                    if self._client is not None and not self._client.alive():
                        reason = reason or "backend_exited"
                    status = self._goal_native.get("status")
                    idle = not self._active_turn
                    if status in _NATIVE_TERMINAL and idle and reason == "turn_limit":
                        reason = ""
                    if status in _NATIVE_TERMINAL and idle and not reason:
                        if status == "complete":
                            progress.request_completion()
                        else:
                            progress.finish({"blocked": "blocked", "budgetLimited": "budget_limited",
                                             "usageLimited": "usage_limited"}[status], status)
                        self._goal_halt.set()
                    elif status == "paused" and idle:
                        reason = reason or "native_paused"
                self._save_checkpoint()
                if reason:
                    self._stop_goal(reason, expected_generation=generation)
                    return
                if self._goal_halt.is_set():
                    self._emit(GOAL_UPDATED, self._goal_thread, self._scope_turn,
                               goal=self.progress_snapshot())
        except Exception as exc:
            self._log("native-goal-monitor", str(exc))
            try:
                self._stop_goal("goal_monitor_failed", expected_generation=generation)
            except Exception as stop_error:
                self._log("native-goal-stop-uncertain", str(stop_error))

    def _stop_live_turn(self, thread_id):
        thread = self._rpc("thread/read", {"threadId": thread_id, "includeTurns": True}).get("thread") or {}
        active = [turn for turn in thread.get("turns", []) if turn.get("status") == "inProgress"]
        if len(active) > 1:
            raise CodexOutcomeUnknown("原生目标出现多个活动 turn")
        for turn in active:
            self._rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn["id"]})
            self._wait_turn_idle(self._client, thread_id, turn["id"])
        thread = self._rpc("thread/read", {"threadId": thread_id, "includeTurns": True}).get("thread") or {}
        if any(turn.get("status") == "inProgress" for turn in thread.get("turns", [])):
            raise CodexOutcomeUnknown("停止后仍存在活动 turn")

    def _stop_goal(self, reason, expected_generation=None):
        with self._control_lock:
            if expected_generation is not None and expected_generation != self._generation:
                return
            self._goal_halt.set()
            if self._goal_stop_confirmed:
                return
            try:
                self._assert_inactive(self._rpc("thread/goal/set", {
                    "threadId": self._goal_thread, "status": "paused"}).get("goal"))
                paused = self._rpc("thread/goal/get", {"threadId": self._goal_thread}).get("goal")
                self._assert_inactive(paused)
                self._observe_goal(paused)
                self._stop_live_turn(self._goal_thread)
                self._goal_stop_confirmed = True
                state = ("budget_limited" if reason in ("turn_limit", "token_budget_observed", "time_limit")
                         else "stopped" if reason in ("user", "shutdown", "native_paused") else "failed")
                self._goal_progress.finish(state, reason)
                self._save_checkpoint()
            except Exception:
                self._goal_progress.finish("failed", "native_stop_unconfirmed")
                CodexExecutionBackend.shutdown(self)
                try:
                    self._save_checkpoint()
                except Exception:
                    pass
                raise
            finally:
                self._emit(GOAL_UPDATED, self._goal_thread, self._scope_turn,
                           goal=self.progress_snapshot())

    def interrupt(self, handle):
        with self._control_lock:
            with self._scope_lock:
                if handle.generation != self._generation or handle.thread_id != self._scope_thread:
                    return ControlResult("stale", handle.thread_id, handle.turn_id, "目标或 turn 已切换")
            try:
                self._stop_goal("user", expected_generation=handle.generation)
                return ControlResult("accepted", handle.thread_id, handle.turn_id, "目标已暂停，活动轮已停止")
            except Exception as exc:
                return ControlResult("uncertain", handle.thread_id, handle.turn_id, str(exc))

    def shutdown(self):
        self._goal_halt.set()
        terminal_idle = self._goal_native.get("status") in _NATIVE_TERMINAL and not self._active_turn
        if self._goal_thread and not terminal_idle and self._client is not None and self._client.alive():
            try:
                self._stop_goal("shutdown")
            except Exception as exc:
                self._log("native-goal-shutdown", str(exc))
        super().shutdown()
