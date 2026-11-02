"""Domain-neutral data contracts for filling conversational context gaps."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass
class BackgroundEvidence:
    value: str
    source: str
    source_id: str = ""
    observed_at: str = ""
    valid_until: str = ""
    confidence: float | None = None
    status: str = "candidate"

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value not in (None, "")}


@dataclass
class BackgroundSlot:
    key: str
    description: str
    required: bool = True
    freshness: str = "any"
    source_policy: tuple[str, ...] = ("current_message", "session", "memory")
    candidates: list[BackgroundEvidence] = field(default_factory=list)
    resolved_value: str = ""
    confirmation_required: bool = False
    conflict: bool = False

    def to_dict(self) -> dict:
        value = asdict(self)
        value["candidates"] = [item.to_dict() for item in self.candidates]
        value["source_policy"] = list(self.source_policy)
        return value


class BackgroundAdapter(Protocol):
    def prompt(self, context: "BackgroundContext", decision: str) -> str: ...
    def before_tool(self, plan: "BackgroundRecallPlan", name: str, arguments: dict) -> str: ...
    def followup(self, plan: "BackgroundRecallPlan", agent) -> str: ...
    def guard_reply(self, plan: "BackgroundRecallPlan", agent, text: str) -> str: ...
    def remember_confirmation(self, plan: "BackgroundRecallPlan", agent, text: str) -> None: ...


@dataclass
class BackgroundContext:
    purpose: str
    slots: list[BackgroundSlot]
    retrieval_query: str = ""
    search_terms: list[str] = field(default_factory=list)
    evidence: list[BackgroundEvidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "slots": [slot.to_dict() for slot in self.slots],
            "retrieval_query": self.retrieval_query,
            "search_terms": list(self.search_terms),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass
class BackgroundRecallPlan:
    client: object
    state: dict
    choice: str
    context: BackgroundContext
    adapter: BackgroundAdapter
    max_retrievals: int = 2
    attempts: int = 0
    evaluated: bool = False
    available_tools: frozenset | None = None
    retrieval_tools: frozenset = field(default_factory=lambda: frozenset({"search_graph_memory"}))

    @property
    def domain(self) -> str:
        return self.context.purpose

    def prompt(self) -> str:
        return self.adapter.prompt(self.context, self.choice)

    def before_tool(self, name: str, arguments: dict) -> str:
        if self.available_tools is not None and name not in self.available_tools:
            return "该工具本轮未开放或已被禁用；不得因背景建议而增加权限。"
        if name in self.retrieval_tools:
            if self.attempts >= self.max_retrievals:
                return f"背景检索已达到本轮上限 {self.max_retrievals}，请基于候选澄清或直接回应。"
            self.attempts += 1
        return self.adapter.before_tool(self, name, arguments)

    def followup(self, agent) -> str:
        return self.adapter.followup(self, agent)

    def guard_reply(self, agent, text: str) -> str:
        return self.adapter.guard_reply(self, agent, text)

    def remember_confirmation(self, agent, text: str) -> None:
        self.adapter.remember_confirmation(self, agent, text)
