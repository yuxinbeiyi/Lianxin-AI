"""
视觉理解模块：通过 SiliconFlow API 调用 DeepSeek-VL2 理解图片内容。
"""

import base64
from pathlib import Path
from openai import OpenAI
from config import get_siliconflow_config


def describe_image(image_path: str, prompt: str = "请详细描述这张图片里的内容。") -> str:
    """分析图片内容并返回自然语言描述。"""

    cfg = get_siliconflow_config()
    if not cfg.get("api_key"):
        return "错误：未配置 SiliconFlow API Key，请在 API 设置中填写。"

    client = OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
    )

    data_url = _encode_image_to_data_url(image_path)

    try:
        response = client.chat.completions.create(
            model=cfg["vision_model"],
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
            timeout=120,
        )
        content = response.choices[0].message.content or "（模型未返回描述）"
        print(f"[视觉] 识图完成（{cfg['vision_model']}）: {content[:200]}{'…' if len(content) > 200 else ''}", flush=True)
        return content
    except Exception as e:
        error_msg = str(e).lower()
        is_retryable = any(kw in error_msg for kw in [
            "timeout", "connection", "getaddrinfo", "name or service not known",
            "rate limit", "server error", "500", "502", "503", "504",
        ])
        if is_retryable:
            import time as _time
            print(f"[视觉] 首次调用失败，3秒后重试: {e}", flush=True)
            _time.sleep(3.0)
            try:
                response = client.chat.completions.create(
                    model=cfg["vision_model"],
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
                    timeout=120,
                )
                content = response.choices[0].message.content or "（模型未返回描述）"
                print(f"[视觉] 识图完成（{cfg['vision_model']}）: {content[:200]}{'…' if len(content) > 200 else ''}", flush=True)
                return content
            except Exception as e2:
                return f"图片理解失败：{e2}"
        return f"图片理解失败：{e}"


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