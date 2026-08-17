"""User music import and playback normalization."""

from pathlib import Path
import hashlib
import shutil
import subprocess
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from utils.paths import get_user_data_dir


SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}


def is_mp3_file(path: str | Path) -> bool:
    """通过文件头判断是否为真正的 MP3 文件。

    仅凭扩展名不可靠：用户可能把 M4A/AAC 改名为 .mp3。
    返回 True 表示文件头符合 MP3 格式（ID3v2 标签 或 MPEG 帧同步）。
    """
    try:
        with open(path, "rb") as f:
            header = f.read(4)
        if len(header) < 3:
            return False
        # ID3v2 标签头
        if header[0:3] == b"ID3":
            return True
        # MPEG 帧同步（FF FB / FF F3 / FF E0 等）
        if header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
            return True
        return False
    except Exception:
        return False


class MusicLibrary:
    def __init__(self):
        self.root = get_user_data_dir() / "music_library"
        self.originals = self.root / "originals"
        self.normalized = self.root / "normalized"
        self.quarantine = self.root / "quarantine"
        for path in (self.originals, self.normalized, self.quarantine):
            path.mkdir(parents=True, exist_ok=True)

    def import_file(self, source: str | Path) -> Path:
        source = Path(source)
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的音频格式: {source.suffix}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        original = self.originals / f"{digest}_{source.name}"
        normalized = self.normalized / f"{digest}_{source.stem}.mp3"
        if not original.exists():
            shutil.copy2(source, original)
        if normalized.exists():
            return normalized
        self._normalize(original, normalized)
        return normalized

    def normalize_path(self, source: str | Path) -> Path | None:
        """对任意路径的音频文件做 MP3 归一化（仅缓存，不复制原件）。

        - 如果本身就是真 MP3，直接返回原路径
        - 如果是其他支持的格式，用 ffmpeg 转码到 normalized/ 缓存，返回缓存路径
        - 转码失败返回 None
        - 已有缓存直接返回缓存路径

        缓存文件直接用原始文件名（替换扩展名为 .mp3），便于在播放列表中
        显示干净的名称。适用于 assets/music/ 等"直接丢进去"的目录。
        """
        source = Path(source)
        if not source.exists():
            return None
        # 已经是真 MP3，直接返回
        if source.suffix.lower() == ".mp3" and is_mp3_file(source):
            return source
        # 扩展名不支持的，尝试按内容判断——如果是真MP3也放行
        if is_mp3_file(source):
            return source
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None
        # 缓存文件直接用原始 stem，便于列表显示
        normalized = self.normalized / f"{source.stem}.mp3"
        if normalized.exists():
            return normalized
        try:
            self._normalize(source, normalized)
            return normalized
        except Exception as exc:
            print(f"[音乐盒] 转码失败: {source.name} ({exc})")
            normalized.unlink(missing_ok=True)
            return None

    def quarantine_files(self) -> list[Path]:
        return sorted(p for p in self.quarantine.iterdir() if p.is_file())

    def clear_quarantine(self):
        for path in self.quarantine_files():
            path.unlink(missing_ok=True)

    def _normalize(self, source: Path, target: Path):
        ffmpeg = self._ffmpeg_path()
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-map_metadata", "-1", "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
            "-ar", "44100", "-ac", "2", str(target),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except Exception:
            target.unlink(missing_ok=True)
            shutil.copy2(source, self.quarantine / source.name)
            raise

    @staticmethod
    def _ffmpeg_path() -> str:
        import shutil as _shutil
        found = _shutil.which("ffmpeg")
        if found:
            return found
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise RuntimeError("未找到 FFmpeg，无法导入音乐") from exc


class MusicImportWorker(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, list)

    def __init__(self, library: MusicLibrary, paths: list[str]):
        super().__init__()
        self.library = library
        self.paths = paths

    @pyqtSlot()
    def run(self):
        imported = 0
        failures = []
        total = len(self.paths)
        for index, path in enumerate(self.paths, 1):
            if self.thread() is not None and self.thread().isInterruptionRequested():
                break
            name = Path(path).name
            self.progress.emit(index - 1, total, f"正在处理：{name}")
            try:
                self.library.import_file(path)
                imported += 1
            except Exception as exc:
                failures.append(f"{name}: {exc}")
        self.progress.emit(total, total, "处理完成")
        self.finished.emit(imported, failures)
