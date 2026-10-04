"""
utils/music_stats.py - 音乐陪伴统计
记录每首歌的累计播放时长、总陪伴时长、最常听歌曲
"""

import json
import threading
from pathlib import Path
from datetime import datetime
from utils.paths import get_user_data_dir

_MUSIC_STATS_FILE = get_user_data_dir() / "music_stats.json"

class MusicStats:
    _file_lock = threading.RLock()

    def __init__(self):
        with self._file_lock:
            self.data = self._load()

    def _load(self):
        if _MUSIC_STATS_FILE.exists():
            with open(_MUSIC_STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "total_seconds": 0,
            "songs": {}          # key: 文件路径字符串, value: {"name": 歌名, "seconds": 累计秒数, "last_played": ISO时间}
        }

    def save(self):
        with self._file_lock:
            _MUSIC_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_MUSIC_STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)

    def update_song(self, file_path: str, duration_seconds: int):
        """增加某首歌的播放时长（秒）"""
        if duration_seconds <= 0:
            return
        with self._file_lock:
            self.data["total_seconds"] += duration_seconds
            path_str = str(file_path)
            if path_str not in self.data["songs"]:
                name = Path(file_path).stem
                self.data["songs"][path_str] = {"name": name, "seconds": 0, "last_played": ""}
            self.data["songs"][path_str]["seconds"] += duration_seconds
            self.data["songs"][path_str]["last_played"] = datetime.now().isoformat()
            self.save()

    def get_total_hours(self) -> float:
        return self.data["total_seconds"] / 3600.0

    def get_most_played_song(self):
        """返回 (歌名, 累计秒数) 或 (None, 0)"""
        songs = self.data["songs"]
        if not songs:
            return None, 0
        best_path = max(songs.items(), key=lambda x: x[1]["seconds"])
        return best_path[1]["name"], best_path[1]["seconds"]
