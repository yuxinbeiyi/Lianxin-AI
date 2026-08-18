import sys

from PyQt5.QtWidgets import QApplication

from vision_lab.ui.main_window import VisionLabWindow


def main():
    app = QApplication(sys.argv)
    window = VisionLabWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
