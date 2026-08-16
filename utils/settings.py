"""
SettingsManager：莲心全局设置管理器
管理静默模式、语音设置等全局配置
"""

import json
from pathlib import Path
from utils.paths import get_user_data_dir   # 新增导入

_SETTINGS_PATH = get_user_data_dir() / "global_settings.json"
_DEFAULT_BACKGROUND_IMAGE = str(
    Path(__file__).resolve().parent.parent / "assets" / "主界面背景图.jpg"
)

_DEFAULT_SETTINGS = {
    "silent_mode": False,                # 全局静默模式：True=不朗读，False=朗读
    "last_autostart_welcome_date": "",   # 上次自启动欢迎消息发送日期（YYYY-MM-DD）
    "show_exit_confirmation": True,      # 退出时显示确认弹窗：True=显示，False=不显示
    "font_size": 12,                     # 聊天字体大小（像素）
    "galgame_font_size": 12,             # Galgame 字体大小（像素）
    "galgame_font_bold": False,          # Galgame 字体加粗
    "standby_auto_send": True,           # 待机模式自动发送：True=开启
    "standby_auto_send_delay": 5,        # 自动发送延迟（秒）
    "standby_end_word": "完毕",           # 待机模式结束词
    "note_file_path": "",                # 小纸条文件路径（空表示使用默认路径）
    "tts_volume": 1.0,                   # TTS 语音音量 0.0-1.0
    "sfx_volume": 1.0,                   # 音效音量 0.0-1.0
    "music_playlist_index": 0,
    "music_is_playing": False,
    "music_volume": 0.5,
    "music_position": 0.0,          # 新增：播放位置（秒）
    "emotion_probability": 0.6,   # 发表情包概率    默认 60%
    "user_name": "雨心",           # 用户称呼（莲心对用户的称呼）
    "startup_check_enabled": True, # 启动时进行开机体检
    "background_enabled": True,
    "background_source": _DEFAULT_BACKGROUND_IMAGE,
    "background_source_type": "single",
    "background_opacity": 0.22,
    "background_fit_mode": "cover",
    "chat_background_opacity": 0.75,
    "tray_enabled": True,
    "close_behavior": "ask",          # ask / tray / quit
    "minimize_to_tray": False,
    "window_mode": "normal",          # normal / compact / companion
    "window_geometry": {},
    "restore_window_state": True,
    "always_on_top": False,
    "reduced_motion": False,
    "desktop_notifications": True,
    "video_call": {
        "mode": "animation",
        "static_image_path": "",
        "background_mode": "wallpaper",
        "background_image_path": "",
    },
    # 桌面端聊天分段停顿（秒）
    "segment_pause_chat_min": 0.45,
    "segment_pause_chat_max": 1.1,
    "segment_pause_semantic_min": 3.0,
    "segment_pause_semantic_max": 7.0,
}


class SettingsManager:
    def __init__(self):
        self._settings = {}
        self._load()

    def _load(self):
        try:
            if _SETTINGS_PATH.exists():
                data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
                self._settings = data
            else:
                self._settings = _DEFAULT_SETTINGS.copy()
        except Exception:
            self._settings = _DEFAULT_SETTINGS.copy()

    def save(self):
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(
            json.dumps(self._settings, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    @property
    def video_call(self) -> dict:
        value = self._settings.get("video_call", {})
        return dict(value) if isinstance(value, dict) else {}

    def update_video_call(self, **values):
        config = self.video_call
        config.update(values)
        self._settings["video_call"] = config
        self.save()

    @property
    def silent_mode(self) -> bool:
        return self._settings.get("silent_mode", False)

    @silent_mode.setter
    def silent_mode(self, val: bool):
        self._settings["silent_mode"] = val
        self.save()

    @property
    def last_autostart_welcome_date(self) -> str:
        return self._settings.get("last_autostart_welcome_date", "")

    @last_autostart_welcome_date.setter
    def last_autostart_welcome_date(self, val: str):
        self._settings["last_autostart_welcome_date"] = val
        self.save()

    @property
    def show_exit_confirmation(self) -> bool:
        return self._settings.get("show_exit_confirmation", True)

    @show_exit_confirmation.setter
    def show_exit_confirmation(self, val: bool):
        self._settings["show_exit_confirmation"] = val
        self.save()

    @property
    def font_size(self) -> int:
        return self._settings.get("font_size", 12)

    @font_size.setter
    def font_size(self, val: int):
        self._settings["font_size"] = val
        self.save()
    # ========== Galgame 字体 ==========
    @property
    def galgame_font_size(self) -> int:
        return self._settings.get("galgame_font_size", 12)

    @galgame_font_size.setter
    def galgame_font_size(self, val: int):
        self._settings["galgame_font_size"] = val
        self.save()

    @property
    def galgame_font_bold(self) -> bool:
        return self._settings.get("galgame_font_bold", False)

    @galgame_font_bold.setter
    def galgame_font_bold(self, val: bool):
        self._settings["galgame_font_bold"] = val
        self.save()
    # ========== 待机模式 ==========
    @property
    def standby_auto_send(self) -> bool:
        return self._settings.get("standby_auto_send", True)

    @standby_auto_send.setter
    def standby_auto_send(self, val: bool):
        self._settings["standby_auto_send"] = val
        self.save()

    @property
    def standby_auto_send_delay(self) -> int:
        return self._settings.get("standby_auto_send_delay", 5)

    @standby_auto_send_delay.setter
    def standby_auto_send_delay(self, val: int):
        self._settings["standby_auto_send_delay"] = val
        self.save()

    @property
    def standby_end_word(self) -> str:
        return self._settings.get("standby_end_word", "完毕")

    @standby_end_word.setter
    def standby_end_word(self, val: str):
        self._settings["standby_end_word"] = val
        self.save()

    # ========== 小纸条路径 ==========
    @property
    def note_file_path(self) -> str:
        """获取小纸条文件路径，如果未配置则返回默认路径"""
        path = self._settings.get("note_file_path", "")
        if path and Path(path).parent.exists():
            return path
        # 默认路径：用户桌面
        return str(Path.home() / "Desktop" / "小纸条.txt")

    @note_file_path.setter
    def note_file_path(self, val: str):
        self._settings["note_file_path"] = val
        self.save()

    # ========== 音量设置 ==========
    @property
    def tts_volume(self) -> float:
        return self._settings.get("tts_volume", 1.0)

    @tts_volume.setter
    def tts_volume(self, val: float):
        # 确保数值在 0.0-1.0 之间
        val = max(0.0, min(1.0, val))
        self._settings["tts_volume"] = val
        self.save()

    @property
    def sfx_volume(self) -> float:
        return self._settings.get("sfx_volume", 1.0)

    @sfx_volume.setter
    def sfx_volume(self, val: float):
        val = max(0.0, min(1.0, val))
        self._settings["sfx_volume"] = val
        self.save()

    # ========== 音乐盒设置 ==========
    @property
    def music_volume(self) -> float:
        return self._settings.get("music_volume", 0.5)

    @music_volume.setter
    def music_volume(self, val: float):
        self._settings["music_volume"] = max(0.0, min(1.0, val))
        self.save()

    @property
    def music_playlist_index(self) -> int:
        return self._settings.get("music_playlist_index", 0)

    @music_playlist_index.setter
    def music_playlist_index(self, val: int):
        self._settings["music_playlist_index"] = val
        self.save()

    @property
    def music_is_playing(self) -> bool:
        return self._settings.get("music_is_playing", False)

    @music_is_playing.setter
    def music_is_playing(self, val: bool):
        self._settings["music_is_playing"] = val
        self.save()

    @property
    def music_position(self) -> float:
        return self._settings.get("music_position", 0.0)

    @music_position.setter
    def music_position(self, val: float):
        self._settings["music_position"] = val
        self.save()
    @property
    
    def global_smart_reminder(self) -> bool:
        return self._settings.get("global_smart_reminder", False)

    @global_smart_reminder.setter
    def global_smart_reminder(self, val: bool):
        self._settings["global_smart_reminder"] = val
        self.save()

    @property
    def emotion_probability(self) -> float:
        return self._settings.get("emotion_probability", 0.6)

    @emotion_probability.setter
    def emotion_probability(self, val: float):
        val = max(0.0, min(1.0, val))   # 限制在 0~1 之间
        self._settings["emotion_probability"] = val
        self.save()

    # ========== 用户称呼 ==========
    @property
    def user_name(self) -> str:
        return self._settings.get("user_name", "雨心")

    @user_name.setter
    def user_name(self, val: str):
        val = val.strip()
        if val:
            self._settings["user_name"] = val
            self.save()

    # ========== 启动体检 ==========
    @property
    def startup_check_enabled(self) -> bool:
        return self._settings.get("startup_check_enabled", True)

    @startup_check_enabled.setter
    def startup_check_enabled(self, val: bool):
        self._settings["startup_check_enabled"] = val
        self.save()

    # ========== 主界面背景 ==========
    @property
    def background_enabled(self) -> bool:
        return bool(self._settings.get("background_enabled", True))

    @background_enabled.setter
    def background_enabled(self, val: bool):
        self._settings["background_enabled"] = bool(val)
        self.save()

    @property
    def background_source(self) -> str:
        return str(self._settings.get("background_source", _DEFAULT_BACKGROUND_IMAGE) or "")

    @background_source.setter
    def background_source(self, val: str):
        self._settings["background_source"] = str(val or "").strip()
        self.save()

    @property
    def background_source_type(self) -> str:
        value = self._settings.get("background_source_type", "single")
        return value if value in {"single", "folder_random", "folder_first"} else "single"

    @background_source_type.setter
    def background_source_type(self, val: str):
        value = val if val in {"single", "folder_random", "folder_first"} else "single"
        self._settings["background_source_type"] = value
        self.save()

    @property
    def background_opacity(self) -> float:
        try:
            return max(0.0, min(1.0, float(self._settings.get("background_opacity", 0.22))))
        except (TypeError, ValueError):
            return 0.22

    @background_opacity.setter
    def background_opacity(self, val: float):
        self._settings["background_opacity"] = max(0.0, min(1.0, float(val)))
        self.save()

    @property
    def background_fit_mode(self) -> str:
        value = self._settings.get("background_fit_mode", "cover")
        return value if value in {"cover", "contain", "stretch"} else "cover"

    @background_fit_mode.setter
    def background_fit_mode(self, val: str):
        value = val if val in {"cover", "contain", "stretch"} else "cover"
        self._settings["background_fit_mode"] = value
        self.save()

    @property
    def chat_background_opacity(self) -> float:
        try:
            return max(0.0, min(1.0, float(self._settings.get("chat_background_opacity", 0.75))))
        except (TypeError, ValueError):
            return 0.75

    @chat_background_opacity.setter
    def chat_background_opacity(self, val: float):
        self._settings["chat_background_opacity"] = max(0.0, min(1.0, float(val)))
        self.save()

    # ========== 窗口、托盘与动效 ==========
    @property
    def tray_enabled(self) -> bool:
        return bool(self._settings.get("tray_enabled", True))

    @tray_enabled.setter
    def tray_enabled(self, val: bool):
        self._settings["tray_enabled"] = bool(val)
        self.save()

    @property
    def close_behavior(self) -> str:
        value = str(self._settings.get("close_behavior", "ask"))
        return value if value in {"ask", "tray", "quit"} else "ask"

    @close_behavior.setter
    def close_behavior(self, val: str):
        self._settings["close_behavior"] = val if val in {"ask", "tray", "quit"} else "ask"
        self.save()

    @property
    def minimize_to_tray(self) -> bool:
        return bool(self._settings.get("minimize_to_tray", False))

    @minimize_to_tray.setter
    def minimize_to_tray(self, val: bool):
        self._settings["minimize_to_tray"] = bool(val)
        self.save()

    @property
    def window_mode(self) -> str:
        value = str(self._settings.get("window_mode", "normal"))
        return value if value in {"normal", "compact", "companion"} else "normal"

    @window_mode.setter
    def window_mode(self, val: str):
        self._settings["window_mode"] = val if val in {"normal", "compact", "companion"} else "normal"
        self.save()

    @property
    def window_geometry(self) -> dict:
        value = self._settings.get("window_geometry", {})
        return dict(value) if isinstance(value, dict) else {}

    @window_geometry.setter
    def window_geometry(self, val: dict):
        self._settings["window_geometry"] = dict(val or {})
        self.save()

    @property
    def restore_window_state(self) -> bool:
        return bool(self._settings.get("restore_window_state", True))

    @restore_window_state.setter
    def restore_window_state(self, val: bool):
        self._settings["restore_window_state"] = bool(val)
        self.save()

    @property
    def always_on_top(self) -> bool:
        return bool(self._settings.get("always_on_top", False))

    @always_on_top.setter
    def always_on_top(self, val: bool):
        self._settings["always_on_top"] = bool(val)
        self.save()

    @property
    def reduced_motion(self) -> bool:
        return bool(self._settings.get("reduced_motion", False))

    @reduced_motion.setter
    def reduced_motion(self, val: bool):
        self._settings["reduced_motion"] = bool(val)
        self.save()

    @property
    def desktop_notifications(self) -> bool:
        return bool(self._settings.get("desktop_notifications", True))

    @desktop_notifications.setter
    def desktop_notifications(self, val: bool):
        self._settings["desktop_notifications"] = bool(val)
        self.save()

    # ========== 桌面端聊天分段停顿（秒） ==========
    @property
    def segment_pause_chat_min(self) -> float:
        try:
            return max(0.1, min(10.0, float(self._settings.get("segment_pause_chat_min", 0.45))))
        except (TypeError, ValueError):
            return 0.45

    @segment_pause_chat_min.setter
    def segment_pause_chat_min(self, val: float):
        self._settings["segment_pause_chat_min"] = max(0.1, min(10.0, float(val)))
        self.save()

    @property
    def segment_pause_chat_max(self) -> float:
        try:
            return max(0.1, min(10.0, float(self._settings.get("segment_pause_chat_max", 1.1))))
        except (TypeError, ValueError):
            return 1.1

    @segment_pause_chat_max.setter
    def segment_pause_chat_max(self, val: float):
        self._settings["segment_pause_chat_max"] = max(0.1, min(10.0, float(val)))
        self.save()

    @property
    def segment_pause_semantic_min(self) -> float:
        try:
            return max(0.1, min(30.0, float(self._settings.get("segment_pause_semantic_min", 3.0))))
        except (TypeError, ValueError):
            return 3.0

    @segment_pause_semantic_min.setter
    def segment_pause_semantic_min(self, val: float):
        self._settings["segment_pause_semantic_min"] = max(0.1, min(30.0, float(val)))
        self.save()

    @property
    def segment_pause_semantic_max(self) -> float:
        try:
            return max(0.1, min(30.0, float(self._settings.get("segment_pause_semantic_max", 7.0))))
        except (TypeError, ValueError):
            return 7.0

    @segment_pause_semantic_max.setter
    def segment_pause_semantic_max(self, val: float):
        self._settings["segment_pause_semantic_max"] = max(0.1, min(30.0, float(val)))
        self.save()


# 全局单例
_global_settings = None

def get_settings() -> SettingsManager:
    global _global_settings
    if _global_settings is None:
        _global_settings = SettingsManager()
    return _global_settings

