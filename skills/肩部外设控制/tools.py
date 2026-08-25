"""
肩部外设控制技能 — 自定义工具
ESP32-CAM 肩载摄像头、云台舵机、观察模式、人体跟踪
"""

import asyncio
import json
import time
import random
import os
from datetime import datetime
from pathlib import Path
from config import get_user_name

# 通过 brain.tools 模块访问全局变量（避免 import 绑死值）
import brain.tools as _brain_tools


# ── 工具定义 ─────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "shoulder_photo",
            "description": "用肩载摄像头（ESP32-CAM）拍一张照片并保存为 JPG 文件。返回保存路径。拍完后如需查看内容，可以调用 describe_image 或 ocr_image。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shoulder_pan",
            "description": "控制肩载摄像头的水平旋转角度（Pan），范围 0~180 度。90 度为正前方，0 度为最左，180 度为最右。",
            "parameters": {
                "type": "object",
                "properties": {
                    "angle": {
                        "type": "integer",
                        "description": "水平角度 0~180，90 为正前方"
                    }
                },
                "required": ["angle"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shoulder_tilt",
            "description": "控制肩载摄像头的垂直俯仰角度（Tilt），范围 0~180 度。90 度为水平，0 度为最下，180 度为最上。",
            "parameters": {
                "type": "object",
                "properties": {
                    "angle": {
                        "type": "integer",
                        "description": "垂直角度 0~180，90 为水平"
                    }
                },
                "required": ["angle"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shoulder_center",
            "description": "将肩载摄像头云台复位到中心位置（Pan=90°, Tilt=45°）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shoulder_status",
            "description": "获取肩载摄像头（ESP32-CAM）的连接状态和基本信息（含温湿度）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shoulder_temp",
            "description": "读取 DHT11 温湿度传感器数据，返回当前温度和湿度。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shoulder_servo",
            "description": "同时控制肩载摄像头云台的水平（Pan）和垂直（Tilt）角度。比单独调 shoulder_pan 再 shoulder_tilt 效率更高，两个舵机会同时动作。Pan 范围 0~180（90=正前方），Tilt 范围 0~180（90=水平）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pan": {
                        "type": "integer",
                        "description": "水平角度 0~180，90 为正前方，0 为最左，180 为最右"
                    },
                    "tilt": {
                        "type": "integer",
                        "description": "垂直角度 0~180，90 为水平，0 为最下，180 为最上"
                    }
                },
                "required": ["pan", "tilt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shoulder_observe",
            "description": "用肩载摄像头观察当前环境：拍照→视觉AI分析→LLM生成描述→发送照片和描述到你的QQ。常用于你想让莲心看看周围环境时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    # ── 观察记忆工具 ─────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "save_observation",
            "description": "记录一次观察发现。当你通过肩载摄像头看到值得关注的事物时调用。观察记录会持久保存，用户可以随时追问。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "详细描述你看到了什么",
                    },
                    "attention": {
                        "type": "string",
                        "description": "什么特别引起了你的注意（可选）",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "关键词标签，如 ['马克杯', '红色', '桌面']",
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_observations",
            "description": "搜索历史观察记录。按关键词、时间范围查找莲心之前通过肩载摄像头看到的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词，匹配描述、关注点和标签",
                    },
                    "time_from": {
                        "type": "string",
                        "description": "起始时间，格式 YYYY-MM-DD HH:MM",
                    },
                    "time_to": {
                        "type": "string",
                        "description": "结束时间，格式 YYYY-MM-DD HH:MM",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回几条，默认 10",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_observations",
            "description": "获取最近 N 条观察记录。当用户问'你刚才看到了什么''有什么发现'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回几条，默认 10",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_shoulder_explore",
            "description": (
                "启动肩载摄像头（ESP32-CAM）自主探索四周环境。莲心会控制舵机转动、拍照、AI分析画面，"
                "自动发现有趣目标并记录观察结果。当用户说'看看周围''观察一下四周''扫描环境'时调用此工具。"
                "注意：需要 ESP32-CAM 已通电并连接 WiFi。返回完整的探索摘要。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    # ── 观察模式工具 ─────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "start_observation_mode",
            "description": (
                "启动【观察模式】——莲心会通过肩载摄像头持续主动观察周围环境。"
                "莲心会像好奇的小宠物一样不断转头观察、拍照、并把看到的内容发到主人QQ上。"
                "此模式会持续运行直到收到关闭指令，或30分钟无消息自动退出。"
                "注意：需要肩载摄像头（ESP32-CAM）已通电在线。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_observation_mode",
            "description": "退出【观察模式】——莲心会停止主动观察，云台复位到中心位置。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    # ── 人体跟踪工具 ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "shoulder_human_track",
            "description": "启动人体跟踪模式：ESP32 摄像头实时推流，本地 MediaPipe Pose 推理人体位置，舵机云台自动跟随。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shoulder_face_track",
            "description": "启动肩载摄像头本人脸实时追踪。电脑端显示 ESP32-CAM 视频、人脸框和中心误差矢量，云台会持续跟随已识别的本人。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_face_tracking",
            "description": "停止肩载设备人脸追踪，关闭视频窗口、停止推流并让云台回中。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_human_tracking",
            "description": "停止人体跟踪模式，云台复位到中心位置。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ── 辅助函数 ─────────────────────────────────────────────

def _shoulder_exec(coro_factory):
    """统一执行模式：创建 loop → 连接 → 执行 → 断开 → 关闭 loop。
    coro_factory 接受 bridge 参数，返回要执行的协程。
    """
    bridge = _brain_tools._get_shoulder_bridge()
    if bridge is None:
        return "肩载设备未连接"
    loop = asyncio.new_event_loop()
    try:
        ok = loop.run_until_complete(bridge.connect())
        if not ok:
            return "连接肩载设备失败：ESP32 不在线"
        result = loop.run_until_complete(coro_factory(bridge))
        loop.run_until_complete(bridge.disconnect())
        return result
    except Exception as e:
        return f"肩载设备通信错误：{e}"
    finally:
        loop.close()


def _compress_jpeg(path: str, max_kb=200) -> str:
    """JPEG 质量压缩到 ≤max_kb KB，返回压缩后的路径。"""
    try:
        from PIL import Image
        img = Image.open(path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        stem, ext = os.path.splitext(path)
        compressed = f"{stem}_c{ext}"
        quality = 85
        while quality >= 20:
            img.save(compressed, "JPEG", quality=quality)
            if os.path.getsize(compressed) <= max_kb * 1024:
                break
            quality -= 5
        return compressed
    except Exception:
        return path


# ── 肩载摄像头工具函数 ─────────────────────────────────

def shoulder_photo() -> str:
    """拍摄一张照片并保存，返回保存路径。"""
    from utils.paths import get_user_data_dir
    save_dir = get_user_data_dir() / "camera_shots"
    save_dir.mkdir(parents=True, exist_ok=True)
    path = str(save_dir / f"shoulder_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")

    async def _do(bridge):
        await bridge.photo()
        await asyncio.sleep(0.3)
        return await bridge.photo(save_path=path)

    data = _shoulder_exec(_do)
    if isinstance(data, str):
        return data
    if data and isinstance(data, bytes) and len(data) > 100:
        return f"拍照成功，已保存到 {path}"
    return "拍照失败：未收到图片数据"


def shoulder_observe() -> str:
    """拍照→视觉分析→LLM描述→发送照片+描述到QQ。一步完成观察。"""
    from utils.paths import get_user_data_dir
    save_dir = get_user_data_dir() / "camera_shots"
    save_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = str(save_dir / f"observe_{timestamp}.jpg")

    async def _do(bridge):
        await bridge.photo()
        await asyncio.sleep(0.3)
        return await bridge.photo(save_path=raw_path)
    data = _shoulder_exec(_do)
    if isinstance(data, str):
        return data
    if not data or not isinstance(data, bytes) or len(data) < 100:
        return "拍照失败：未收到图片数据"

    photo_path = _compress_jpeg(raw_path)

    try:
        from brain.vision import describe_image
        prompt = (
            "请详细描述这张画面里的内容。注意观察——"
            "画面中有什么人物、物体、场景、颜色、动作、文字等。"
            "尽量关注细节，比如物品的位置、状态、颜色、人物表情动作。"
        )
        description = describe_image(photo_path, prompt=prompt)
    except Exception as e:
        description = f"（视觉分析失败：{e}）"

    try:
        from openai import OpenAI
        from config import get_api_config, get_agnes_config
        cfg = get_api_config()
        provider = cfg.get("provider", "deepseek")
        if provider == "agnes":
            agnes_cfg = get_agnes_config()
            api_key = agnes_cfg["api_key"]
            base_url = agnes_cfg["base_url"]
            model = agnes_cfg["model"]
        else:
            api_key = cfg["api_key"]
            base_url = cfg["base_url"]
            model = cfg["model"]
        if api_key:
            client = OpenAI(api_key=api_key, base_url=base_url)
            resp = client.chat.completions.create(
                model=model,
                max_tokens=400,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是莲心，刚刚用肩载摄像头看了一眼周围。\n"
                            "用可爱简短的语气说一句话。要求：\n"
                            "- 控制在 300 字以内，越短越好\n"
                            "- 保留视觉识别的核心内容\n"
                            "- 语气活泼好奇，可以加颜文字\n"
                            f"- 称呼用户为'{get_user_name()}'\n"
                            "- 直接说看到的内容"
                        ),
                    },
                    {"role": "user", "content": f"画面内容：{description}"},
                ],
                timeout=20,
            )
            message = resp.choices[0].message.content or ""
            if len(message) > 300:
                message = message[:297] + "..."
        else:
            message = ""
    except Exception as e:
        message = ""

    sent_photo = False
    sent_text = False
    try:
        qq = _brain_tools._qq_bridge_worker
        if qq:
            qq.send_file_to_qq(photo_path)
            sent_photo = True
            time.sleep(random.uniform(0.5, 1.5))
            if message:
                qq.send_to_owner(message)
                sent_text = True
    except Exception:
        pass

    parts = []
    if sent_photo:
        parts.append("照片已发送")
    if sent_text:
        parts.append("描述已发送")
    if not parts:
        return f"观察完成，描述：{description[:100]}..."
    return "观察完成，" + "，".join(parts)


def shoulder_pan(angle: int) -> str:
    """控制云台水平旋转。"""
    def _do(bridge):
        return bridge.pan(angle)
    result = _shoulder_exec(_do)
    if isinstance(result, dict) and "pan" in result:
        return f"水平旋转到 {angle} 度完成"
    if isinstance(result, str):
        return result
    return "水平旋转失败"


def shoulder_tilt(angle: int) -> str:
    """控制云台垂直俯仰。"""
    def _do(bridge):
        return bridge.tilt(angle)
    result = _shoulder_exec(_do)
    if isinstance(result, dict) and "tilt" in result:
        return f"垂直旋转到 {angle} 度完成"
    if isinstance(result, str):
        return result
    return "垂直旋转失败"


def shoulder_servo(pan: int, tilt: int) -> str:
    """同时控制云台水平和垂直角度。"""
    def _do(bridge):
        return bridge.servo(pan, tilt)
    result = _shoulder_exec(_do)
    if isinstance(result, dict) and "pan" in result and "tilt" in result:
        return f"云台已转到 水平={pan}° 垂直={tilt}°"
    if isinstance(result, str):
        return result
    return "云台控制失败"


def shoulder_center() -> str:
    """云台复位到中心 (Pan=90°, Tilt=45°)。"""
    def _do(bridge):
        return bridge.center()
    result = _shoulder_exec(_do)
    if isinstance(result, dict) and result.get("pan") == 90:
        return "云台已复位到中心 (Pan=90°, Tilt=45°)"
    if isinstance(result, str):
        return result
    return "云台复位失败"


def shoulder_status() -> str:
    """查询设备状态。"""
    def _do(bridge):
        return bridge.status()
    result = _shoulder_exec(_do)
    if isinstance(result, dict):
        result.pop("type", None)
        return json.dumps(result, ensure_ascii=False, indent=2)
    if isinstance(result, str):
        return result
    return "获取状态失败"


def shoulder_temp() -> str:
    """读取 DHT11 温湿度。"""
    def _do(bridge):
        return bridge.temp()
    result = _shoulder_exec(_do)
    if isinstance(result, dict) and result.get("type") == "temp":
        return f"当前温度：{result['temp']}°C，湿度：{result['humidity']}%"
    if isinstance(result, str):
        return result
    return "读取温湿度失败：传感器无响应"


# ── 观察记忆工具 ─────────────────────────────────────────────

def _save_observation(description: str, attention: str = "", tags: list = None):
    """记录一次观察发现。"""
    from brain.observation_store import add
    record = add(
        description=description,
        attention=attention,
        tags=tags or [],
    )
    return (
        f"已记录观察 #{record['id']}: {description[:100]}"
        + (f"（关注：{attention}）" if attention else "")
    )


def _search_observations(keyword: str = "", time_from: str = "", time_to: str = "",
                         limit: int = 10):
    """搜索历史观察记录。"""
    from brain.observation_store import search
    results = search(keyword=keyword, time_from=time_from, time_to=time_to, limit=limit)
    if not results:
        return "没有找到匹配的观察记录。"
    lines = [f"找到 {len(results)} 条观察记录:"]
    for r in results:
        lines.append(
            f"- [{r['timestamp']}] {r['description'][:120]}"
            + (f" (关注: {r['attention']})" if r.get('attention') else "")
        )
    return "\n".join(lines)


def _get_recent_observations(limit: int = 10):
    """获取最近 N 条观察记录。"""
    from brain.observation_store import recent
    results = recent(limit=limit)
    if not results:
        return "目前还没有观察记录。"
    lines = [f"最近 {len(results)} 条观察记录:"]
    for r in results:
        lines.append(
            f"- [{r['timestamp']}] {r['description'][:120]}"
            + (f" (关注: {r['attention']})" if r.get('attention') else "")
        )
    return "\n".join(lines)


def _start_shoulder_explore():
    """启动肩载摄像头自主探索，返回探索摘要。"""
    from brain.observation_engine import ObservationEngine
    engine = ObservationEngine()
    result = engine.run_explore()

    summary = result.get("summary", "")
    observations = result.get("observations", [])
    chain_id = result.get("chain_id", "")

    if observations:
        lines = [f"探索完成！{summary}"]
        lines.append(f"（探索链 {chain_id}，共记录 {len(observations)} 条发现）")
        for obs in observations:
            desc = obs["description"][:100]
            if obs.get("attention"):
                desc += f"（关注：{obs['attention']}）"
            lines.append(f"  - {desc}")
        return "\n".join(lines)
    else:
        return f"探索完成。{summary}（无新增记录）"


# ── 观察模式工具 ────────────────────────────────────────

def _start_observation_mode() -> str:
    """启动【观察模式】——莲心持续主动观察环境。"""
    from brain.observation_mode import get_observation_state
    state = get_observation_state()
    if state.is_active:
        return "【观察模式】已经在运行中了哦，我正看着周围呢(｀・ω・´)"

    state.activate()

    qq = _brain_tools._qq_bridge_worker
    if qq and hasattr(qq, '_owner_qq') and qq._owner_qq:
        from workers.observation_mode_worker import ObservationModeWorker
        state.set_qq_bridge(qq)
        worker = ObservationModeWorker(state)
        worker.pending_messages.connect(qq._on_observation_pending_messages)
        worker.mode_exited.connect(qq._on_observation_mode_exit)
        worker.start()
        qq._obs_worker = worker
        return "【观察模式】已启动！让我看看周围有什么有趣的东西～(^-^)"
    else:
        return "【观察模式】已激活，但未检测到 QQ 桥接，观察结果将仅显示在桌面端。"


def _stop_observation_mode() -> str:
    """退出【观察模式】。停止循环并复位云台。"""
    from brain.observation_mode import get_observation_state
    state = get_observation_state()
    if not state.is_active:
        return "【观察模式】本来就是关闭的哦(｀・ω・´)"
    state.deactivate()
    try:
        result = shoulder_center()
    except Exception:
        pass
    return "【观察模式】已退出，云台已复位～(´-ω-`)"


# ── 人体跟踪工具 ────────────────────────────────────────

def _start_human_tracking() -> str:
    """启动人体跟踪模式：帧接收 + Pose 推理 + 舵机跟随。"""
    from brain.human_tracking import get_track_manager
    manager = get_track_manager()
    if manager.is_active:
        return "人体跟踪已经在运行中啦(｀・ω・´)"

    manager.activate()
    qq = _brain_tools._qq_bridge_worker
    if qq and hasattr(qq, '_owner_qq') and qq._owner_qq:
        from workers.track_worker import TrackWorker

        manager.set_qq_bridge(qq)
        worker = TrackWorker()
        worker.mode_exited.connect(qq._on_human_tracking_exit)
        worker.start()
        qq._track_worker = worker
        return "好嘞！莲心人体跟踪模式启动，让我看看周围有没有人～(｀・ω・´)"
    else:
        manager.deactivate()
        return "未检测到 QQ 桥接，人体跟踪需要 QQ 远程控制哦～"


def _stop_human_tracking() -> str:
    """停止人体跟踪模式。"""
    from brain.human_tracking import get_track_manager
    manager = get_track_manager()
    if not manager.is_active:
        return "人体跟踪没有在运行哦~"
    manager.deactivate()
    try:
        shoulder_center()
    except Exception:
        pass
    return "人体跟踪已停止，云台已回中~"


def _start_face_tracking() -> str:
    """在 Qt 主线程创建人脸追踪窗口和后台闭环 worker。"""
    from brain.face_tracking import get_face_tracking_controller
    controller = get_face_tracking_controller()
    if controller is None:
        return "无法启动人脸追踪：莲心桌面端没有可用的 Qt 界面"
    if controller.worker is not None and controller.worker.isRunning():
        return "人脸追踪已经在运行中"
    controller.request_start()
    return "人脸追踪已启动，正在连接肩载摄像头"


def _stop_face_tracking() -> str:
    from brain.face_tracking import get_face_tracking_controller
    controller = get_face_tracking_controller()
    if controller is None or controller.worker is None:
        return "人脸追踪当前没有运行"
    controller.request_stop()
    return "正在停止人脸追踪，云台将回到中心"


# ── 工具调度表 ───────────────────────────────────────────────
TOOL_EXECUTORS = {
    "shoulder_photo":       lambda inp: shoulder_photo(),
    "shoulder_pan":         lambda inp: shoulder_pan(inp["angle"]),
    "shoulder_tilt":        lambda inp: shoulder_tilt(inp["angle"]),
    "shoulder_servo":       lambda inp: shoulder_servo(inp["pan"], inp["tilt"]),
    "shoulder_center":      lambda inp: shoulder_center(),
    "shoulder_status":      lambda inp: shoulder_status(),
    "shoulder_temp":        lambda inp: shoulder_temp(),
    "shoulder_observe":     lambda inp: shoulder_observe(),
    "save_observation":     lambda inp: _save_observation(
        inp["description"],
        inp.get("attention", ""),
        inp.get("tags", []),
    ),
    "search_observations":  lambda inp: _search_observations(
        keyword=inp.get("keyword", ""),
        time_from=inp.get("time_from", ""),
        time_to=inp.get("time_to", ""),
        limit=inp.get("limit", 10),
    ),
    "get_recent_observations": lambda inp: _get_recent_observations(
        limit=inp.get("limit", 10),
    ),
    "start_shoulder_explore":   lambda inp: _start_shoulder_explore(),
    "start_observation_mode":   lambda inp: _start_observation_mode(),
    "stop_observation_mode":    lambda inp: _stop_observation_mode(),
    "shoulder_human_track":     lambda inp: _start_human_tracking(),
    "stop_human_tracking":      lambda inp: _stop_human_tracking(),
    "shoulder_face_track":       lambda inp: _start_face_tracking(),
    "stop_face_tracking":         lambda inp: _stop_face_tracking(),
}
