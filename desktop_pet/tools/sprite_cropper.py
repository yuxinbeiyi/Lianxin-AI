"""Interactive pixel-art cropper for the desktop pet source boards."""
from pathlib import Path
import sys

from PIL import Image
from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)


class CropCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.pixmap = QPixmap()
        self.scale = 1.0
        self.selection = QRect()
        self.mode = False
        self.drag_kind = None
        self.drag_origin = QPoint()
        self.start_selection = QRect()
        self.setMinimumSize(640, 480)
        self.setMouseTracking(True)

    def set_image(self, path):
        self.source = Image.open(path).convert("RGBA")
        data = self.source.tobytes("raw", "RGBA")
        image = QImage(data, self.source.width, self.source.height,
                       self.source.width * 4, QImage.Format_RGBA8888)
        self.pixmap = QPixmap.fromImage(image.copy())
        self.selection = QRect()
        self.update()

    def _image_rect(self):
        if self.pixmap.isNull():
            return QRect()
        scale = min(self.width() / self.pixmap.width(),
                    self.height() / self.pixmap.height(), 1.0)
        w, h = round(self.pixmap.width() * scale), round(self.pixmap.height() * scale)
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def _to_image(self, point):
        rect = self._image_rect()
        x = round((point.x() - rect.left()) / self.scale)
        y = round((point.y() - rect.top()) / self.scale)
        return QPoint(max(0, min(self.pixmap.width(), x)),
                      max(0, min(self.pixmap.height(), y)))

    def set_selection_size(self, width, height):
        if self.source is None:
            return
        x = self.selection.x() if not self.selection.isNull() else 0
        y = self.selection.y() if not self.selection.isNull() else 0
        self.selection = QRect(x, y, min(width, self.source.width - x),
                               min(height, self.source.height - y))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#252525"))
        if self.pixmap.isNull():
            painter.setPen(Qt.white)
            painter.drawText(self.rect(), Qt.AlignCenter, "请选择源图片")
            return
        rect = self._image_rect()
        self.scale = rect.width() / self.pixmap.width()
        painter.drawPixmap(rect, self.pixmap)
        if self.mode and not self.selection.isNull():
            s = QRect(rect.left() + round(self.selection.x() * self.scale),
                      rect.top() + round(self.selection.y() * self.scale),
                      round(self.selection.width() * self.scale),
                      round(self.selection.height() * self.scale))
            painter.setPen(QPen(QColor("#ffcc33"), 2))
            painter.drawRect(s)
            painter.fillRect(s.right() - 7, s.bottom() - 7, 10, 10, QColor("#ffcc33"))

    def mousePressEvent(self, event):
        if not self.mode or self.source is None or event.button() != Qt.LeftButton:
            return
        point = self._to_image(event.pos())
        handle = QRect(self.selection.right() - 10, self.selection.bottom() - 10, 20, 20)
        if not self.selection.isNull() and handle.contains(point):
            self.drag_kind = "resize"
        elif not self.selection.isNull() and self.selection.contains(point):
            self.drag_kind = "move"
        else:
            self.drag_kind = "new"
            self.selection = QRect(point, point)
        self.drag_origin, self.start_selection = point, QRect(self.selection)
        self.update()

    def mouseMoveEvent(self, event):
        if not self.drag_kind:
            return
        point = self._to_image(event.pos())
        dx, dy = point.x() - self.drag_origin.x(), point.y() - self.drag_origin.y()
        if self.drag_kind == "move":
            r = QRect(self.start_selection).translated(dx, dy)
            r.moveLeft(max(0, min(r.left(), self.source.width - r.width())))
            r.moveTop(max(0, min(r.top(), self.source.height - r.height())))
        elif self.drag_kind == "resize":
            r = QRect(self.start_selection)
            r.setWidth(max(1, min(self.source.width - r.x(), r.width() + dx)))
            r.setHeight(max(1, min(self.source.height - r.y(), r.height() + dy)))
        else:
            r = QRect(self.drag_origin, point).normalized()
        self.selection = r
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag_kind = None
        self.update()


class SpriteCropper(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("莲心像素素材裁剪工具")
        self.resize(1200, 760)
        self.canvas = CropCanvas()
        self.output = QLineEdit(str(Path(__file__).resolve().parents[1] / "assets" / "idle"))
        self.width_box, self.height_box = QSpinBox(), QSpinBox()
        for box in (self.width_box, self.height_box):
            box.setRange(1, 9999)
        self.width_box.setValue(200)
        self.height_box.setValue(275)
        self.width_box.valueChanged.connect(self.sync_selection_size)
        self.height_box.valueChanged.connect(self.sync_selection_size)
        self.status = QLabel("请选择源图片")
        choose = QPushButton("选择源图片")
        choose.clicked.connect(self.choose_image)
        select = QPushButton("框选")
        select.clicked.connect(self.enable_selection)
        output_button = QPushButton("选择保存目录")
        output_button.clicked.connect(self.choose_output)
        crop = QPushButton("裁剪并保存")
        crop.clicked.connect(self.crop)
        form = QFormLayout()
        form.addRow("裁剪宽度（原图像素）", self.width_box)
        form.addRow("裁剪高度（原图像素）", self.height_box)
        form.addRow("保存目录", self.output)
        side = QVBoxLayout()
        side.addWidget(choose)
        side.addWidget(select)
        side.addLayout(form)
        side.addWidget(output_button)
        side.addWidget(crop)
        side.addWidget(self.status)
        side.addStretch()
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.addWidget(self.canvas, 1)
        right = QWidget(); right.setLayout(side); right.setFixedWidth(300)
        layout.addWidget(right)
        self.setCentralWidget(panel)

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择源图片", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.canvas.set_image(path)
            self.status.setText(f"已加载：{Path(path).name}")

    def enable_selection(self):
        self.canvas.mode = True
        self.canvas.set_selection_size(self.width_box.value(), self.height_box.value())
        self.status.setText("请在左侧拖动裁剪框；拖动框内移动，拖动右下角调整大小")
        self.canvas.setFocus(); self.canvas.update()

    def sync_selection_size(self):
        if self.canvas.mode and not self.canvas.selection.isNull():
            self.canvas.set_selection_size(self.width_box.value(), self.height_box.value())

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存目录", self.output.text())
        if path: self.output.setText(path)

    def crop(self):
        if self.canvas.source is None or self.canvas.selection.isNull():
            QMessageBox.warning(self, "无法裁剪", "请先选择图片并创建裁剪框。")
            return
        target = Path(self.output.text()).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        index = 0
        while (target / f"frame_{index:02d}.png").exists(): index += 1
        r = self.canvas.selection
        self.canvas.source.crop((r.x(), r.y(), r.right() + 1, r.bottom() + 1)).save(
            target / f"frame_{index:02d}.png")
        self.status.setText(f"已保存 frame_{index:02d}.png，裁剪框保留在原处")


def main():
    app = QApplication(sys.argv)
    window = SpriteCropper(); window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
