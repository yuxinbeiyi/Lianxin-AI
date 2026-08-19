import time

import cv2


class CameraManager:
    def __init__(self, index=0, width=1280, height=720, fps=30):
        self.index = index
        self.width = width
        self.height = height
        self.target_fps = fps
        self.capture = None
        self.fps = 0.0
        self.error = ""
        self._last_time = None

    @property
    def running(self):
        return self.capture is not None and self.capture.isOpened()

    def start(self):
        self.error = ""
        self.capture = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not self.capture.isOpened():
            self.capture.release()
            self.capture = cv2.VideoCapture(self.index)
        if not self.capture.isOpened():
            self.error = "无法打开摄像头，请检查设备是否被其他程序占用"
            self.capture = None
            return False
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.capture.set(cv2.CAP_PROP_FPS, self.target_fps)
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height
        self._last_time = time.monotonic()
        return True

    def read(self):
        if not self.running:
            return None
        ok, frame = self.capture.read()
        if not ok:
            self.error = "摄像头读取视频帧失败"
            return None
        now = time.monotonic()
        elapsed = now - self._last_time if self._last_time else 0
        if elapsed > 0:
            self.fps = 0.9 * self.fps + 0.1 / elapsed if self.fps else 1 / elapsed
        self._last_time = now
        return cv2.flip(frame, 1)

    def stop(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None
