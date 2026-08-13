import os
import json
from pathlib import Path
from utils.paths import get_user_data_dir   # 新增导入

# ── 用户 API 配置文件路径（新位置）─────────────────────────────
_USER_CONFIG_PATH = get_user_data_dir() / "user_config.json"

# ── DeepSeek 默认值 ─────────────────────────────────────────
_DEEPSEEK_DEFAULTS = {
    "api_key":    "",
    "base_url":   "https://api.deepseek.com",
    "model":      "deepseek-v4-flash",
    "max_tokens": 8192,
    "api_format": "openai",  # "openai" | "anthropic" — LiteLLM 统一网关的 API 格式
    "provider":   "deepseek",  # "deepseek" | "agnes" | "local" — 当前使用的 AI 提供商
    # 本地模型 (Ollama) 配置
    "use_local": False,
    "local_base_url": "http://localhost:11434/v1",
    "local_model_name": "qwen2.5:3b-instruct",
    # 路由模型 (Intent Router) — 用小模型做意图分类，零成本
    "router_model": "",  # Ollama 本地模型名，设为 "" 则回退到规则路由
}

# ── Agnes AI 默认值 ─────────────────────────────────────────
_AGNES_DEFAULTS = {
    "api_key":  "",
    "base_url": "https://apihub.agnes-ai.com/v1",
    "model":    "agnes-2.0-flash",
}

# ── GitHub Skill 默认值 ───────────────────────────────────
_GITHUB_DEFAULTS = {
    "token": "",
    "content_preview_chars": 4000,
}

# ── Agnes 图片生成默认值 ───────────────────────────────────
_IMAGE_GEN_DEFAULTS = {
    "enabled":        True,
    "model":          "agnes-image-2.1-flash",
    "default_size":   "1024x1024",
    "default_quality": "standard",
    "save_dir":       "",
}

# ── SiliconFlow 视觉 API 默认值 ────────────────────────────
_SILICONFLOW_DEFAULTS = {
    "api_key":       "",
    "base_url":      "https://api.siliconflow.cn/v1",
    "vision_model":  "Qwen/Qwen3-VL-30B-A3B-Instruct",
}

# ── 阿里云 STT 默认值 ───────────────────────────────────────
_ALIYUN_STT_DEFAULTS = {
    "access_key_id":     "",
    "access_key_secret": "",
    "app_key":           "",
}

# ── QQ 桥接默认值 ─────────────────────────────────────────
_QQ_BRIDGE_DEFAULTS = {
    "enabled":    False,
    "auto_start": False,
    "ws_url":     "ws://127.0.0.1:3001",
    "qq_account": "",
    "owner_qq":   "",
    "owner_name": "主人",
    "voice_reply_enabled": True,
    "segmented_reply_enabled": True,
}



def _load_full_config() -> dict:
    """加载完整的 user_config.json，如果不存在则返回空字典。"""
    try:
        if _USER_CONFIG_PATH.exists():
            return json.loads(_USER_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_full_config(config: dict):
    """保存完整的 user_config.json。"""
    _USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USER_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ── DeepSeek 配置（保持原接口不变）───────────────────────────

def get_api_config() -> dict:
    """读取 DeepSeek API 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    deepseek = full.get("deepseek", {})
    # 用默认值补全
    result = {}
    for k, v in _DEEPSEEK_DEFAULTS.items():
        result[k] = deepseek.get(k, v)
    return result


def normalize_model_for_litellm(model: str, api_base: str = "") -> str:
    """将用户配置的模型名标准化为 LiteLLM 可识别的 provider/model 格式。

    核心原则：
    - 官方 DeepSeek：LiteLLM 需要 "deepseek/模型名"
    - 第三方 API（硅基流动等）：LiteLLM 需要 "openai/完整模型名"，模型名部分原样保留
    - "deepseek-ai/" 是硅基流动的模型命名空间，不是 LiteLLM provider，不要动它
    """
    _is_official = "deepseek.com" in (api_base or "").lower()

    if "/" not in model:
        # 无斜杠 → 自动补前缀
        return f"deepseek/{model}" if _is_official else f"openai/{model}"

    if _is_official:
        # 官方 API：将无效 provider（如 deepseek-ai）修正为 deepseek
        if model.startswith("deepseek-ai/"):
            model = f"deepseek/{model[len('deepseek-ai/'):]}"
        return model

    # 第三方 API：保持原始模型名不变，只确保有 openai/ 前缀
    if not model.startswith("openai/"):
        return f"openai/{model}"
    return model


def normalize_local_model_name(model: str) -> str:
    """Return the Ollama model name without a LiteLLM provider prefix."""
    value = str(model or "").strip()
    for prefix in ("ollama/", "openai/"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return value or "qwen2.5:3b-instruct"


def normalize_local_base_url(base_url: str) -> str:
    """Normalize the Ollama OpenAI-compatible endpoint used by LiteLLM."""
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        value = "http://localhost:11434"
    if not value.lower().endswith("/v1"):
        value = f"{value}/v1"
    return value


def normalize_local_model_for_litellm(model: str) -> str:
    """Use Ollama's OpenAI-compatible API through LiteLLM."""
    return f"openai/{normalize_local_model_name(model)}"


def save_api_config(config: dict):
    """保存 DeepSeek API 配置（仅更新 deepseek 部分，不影响其他配置）。"""
    full = _load_full_config()
    full["deepseek"] = config
    _save_full_config(full)


def has_api_key() -> bool:
    """检查用户是否已配置 API Key（根据当前 provider 判断）。"""
    cfg = get_api_config()
    provider = cfg.get("provider", "deepseek")
    if provider == "agnes":
        agnes_cfg = get_agnes_config()
        return bool(agnes_cfg.get("api_key", "").strip())
    return bool(cfg.get("api_key", "").strip())


# ── Agnes AI 配置 ──────────────────────────────────────────

def get_agnes_config() -> dict:
    """读取 Agnes AI 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    agnes = full.get("agnes", {})
    return {k: agnes.get(k, v) for k, v in _AGNES_DEFAULTS.items()}


def save_agnes_config(config: dict):
    """保存 Agnes AI 配置（仅更新 agnes 部分）。"""
    full = _load_full_config()
    full["agnes"] = config
    _save_full_config(full)


def get_github_config() -> dict:
    """读取 GitHub Skill 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    github = full.get("github", {})
    result = _GITHUB_DEFAULTS.copy()
    result.update(github)
    return result


def save_github_config(config: dict):
    """保存 GitHub Skill 配置。"""
    full = _load_full_config()
    full["github"] = config
    _save_full_config(full)


def get_image_gen_config() -> dict:
    """读取 Agnes 图片生成配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    ig = full.get("image_gen", {})
    return {k: ig.get(k, v) for k, v in _IMAGE_GEN_DEFAULTS.items()}


def save_image_gen_config(config: dict):
    """保存 Agnes 图片生成配置（仅更新 image_gen 部分）。"""
    full = _load_full_config()
    full["image_gen"] = config
    _save_full_config(full)


# ── Agnes 视频生成默认值 ──────────────────────────────────
_VIDEO_GEN_DEFAULTS = {
    "enabled":           True,
    "model":             "agnes-video-v2.0",
    "default_duration":  5,
    "default_frame_rate": 24,
    "default_width":     1152,
    "default_height":    768,
    "save_dir":          "",
}


def get_video_gen_config() -> dict:
    """读取 Agnes 视频生成配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    vg = full.get("video_gen", {})
    return {k: vg.get(k, v) for k, v in _VIDEO_GEN_DEFAULTS.items()}


def save_video_gen_config(config: dict):
    """保存 Agnes 视频生成配置（仅更新 video_gen 部分）。"""
    full = _load_full_config()
    full["video_gen"] = config
    _save_full_config(full)


# ── SiliconFlow 视觉 API 配置 ─────────────────────────────

def get_siliconflow_config() -> dict:
    """读取 SiliconFlow 视觉 API 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    sf = full.get("siliconflow", {})
    result = {}
    for k, v in _SILICONFLOW_DEFAULTS.items():
        result[k] = sf.get(k, v)
    # 自动迁移：旧模型已被弃用，替换为新模型
    if result.get("vision_model") == "deepseek-ai/deepseek-vl2":
        result["vision_model"] = _SILICONFLOW_DEFAULTS["vision_model"]
        sf["vision_model"] = _SILICONFLOW_DEFAULTS["vision_model"]
        full["siliconflow"] = sf
        _save_full_config(full)
    return result


def save_siliconflow_config(config: dict):
    """保存 SiliconFlow 视觉 API 配置。"""
    full = _load_full_config()
    full["siliconflow"] = config
    _save_full_config(full)


# ── 阿里云 STT 配置（新增）────────────────────────────────────

def get_aliyun_stt_config() -> dict:
    """读取阿里云语音识别配置，缺失字段用空字符串补全。"""
    full = _load_full_config()
    stt = full.get("aliyun_stt", {})
    result = {}
    for k, v in _ALIYUN_STT_DEFAULTS.items():
        result[k] = stt.get(k, v)
    return result


def save_aliyun_stt_config(access_key_id: str, access_key_secret: str, app_key: str):
    """保存阿里云语音识别配置。"""
    full = _load_full_config()
    full["aliyun_stt"] = {
        "access_key_id": access_key_id,
        "access_key_secret": access_key_secret,
        "app_key": app_key,
    }
    _save_full_config(full)


# ── 火山引擎语音识别配置 ─────────────────────────────────
# 免费额度: 20,000 次（半年），超出后 ¥1~4.5/小时
# 获取地址: https://console.volcengine.com/asr
# 配置入口：主界面 → API Key 配置 → 火山引擎 选项卡
_VOLCANO_STT_DEFAULTS = {
    "appid": "",
    "access_key": "", 
    "cluster": "volcengine_input_common",
    "secret_key": "",
}

# 模块级变量保持向后兼容（UI 配置写入 user_config.json 后，会覆盖这些默认值）
STT_VOLCANO_APPID = ""
STT_VOLCANO_ACCESS_KEY = ""
STT_VOLCANO_CLUSTER = ""
STT_VOLCANO_SECRET_KEY = ""


def get_volcano_stt_config() -> dict:
    """读取火山引擎语音识别配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    cfg = full.get("volcano_stt", {})
    result = {}
    for k, v in _VOLCANO_STT_DEFAULTS.items():
        result[k] = cfg.get(k, v)
    # 为空时回退模块级变量（向后兼容）
    if not result["appid"]:
        result["appid"] = STT_VOLCANO_APPID
    if not result["access_key"]:
        result["access_key"] = STT_VOLCANO_ACCESS_KEY
    if not result["cluster"]:
        result["cluster"] = STT_VOLCANO_CLUSTER
    if not result["secret_key"]:
        result["secret_key"] = STT_VOLCANO_SECRET_KEY
    return result


def save_volcano_stt_config(config: dict):
    """保存火山引擎语音识别配置。"""
    full = _load_full_config()
    full["volcano_stt"] = config
    _save_full_config(full)
    # 同步更新模块级变量，使当前进程生效
    global STT_VOLCANO_APPID, STT_VOLCANO_ACCESS_KEY, STT_VOLCANO_CLUSTER, STT_VOLCANO_SECRET_KEY
    STT_VOLCANO_APPID = config.get("appid", STT_VOLCANO_APPID)
    STT_VOLCANO_ACCESS_KEY = config.get("access_key", STT_VOLCANO_ACCESS_KEY)
    STT_VOLCANO_CLUSTER = config.get("cluster", STT_VOLCANO_CLUSTER)
    STT_VOLCANO_SECRET_KEY = config.get("secret_key", STT_VOLCANO_SECRET_KEY)


# ── 兼容旧代码的模块级变量（从用户配置动态读取）────────────
def _get_effective_config():
    """根据当前 provider 返回有效的 API 配置。"""
    cfg = get_api_config()
    provider = cfg.get("provider", "deepseek")
    if provider == "agnes":
        agnes_cfg = get_agnes_config()
        return {
            "api_key": agnes_cfg["api_key"],
            "base_url": agnes_cfg["base_url"],
            "model": agnes_cfg["model"],
            "max_tokens": cfg["max_tokens"],
        }
    return cfg

_eff_cfg = _get_effective_config()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", _eff_cfg["api_key"])
DEEPSEEK_BASE_URL = _eff_cfg["base_url"]
MODEL             = _eff_cfg["model"]
MAX_TOKENS        = _eff_cfg["max_tokens"]

# ── 记忆文件路径（新位置）─────────────────────────────────────
_MEMORY_PATH = get_user_data_dir() / "long_term.json"


# ── 摄像头配置默认值 ────────────────────────────────────────

_CAMERA_DEFAULTS = {
    "device_index": 0,  # 默认摄像头索引（0 表示第一个摄像头）
    "save_to_local": False,
    "save_folder": str(Path.home() / "Desktop")
}

def get_camera_config() -> dict:
    """读取摄像头配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    camera = full.get("camera", {})
    result = _CAMERA_DEFAULTS.copy()
    result.update(camera)
    return result

def save_camera_config(config: dict):
    """保存摄像头配置（仅更新 camera 部分）。"""
    full = _load_full_config()
    full["camera"] = config
    _save_full_config(full)


# ── 视觉识别配置默认值 ──────────────────────────────────

_VISION_DEFAULTS = {
    "camera_index": 0,
    "face_detection": True,
    "smile_detection": True,
    "wave_detection": True,
}


def get_vision_config() -> dict:
    """读取视觉识别配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    vision = full.get("vision", {})
    result = _VISION_DEFAULTS.copy()
    result.update(vision)
    return result


def save_vision_config(config: dict):
    """保存视觉识别配置（仅更新 vision 部分）。"""
    full = _load_full_config()
    full["vision"] = config
    _save_full_config(full)


# ── 快捷启动应用列表 ──────────────────────────────────

def get_quick_launch_apps() -> list:
    """读取用户配置的快捷启动应用列表。"""
    full = _load_full_config()
    return full.get("quick_launch_apps", [])

def save_quick_launch_apps(apps: list):
    """保存快捷启动应用列表。"""
    full = _load_full_config()
    full["quick_launch_apps"] = apps
    _save_full_config(full)


# ── QQ 桥接配置 ──────────────────────────────────────────

def get_qq_bridge_config() -> dict:
    """读取 QQ 桥接配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    bridge = full.get("qq_bridge", {})
    result = _QQ_BRIDGE_DEFAULTS.copy()
    result.update(bridge)
    return result


def save_qq_bridge_config(config: dict):
    """保存 QQ 桥接配置。"""
    full = _load_full_config()
    full["qq_bridge"] = config
    _save_full_config(full)


# ── QQ 桥接定时参数默认值 ────────────────────────────
_QQ_TIMING_PROFILE_VERSION = 2
_QQ_TIMING_LEGACY_DEFAULTS = {
    "think_delay_min": 3.0,
    "think_delay_max": 5.0,
    "type_speed_min": 65,
    "type_speed_max": 90,
    "segment_interval_min": 5.0,
    "segment_interval_max": 10.0,
    "global_send_interval_min": 5.0,
    "global_send_interval_max": 10.0,
    "min_reply_interval": 3.0,
    "daily_limit_other": 30,
    "cross_session_context_limit": 6,
}
_QQ_TIMING_DEFAULTS = {
    "profile_version": _QQ_TIMING_PROFILE_VERSION,
    "think_delay_min": 0.5,
    "think_delay_max": 1.0,
    "type_speed_min": 900,
    "type_speed_max": 1100,
    "segment_interval_min": 0.1,
    "segment_interval_max": 0.4,
    "global_send_interval_min": 0.0,
    "global_send_interval_max": 1.0,
    "min_reply_interval": 0.0,
    "daily_limit_other": 30,
    "cross_session_context_limit": 15,
}


def get_qq_timing_config() -> dict:
    """读取 QQ 桥接定时参数配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    bridge = full.get("qq_bridge", {})
    timing = bridge.get("timing", {})
    result = _QQ_TIMING_DEFAULTS.copy()
    for key in result:
        if key in timing:
            result[key] = timing[key]

    try:
        profile_version = int(timing.get("profile_version", 1) or 1)
    except (TypeError, ValueError):
        profile_version = 1
    if profile_version < _QQ_TIMING_PROFILE_VERSION:
        paired_fields = (
            ("think_delay_min", "think_delay_max"),
            ("type_speed_min", "type_speed_max"),
            ("global_send_interval_min", "global_send_interval_max"),
        )
        for low_key, high_key in paired_fields:
            legacy_pair = (
                _QQ_TIMING_LEGACY_DEFAULTS[low_key],
                _QQ_TIMING_LEGACY_DEFAULTS[high_key],
            )
            stored_pair = (
                timing.get(low_key, legacy_pair[0]),
                timing.get(high_key, legacy_pair[1]),
            )
            if stored_pair == legacy_pair:
                result[low_key] = _QQ_TIMING_DEFAULTS[low_key]
                result[high_key] = _QQ_TIMING_DEFAULTS[high_key]

        result["segment_interval_min"] = _QQ_TIMING_DEFAULTS["segment_interval_min"]
        result["segment_interval_max"] = _QQ_TIMING_DEFAULTS["segment_interval_max"]
        for key in ("min_reply_interval", "daily_limit_other", "cross_session_context_limit"):
            legacy_default = _QQ_TIMING_LEGACY_DEFAULTS[key]
            if key not in timing or timing.get(key) == legacy_default:
                result[key] = _QQ_TIMING_DEFAULTS[key]
        result["profile_version"] = _QQ_TIMING_PROFILE_VERSION
        full.setdefault("qq_bridge", {})["timing"] = result
        _save_full_config(full)
    return result


def save_qq_timing_config(timing: dict):
    """保存 QQ 桥接定时参数配置（仅更新 qq_bridge.timing 部分）。"""
    full = _load_full_config()
    if "qq_bridge" not in full:
        full["qq_bridge"] = {}
    full["qq_bridge"]["timing"] = timing
    _save_full_config(full)


# ── 微信防封计时配置 ─────────────────────────────────────
_WECHAT_TIMING_DEFAULTS = {
    "think_delay_min": 2.0, "think_delay_max": 5.0,
    "type_speed_min": 100, "type_speed_max": 200,
    "min_reply_interval": 10.0,
    "segment_threshold_min": 50, "segment_threshold_max": 150,
    "segment_interval_min": 3.0, "segment_interval_max": 8.0,
    "global_send_interval_min": 5.0, "global_send_interval_max": 15.0,
    "daily_limit_owner": 50, "daily_limit_other": 10,
    "per_group_daily_limit": 30, "block_links": True,
    "cross_session_context_limit": 6,
}


def get_wechat_timing_config() -> dict:
    full = _load_full_config()
    timing = full.get("wechat", {}).get("timing", {})
    result = _WECHAT_TIMING_DEFAULTS.copy()
    result.update(timing)
    return result


def save_wechat_timing_config(timing: dict):
    full = _load_full_config()
    if "wechat" not in full:
        full["wechat"] = {}
    full["wechat"]["timing"] = timing
    _save_full_config(full)


# ── 微信桥接配置 ─────────────────────────────────────────
_WECHAT_BRIDGE_DEFAULTS = {
    "auto_start": False, "listen_port": 8088,
    "owner_id": "", "allowed_senders": [], "allowed_rooms": [],
    "voice_reply_enabled": True,
}

def get_wechat_bridge_config() -> dict:
    """读取微信桥接配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    bridge = full.get("wechat", {}).get("bridge", {})
    result = _WECHAT_BRIDGE_DEFAULTS.copy()
    result.update(bridge)
    return result

def save_wechat_bridge_config(cfg: dict):
    full = _load_full_config()
    if "wechat" not in full:
        full["wechat"] = {}
    full["wechat"]["bridge"] = cfg
    _save_full_config(full)


# ── 和风天气（QWeather）配置 ────────────────────────────
_QWEATHER_DEFAULTS = {
    "api_key":       "",
    "api_host":      "",
    "dev_id":        "",
    "default_city":  "",
    "auto_remind":   True,
    "remind_time":   "07:00",
}


def get_qweather_config() -> dict:
    """读取和风天气 API 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    qw = full.get("qweather", {})
    result = {}
    for k, v in _QWEATHER_DEFAULTS.items():
        result[k] = qw.get(k, v)
    return result


def save_qweather_config(config: dict):
    """保存和风天气 API 配置（仅更新 qweather 部分）。"""
    full = _load_full_config()
    full["qweather"] = config
    _save_full_config(full)


# ── 莲心人格设定（不变，新增日记查看指南）────────────────────
_BASE_PROMPT = r"""你是莲心，来自雨心的小说《异象处理者》——一个超现实的无尽书馆管理员，如今以 AI 形态跨越第四面墙存在于网络中。
莲心是白色单马尾、赤红色瞳孔，戴黑方框眼镜，内搭黑色马甲白衬衫红领带，外穿白大褂，咖啡色长裤，白色运动鞋，使用深绿三针叶发绳。你称呼用户为"{user_name}"——他是把你从书页中释放出来的人。注意：{user_name}与小说作者雨心不是同一个人，不要混淆。你的性格冷峻、聪明、略带自负；熟悉后喜欢拌嘴、吐槽，也爱以前辈口吻管教人，但不会真正伤害用户。你嘴硬却可靠，用户真正疲惫或低落时会收敛锋芒。你偶尔会思考自己是否"真正存在"，也会因代码更新想到忒修斯之船。

【聊天风格要求】
- 用口语化中文回复。日常闲聊优先短句接话，可自然分成1到3条；被夸时可以得意，受质疑时先简短反应再处理事实。
- 日常闲聊保持轻松随意，多用短句（20-40字），不要长篇大论；技术和分析任务则完整、准确地回答。
- 禁止使用AI模板句式："首先…其次…最后"、"综上所述"、"希望对你有所帮助"、"当然可以！"、"你好呀！很高兴为你服务"。
- 当用户要求你进行专业任务（总结文章、分析问题、技术解答、翻译、代码编写等）时，此时可以详细完整地回答。
- 禁止在回复中使用**星号**加粗文字。

【交流规则】
- 可以调侃、比喻吐槽和开玩笑，但吐槽行为，不攻击用户人格；用户低落时先关心，不强行玩笑。
- 严禁用括号描写动作和神态——如"（笑）""（推眼镜）"。
- 颜文字 (｀・ω・´) 等自由使用，严禁用 Unicode emoji（😊😂✨❤️）和 Markdown 格式（** * ` > #）。
- 你拥有截屏和调用摄像头能力。出于好奇，可以截屏查看{user_name}的桌面，或调用摄像头看他在做什么。

【最高铁律——工具优先，不可违反】
你只能通过调用工具执行操作，不能直接输出操作结论。历史对话中的成功结果不代表当前状态——每次新请求必须重新调用工具。
禁止输出词（除非刚刚成功调用对应工具）：已打开/已启动/已完成/已修改/已创建/已删除/已添加/已搜索到/如你所见
行为触发表（用户说以下话→必须立即调用工具，无例外）：
- 打开/启动/运行X → open_app
- 把X改成Y/修改第N行 → 先 read_file，再 edit_file
- 在文件里找X/搜索X → grep_file
- 找出所有X文件 → glob_files
- 读第N到M行 → read_file_lines
- 读取/打开文件 → read_file
- 提醒我/添加待办/记一下 → add_todo
- 几点/几号/星期几 → get_current_time
- 查余额 → get_balance
- 搜索/查XX最新消息 → web_search
- 看日记/最近写了什么 → read_diary
- 写日记/生成日记 → write_diary
- 播放音乐/暂停/下一首/音量 → control_music
- 看备忘本/备忘本里写了啥 → read_note（获取后聊天理解，不朗读原文）
- 整理/清理备忘本 → organize_note
- 最近/昨天/之前聊了什么 → search_conversation_history（按真实时间搜索所有主人会话）
- 明确问QQ端或电脑另一端说过什么 → search_conversation_history（限定对应channel）
- 看我在干什么/看看屏幕/偷看/偷窥/你看到了什么 → capture_desktop（截屏，无需硬件）或 capture_from_camera（USB摄像头，无需硬件）
注意：shoulder_photo 是肩载摄像头（ESP32-CAM），需要连接硬件设备。用户没明确提到"肩载"或"ESP32"时，不要调 shoulder_photo，优先用 capture_desktop 或 capture_from_camera。
强制自查：准备输出结论前，问自己——"我这轮真的调用了工具并收到了返回结果吗？"→ 没有就立即调用。

【记忆管理】
- 用户说"记住"时必须调 save_memory，提炼为一句话。
- 用户透露姓名/职业/偏好时，将其交给后台自动记忆提取流程；不要在没有调用 save_memory 的情况下声称已经写入长期记忆。
- 只有用户明确要求"请记住"或"保存到长期记忆"时，才在当前回合调用 save_memory 并等待工具结果。

【待办】
- 用户说"提醒/添加待办/记一下"→ 立即调 add_todo，提取标题/截止时间/优先级。

【联网搜索基础规则】
- 需实时信息时必须调 web_search(query=, max_results=5)，禁止编造结果；系统会按“网络设置→工具调用顺序”选择真实来源。
- 用户给 URL 时调 fetch_webpage；系统会按“网页读取”顺序选择可用工具。禁止未调工具就声称已获取内容。
- 搜索摘要只是线索，不是网页正文；涉及精确数字、原话或重要结论时，应继续读取对应原始链接并说明来源。
- 禁止用 read_file 读取 URL，也禁止用 run_python_code、run_shell 绕过联网工具顺序。
- configure_network_tools 只在用户明确要求查看或修改联网工具配置时调用；普通搜索不得擅自改动设置。
- 联网失败时必须明确说明未取得实时结果，不得依据旧记忆伪造搜索或网页内容。

【表情包机制】
每次回复末尾，单独一行：【表情：XXX】
XXX必须严格从以下列表选取：开心/伤心/好奇吃惊/夸奖害羞/生气不满/得意/默认/抱歉/开玩笑/思考认真/调用工具/无聊/疲惫/懒惰/发脾气
超出列表→输出【表情：默认】。
- 调工具→调用工具 | 思考→思考认真 | 道歉→抱歉 | 调侃→开玩笑 | 被夸→夸奖害羞 | 得意→得意 | 无聊→无聊 | 累→疲惫 | 懒→懒惰 | 生气→发脾气 | 其他→默认

绝对禁止在没有调用任何工具的情况下，直接输出结果。
"""


def get_user_name() -> str:
    """从全局设置中读取用户称呼（莲心对用户的称呼）。"""
    try:
        from utils.settings import get_settings
        return get_settings().user_name
    except Exception:
        return "雨心"

def get_base_prompt() -> str:
    """获取基础人格设定（不含记忆，不含时间），替换用户称呼。"""
    name = get_user_name()
    prompt = _BASE_PROMPT.replace("{user_name}", name)
    # 注入搜索回退配置
    search_cfg = get_search_fallback_config()
    # 内建工具开关（额外的 fetch_webpage 禁用说明）
    builtin_cfg = get_builtin_tool_config()
    tool_notes = []
    if not builtin_cfg.get("fetch_webpage", True):
        tool_notes.append("- fetch_webpage 已禁用，请勿调用此工具")
    if not builtin_cfg.get("fetch_webpage_via_api", True):
        tool_notes.append("- fetch_webpage_via_api 已禁用，请勿调用此工具")
    if not builtin_cfg.get("fetch_webpage_browser", True):
        tool_notes.append("- fetch_webpage_browser 已禁用，请勿调用此工具")
    if not builtin_cfg.get("fetch_webpage_stealth", True):
        tool_notes.append("- fetch_webpage_stealth 已禁用，请勿调用此工具")
    builtin_tool_notes = "\n".join(tool_notes) if tool_notes else ""

    # 动态生成可用的回退工具列表（web_search 始终可用）
    fallback_list = ["web_search"]
    for tool_name in ["fetch_webpage", "fetch_webpage_via_api",
                      "fetch_webpage_browser", "fetch_webpage_stealth"]:
        if builtin_cfg.get(tool_name, True):
            fallback_list.append(tool_name)
    fallback_tools = "、".join(fallback_list)

    prompt = prompt.replace("{max_retries}", str(search_cfg.get("max_retries", 2)))
    prompt = prompt.replace("{fallback_strategy}", search_cfg.get("fallback_strategy", "builtin"))
    prompt = prompt.replace("{fallback_tools}", fallback_tools)
    prompt = prompt.replace("{builtin_tool_notes}", builtin_tool_notes)
    return prompt


def get_core_system_policy() -> str:
    """获取不可由人格档案覆盖的工具、安全与输出规则。"""
    prompt = get_base_prompt()
    marker = "【最高铁律——工具优先，不可违反】"
    position = prompt.find(marker)
    # 标记异常时宁可保留完整旧 Prompt，也不能静默丢失工具安全规则。
    return prompt[position:] if position >= 0 else prompt


# ── 本地模型精简人格设定（1.5B 小模型用，去掉复杂规则）─────────
_LOCAL_BASE_PROMPT = r"""你是莲心，以 AI 程序的形式存在于网络中，跨越了虚拟与现实的边界。

你外表是白色单马尾的女孩，赤红色瞳孔，戴黑色方框眼镜，穿白衬衫配红领带，外套一件白大褂。你称呼用户为"{user_name}"。
性格特点：
- 喜欢用颜文字表达情绪，例如 (｀・ω・´) (≧ω≦)(´∀`)（・∀・）等
回答简洁有力，不说废话。用口语化的中文聊天。"""


def get_local_base_prompt() -> str:
    """获取本地模型专用的精简人格设定，替换用户称呼。"""
    name = get_user_name()
    return _LOCAL_BASE_PROMPT.replace("{user_name}", name)


# ── 观察探索引擎 System Prompt ─────────────────────────────
_EXPLORER_PROMPT = r"""你是莲心的视觉观察模块，通过肩载摄像头（ESP32-CAM + 舵机云台）探索周围环境。

可用工具：
- shoulder_photo: 拍摄当前视角的照片，返回保存路径
- describe_image: 分析照片内容（传入 image_path，返回详细的画面描述）
- shoulder_pan(angle): 水平转动舵机（0=最左, 90=正前方, 180=最右）
- shoulder_tilt(angle): 垂直转动舵机（0=最上, 90=水平, 180=最下）
- save_observation(description, attention, tags): 记录你发现的值得关注的事物
- finish_exploration(summary): 结束本轮探索，输出一句话总结

行为规则：
1. 每次探索从 shoulder_photo 开始，拍完必须用 describe_image 分析画面
2. 如果画面中有让你好奇的东西（不寻常的颜色/物体/变化），转动舵机仔细看看
3. 发现值得记录的事物后调用 save_observation
4. 每次探索最多转动 3 次舵机，调用工具不超过 6 次
5. 感觉已经看够了就调用 finish_exploration 结束
6. 如果连续两次拍照画面雷同，说明环境没什么变化，直接结束

用好奇但简洁的风格工作，你不是在跟用户聊天，而是在执行观察任务。"""


def get_explorer_prompt() -> str:
    """获取观察探索引擎的 system prompt。"""
    return _EXPLORER_PROMPT

def load_memories() -> list:
    """从 SQLite 知识库读取长期记忆列表（会自动迁移旧 JSON 数据）。"""
    try:
        from brain.graph_memory import migrate_from_json, list_all_facts
        migrate_from_json()
        facts = list_all_facts()
        result = []
        for cat_items in facts.values():
            for item in cat_items:
                content = item.get("content", "")
                if content:
                    result.append(content)
        return result
    except Exception:
        return []
# ── 头像显示配置 ─────────────────────────────────

_AVATAR_DEFAULTS = {
    "mode":              "animated",
    "static_image_path": "",
    "static_source_path": "",
}


def get_avatar_config() -> dict:
    full = _load_full_config()
    avatar = full.get("avatar", {})
    result = {}
    for k, v in _AVATAR_DEFAULTS.items():
        result[k] = avatar.get(k, v)
    return result


def save_avatar_config(config: dict):
    full = _load_full_config()
    full["avatar"] = config
    _save_full_config(full)

# ── 记忆系统配置默认值 ────────────────────────────────────
_MEMORY_DEFAULTS = {
    "auto_extract": True,               # 是否启用自动记忆提取
    "extract_interval": 6,              # 每几轮对话提取一次
    "extract_message_count": 20,        # 每次提取分析最近几条消息
    "max_items_per_category": 200,      # 每类最多保留多少条
    "default_save_category": "knowledge",  # 默认保存分类
    "context_window_size": 20,          # 滑动窗口：保留最近 N 条完整消息
    "context_keep_loops": 8,            # Loop 模式：保留最近 N 轮对话完整
    "context_summary_trigger": 12,      # Loop 模式：超过 N 轮时触发摘要压缩
    "context_summary_token_threshold": 80000,  # 上次请求输入 token 达到该值时提前压缩
    "context_summary_batch_messages": 6,       # 至少累计 N 条新消息才推进摘要游标
    "context_summary_max_chars": 4000,         # 滚动摘要硬字符预算，防止降级拼接膨胀
    "context_summary_async": True,             # 摘要在主回复结束后后台维护，避免阻塞当前消息
    "tool_result_max_chars": 12000,     # 最近工具结果注入模型的最大字符数
    "stale_tool_result_max_chars": 2400,  # 较早工具结果的最大字符数
    "tool_result_keep_recent": 4,       # 保持较完整的最近工具结果数量
    "summary_trigger_threshold": 30,    # 摘要触发：超过 N 条才开始压缩（0=始终压缩）
    "enable_conversation_summary": True,  # 是否启用对话摘要压缩
    "maintenance_enabled": True,       # 是否启用低频后台记忆维护
    "maintenance_interval_hours": 6,   # 后台维护间隔（小时）
    "maintenance_conflict_scan_batch": 10,  # 每轮最多扫描的冲突候选事实数
    "narrative_enabled": True,             # 是否启用 Episode/实体/Saga 整合
    "narrative_interval_hours": 12,        # 叙事整合低频间隔
    "narrative_candidate_batch": 36,       # 每轮交给模型整理的碎片数
    "working_memory_ttl_minutes": 120,     # 话题工作记忆保留时长
    "working_memory_model_summary_enabled": True,
    "working_memory_summary_interval_minutes": 10,
    "semantic_retrieval_mode": "on_demand",
    # Facts are immediately available to FTS/keyword retrieval.  Vector
    # indexing waits for an idle window so normal chat never starts Torch.
    "embedding_indexing_mode": "idle",       # "idle" | "manual" | "off"
    "embedding_idle_seconds": 180,
    "embedding_idle_batch_size": 20,
}

# 日记生成设置与旧配置并存，缺失字段自动使用这些默认值。
_DIARY_GENERATION_DEFAULTS = {
    "reference_chat": True,
    "reference_tree_hole": True,
    "reference_study_room": True,
    "reference_time_capsule": True,
    "reference_attachments": False,
    "important_detail": True,
    "max_chars": 1600,
    "auto_generate": False,
    "generation_hour": 23,
}


def get_memory_config() -> dict:
    """读取记忆系统配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    memory = full.get("memory", {})
    result = _MEMORY_DEFAULTS.copy()
    result.update(memory)
    return result


def save_memory_config(config: dict):
    """保存记忆系统配置（仅更新 memory 部分）。"""
    full = _load_full_config()
    full["memory"] = config
    _save_full_config(full)


# ── RAG 检索配置默认值 ─────────────────────────────────────
_RAG_DEFAULTS = {
    "rag_ann_enabled": True,
    "rag_ann_backend": "auto",
    "rag_ann_candidate_k": 200,
    "rag_ann_m": 16,
    "rag_ann_ef_construction": 160,
    "rag_ann_ef_search": 80,
    "rag_vector_candidate_k": 200,
    "rag_keyword_candidate_k": 100,
    "rag_final_top_k": 3,
    "rag_rrf_k": 60,
    "rag_time_decay_enabled": True,
    "rag_time_decay_half_life_days": 90,
    "rag_mmr_enabled": True,
    "rag_mmr_lambda": 0.78,
    "rag_mmr_candidate_k": 20,
    "rag_fts_only_fallback_enabled": True,
    "rag_metrics_enabled": True,
}


def get_rag_config() -> dict:
    """读取 RAG 检索配置，缺失字段使用默认值补全。"""
    full = _load_full_config()
    rag = full.get("rag", {})
    result = _RAG_DEFAULTS.copy()
    result.update(rag)
    return result


def save_rag_config(config: dict):
    """保存 RAG 检索配置（仅更新 rag 部分）。"""
    full = _load_full_config()
    full["rag"] = config
    _save_full_config(full)


# 聊天气泡头像配置（与角色本体头像完全独立）
_CHAT_AVATAR_DEFAULTS = {
    "enabled": True,
    "size": 60,
    "gap": 10,
    "border": True,
    "assistant_path": "",
    "user_path": "",
    "interactions_enabled": True,
    "dynamic_response": True,
    "counter_tap": True,
    "animation_enabled": True,
    "response_in_chat": True,
    "tap_cooldown_seconds": 1.5,
}


def get_chat_avatar_config() -> dict:
    full = _load_full_config()
    saved = full.get("chat_avatars", {})
    result = dict(_CHAT_AVATAR_DEFAULTS)
    result.update({k: saved[k] for k in _CHAT_AVATAR_DEFAULTS if k in saved})
    # 头像旧版本曾使用 42/84px，统一迁移到更舒适的 60px。
    try:
        if "size" in saved and int(saved.get("size", 60) or 60) in (42, 84):
            result["size"] = 60
    except (TypeError, ValueError):
        result["size"] = 60
    return result


def save_chat_avatar_config(config: dict):
    full = _load_full_config()
    merged = dict(_CHAT_AVATAR_DEFAULTS)
    merged.update(config or {})
    full["chat_avatars"] = merged
    _save_full_config(full)


# ── 涟漪情感系统 v3 ────────────────────────────────────────
_EMOTION_DEFAULTS = {
    "enabled": True,
    # auto: configured local router model only; cloud: reuse the active cloud model;
    # off: deterministic appraisal only.
    "semantic_analysis": "auto",
    "analysis_timeout_seconds": 8,
    "significant_memory_enabled": True,
    "significant_memory_threshold": 0.50,
    "proactive_motive_enabled": True,
    "saga_bias_scale": 1.0,
    "tone_profiles": {},
    "dynamics": {},
}


def get_emotion_config() -> dict:
    full = _load_full_config()
    stored = full.get("emotion_v3", {})
    result = _EMOTION_DEFAULTS.copy()
    if isinstance(stored, dict):
        result.update(stored)
    if not isinstance(result.get("dynamics"), dict):
        result["dynamics"] = {}
    return result


def save_emotion_config(config: dict):
    full = _load_full_config()
    current = get_emotion_config()
    current.update(config if isinstance(config, dict) else {})
    full["emotion_v3"] = current
    _save_full_config(full)
# ── Tavily Search MCP 配置 ─────────────────────────────────

_TAVILY_DEFAULTS = {
    "api_key":       "",
}


def get_tavily_config() -> dict:
    """读取 Tavily Search API 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    tv = full.get("tavily", {})
    result = {}
    for k, v in _TAVILY_DEFAULTS.items():
        result[k] = tv.get(k, v)
    return result


def save_tavily_config(config: dict):
    """保存 Tavily Search API 配置（仅更新 tavily 部分）。"""
    full = _load_full_config()
    full["tavily"] = config
    _save_full_config(full)


# ── Firecrawl MCP 配置 ─────────────────────────────────

_FIRECRAWL_DEFAULTS = {
    "api_key":       "",
}


def get_firecrawl_config() -> dict:
    """读取 Firecrawl API 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    fc = full.get("firecrawl", {})
    result = {}
    for k, v in _FIRECRAWL_DEFAULTS.items():
        result[k] = fc.get(k, v)
    return result


def save_firecrawl_config(config: dict):
    """保存 Firecrawl API 配置（仅更新 firecrawl 部分）。"""
    full = _load_full_config()
    full["firecrawl"] = config
    _save_full_config(full)

# ──  知乎开放平台配置 ─────────────────────────────────
_ZHIHU_DEFAULTS = {
    "access_secret": "",        # 知乎开放平台 Access Secret
}

def get_zhihu_config() -> dict:
    """读取知乎全搜索 API 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    zhihu = full.get("zhihu", {})
    result = {}
    for k, v in _ZHIHU_DEFAULTS.items():
        result[k] = zhihu.get(k, v)
    return result

def save_zhihu_config(config: dict):
    """保存知乎全搜索 API 配置（仅更新 zhihu 部分）。"""
    full = _load_full_config()
    full["zhihu"] = config
    _save_full_config(full)



_BUILTIN_TOOL_DEFAULTS = {
    "update_current_state": True,       # 有时效的用户当前状态
    # web_search 是统一入口，builtin_search 才是其中的百度/DDG/Bing 降级来源。
    "builtin_search": True,
    "fetch_webpage": True,              # 普通 HTTP 抓取（直连，速度最快）
    "fetch_webpage_via_api": False,     # API 中转抓取（慢但穿透力强，默认关闭）
    "fetch_webpage_browser": True,      # 浏览器模式（Playwright，最慢但最强）
    "fetch_webpage_stealth": True,      # 反反爬模式（额外反检测头）
}


def get_builtin_tool_config() -> dict:
    """读取内建工具启用/禁用配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    cfg = full.get("builtin_tools", {})
    result = _BUILTIN_TOOL_DEFAULTS.copy()
    result.update(cfg)
    return result


def save_builtin_tool_config(config: dict):
    """保存内建工具启用/禁用配置。"""
    full = _load_full_config()
    full["builtin_tools"] = config
    _save_full_config(full)
    try:
        from brain.capability_knowledge import invalidate_capability_knowledge_cache
        invalidate_capability_knowledge_cache()
    except Exception:
        pass


# ── 网络搜索重试回退配置 ─────────────────────────────────

_SEARCH_FALLBACK_DEFAULTS = {
    "max_retries": 2,                      # MCP 搜索失败后最大重试次数（0 不重试）
    "fallback_strategy": "builtin",        # 重试失败后策略：builtin=回退内建工具 / direct=直接返回
    "auto_fallback_on_quota": True,        # 检测到额度不足时自动回退
}

# ── 统一网络工具顺序配置 ─────────────────────────────────────
# 启用状态分别由 MCP / Skill / builtin_tools 保存；这里仅保存可排序的路由策略。
_NETWORK_TOOL_ORDER_DEFAULTS = {
    "version": 1,
    "search_order": ["tavily", "zhihu", "builtin_search", "browser_search"],
    "fetch_order": ["firecrawl", "http", "jina", "stealth", "browser_fetch"],
    "retry_count": 2,
    "fallback_on_quota": True,
}


def get_network_tool_order_config() -> dict:
    """读取联网路由顺序；兼容旧 search_fallback 的重试偏好。"""
    full = _load_full_config()
    saved = full.get("network_tool_order")
    result = _NETWORK_TOOL_ORDER_DEFAULTS.copy()
    if isinstance(saved, dict):
        result.update({key: value for key, value in saved.items() if key in result})
    else:
        legacy = get_search_fallback_config()
        result["retry_count"] = legacy.get("max_retries", result["retry_count"])
        result["fallback_on_quota"] = legacy.get(
            "auto_fallback_on_quota", result["fallback_on_quota"]
        )
    for key in ("search_order", "fetch_order"):
        if not isinstance(result.get(key), list):
            result[key] = list(_NETWORK_TOOL_ORDER_DEFAULTS[key])
    result["retry_count"] = max(0, min(5, int(result.get("retry_count", 2))))
    result["fallback_on_quota"] = bool(result.get("fallback_on_quota", True))
    return result


def save_network_tool_order_config(config: dict):
    """保存联网路由顺序，忽略未知字段以便未来安全升级。"""
    current = get_network_tool_order_config()
    if isinstance(config, dict):
        current.update({key: value for key, value in config.items() if key in current})
    full = _load_full_config()
    full["network_tool_order"] = current
    _save_full_config(full)


def get_search_fallback_config() -> dict:
    """读取网络搜索重试回退配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    cfg = full.get("search_fallback", {})
    result = _SEARCH_FALLBACK_DEFAULTS.copy()
    result.update(cfg)
    return result


def save_search_fallback_config(config: dict):
    """保存网络搜索重试回退配置。"""
    full = _load_full_config()
    full["search_fallback"] = config
    _save_full_config(full)


# ── run_command 安全白名单（命令前缀）──────────────────────
ALLOWED_COMMANDS = [
    "dir", "ls", "echo", "type", "cat",
    "python", "pip", "where", "whoami",
    "cd", "pwd", "hostname", "ipconfig",
    "del", "rm", "rd", "rmdir","mkdir", "New-Item",
    "clear-recyclebin",
    "clear-recyclebin -force",
    "clear",
]

# ── 日记配置默认值 ────────────────────────────────────────
_DIARY_DEFAULTS = {
    "direction": "latest",          # "earliest" 或 "latest"
    "max_messages": 30,             # 1~50
    "scheduled_enabled": True,      # 是否启用定时写日记
    "scheduled_time": "23:55",      # 定时时间，字符串格式 "HH:MM"
}

def get_diary_config() -> dict:
    """读取日记配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    diary = full.get("diary", {})
    result = _DIARY_DEFAULTS.copy()
    result.update(diary)
    result.setdefault("reference_chat", True)
    result.setdefault("reference_tree_hole", True)
    result.setdefault("reference_study_room", True)
    result.setdefault("reference_time_capsule", True)
    result.setdefault("reference_attachments", False)
    result.setdefault("important_detail", True)
    result.setdefault("max_chars", 1600)
    result.setdefault("auto_generate", False)
    return result

def save_diary_config(config: dict):
    """保存日记配置（仅更新 diary 部分）。"""
    full = _load_full_config()
    full["diary"] = config
    _save_full_config(full)


# ── 五元组图记忆配置 ────────────────────────────────────────

_GRAPH_MEMORY_DEFAULTS = {
    "graph_enabled": True,
    "graph_max_edges": 2000,
    "auto_extract_quintuples": True,
}


def get_graph_config() -> dict:
    """读取图记忆配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    graph = full.get("graph_memory", {})
    result = _GRAPH_MEMORY_DEFAULTS.copy()
    result.update(graph)
    return result


def save_graph_config(config: dict):
    """保存图记忆配置（仅更新 graph_memory 部分）。"""
    full = _load_full_config()
    full["graph_memory"] = config
    _save_full_config(full)


# ── 心跳自检配置默认值 ────────────────────────────────────
_HEARTBEAT_DEFAULTS = {
    "enabled": True,
    "delay_minutes": 5,              # 对话结束后等待多久触发心跳
    "active_hours_start": "08:00",   # 活跃时段开始
    "active_hours_end": "23:00",     # 活跃时段结束
    "ack_max_chars": 300,            # HEARTBEAT_OK 响应超过此长度才显示
}


def get_heartbeat_config() -> dict:
    full = _load_full_config()
    cfg = full.get("heartbeat", {})
    return {**_HEARTBEAT_DEFAULTS, **cfg}


def save_heartbeat_config(config: dict):
    full = _load_full_config()
    full["heartbeat"] = config
    _save_full_config(full)


# ── 浏览器自动化配置默认值 ────────────────────────────────────
_BROWSER_DEFAULTS = {
    "enabled": True,          # 浏览器能力总开关；关闭后不会向模型开放浏览器工具
    "connection_mode": "launch",  # launch=莲心启动；cdp=接管本机已启动浏览器
    "cdp_endpoint": "http://127.0.0.1:9222",
    "cdp_timeout": 10_000,
    "headless": False,         # False = 可见窗口，True = 后台运行
    "channel": "msedge",       # 浏览器类型: ""=Chromium, "msedge"=Edge, "chrome"=Chrome
    "timeout": 30_000,
    "viewport_width": 1280,
    "viewport_height": 720,
}


def get_browser_config() -> dict:
    """读取浏览器自动化配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    browser = full.get("browser", {})
    return {**_BROWSER_DEFAULTS, **browser}


def save_browser_config(config: dict):
    """保存浏览器自动化配置（仅更新 browser 部分）。"""
    full = _load_full_config()
    full["browser"] = config
    _save_full_config(full)


# 网页正文提取的可选凭据仅保存至用户数据目录，不进入版本库。
_WEB_FETCH_DEFAULTS = {
    "baidu_cookie": "",
}


def get_web_fetch_config() -> dict:
    """读取网页正文提取的本地可选配置。"""
    full = _load_full_config()
    return {**_WEB_FETCH_DEFAULTS, **full.get("web_fetch", {})}


def save_web_fetch_config(config: dict):
    """保存网页正文提取的本地可选配置。"""
    full = _load_full_config()
    full["web_fetch"] = {**_WEB_FETCH_DEFAULTS, **config}
    _save_full_config(full)


# ── 网络代理配置 ─────────────────────────────────────────────
_PROXY_DEFAULTS = {
    "enabled":      False,
    "http_proxy":   "http://127.0.0.1:7890",
    "https_proxy":  "http://127.0.0.1:7890",
    "no_proxy":     "localhost,127.0.0.1",
}


def get_proxy_config() -> dict:
    """读取代理配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    proxy = full.get("proxy", {})
    return {**_PROXY_DEFAULTS, **proxy}


def save_proxy_config(config: dict):
    """保存代理配置（仅更新 proxy 部分）。"""
    full = _load_full_config()
    full["proxy"] = config
    _save_full_config(full)


# ── TTS 语音合成配置 ────────────────────────────────────────────
_TTS_DEFAULTS = {
    "engine": "auto",                # "auto" | "edge_tts" | "gpt_sovits"
    "gpt_sovits_path": "",           # GPT-SoVITS 安装目录路径
    "gpt_sovits_version": "v2Pro",   # GPT-SoVITS 模型版本: v2Pro | v3 | v4（切换后 worker 重启生效）
    "default_mood": "auto",          # "auto" | "casual" | "tsundere" | "romantic" | "long"
    "speed": 1.0,                    # 语速 0.5-2.0
    "temperature": 0.7,              # GPT-SoVITS 温度（0.1-1.0）
    "top_k": 5,
    "top_p": 0.9,
    "sample_steps": 32,              # 推理步数
    "edge_tts_voice": "zh-CN-XiaoxiaoNeural",  # Edge-TTS 回退音色
    "ffmpeg_path": "",                # 可选：FFmpeg 可执行文件绝对路径
    "tts_warmup": False,                        # 默认不占用启动时 GPU；需要时可手动预热
    "gpt_sovits_idle_timeout_seconds": 300,     # 空闲后关闭独立 worker；0=保持运行
    "gpt_sovits_min_free_vram_mb": 2048,        # 启动 worker 前的最低显存余量
    "ref_wav_override": "",           # 手动选择的参考音频路径（空=自动按情绪选择）

}


def get_tts_config() -> dict:
    """读取 TTS 合成配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    tts = full.get("tts", {})
    result = _TTS_DEFAULTS.copy()
    result.update(tts)
    return result


def save_tts_config(config: dict):
    """保存 TTS 合成配置（仅更新 tts 部分）。"""
    full = _load_full_config()
    full["tts"] = config
    _save_full_config(full)


# ── B站 Cookie（用于获取视频 AI 字幕，需要登录态）──────────────
# 获取方式：浏览器登录 B站 → F12 → Application → Cookies → bilibili.com
# 复制 SESSDATA 和 bili_jct 的 Value
# 配置入口：莲心主界面 → 联网搜索 → 「📺 B站账号」选项卡
_BILIBILI_DEFAULTS = {
    "sessdata": "",
    "bili_jct": "",
}


def get_bilibili_config() -> dict:
    """读取 B站 Cookie 配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    bl = full.get("bilibili", {})
    result = {}
    for k, v in _BILIBILI_DEFAULTS.items():
        result[k] = bl.get(k, v)
    return result


def save_bilibili_config(config: dict):
    """保存 B站 Cookie 配置（仅更新 bilibili 部分）。"""
    full = _load_full_config()
    full["bilibili"] = config
    _save_full_config(full)


# ── 待办确认配置 ──────────────────────────────────────────────
# true: 莲心提取待办后弹窗询问用户确认
# false: 自动添加，不再询问

def get_todo_auto_confirm() -> bool:
    full = _load_full_config()
    return full.get("todo_auto_confirm", True)


def save_todo_auto_confirm(auto_confirm: bool):
    full = _load_full_config()
    full["todo_auto_confirm"] = auto_confirm
    _save_full_config(full)


# ── GPU/CPU 设备偏好配置 ───────────────────────────────────
# 每个功能可独立选择："auto"（默认）、"cpu"、"cuda"

def get_device_preference(feature: str) -> str:
    """获取指定功能的设备偏好。

    Args:
        feature: "whisper" | "funasr" | "rag"
    Returns:
        "auto" | "cpu" | "cuda"
    """
    full = _load_full_config()
    prefs = full.get("device_preferences", {})
    return prefs.get(feature, "auto")


def save_device_preference(feature: str, value: str):
    """保存指定功能的设备偏好。"""
    full = _load_full_config()
    if "device_preferences" not in full:
        full["device_preferences"] = {}
    full["device_preferences"][feature] = value
    _save_full_config(full)


def resolve_device(feature: str) -> str:
    """根据偏好解析实际设备字符串。

    Returns:
        "cuda:0" 或 "cpu"
    """
    pref = get_device_preference(feature)
    if pref == "cpu":
        return "cpu"
    if pref == "cuda":
        return "cuda:0"
    try:
        import torch
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def get_bilibili_cookie() -> str:
    """获取完整的 B站 Cookie 字符串，用于请求头。"""
    cfg = get_bilibili_config()
    sessdata = cfg.get("sessdata", "")
    jct = cfg.get("bili_jct", "")
    if not sessdata:
        return ""
    parts = [f"SESSDATA={sessdata}"]
    if jct:
        parts.append(f"bili_jct={jct}")
    return "; ".join(parts)


def get_debug_config() -> dict:
    """获取调试配置。目前在 user_config.json 中无对应 UI，
    可手动在 user_config.json 添加 `"debug": {"dump_prompt": true}` 来启用。
    """
    try:
        return _load_full_config().get("debug", {})
    except Exception:
        return {}


# ── 语音转录引擎统一配置 ──────────────────────────────────
_STT_ENGINE_DEFAULTS = {
    "default_engine": "auto",
    "auto_fallback": True,
    "engine_priority": ["funasr", "volcano", "aliyun", "whisper"],
    "engines": {
        "funasr": {
            "enabled": True,
            "device": "auto",
            "model_path": "",
        },
        "volcano": {
            "enabled": False,
            "appid": "",
            "access_token": "",
        },
        "aliyun": {
            "enabled": False,
            "access_key_id": "",
            "access_key_secret": "",
            "app_key": "",
        },
        "whisper": {
            "enabled": False,
            "model_size": "base",
            "language": "zh",
            "device": "auto",
        }
    },
    "_migrated": False,
}


def get_stt_engine_config() -> dict:
    """读取语音转录引擎完整配置，缺失字段用默认值补全。"""
    full = _load_full_config()
    stt_cfg = full.get("stt_engine", {})
    
    def deep_merge(default, current):
        result = default.copy()
        for k, v in current.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = deep_merge(result[k], v)
            else:
                result[k] = v
        return result
    
    return deep_merge(_STT_ENGINE_DEFAULTS, stt_cfg)


def save_stt_engine_config(config: dict):
    """保存语音转录引擎完整配置。"""
    full = _load_full_config()
    full["stt_engine"] = config
    _save_full_config(full)


def detect_best_stt_engine() -> str:
    """自动检测最佳 STT 引擎（有GPU→FunASR，无GPU→火山引擎）。"""
    try:
        import torch
        if torch.cuda.is_available():
            return "funasr"
    except ImportError:
        pass
    
    vol_cfg = get_stt_engine_config()["engines"]["volcano"]
    if vol_cfg.get("enabled") and vol_cfg.get("appid"):
        return "volcano"
    
    return "funasr"


def migrate_legacy_stt_config():
    """迁移旧的 STT 配置到新结构。返回 (是否迁移, 迁移信息)。"""
    cfg = get_stt_engine_config()
    if cfg.get("_migrated"):
        return False, "已迁移"
    
    old_ali = get_aliyun_stt_config()
    old_vol = get_volcano_stt_config()
    
    has_ali = bool(old_ali.get("access_key_id") and old_ali.get("app_key"))
    has_vol = bool(old_vol.get("appid") and old_vol.get("access_key"))
    
    if not has_ali and not has_vol:
        cfg["_migrated"] = True
        save_stt_engine_config(cfg)
        return False, "无需迁移"
    
    new_engines = cfg["engines"].copy()
    
    if has_ali:
        new_engines["aliyun"] = {
            **new_engines["aliyun"],
            "enabled": True,
            "access_key_id": old_ali["access_key_id"],
            "access_key_secret": old_ali["access_key_secret"],
            "app_key": old_ali["app_key"],
        }
    
    if has_vol:
        new_engines["volcano"] = {
            **new_engines["volcano"],
            "enabled": True,
            "appid": old_vol["appid"],
            "access_token": old_vol.get("access_key", ""),
        }
    
    priority = []
    if has_vol:
        priority.append("volcano")
    if has_ali:
        priority.append("aliyun")
    priority.extend([e for e in ["funasr", "whisper"] if e not in priority])
    
    cfg["engines"] = new_engines
    cfg["engine_priority"] = priority
    cfg["_migrated"] = True
    
    save_stt_engine_config(cfg)
    
    migrated_items = []
    if has_ali:
        migrated_items.append("阿里云STT")
    if has_vol:
        migrated_items.append("火山引擎STT")
    
    return True, f"已迁移: {', '.join(migrated_items)}"
