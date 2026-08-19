import sys
from pathlib import Path

# Allow `python app\run.py` to work from the VisionLab project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtWidgets import QApplication

from app.ui.main_window import VisionLabWindow


def main():
    app = QApplication(sys.argv)
    window = VisionLabWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
