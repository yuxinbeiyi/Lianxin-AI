import json
import os
import tempfile
import threading
from pathlib import Path


_LOCK = threading.RLock()


def save_extension_section(path: Path, section: str, names):
    with _LOCK:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("扩展配置格式错误，未覆盖原文件")
        data[section] = sorted(names)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                temporary = handle.name
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
