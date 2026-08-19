"""
莲心视觉感知实验室 · 配置集中管理
所有魔法数字集中在这里，便于调参。
"""

from pathlib import Path

# ── 路径 ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[3]
MODELS_DIR = BASE_DIR / "models" / "hand"
LOGS_DIR = BASE_DIR / "logs"
MODEL_PATH = MODELS_DIR / "hand_landmarker.task"
DIGIT_MODEL_PATH = MODELS_DIR / "keypoint_classifier.tflite"
DIGIT_LABELS_PATH = MODELS_DIR / "keypoint_classifier_label.csv"

# ── 摄像头 ────────────────────────────────────────────
CAMERA_INDEX = 0               # 默认前置摄像头
CAMERA_WIDTH = 1280            # 目标宽度
CAMERA_HEIGHT = 720            # 目标高度
CAMERA_FPS = 30                # 目标帧率
CAMERA_FALLBACK_WIDTH = 640    # 降级宽度
CAMERA_FALLBACK_HEIGHT = 480   # 降级高度

# ── 手势识别 ──────────────────────────────────────────
MAX_NUM_HANDS = 2              # 最多检测手的数量
MIN_HAND_DETECTION_CONFIDENCE = 0.6
MIN_HAND_PRESENCE_CONFIDENCE = 0.5
MIN_TRACKING_CONFIDENCE = 0.5

# 连续帧确认：连续多少帧相同才确认手势
GESTURE_CONFIRM_FRAMES = 4

# 冷却时间（秒）：触发后多少秒内不能再次触发同类手势
GESTURE_COOLDOWN = 2.0

# ── 手势识别阈值 ──────────────────────────────────────
# OK 手势：拇指食指指尖距离 / 手掌宽度  < 此值判定为 OK
OK_DISTANCE_RATIO = 0.25
# OK 手势：其余三指必须伸展（指尖 y < 对应 PIP 关节 y 的比例）
OK_FINGER_EXTEND_RATIO = 0.85

# 竖大拇指：拇指指尖到手腕距离 / 手掌高度 > 此值
THUMBS_UP_RATIO = 0.75
# 竖大拇指：其余四指必须弯曲（指尖到掌心距离 < 手指长度 * 此值）
THUMBS_FINGER_CURL_RATIO = 0.6

# ── 挥手（动态手势） ──────────────────────────────────
WAVE_WINDOW_SECONDS = 2.0      # 轨迹时间窗口
WAVE_MIN_SWINGS = 3            # 最少往返次数
WAVE_MIN_DISPLACEMENT_RATIO = 0.3  # 单次水平位移 / 手掌宽度
WAVE_AREA_STABILITY = 0.3      # 手掌面积波动容忍度（0.3 = ±30%）

# ── UI ────────────────────────────────────────────────
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 720
WINDOW_TITLE = "莲心视觉感知实验室 · Gesture Lab"
MAX_LOG_COUNT = 500            # 日志最多保留条数
VIDEO_WIDTH_RATIO = 0.62       # 视频区占窗口宽度比例

# ── 日志 ──────────────────────────────────────────────
LOG_FILENAME = "gesture_lab.log"
LOG_LEVEL = "INFO"
