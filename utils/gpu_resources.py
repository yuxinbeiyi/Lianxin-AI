"""Small GPU resource probes that do not import PyTorch or CUDA."""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional


def get_gpu_memory() -> Optional[dict[str, int]]:
    """Return primary GPU free/total memory in MiB, or None if unavailable."""
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return None
        first_line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
        free_text, total_text = [part.strip() for part in first_line.split(",", 1)]
        return {"free_mb": int(float(free_text)), "total_mb": int(float(total_text))}
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def has_free_vram(minimum_mb: int) -> Optional[bool]:
    """Return True/False when NVIDIA telemetry exists, otherwise None."""
    memory = get_gpu_memory()
    if memory is None:
        return None
    return memory["free_mb"] >= max(0, int(minimum_mb))
