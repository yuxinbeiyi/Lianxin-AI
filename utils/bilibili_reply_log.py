"""Safe, single-line diagnostics shared by Bilibili reply stages."""

import re

from utils.bridge_log import log


def _should_show_on_console(fields):
    return any(key in fields for key in ("rpid", "phase", "elapsed_ms", "history_turns"))

def reply_log(stage, **fields):
    safe = {key: value for key, value in fields.items() if key not in {
        "credential", "cookie", "sessdata", "bili_jct", "prompt", "context", "text", "content",
        "api_key", "token", "authorization", "password",
    }}
    safe = {key: re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:160] for key, value in safe.items()}
    detail = " ".join(f"{key}={value}" for key, value in safe.items())
    important = _should_show_on_console(fields)
    log("\u0042\u7ad9\u7eed\u804a", f"{stage} {detail}".strip(), console=important)
