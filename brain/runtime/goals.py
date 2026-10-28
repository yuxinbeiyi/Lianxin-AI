"""Explicit bounded goal contracts and observed verification evidence."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass


class GoalContractError(ValueError):
    pass


def _text(value, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise GoalContractError("%s 必须是 1～%d 字符的非空文本" % (name, limit))
    return value.strip()


def _integer(value, name: str, minimum: int, maximum=None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise GoalContractError("%s 必须是范围内的整数" % name)
    return value


def _seconds(value) -> float:
    try:
        valid = type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise GoalContractError("累计执行秒数必须是有限非负数")
    return float(value)


def goal_mode_status() -> dict:
    return {
        "available": True,
        "driver": "native",
        "code": "native_goal_explicit_beta",
        "reason": "仅显式目标契约启用；普通任务仍单轮，完整桌面验收尚未完成",
    }


@dataclass(frozen=True)
class VerificationCheck:
    criterion_index: int
    command: str
    kind: str = "test"

    def __post_init__(self) -> None:
        _integer(self.criterion_index, "验收条件序号", 0, 11)
        object.__setattr__(self, "command", _text(self.command, "验收命令", 2000))
        if self.kind not in ("test", "build"):
            raise GoalContractError("自动验收只接受 test 或 build 命令")

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) - {"criterionIndex", "command", "kind"}:
            raise GoalContractError("验收命令必须是仅包含已知字段的对象")
        return cls(data.get("criterionIndex"), data.get("command"), data.get("kind", "test"))

    def to_dict(self):
        return {"criterionIndex": self.criterion_index, "command": self.command, "kind": self.kind}


@dataclass(frozen=True)
class GoalContract:
    objective: str
    acceptance_criteria: tuple[str, ...]
    max_turns: int = 8
    max_tokens: int = 40_000
    max_elapsed_seconds: int = 900
    driver: str = "native"
    verification_checks: tuple[VerificationCheck, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "objective", _text(self.objective, "目标", 2000))
        if (not isinstance(self.acceptance_criteria, (list, tuple))
                or not 1 <= len(self.acceptance_criteria) <= 12):
            raise GoalContractError("验收条件必须是包含 1～12 项的列表")
        criteria = tuple(_text(item, "验收条件", 500) for item in self.acceptance_criteria)
        if len(set(criteria)) != len(criteria):
            raise GoalContractError("验收条件不能重复")
        object.__setattr__(self, "acceptance_criteria", criteria)
        _integer(self.max_turns, "最大执行轮数", 1, 16)
        _integer(self.max_tokens, "累计 token 预算", 1, 200_000)
        _integer(self.max_elapsed_seconds, "最长执行秒数", 60, 3600)
        if self.driver != "native":
            raise GoalContractError("只允许 native 唯一驱动，不叠加 managed 续跑")
        if not isinstance(self.verification_checks, (list, tuple)) or len(self.verification_checks) > 12:
            raise GoalContractError("验收命令最多 12 项")
        checks = tuple(check if isinstance(check, VerificationCheck) else VerificationCheck.from_dict(check)
                       for check in self.verification_checks)
        if (any(check.criterion_index >= len(criteria) for check in checks) or
                len({check.criterion_index for check in checks}) != len(checks)):
            raise GoalContractError("每项验收条件最多绑定一个命令，且序号必须存在")
        object.__setattr__(self, "verification_checks", checks)

    @classmethod
    def from_dict(cls, data: dict) -> GoalContract:
        if not isinstance(data, dict):
            raise GoalContractError("目标契约必须是对象")
        allowed = {"objective", "acceptanceCriteria", "maxTurns", "maxTokens",
                   "maxElapsedSeconds", "driver", "verificationChecks"}
        if set(data) - allowed:
            raise GoalContractError("目标契约包含未知字段")
        return cls(
            objective=data.get("objective"),
            acceptance_criteria=data.get("acceptanceCriteria"),
            max_turns=data.get("maxTurns", 8),
            max_tokens=data.get("maxTokens", 40_000),
            max_elapsed_seconds=data.get("maxElapsedSeconds", 900),
            driver=data.get("driver", "native"),
            verification_checks=data.get("verificationChecks", ()),
        )

    def to_dict(self) -> dict:
        data = {
            "objective": self.objective,
            "acceptanceCriteria": list(self.acceptance_criteria),
            "maxTurns": self.max_turns,
            "maxTokens": self.max_tokens,
            "maxElapsedSeconds": self.max_elapsed_seconds,
            "driver": self.driver,
        }
        if self.verification_checks:
            data["verificationChecks"] = [check.to_dict() for check in self.verification_checks]
        return data


@dataclass(frozen=True)
class VerificationEvidence:
    criterion_index: int
    kind: str
    status: str
    summary: str
    source: str
    turn_id: str

    def __post_init__(self) -> None:
        _integer(self.criterion_index, "验收条件序号", 0, 11)
        if self.kind not in ("test", "build", "review"):
            raise GoalContractError("证据类型必须是 test、build 或 review")
        if self.status not in ("passed", "failed", "inconclusive"):
            raise GoalContractError("验证状态必须是 passed、failed 或 inconclusive")
        object.__setattr__(self, "summary", _text(self.summary, "证据摘要", 1000))
        object.__setattr__(self, "source", _text(self.source, "证据来源", 1000))
        object.__setattr__(self, "turn_id", _text(self.turn_id, "turnId", 200))

    @classmethod
    def from_dict(cls, data: dict) -> VerificationEvidence:
        if not isinstance(data, dict):
            raise GoalContractError("验证证据必须是对象")
        allowed = {"criterionIndex", "kind", "status", "summary", "source", "turnId"}
        if set(data) - allowed:
            raise GoalContractError("验证证据包含未知字段")
        return cls(data.get("criterionIndex"), data.get("kind"), data.get("status"),
                   data.get("summary"), data.get("source"), data.get("turnId"))

    def to_dict(self) -> dict:
        return {"criterionIndex": self.criterion_index, "kind": self.kind,
                "status": self.status, "summary": self.summary,
                "source": self.source, "turnId": self.turn_id}


class GoalProgress:
    TERMINAL_STATES = frozenset({
        "completed", "failed", "stopped", "blocked", "budget_limited", "usage_limited",
    })

    def __init__(self, contract: GoalContract) -> None:
        if not isinstance(contract, GoalContract):
            raise GoalContractError("进度记录需要有效的目标契约")
        self.contract = contract
        self.goal_id = "g-" + uuid.uuid4().hex
        self.state = "active"
        self.tokens_used = 0
        self.elapsed_seconds = 0.0
        self.end_reason = ""
        self.current_turn_id = ""
        self._started_turns: list[str] = []
        self._completed_turns: set[str] = set()
        self._evidence: dict[int, VerificationEvidence] = {}

    def begin_turn(self, turn_id: str) -> bool:
        turn_id = _text(turn_id, "turnId", 200)
        if self.state != "active":
            raise GoalContractError("非活动目标不能启动执行轮")
        if turn_id in self._started_turns:
            return False
        if self.current_turn_id:
            raise GoalContractError("已有执行轮，不能并行启动")
        if self.limit_reason():
            raise GoalContractError("目标预算已耗尽，不能继续执行")
        self._started_turns.append(turn_id)
        self.current_turn_id = turn_id
        self._evidence.clear()
        return True

    @classmethod
    def restore_for_resume(cls, contract: GoalContract, snapshot: dict):
        if not isinstance(snapshot, dict) or snapshot.get("contract") != contract.to_dict():
            raise GoalContractError("持久化进度与目标契约不一致")
        if snapshot.get("state") not in ("active", "stopped"):
            raise GoalContractError("只有活动中断或显式停止的目标可恢复")
        started = snapshot.get("startedTurnIds")
        completed = snapshot.get("completedTurnIds")
        if not isinstance(started, list) or not isinstance(completed, list):
            raise GoalContractError("持久化执行轮列表无效")
        started = [_text(turn, "turnId", 200) for turn in started]
        completed = [_text(turn, "turnId", 200) for turn in completed]
        if (len(set(started)) != len(started) or len(set(completed)) != len(completed)
                or len(started) > contract.max_turns or not set(completed).issubset(started)):
            raise GoalContractError("持久化执行轮身份或计数无效")
        if (snapshot.get("turnsStarted") != len(started) or
                snapshot.get("turnsCompleted") != len(completed)):
            raise GoalContractError("持久化执行轮计数不一致")
        current = snapshot.get("currentTurnId")
        if current and (not started or current != started[-1] or current in completed):
            raise GoalContractError("持久化活动轮身份无效")
        progress = cls(contract)
        progress.goal_id = _text(snapshot.get("goalId"), "goalId", 200)
        progress._started_turns = started
        progress._completed_turns = set(completed)
        progress.observe_usage(snapshot.get("tokensUsed"), snapshot.get("elapsedSeconds"))
        if progress.limit_reason():
            raise GoalContractError("累计预算已耗尽，不能恢复")
        return progress

    def finish_turn(self, turn_id: str) -> bool:
        if turn_id in self._completed_turns:
            return False
        if turn_id != self.current_turn_id or not turn_id:
            raise GoalContractError("执行轮不属于当前目标进度")
        if self.state in self.TERMINAL_STATES:
            return False
        self._completed_turns.add(turn_id)
        self.current_turn_id = ""
        return True

    def observe_usage(self, cumulative_tokens: int, cumulative_seconds=None) -> None:
        tokens = _integer(cumulative_tokens, "累计已用 token", 0)
        seconds = self.elapsed_seconds if cumulative_seconds is None else _seconds(cumulative_seconds)
        self.tokens_used = max(self.tokens_used, tokens)
        self.elapsed_seconds = max(self.elapsed_seconds, seconds)

    def limit_reason(self) -> str:
        if not self.current_turn_id and len(self._started_turns) >= self.contract.max_turns:
            return "turn_limit"
        if self.tokens_used >= self.contract.max_tokens:
            return "token_budget_observed"
        if self.elapsed_seconds >= self.contract.max_elapsed_seconds:
            return "time_limit"
        return ""

    def add_evidence(self, evidence: VerificationEvidence) -> None:
        if not isinstance(evidence, VerificationEvidence):
            raise GoalContractError("验证证据类型无效")
        if self.state in self.TERMINAL_STATES:
            raise GoalContractError("终态目标不再接受验证证据")
        if evidence.criterion_index >= len(self.contract.acceptance_criteria):
            raise GoalContractError("验证证据没有对应的验收条件")
        if not self._started_turns or evidence.turn_id != self._started_turns[-1]:
            raise GoalContractError("验证证据不属于最新执行轮")
        self._evidence[evidence.criterion_index] = evidence

    @property
    def verification_passed(self) -> bool:
        return all(index in self._evidence and self._evidence[index].status == "passed"
                   for index in range(len(self.contract.acceptance_criteria)))

    def invalidate_evidence(self) -> None:
        if self.state not in self.TERMINAL_STATES:
            self._evidence.clear()

    def request_completion(self) -> bool:
        if self.state == "completed":
            return True
        if self.state in self.TERMINAL_STATES:
            raise GoalContractError("已结束的目标不能重新完成")
        if self.current_turn_id:
            raise GoalContractError("当前执行轮尚未结束")
        passed = self.verification_passed
        self.state = "completed" if passed else "awaiting_verification"
        self.end_reason = "acceptance_verified" if passed else "verification_missing_or_failed"
        return passed

    def finish(self, state: str, reason: str) -> bool:
        if state not in self.TERMINAL_STATES - {"completed"}:
            raise GoalContractError("结束状态无效；完成必须经过证据验收")
        reason = _text(reason, "结束原因", 500)
        if self.state in self.TERMINAL_STATES:
            return False
        self.state = state
        self.end_reason = reason
        return True

    def to_dict(self) -> dict:
        return {
            "goalId": self.goal_id, "contract": self.contract.to_dict(), "state": self.state,
            "turnsStarted": len(self._started_turns),
            "turnsCompleted": len(self._completed_turns),
            "tokensUsed": self.tokens_used, "elapsedSeconds": self.elapsed_seconds,
            "currentTurnId": self.current_turn_id, "endReason": self.end_reason,
            "verificationPassed": self.verification_passed,
            "startedTurnIds": list(self._started_turns),
            "completedTurnIds": [turn for turn in self._started_turns if turn in self._completed_turns],
            "evidence": [self._evidence[index].to_dict() for index in sorted(self._evidence)],
        }
