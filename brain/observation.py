"""
Observation 模块：截图、摄像头抓拍、观察分析
截图用 PIL（零额外依赖），摄像头用 OpenCV。
所有函数在后台线程调用，不会阻塞 UI。
"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

# 会产出"莲心看到的画面"的工具：聊天界面据此显示莲心侧图片气泡。
# 新增视觉工具时只需改这里（brain/agent.py、api_server.py 共用）。
OBSERVATION_TOOLS = ("capture_desktop", "capture_from_camera", "look_at_camera")

# 观察图片托管目录：api_server 的 /api/attachments 只放行
# ~/.lianxin/{images,files,observations}，所以临时截图必须先搬进来才能被前端加载。
OBSERVATIONS_DIR = Path.home() / ".lianxin" / "observations"
OBSERVATION_KEEP = 50


def adopt_observation_image(image_path: str, keep: int = OBSERVATION_KEEP) -> Optional[str]:
    """把临时观察图片收进托管目录，返回新路径；失败返回 None。

    顺带清理超出 keep 张的旧观察图；临时原图搬迁成功后删除。
    """
    try:
        src = Path(str(image_path or ""))
        if not src.is_file():
            return None
        OBSERVATIONS_DIR.mkdir(parents=True, exist_ok=True)
        suffix = src.suffix or ".png"
        stamp = int(time.time() * 1000)
        dst = OBSERVATIONS_DIR / f"obs_{stamp}{suffix}"
        counter = 1
        while dst.exists():
            dst = OBSERVATIONS_DIR / f"obs_{stamp}_{counter}{suffix}"
            counter += 1
        shutil.copy2(src, dst)
        try:
            src.unlink()
        except OSError:
            pass
        try:
            files = sorted(
                OBSERVATIONS_DIR.glob("obs_*"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for old in files[max(0, int(keep)):]:
                old.unlink()
        except OSError:
            pass
        return str(dst)
    except Exception as exc:
        print(f"[观察] 收纳观察图片失败: {exc}", flush=True)
        return None


def capture_screen() -> Optional[str]:
    """截取当前屏幕。返回临时 PNG 路径，失败返回 None。"""
    print("[观察-调试] capture_screen: 开始...")
    try:
        from PIL import ImageGrab
        print("[观察-调试] capture_screen: 调用 ImageGrab.grab()...")
        img = ImageGrab.grab()
        print(f"[观察-调试] capture_screen: 截屏成功, size={img.size}")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        img.save(tmp, "PNG")
        tmp.close()
        print(f"[观察-调试] capture_screen: 保存到 {tmp.name}")
        return tmp.name
    except Exception as e:
        print(f"[观察] 截图失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def capture_camera(camera_index: int = 0, wait_seconds: int = 5) -> Optional[str]:
    """打开摄像头，等待 wait_seconds 秒（让画面稳定）后抓拍一张。
    返回临时 JPG 路径，失败返回 None。"""
    try:
        import cv2
    except ImportError:
        print("[观察] 需要 opencv-python，请执行: pip install opencv-python")
        return None

    cap = None
    try:
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            print(f"[观察] 无法打开摄像头 index={camera_index}")
            return None

        # 预热 — 丢弃前几帧（自动曝光/白平衡稳定）
        for _ in range(15):
            cap.read()

        # 持续读取直到等待时间结束，然后抓一帧
        start = time.monotonic()
        while time.monotonic() - start < wait_seconds:
            cap.read()

        ret, frame = cap.read()
        if not ret or frame is None:
            print("[观察] 摄像头抓拍失败")
            return None

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        cv2.imwrite(tmp.name, frame)
        tmp.close()
        return tmp.name
    except Exception as e:
        print(f"[观察] 摄像头异常: {e}")
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _grab_vision_panel_frame():
    """查找运行中的视觉感知运行时/面板，返回 (是否找到, 当前帧副本)。

    新版界面由 api_server 常驻运行时持有摄像头；旧 Qt 面板路径保留给
    原版界面。运行中但没有画面时返回 (True, None)，避免二次打开摄像头。
    """
    try:
        from brain.vision_runtime import peek_vision_runtime
        runtime = peek_vision_runtime()
        if runtime is not None and runtime.is_active():
            return True, runtime.latest_frame()
    except Exception as exc:
        print(f"[观察] 从视觉运行时获取帧失败: {exc}")
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None or not isinstance(app, QApplication):
            # 无 GUI 后端（如 api_server 只建 QCoreApplication）没有视觉感知面板，静默跳过
            return False, None
        for widget in app.topLevelWidgets():
            panel = getattr(widget, "_vision_panel", None)
            if panel is None:
                continue
            worker = getattr(panel, "_worker", None)
            if worker is None:
                continue  # 面板存在但视觉未启动
            frame = panel.get_current_frame()
            if frame is not None:
                return True, frame.copy()
            return True, None
    except Exception as exc:
        print(f"[观察] 从视觉面板获取帧失败: {exc}")
    return False, None


def capture_live_camera_frame(camera_index: int = 0, wait_seconds: int = 5):
    """优先复用运行中视觉感知面板的当前帧，避免与已占用的摄像头冲突。

    返回 (图片路径, 来源)。来源为 "视觉感知面板" 或 "摄像头"；
    面板正在运行但暂无画面，或面板与摄像头均不可用时返回 (None, "")。
    """
    try:
        panel_found, frame = _grab_vision_panel_frame()
    except Exception as exc:
        print(f"[观察] 视觉面板抓帧异常，回退摄像头: {exc}")
        panel_found, frame = False, None
    if panel_found:
        if frame is None:
            print("[观察] 视觉感知面板正在运行但暂无画面，跳过二次打开摄像头以避免冲突")
            return None, ""
        try:
            import cv2
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            tmp.close()
            if cv2.imwrite(tmp.name, frame):
                return tmp.name, "视觉感知面板"
            print("[观察] 视觉面板帧保存失败，回退摄像头")
        except Exception as exc:
            print(f"[观察] 视觉面板帧保存失败: {exc}")
        return None, ""
    path = capture_camera(camera_index, wait_seconds)
    return path, ("摄像头" if path else "")


def analyze_observation(image_path: str, source_name: str = "截图") -> str:
    """分析观察到的画面，返回自然语言描述。

    失败时原样返回以"图片理解失败："开头的原因（不再静默归一化成空串），
    这样工具结果里能看到失败原因，模型也不用靠猜或复用历史里的旧路径。
    """
    print(f"[观察-调试] analyze_observation: {source_name}, path={image_path}")
    try:
        from brain.vision import describe_image
        from brain.observation_quality import (
            OBSERVATION_PROMPT,
            is_vision_failure,
            normalize_observation,
        )
        print("[观察-调试] analyze_observation: 调用 describe_image...")
        raw = describe_image(image_path, prompt=OBSERVATION_PROMPT)
        if is_vision_failure(raw):
            reason = str(raw or "").strip()
            print(f"[观察-调试] analyze_observation: 视觉失败 -> {reason[:200]}", flush=True)
            return reason
        result = normalize_observation(raw)
        print(f"[观察-调试] analyze_observation: 完成, len={len(result)}")
        return result
    except Exception as e:
        reason = f"{source_name}分析异常（{type(e).__name__}: {e}）"
        print(f"[观察-调试] analyze_observation: 失败 {reason}", flush=True)
        return f"图片理解失败：{reason}"
