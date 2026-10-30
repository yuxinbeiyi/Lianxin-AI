"""Bounded, typed d3-ultra decisions without tool execution privileges."""

from __future__ import annotations

import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from utils.decision_credentials import get_api_key


MODEL = "vllm-sr/d3-ultra"
ENDPOINT = "https://decision.vllm-sr.ai/v1/systemone"


class DecisionError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def redact_context(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"(?i)\b(?:sk-[a-z0-9_-]{8,}|bearer\s+[a-z0-9._-]{8,})", "[密钥已隐藏]", value)
    value = re.sub(
        r"(?i)((?:密码|口令|api[_ -]?key|access[_ -]?token|password|secret)\s*[:：=]\s*)[^\s,，;；]+",
        r"\1[已隐藏]", value,
    )
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已隐藏]", value)
    return re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[电话已隐藏]", value)


def _number(value, minimum=0.0, maximum=1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionError("invalid_number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise DecisionError("invalid_number")
    return number


def _redact_state(value):
    if isinstance(value, str):
        return redact_context(value)
    if isinstance(value, dict):
        return {
            key: "[已隐藏]" if re.fullmatch(
                r"(?i)password|secret|api[_ -]?key|access[_ -]?token|密码|口令", str(key),
            ) else _redact_state(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_state(item) for item in value]
    return value


def validate_answers(payload: dict, questions: dict, expected_model: str = MODEL) -> dict:
    if not isinstance(payload, dict) or payload.get("model") != expected_model:
        raise DecisionError("unexpected_model")
    answers = payload.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise DecisionError("missing_answers")
    for name, question in questions.items():
        answer = answers[name]
        kind = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise DecisionError("invalid_answer_type")
        if kind == "noul":
            _number(answer.get("noul"))
            continue
        criteria = question["criteria"]
        options = list(criteria) if kind == "choice" else [str(index) for index in range(len(criteria))]
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(options):
            raise DecisionError("invalid_distribution")
        values = {option: _number(probabilities[option]) for option in options}
        if not math.isclose(sum(values.values()), 1.0, abs_tol=0.02):
            raise DecisionError("invalid_distribution")
        if kind == "choice":
            selected = answer.get("choice")
            if selected not in values or values[selected] < max(values.values()) - 0.001:
                raise DecisionError("invalid_choice")
        elif kind == "score":
            score = _number(answer.get("score"), maximum=len(options) - 1)
            expected = sum(index * values[option] for index, option in enumerate(options))
            if not math.isclose(score, expected, abs_tol=0.05):
                raise DecisionError("invalid_score")
        else:
            raise DecisionError("unsupported_question")
    return answers


class DecisionClient:
    def __init__(self, config: dict):
        self.timeout = config["timeout_seconds"]
        self.provider = config.get("provider", "api")
        self.model = MODEL
        self.endpoint = ENDPOINT
        self.api_key = ""
        if self.provider == "local":
            from brain import decision_local
            try:
                decision_local.ensure_ready(config)
            except (RuntimeError, ValueError) as exc:
                raise DecisionError(str(exc)) from None
            except OSError:
                raise DecisionError("local_start_failed") from None
            self.model = decision_local.LOCAL_MODEL
            self.endpoint = decision_local.endpoint(config)
        else:
            try:
                self.api_key = get_api_key()
            except RuntimeError:
                raise DecisionError("credential_unreadable") from None
            if not self.api_key:
                raise DecisionError("missing_api_key")
        self.deadline = time.monotonic() + config["budget_seconds"]
        self.on_event = None

    def _notify(self, event: dict):
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def decide(self, state: dict, questions: dict) -> dict:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DecisionError("budget_exhausted")
        state_text = json.dumps(_redact_state(state), ensure_ascii=False)
        if len(state_text) > 10000:
            raise DecisionError("context_too_long")
        body = json.dumps({"model": self.model, "state": state_text, "questions": questions}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        opener = build_opener(_NoRedirect(), ProxyHandler({})) if self.provider == "local" else build_opener(_NoRedirect())
        stage = "回复前决策" if "next_step" in questions else "回忆证据评估"
        details = {"model": self.model, "provider": self.provider, "stage": stage}
        started = time.monotonic()
        preview = "调用失败：unexpected_error；本轮按既有策略回退，不使用未核验决策。"
        is_error = True
        self._notify({**details, "kind": "call"})
        try:
            try:
                with opener.open(request, timeout=min(self.timeout, remaining)) as response:
                    raw = response.read(65537)
                if len(raw) > 65536:
                    raise DecisionError("response_too_large")
                payload = json.loads(raw)
            except HTTPError as exc:
                raise DecisionError(f"http_{exc.code}") from None
            except (URLError, TimeoutError, OSError):
                raise DecisionError("transport_error") from None
            except (ValueError, UnicodeError):
                raise DecisionError("invalid_json") from None
            if time.monotonic() > self.deadline:
                raise DecisionError("budget_exhausted")
            answers = validate_answers(payload, questions, self.model)
            preview = "接口响应校验通过"
            if "next_step" in answers:
                labels = {
                    "reply": "直接回复", "memory_read": "先查询记忆", "web_read": "需要外部查证",
                    "requested_action": "明确执行请求", "clarify": "需要澄清",
                }
                preview += "；建议：" + labels.get(answers["next_step"]["choice"], "其他选项")
            if "match" in answers and "ambiguous" in answers:
                preview += (f"；证据匹配期望分 {answers['match']['score']:.2f}/4"
                            f"，歧义概率 {answers['ambiguous']['noul']:.0%}")
            is_error = False
            return answers
        except DecisionError as exc:
            preview = f"调用失败：{exc}；本轮按既有策略回退，不使用未核验决策。"
            raise
        finally:
            self._notify({**details, "kind": "result", "preview": preview, "is_error": is_error,
                          "elapsedMs": (time.monotonic() - started) * 1000})
