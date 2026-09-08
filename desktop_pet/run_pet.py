"""Standalone V0.1 launcher; does not initialize the Lianxin main window."""
import sys
from pathlib import Path

# When executed as ``python desktop_pet/run_pet.py``, Python puts the
# package directory on sys.path instead of the project root.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from desktop_pet.pet_controller import DesktopPetController


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    DesktopPetController(app)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
