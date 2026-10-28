"""ESP32 OLED 表情：本地预览与一次性命令。

从旧 Qt 面板里抽出的无界面版本：``face_engine`` 渲染 128x64 帧，
``HardwareBridge.send_one_shot`` 负责把命令发给 ESP32。
"""

from __future__ import annotations

import asyncio
import io
import threading

from PIL import Image

_LOCK = threading.Lock()


def list_emotions() -> list[dict]:
    from brain import face_engine as fe
    return [{"num": num, "name": name, "label": label} for num, name, label, *_ in fe.EMOTIONS]


def preview_png(num: int) -> bytes:
    """渲染指定表情的静态帧（PNG），前端按像素放大显示。"""
    from brain import face_engine as fe
    num = max(1, min(len(fe.EMOTIONS), int(num)))
    sim = fe.FaceSim()
    sim.goto(num, 0)
    frame = sim.tick(3000)
    image = Image.new("L", (frame.width, frame.height), 0)
    pixels = image.load()
    for y, row in enumerate(frame.pixels):
        for x, lit in enumerate(row):
            if lit:
                pixels[x, y] = 255
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def send_command(command: str) -> dict:
    with _LOCK:
        try:
            from brain.hardware_bridge import HardwareBridge
            bridge = HardwareBridge()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(bridge.send_one_shot(command))
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
        except Exception as exc:
            return {"ok": False, "message": f"连接失败：{exc}"}
        if response:
            return {"ok": True, "message": f"OK → {response.strip()}"}
        return {"ok": False, "message": "无响应（确认中继/同一局域网，ESP32 在线）"}


def run_action(action: str, value=None) -> dict:
    action = str(action or "").strip()
    if action == "on":
        return send_command("emoji on")
    if action == "off":
        return send_command("emoji off")
    if action == "show":
        from brain import face_engine as fe
        try:
            num = int(value)
        except (TypeError, ValueError):
            raise ValueError("表情编号无效")
        if not 1 <= num <= len(fe.EMOTIONS):
            raise ValueError("表情编号超出范围")
        return send_command(f"emoji {num}")
    raise ValueError("未知的 OLED 操作")
