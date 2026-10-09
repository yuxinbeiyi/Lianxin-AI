# utils/paths.py
import os
from pathlib import Path
import shutil
from pathlib import Path

def get_user_data_dir() -> Path:
    r"""获取用户数据目录（例如 C:\Users\你的用户名\.lianxin\）"""
    home = Path.home()
    data_dir = home / ".lianxin"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

def get_legacy_data_dir() -> Path:
    """旧的数据目录（项目根目录下的 data/）"""
    return Path(__file__).parent.parent / "data"

def get_legacy_memory_dir() -> Path:
    """旧的记忆目录（项目根目录下的 memory/）"""
    return Path(__file__).parent.parent / "memory"


def migrate_legacy_files():
    """将旧位置的文件迁移到新位置（如果存在且新位置没有同名文件）"""
    user_dir = get_user_data_dir()
    legacy_data = get_legacy_data_dir()
    legacy_memory = get_legacy_memory_dir()

    # 迁移 data 目录下的核心配置文件
    for filename in ["user_config.json", "global_settings.json"]:
        src = legacy_data / filename
        dst = user_dir / filename
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))   # 使用 shutil.move
            print(f"[迁移] 已移动 {src} -> {dst}")

    # 迁移 memory 目录下的 long_term.json
    src_mem = legacy_memory / "long_term.json"
    dst_mem = user_dir / "long_term.json"
    if src_mem.exists() and not dst_mem.exists():
        shutil.move(str(src_mem), str(dst_mem))
        print(f"[迁移] 已移动 {src_mem} -> {dst_mem}")

    # 可选：迁移其他可能的数据文件
    other_files = [
        ("accompany_stats.json", "accompany_stats.json"),
        ("todo_list.json", "todo_list.json"),
    ]
    for src_name, dst_name in other_files:
        src = legacy_data / src_name
        dst = user_dir / dst_name
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
            print(f"[迁移] 已移动 {src} -> {dst}")


# ── 网易云（netease-music-mcp）播放状态文件 ─────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _netease_state_candidates() -> list:
    return [
        get_user_data_dir() / "netease" / ".listening-state.json",
        _PROJECT_ROOT / "参考项目" / "netease-music-mcp-main" / ".listening-state.json",
        _PROJECT_ROOT / "参考项目" / "netease-music-mcp-main" / "listening-state.json",
    ]


def get_netease_state_file() -> Path:
    """netease-music-mcp 播放状态文件位置。

    优先使用环境变量 LIANXIN_NETEASE_STATE_FILE；否则依次探测用户数据目录、
    参考项目目录，返回第一个已存在的路径；都不存在时返回参考项目默认路径。
    """
    override = os.environ.get("LIANXIN_NETEASE_STATE_FILE", "").strip()
    if override:
        return Path(override)
    candidates = _netease_state_candidates()
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return candidates[1]


def get_netease_state_file_candidates() -> list:
    """按优先级返回所有可能的状态文件路径（兼容历史命名）。"""
    override = os.environ.get("LIANXIN_NETEASE_STATE_FILE", "").strip()
    if override:
        return [Path(override)]
    return _netease_state_candidates()
