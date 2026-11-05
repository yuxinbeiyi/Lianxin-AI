"""Small GPU resource probes that do not import PyTorch or CUDA."""

from __future__ import annotations

import shutil
import subprocess
import csv
import io
import math
import os
from pathlib import Path
from typing import Optional


def _nvidia_smi() -> Optional[str]:
    executable = shutil.which("nvidia-smi")
    if executable:
        return executable
    if os.name == "nt":
        for path in (
            Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/nvidia-smi.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe",
        ):
            if path.is_file():
                return str(path)
    return None


def get_gpu_memory() -> Optional[dict[str, int]]:
    """Return primary GPU free/total memory in MiB, or None if unavailable."""
    executable = _nvidia_smi()
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


def _metric(value: str):
    try:
        number = float(value.strip())
        return number if math.isfinite(number) and number >= 0 else None
    except ValueError:
        return None


def gpu_snapshot(pid: Optional[int] = None) -> dict:
    process = {"pid": pid, "status": "not_managed" if pid is None else "unavailable", "used_mb": None}
    result = {"status": "unavailable", "devices": [], "process": process}
    executable = _nvidia_smi()
    if not executable:
        return result
    def query(fields):
        response = subprocess.run(
            [executable, fields, "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=2, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if response.returncode != 0:
            raise OSError("gpu_query_failed")
        return csv.reader(io.StringIO(response.stdout), skipinitialspace=True)
    try:
        rows = query("--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu")
        for row in rows:
            if len(row) != 7:
                continue
            index, uuid, name, total, used, free, utilization = [value.strip() for value in row]
            if not index.isdigit():
                continue
            result["devices"].append({"index": int(index), "uuid": uuid, "name": name,
                                      "total_mb": _metric(total), "used_mb": _metric(used),
                                      "free_mb": _metric(free), "utilization_percent": _metric(utilization)})
        if not result["devices"]:
            return result
        result["status"] = "ok"
        if pid is not None:
            matches = [row for row in query("--query-compute-apps=gpu_uuid,pid,used_gpu_memory")
                       if len(row) == 3 and row[1].strip() == str(pid)]
            values = [_metric(row[2]) for row in matches]
            if values and all(value is not None for value in values):
                process.update(status="ok", used_mb=sum(values))
            elif matches:
                process["status"] = "driver_unavailable"
            else:
                process["status"] = "not_reported"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return result
