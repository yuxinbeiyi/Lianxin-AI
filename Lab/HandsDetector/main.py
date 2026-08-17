"""
莲心视觉感知实验室 · Gesture Lab
独立测试程序入口

第一阶段：纯本地视觉识别，不接入 LLM/Agent/TTS/RAG。
用法：
    python main.py
"""

import sys
from pathlib import Path

# 确保 gesture_lab 包可被导入
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont

from gesture_lab.ui.main_window import GestureLabWindow
from gesture_lab.config import WINDOW_TITLE


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    window = GestureLabWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
