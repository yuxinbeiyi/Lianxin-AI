# -*- coding: utf-8 -*-
"""莲心 OLED 表情控制面板（布局照搬 esp32-cam/sim/oled_sim.py）

左侧 128x64 像素块模拟 SSD1306 OLED；右侧输入表情编号(1~18)或点快捷按钮，
电脑端实时渲染（与固件 face_engine.h 逐像素一致），并可通过 WebSocket 推送到
ESP32-CAM 的实体 OLED，实现电脑与实体同步播放。

相对 oled_sim.py 新增：
- 「激活OLED」：向 ESP32 发 【表情】启动（实体轮播 1~18）
- 「关闭OLED」：向 ESP32 发 【表情】关闭（实体回到信息屏：IP/舵机/温湿度）
- 点击表情按钮 / 输入编号回车：本地显示 + 向 ESP32 发 【表情】N（固定显示该表情）
"""
import asyncio
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QElapsedTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (QApplication, QWidget, QDialog, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QLineEdit, QPushButton,
                             QLabel, QCheckBox, QGroupBox, QSizePolicy)

# 让脚本可独立运行：python gui/oled_panel.py 也能跑（借用 brain.face_engine）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from brain import face_engine as fe

OLED_W = fe.OLED_W
OLED_H = fe.OLED_H
PIXEL_ON = QColor(0xE8, 0xF6, 0xFF)
PIXEL_OFF = QColor(0x08, 0x0B, 0x10)
BG = QColor(0x00, 0x00, 0x00)
FRAME = QColor(0x2A, 0x33, 0x3E)


class OLEDView(QWidget):
    """Draws the 128x64 framebuffer scaled up as visible pixel blocks."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fb = None
        self.setMinimumSize(OLED_W * 4, OLED_H * 4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), BG)
        self.setPalette(pal)

    def set_framebuffer(self, fb):
        self.fb = fb
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        pad = 14
        avail_w = w - 2 * pad
        avail_h = h - 2 * pad
        scale = min(avail_w / OLED_W, avail_h / OLED_H)
        pw = OLED_W * scale
        ph = OLED_H * scale
        ox = (w - pw) / 2
        oy = (h - ph) / 2
        painter.fillRect(0, 0, w, h, BG)
        painter.fillRect(int(ox - 4), int(oy - 4), int(pw + 8), int(ph + 8), FRAME)
        painter.fillRect(int(ox), int(oy), int(pw), int(ph), PIXEL_OFF)
        gap = 1 if scale >= 5 else 0
        if self.fb is None:
            painter.end()
            return
        for y in range(OLED_H):
            for x in range(OLED_W):
                if self.fb.pixels[y][x]:
                    px = ox + x * scale
                    py = oy + y * scale
                    painter.fillRect(int(px), int(py), int(scale) - gap, int(scale) - gap, PIXEL_ON)
        painter.end()


class FaceBridgeWorker(QThread):
    """后台线程：连中继/直连 ESP32，发一条表情命令，断开。不卡 UI。"""

    result = pyqtSignal(bool, str)

    def __init__(self, command: str, parent=None):
        super().__init__(parent)
        self._command = command

    def run(self):
        try:
            from brain.hardware_bridge import HardwareBridge
            bridge = HardwareBridge()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                resp = loop.run_until_complete(bridge.send_one_shot(self._command))
                if resp:
                    self.result.emit(True, f"OK → {resp.strip()}")
                else:
                    self.result.emit(False, "无响应（确认中继/同一局域网，ESP32 在线）")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
        except Exception as exc:
            self.result.emit(False, f"连接失败：{exc}")


class OledPanel(QDialog):
    """莲心 OLED 表情控制面板（布局与 oled_sim.py 一致）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("莲心 OLED 表情控制 · SSD1306 128x64")
        self.resize(1040, 640)

        self.face = fe.FaceSim()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._on_tick)
        self.clock = QElapsedTimer()
        self.clock.start()

        self.auto_cycle_timer = QTimer(self)
        self.auto_cycle_timer.setInterval(4000)
        self.auto_cycle_timer.timeout.connect(self._auto_cycle)

        self._bridge_thread = None

        self._build_ui()
        self._show_current()
        self.timer.start()

    # ---------- UI（照搬 oled_sim.py + ESP32 连接区） ----------
    def _build_ui(self):
        # 自包含深色样式：覆盖父级（视觉面板）级联进来的 QPushButton/QCheckBox 全局规则。
        # 若不覆盖，父级 `QPushButton{min-width:100px;min-height:40px;padding:10px 20px;font-size:14px}`
        # 会让快捷按钮溢出面板、并把两行文字裁得看不清。
        self.setStyleSheet("""
            QPushButton {
                background-color: #455364;
                color: #DFE1E2;
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                min-width: 0;
                min-height: 24px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #54687A; }
            QPushButton:pressed { background-color: #60798B; }
            QCheckBox { padding: 2px 0px; font-size: 12px; color: #DCEFE8; }
            QPushButton#quickBtn {
                min-height: 48px;
                max-height: 56px;
                padding: 2px 4px;
                font-size: 12px;
            }
        """)
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(16)

        self.oled = OLEDView()
        root.addWidget(self.oled, stretch=1)

        panel = QWidget()
        panel.setFixedWidth(380)
        pl = QVBoxLayout(panel)
        pl.setSpacing(10)

        title = QLabel("表情编号 → 像素显示")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        pl.addWidget(title)

        hint = QLabel("输入 1 ~ 18（对应固件情绪循环编号），回车或点“显示”。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666;")
        pl.addWidget(hint)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("例如 1")
        self.input.setMaximumWidth(90)
        self.input.returnPressed.connect(self._on_send)
        send = QPushButton("显示")
        send.clicked.connect(self._on_send)
        row.addWidget(self.input)
        row.addWidget(send)
        row.addStretch(1)
        pl.addLayout(row)

        self.current_label = QLabel()
        self.current_label.setStyleSheet("font-size:14px; font-weight:600; color:#1a73e8;")
        pl.addWidget(self.current_label)

        self.status_label = QLabel(" ")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color:#c0392b;")
        pl.addWidget(self.status_label)

        grid = QGroupBox("快捷选择（点击即本地显示 + 推送到 ESP32 OLED）")
        gl = QGridLayout(grid)
        gl.setSpacing(6)
        for idx, (num, en, cn, *_) in enumerate(fe.EMOTIONS):
            b = QPushButton(f"{num}\n{cn}")
            b.setObjectName("quickBtn")
            b.setToolTip(f"{num} · {en}")
            b.clicked.connect(lambda _, n=num: self.goto(n, push=True))
            gl.addWidget(b, idx // 6, idx % 6)
        pl.addWidget(grid)

        opts = QGroupBox("模拟选项")
        ol = QVBoxLayout(opts)
        self.chk_blink = QCheckBox("随机眨眼（约 3.5s）")
        self.chk_look = QCheckBox("随机视线（约 4s）")
        self.chk_auto = QCheckBox("自动循环 1→18（每 4s，同固件）")
        self.chk_blink.stateChanged.connect(self._on_opts)
        self.chk_look.stateChanged.connect(self._on_opts)
        self.chk_auto.stateChanged.connect(self._on_auto)
        ol.addWidget(self.chk_blink)
        ol.addWidget(self.chk_look)
        ol.addWidget(self.chk_auto)
        pl.addWidget(opts)

        # ── ESP32 连接区（新增） ──
        conn = QGroupBox("ESP32 OLED 连接")
        cl = QVBoxLayout(conn)
        mode_hint = QLabel("本地先渲染；点击下方按钮推送命令到 ESP32。\n需要同一局域网：本地中继运行中，或直连 ESP32。")
        mode_hint.setWordWrap(True)
        mode_hint.setStyleSheet("color:#888; font-size:12px;")
        cl.addWidget(mode_hint)
        btn_row = QHBoxLayout()
        self.btn_on = QPushButton("激活OLED")
        self.btn_off = QPushButton("关闭OLED")
        self.btn_on.clicked.connect(lambda: self._push("emoji on"))
        self.btn_off.clicked.connect(lambda: self._push("emoji off"))
        btn_row.addWidget(self.btn_on)
        btn_row.addWidget(self.btn_off)
        cl.addLayout(btn_row)
        self.conn_label = QLabel("未连接")
        self.conn_label.setStyleSheet("color:#666; font-size:12px;")
        self.conn_label.setWordWrap(True)
        cl.addWidget(self.conn_label)
        pl.addWidget(conn)

        note = QLabel("提示：画面左下角显示当前表情编号，与固件 OLED 一致。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size:12px;")
        pl.addWidget(note)
        pl.addStretch(1)

        root.addWidget(panel)
        self.input.setFocus()

    # ---------- behaviour ----------
    def _now(self):
        return self.clock.elapsed()

    def _on_send(self):
        text = self.input.text().strip()
        if not text:
            return
        try:
            n = int(text)
        except ValueError:
            self.status_label.setText("请输入数字 1~18")
            return
        if 1 <= n <= len(fe.EMOTIONS):
            self.status_label.setText(" ")
            self.goto(n, push=True)
        else:
            self.status_label.setText("编号超出范围：应为 1 ~ 18")

    def goto(self, n, push=False):
        self.face.goto(n, self._now())
        self.input.setText(str(n))
        self._show_current()
        if push:
            self._push(f"emoji {int(n)}")

    def _show_current(self):
        num = self.face.current_num
        en, cn = fe.EMOTION_BY_NUM[num][1], fe.EMOTION_BY_NUM[num][2]
        self.current_label.setText(f"当前表情：{num} · {cn}（{en}）")

    def _on_opts(self):
        self.face.random_blink = self.chk_blink.isChecked()
        self.face.random_look = self.chk_look.isChecked()

    def _on_auto(self, state):
        if state == Qt.Checked:
            self.auto_cycle_timer.start()
        else:
            self.auto_cycle_timer.stop()

    def _auto_cycle(self):
        n = self.face.current_num
        self.goto(n % len(fe.EMOTIONS) + 1)  # 本地轮播，不推送

    def _on_tick(self):
        fb = self.face.tick(self._now())
        self.oled.set_framebuffer(fb)

    def _push(self, command: str):
        """后台线程发送命令到 ESP32，避免卡 UI。"""
        if self._bridge_thread is not None and self._bridge_thread.isRunning():
            self.conn_label.setText("上一条命令还在发送中…")
            return
        self.conn_label.setText(f"发送中：{command}")
        self._bridge_thread = FaceBridgeWorker(command, self)
        self._bridge_thread.result.connect(self._on_push_result)
        self._bridge_thread.start()

    def _on_push_result(self, ok: bool, msg: str):
        if ok:
            self.conn_label.setText(f"✓ {msg}")
            self.status_label.setText(" ")
        else:
            self.conn_label.setText(f"✗ {msg}")
            self.status_label.setText(msg)


def main():
    app = QApplication(sys.argv)
    win = OledPanel()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
