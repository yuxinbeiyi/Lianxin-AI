"""Launch the first-phase video-call UI preview.

Run from the project root:
    python -m gui.video_call_preview
"""

import sys

from PyQt5.QtWidgets import QApplication

from gui.video_call_window import VideoCallWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("莲心语音聊天界面预览")
    window = VideoCallWindow(preview_mode=True)
    window.show()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
