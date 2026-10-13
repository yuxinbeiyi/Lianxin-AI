"""
视觉理解模块：通过 SiliconFlow API 调用视觉大模型理解图片内容。

超时预算（关键）：
    单次 HTTP 30s × 最多 2 次应用层尝试，且显式关闭 OpenAI SDK 自带重试。
    SDK 默认 max_retries=2 会把一次超时放大成 3 次；此前本模块 timeout=120
    再叠加 SDK 重试与应用层重试，最坏可达 12 分钟，必然撞上 brain/agent.py 的
    TOOL_TIMEOUT(120s)，工具结果被丢弃、截图白做。现在最坏约 60s。
"""

import base64
import threading
import time
from pathlib import Path

from openai import OpenAI

from config import get_siliconflow_config

VISION_TIMEOUT = 30.0     # 单次请求超时（真实 1920x1080 截图实测约 10s）
VISION_ATTEMPTS = 2       # 应用层尝试次数
VISION_MAX_RETRIES = 0    # 关闭 SDK 自带重试，避免超时被成倍放大

# 熔断：连续失败达到阈值后，在冷却窗口内不再真实调用，直接快速失败。
# 目的：视觉服务挂掉时，用户每次"看看我的屏幕"不必再干等 30~60s。
CIRCUIT_FAIL_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECONDS = 300.0
_CIRCUIT_USER_HINT = "视觉服务连续失败，已暂时熔断，约 5 分钟后自动恢复"

_circuit_lock = threading.Lock()
_circuit_fails = 0
_circuit_opened_at = 0.0


def describe_image(image_path: str, prompt: str = "请详细描述这张图片里的内容。") -> str:
    """分析图片内容并返回自然语言描述。

    失败时返回以"图片理解失败："开头的原因文本，且一定打印日志。
    历史上失败被静默吞成空串，导致莲心"看不到画面却照常评论"且日志无迹可查。
    """
    cfg = get_siliconflow_config()
    if not cfg.get("api_key"):
        return _fail("未配置 SiliconFlow API Key，请在 API 设置中填写。")

    if not str(image_path or "").strip() or not Path(str(image_path)).is_file():
        return _fail(f"图片不存在或不可读：{image_path}")

    try:
        data_url = _encode_image_to_data_url(image_path)
    except Exception as e:
        return _fail(f"无法读取图片（{type(e).__name__}: {e}）")

    blocked = _circuit_blocking_reason()
    if blocked:
        return _fail(blocked)

    client = OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        max_retries=VISION_MAX_RETRIES,
    )
    model = cfg["vision_model"]
    last_error = ""
    for attempt in range(1, VISION_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                max_tokens=2048,
                timeout=VISION_TIMEOUT,
            )
            elapsed = time.monotonic() - started
            content = (response.choices[0].message.content or "").strip()
            if not content:
                last_error = f"{model} 返回了空内容"
                print(
                    f"[视觉] 第 {attempt}/{VISION_ATTEMPTS} 次返回空内容（{elapsed:.1f}s）",
                    flush=True,
                )
            else:
                print(
                    f"[视觉] 识图完成（{model}）{elapsed:.1f}s: "
                    f"{content[:200]}{'…' if len(content) > 200 else ''}",
                    flush=True,
                )
                _record_success()
                return content
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(
                f"[视觉] 第 {attempt}/{VISION_ATTEMPTS} 次调用失败"
                f"（{time.monotonic() - started:.1f}s）: {last_error}",
                flush=True,
            )
        if attempt < VISION_ATTEMPTS:
            time.sleep(1.5)
    _record_failure()
    return _fail(last_error or "未知错误")


def vision_circuit_state() -> dict:
    """返回熔断状态（供设置界面/诊断显示）。"""
    with _circuit_lock:
        opened_at = _circuit_opened_at
        fails = _circuit_fails
    if not opened_at:
        return {"open": False, "consecutive_failures": fails, "remaining_seconds": 0.0}
    remaining = CIRCUIT_COOLDOWN_SECONDS - (time.monotonic() - opened_at)
    return {
        "open": remaining > 0,
        "consecutive_failures": fails,
        "remaining_seconds": max(0.0, round(remaining, 1)),
    }


def reset_vision_circuit() -> None:
    """清除熔断状态（用户在处理后手动重试时调用）。"""
    global _circuit_fails, _circuit_opened_at
    with _circuit_lock:
        _circuit_fails = 0
        _circuit_opened_at = 0.0


def _circuit_blocking_reason() -> str:
    global _circuit_fails, _circuit_opened_at
    with _circuit_lock:
        if not _circuit_opened_at:
            return ""
        remaining = CIRCUIT_COOLDOWN_SECONDS - (time.monotonic() - _circuit_opened_at)
        if remaining > 0:
            return f"{_CIRCUIT_USER_HINT}（还需约 {int(remaining)} 秒）"
        _circuit_fails = 0
        _circuit_opened_at = 0.0
        return ""


def _record_success() -> None:
    global _circuit_fails, _circuit_opened_at
    with _circuit_lock:
        _circuit_fails = 0
        _circuit_opened_at = 0.0


def _record_failure() -> None:
    global _circuit_fails, _circuit_opened_at
    with _circuit_lock:
        _circuit_fails += 1
        if _circuit_fails >= CIRCUIT_FAIL_THRESHOLD and not _circuit_opened_at:
            _circuit_opened_at = time.monotonic()
            print(
                f"[视觉] 连续失败 {_circuit_fails} 次，熔断 "
                f"{int(CIRCUIT_COOLDOWN_SECONDS)}s：{_CIRCUIT_USER_HINT}",
                flush=True,
            )


def _fail(reason: str) -> str:
    print(f"[视觉] 识图失败: {reason}", flush=True)
    return f"图片理解失败：{reason}"


def _encode_image_to_data_url(image_path: str) -> str:
    """读取图片、压缩到合理尺寸、转为 base64 data URL。"""
    from io import BytesIO

    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("缺少 Pillow 库，请执行：pip install Pillow")

    img = Image.open(image_path)
    fmt = img.format or "PNG"
    mime = f"image/{fmt.lower()}"

    longest = max(img.size)
    if longest > 1024:
        ratio = 1024 / longest
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format=fmt)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return f"data:{mime};base64,{b64}"


def get_vision_model() -> str:
    """返回当前配置的视觉模型名称，供 UI 显示等使用。"""
    cfg = get_siliconflow_config()
    return cfg.get("vision_model", "Qwen/Qwen3-VL-30B-A3B-Instruct")
