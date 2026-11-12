"""Qt-free Live2D runtime asset resolution shared by the PyQt player and the HTTP player."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "Live2D"
PLAYER_DIR = ROOT / "gui" / "galgame" / "live2d_web"


def load_runtime_config(directory=RUNTIME_DIR) -> dict[str, Path]:
    directory = Path(directory).resolve()
    config_file = directory / "config.json"
    if not config_file.is_file():
        raise FileNotFoundError("尚未准备 Live2D 本地资源，请按 docs/莲心Live2D接入说明.md 操作")
    config = json.loads(config_file.read_text(encoding="utf-8"))
    files = {key: (directory / config[key]).resolve() for key in ("model", "core")}
    files.update(pixi=directory / "pixi.min.js", display=directory / "cubism4.min.js")
    for path in files.values():
        if not path.is_file():
            raise FileNotFoundError(f"Live2D 资源不存在：{path}")
    return files


def model_root(files: dict[str, Path]) -> Path:
    """The directory the model3.json relative texture/motion paths resolve from."""
    return files["model"].parent
