"""Save native history exports in a separate Qt GUI process."""

import json
from pathlib import Path
import subprocess
import sys


def save_history_export(payload):
    process = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        input=json.dumps(payload, ensure_ascii=False), encoding="utf-8",
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        result = json.loads(process.stdout)
    except (ValueError, TypeError) as error:
        raise RuntimeError("本地保存窗口未正常完成，请检查 Qt 环境后重试") from error
    if process.returncode or not isinstance(result, dict) or result.get("error"):
        raise RuntimeError(result.get("error", "本地导出失败") if isinstance(result, dict) else "本地导出响应无效")
    if result.get("cancelled") is True:
        return {"saved": False, "cancelled": True}
    if result.get("saved") is not True or not isinstance(result.get("path"), str) or not result["path"]:
        raise RuntimeError("本地导出结果未确认，请检查目标文件")
    return {"saved": True, "cancelled": False, "path": result["path"]}


def save_with_dialog(payload):
    from PyQt5.QtCore import QIODevice, QSaveFile, Qt
    from PyQt5.QtWidgets import QFileDialog

    filename = payload["filename"]
    suffix = Path(filename).suffix.lower()
    if suffix not in (".txt", ".json") or Path(filename).name != filename:
        raise ValueError("无效的历史导出文件名")
    file_filter = "文本文件 (*.txt)" if suffix == ".txt" else "JSON 文件 (*.json)"
    dialog = QFileDialog(None, "导出历史记录", filename, file_filter)
    dialog.setAcceptMode(QFileDialog.AcceptSave)
    dialog.setFileMode(QFileDialog.AnyFile)
    dialog.setDefaultSuffix(suffix[1:])
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    if dialog.exec_() != QFileDialog.Accepted:
        return {"saved": False, "cancelled": True}
    path = dialog.selectedFiles()[0]
    output = QSaveFile(path)
    if not output.open(QIODevice.WriteOnly):
        raise OSError(output.errorString())
    content = payload["content"].encode("utf-8")
    if output.write(content) != len(content):
        error = output.errorString()
        output.cancelWriting()
        raise OSError(error or "文件写入不完整，未替换原文件")
    if not output.commit():
        raise OSError(output.errorString() or "文件保存失败，未替换原文件")
    return {"saved": True, "cancelled": False, "path": str(Path(path).resolve())}


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        payload = json.load(sys.stdin)
        from PyQt5.QtWidgets import QApplication
        app = QApplication([sys.argv[0]])
        app.setApplicationName("莲心历史记录导出")
        result = save_with_dialog(payload)
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
