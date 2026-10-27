"""Bounded model requests and privacy-safe diagnostics for Bilibili surfing."""

import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from openai import OpenAI


class SurfModelError(Exception):
    def __init__(self, diagnostic):
        super().__init__(diagnostic["reason"])
        self.diagnostic = diagnostic


def safe_label(value, maximum=96):
    text = str(value or "")
    return text if re.fullmatch(r"[A-Za-z0-9_.:/-]{1," + str(maximum) + "}", text) else ""


def error_diagnostic(error, client, model, stage, attempt, elapsed_ms):
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else body
    detail = detail if isinstance(detail, dict) else {"message": detail} if isinstance(detail, str) else {}
    message = str(detail.get("message") or "")
    code = str(detail.get("code") or detail.get("type") or "")
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    signal = (code + " " + message).casefold()
    compact_code = re.sub(r"[^a-z0-9]", "", code.casefold())
    reason = "model_error"
    temporary = False
    quota_code = any(term in compact_code for term in (
        "quotaexceeded", "insufficientquota", "insufficientuserquota", "outofquota"))
    if quota_code or any(term in signal for term in ("insufficient_quota", "insufficient_user_quota", "out_of_quota",
            "quota_exceeded", "current quota", "insufficient balance",
            "insufficient funds", "credit balance", "billing", "额度不足", "额度已用尽", "余额不足", "配额耗尽")):
        reason = "model_quota_error"
    elif any(term in signal for term in ("no available channel", "channel_not_found", "channel unavailable",
            "model_not_found", "无可用渠道", "没有可用渠道", "渠道不可用")):
        reason = "model_channel_error"
    elif status in (401, 403):
        reason = "model_auth_error"
    elif status == 429:
        reason = "model_rate_limited"
        temporary = any(term in signal for term in ("rate_limit", "rate limit", "too many requests",
            "requests per", "tokens per", "请求频率", "并发限制", "请求过于频繁"))
    elif "timeout" in type(error).__name__.casefold():
        reason = "model_timeout"
    descriptions = {
        "model_error": "模型请求异常，服务端细节未能安全归类",
        "model_quota_error": "服务端提示额度、余额或计费配额不足",
        "model_channel_error": "服务端提示模型或渠道不可用",
        "model_auth_error": "服务端拒绝模型访问授权",
        "model_rate_limited": "服务端返回429，暂时限流" if temporary else "服务端返回429，具体限制未明确",
        "model_timeout": "等待模型响应超时",
    }
    try:
        host = urlsplit(str(getattr(client, "base_url", ""))).hostname or ""
    except ValueError:
        host = ""
    return {"stage": safe_label(stage), "model": safe_label(model, 120), "api_host": safe_label(host),
        "error_type": safe_label(type(error).__name__), "http_status": status if isinstance(status, int) else 0,
        "error_code": safe_label(code, 80), "request_id": safe_label(getattr(error, "request_id", None)
            or headers.get("x-request-id") or headers.get("request-id")),
        "reason": reason, "error_summary": descriptions[reason], "attempt": attempt,
        "elapsed_ms": elapsed_ms, "temporary": temporary}


def retry_delay(error):
    headers = getattr(getattr(error, "response", None), "headers", {}) or {}
    value = headers.get("retry-after")
    if value is None and headers.get("retry-after-ms") is not None:
        try:
            delay = float(headers["retry-after-ms"])/1000
            return delay if 0 <= delay <= 5 else None
        except (ValueError, TypeError):
            return None
    if value is None:
        return 1.0
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return delay if 0 <= delay <= 5 else None


def request_completion(client, model, stage, messages, options, cancelled=lambda: False,
                       on_error=lambda diagnostic: None, sleep=time.sleep, clock=time.monotonic):
    request_client = client.with_options(max_retries=0, timeout=35) if isinstance(client, OpenAI) else client
    for attempt in (1, 2):
        if cancelled():
            raise SurfModelError({"reason": "model_cancelled", "error_type": "Cancelled", "stage": stage})
        started = clock()
        try:
            response = request_client.chat.completions.create(model=model, messages=messages, **options)
            if cancelled():
                raise SurfModelError({"reason": "model_cancelled", "error_type": "Cancelled", "stage": stage})
            if attempt > 1:
                print(f"[B站冲浪] 模型请求恢复 stage={safe_label(stage)} attempt={attempt} "
                    f"elapsed_ms={int((clock()-started)*1000)}")
            return response
        except SurfModelError:
            raise
        except Exception as error:
            diagnostic = error_diagnostic(error, client, model, stage, attempt, int((clock()-started)*1000))
            delay = retry_delay(error) if diagnostic["temporary"] and attempt == 1 else None
            diagnostic["retry_seconds"] = delay
            print("[B站冲浪] 模型请求异常 " + json.dumps(diagnostic, ensure_ascii=False))
            on_error(diagnostic)
            if delay is None or cancelled():
                raise SurfModelError(diagnostic) from None
            sleep(delay)
