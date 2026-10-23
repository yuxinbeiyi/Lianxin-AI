"""Safe, single-line diagnostics shared by Bilibili reply stages."""

import re

from utils.bridge_log import log


def reply_log(stage, **fields):
    safe = {key: value for key, value in fields.items() if key not in {
        "credential", "cookie", "sessdata", "bili_jct", "prompt", "context", "text", "content",
        "api_key", "token", "authorization", "password",
    }}
    safe = {key: re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:160] for key, value in safe.items()}
    detail = " ".join(f"{key}={value}" for key, value in safe.items())
    log("B站续聊", f"{stage} {detail}".strip())
