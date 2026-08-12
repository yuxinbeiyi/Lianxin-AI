"""
GPT-SoVITS 合成辅助脚本 — 由 TtsEngine 通过子进程调用。

两种模式：
  1. CLI 模式（默认）：python worker.py <text> <ref_wav> <output> [mood]
  2. 持久模式：python worker.py --persistent
     从 stdin 读取 JSON 请求，处理后输出 JSON 到 stdout。

持久模式下模型只加载一次，避免反复加载模型的时间开销。
"""
import sys
import json
import os
import random
import re

import numpy as np
import scipy.io.wavfile
import tempfile
import asyncio



def _detect_language(text: str) -> str:
    """自动检测文本语言。优先级：中文 > 日文 > 英文。"""
    if re.search(r'[一-鿿]', text):
        return "中文"
    if re.search(r'[぀-ゟ゠-ヿ]', text):
        return "日文"
    if re.search(r'[a-zA-Z]{3,}', text):
        return "英文"
    return "中文"


# 修改后
def _normalize_audio(audio, target_peak: int = 28000):
    """音量归一化：将所有句子的峰值统一拉到 target_peak，消除句间音量波动。"""
    import numpy as np
    original_max = np.max(np.abs(audio))
    if original_max > 0:
        gain = target_peak / original_max
        # 只做衰减/小幅提升，避免噪音也被过度放大
        gain = min(gain, 3.0)
        audio_float = audio.astype(np.float32) * gain
        audio_float = np.clip(audio_float, -32768, 32767).astype(np.int16)
        audio = audio_float
    return audio


def synthesize(gs_path: str, text: str, ref_wav: str, output_path: str,
               mood_hint: str = None, sample_steps: int = 16,
               temperature: float = 0.3, top_k: int = 5, top_p: float = 0.9,
               how_to_cut: str = "不切", pause_second: float = 0.3,
               speed: float = 1.0, ref_text: str = "", ref_lang: str = "中文") -> dict:
    """执行一次 GPT-SoVITS 合成，返回 {"success": bool, "output": str, "error": str}。"""
    # 重定向 stdout → stderr，避免 GPT-SoVITS 日志污染 JSON 输出
    _orig_stdout = sys.stdout
    sys.stdout = sys.stderr

    from GPT_SoVITS.inference_webui import get_tts_wav # type: ignore

    # 参考音频路径
    ref_path = ref_wav if os.path.isfile(ref_wav) else ""
    if not ref_path:
        # 扫描技能目录中的参考音频
        ref_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            os.pardir, "skills", "语音合成", "ref_wavs"
        )
        ref_dir = os.path.abspath(ref_dir)
        candidates = []
        if os.path.isdir(ref_dir):
            for root, _dirs, files in os.walk(ref_dir):
                for f in files:
                    if f.endswith(".wav"):
                        candidates.append(os.path.join(root, f))
        if not candidates:
            sys.stdout = _orig_stdout
            return {"success": False, "error": "无参考音频"}
        ref_path = random.choice(candidates)

    text_lang = _detect_language(text)

    # v3/v4 的 S2（Diffusion）不支持 ref_free，必须提供参考音频文本
    version = os.environ.get("GPT_SOVITS_VERSION", "").strip()
    is_v3v4 = version in ("v3", "v4")
    prompt_text = ref_text.strip() if is_v3v4 else ""
    if is_v3v4 and not prompt_text:
        prompt_text = "你好，我是莲心。"
        sys.stderr.write(
            "[worker] 警告：v3/v4 需要参考音频文本（ref_wavs/config.json 的 text 字段），"
            "未配置时使用默认文本，音色相似度可能下降。\n"
        )

    params = {
        "ref_wav_path": ref_path,
        "prompt_text": prompt_text,
        "prompt_language": ref_lang if prompt_text else "中文",
        "text": text,
        "text_language": text_lang,
        "how_to_cut": how_to_cut,
        "top_k": top_k,
        "top_p": top_p,
        "temperature": temperature,
        "ref_free": not is_v3v4,
        "speed": speed,
        "if_freeze": False,
        "inp_refs": None,
        "sample_steps": sample_steps,
        "if_sr": False,
        "pause_second": pause_second,
    }

    gen = get_tts_wav(**params)
    saved = False
    for item in gen:
        if isinstance(item, tuple) and len(item) == 2:
            sr, audio = item
            audio = _normalize_audio(audio)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            scipy.io.wavfile.write(output_path, sr, audio)
            saved = True

    # 恢复 stdout 输出 JSON
    sys.stdout = _orig_stdout

    if saved:
        return {"success": True, "output": output_path}
    else:
        return {"success": False, "error": "GPT-SoVITS 未生成音频"}


def _setup_model_version(gs_path: str) -> str:
    """根据 GPT_SOVITS_VERSION 环境变量设置 GPT-SoVITS 模型版本。

    必须在首次 import GPT_SoVITS.inference_webui 之前调用：
    设置 version / gpt_path / sovits_path 环境变量，让 inference_webui
    在 import 时自动加载指定版本的预训练权重。返回解析后的版本名（空=未指定）。
    """
    version = os.environ.get("GPT_SOVITS_VERSION", "").strip()
    if not version:
        return ""
    os.environ["version"] = version
    try:
        # 使用 GPT-SoVITS 自带的版本→权重路径映射（相对 gs_path 解析）
        from config import pretrained_sovits_name, pretrained_gpt_name
        sovits_path = pretrained_sovits_name.get(version)
        gpt_path = pretrained_gpt_name.get(version)
        if sovits_path:
            os.environ["sovits_path"] = sovits_path
        if gpt_path:
            os.environ["gpt_path"] = gpt_path
        sys.stderr.write(f"[worker] 模型版本 {version}: sovits={sovits_path}, gpt={gpt_path}\n")
    except Exception as e:
        sys.stderr.write(f"[worker] 设置版本 {version} 权重路径失败（将使用默认权重）: {e}\n")
    return version


def main():
    gs_path = os.environ.get("GPT_SOVITS_PATH", "")
    if not gs_path:
        print(json.dumps({"error": "环境变量 GPT_SOVITS_PATH 未设置"}), flush=True)
        return 1

    # ── 路径设置 ─────────────────────────────────────────────
    os.chdir(gs_path)
    sys.path.insert(0, gs_path)
    sovits_dir = os.path.join(gs_path, "GPT_SoVITS")
    if os.path.isdir(sovits_dir):
        sys.path.insert(0, sovits_dir)

    # 设置模型版本（必须在首次 import inference_webui 之前）
    _setup_model_version(gs_path)

    is_persistent = "--persistent" in sys.argv

    if is_persistent:
        # ── 持久模式：从 stdin 读取 JSON 请求，模型只加载一次 ──
        # 首次调用会触发模型加载
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                text = req.get("text", "")
                if not text.strip():
                    result = {"success": False, "error": "text 为空"}
                else:
                    result = synthesize(
                        gs_path, text,
                        req.get("ref_wav", ""),
                        req.get("output_path", ""),
                        req.get("mood"),
                        sample_steps=req.get("sample_steps", 16),
                        temperature=req.get("temperature", 0.3),
                        top_k=req.get("top_k", 5),
                        top_p=req.get("top_p", 0.9),
                        how_to_cut=req.get("how_to_cut", "不切"),
                        pause_second=req.get("pause_second", 0.3),
                        speed=req.get("speed", 1.0),
                        ref_text=req.get("ref_text", ""),
                        ref_lang=req.get("ref_lang", "中文"),
                    )
            except Exception as e:
                result = {"success": False, "error": str(e)}
            print(json.dumps(result), flush=True)
    else:
        # ── CLI 模式：一次合成 ─────────────────────────────────
        if len(sys.argv) < 4:
            print(json.dumps({"error": "参数不足: text ref_wav output_path [mood]"}), flush=True)
            return 1

        text = sys.argv[1]
        ref_wav_arg = sys.argv[2]
        output_path = sys.argv[3]
        mood_hint = sys.argv[4] if len(sys.argv) > 4 else None

        result = synthesize(gs_path, text, ref_wav_arg, output_path, mood_hint)
        print(json.dumps(result), flush=True)
        return 0 if result.get("success") else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
