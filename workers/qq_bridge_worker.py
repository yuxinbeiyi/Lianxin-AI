"""
QQBridgeWorker：莲心AI × QQ 桥接模块
连接 NapCatQQ（OneBot v11 WebSocket），将 QQ 消息路由到 AgentCore。

工具调用修复说明：
- 部分 DeepSeek 模型倾向于在文本中假装调用了工具，而不实际触发 Function Calling。
- 为此实现了关键词强制回退机制：首次 chat 未调工具时，若消息匹配关键词则直接执行
  execute_tool 并将工具返回结果作为回复，绕过模型的配合问题。
- _TOOL_KEYWORDS 字典定义了消息关键词到工具名的映射，_extract_tool_args 负责提取参数。
"""

import json
import os
import re
import tempfile
import time
import random
import requests
import websocket
from threading import Lock, RLock, Thread, Timer, Event
from pathlib import Path
from PyQt5.QtCore import QThread, pyqtSignal

from brain.agent import AgentCore
from brain.decision import decide
from config import get_qq_bridge_config, get_qq_timing_config
from utils.emotion_manager import parse_emotion_tag, get_random_emotion_image
from utils.settings import get_settings


# ── 风控防护参数 ─────────────────────────────────────────
SILENT_HOUR_START  = 1     # 凌晨静默开始（1:00）
SILENT_HOUR_END    = 7     # 凌晨静默结束（7:00）

# ── 分段接收超时 ──────────────────────────────────────
SEGMENT_WAIT_TIMEOUT = 60  # 用户用。。分段时，等待下一段的超时秒数

# ── 合并转发处理参数 ────────────────────────────────
MAX_FORWARD_NODES = 100      # 最多处理的消息条数
MAX_FORWARD_CHARS = 4000     # 最大输出字符数
MAX_FORWARD_DEPTH = 2        # 最大嵌套深度（合并转发内嵌套转发）

# ── QQ 会话映射文件 ──────────────────────────────────────
_SESSION_MAP_PATH = Path(__file__).parent.parent / "memory" / "qq_session_map.json"

# ── 工具关键词→工具名映射（【指令】模式专用） ──────────
# 只在消息以 【指令】 开头时匹配关键词，直接执行 execute_tool 并返回结果。
# 普通聊天不会触发关键词匹配，模型可自主决定是否调用工具。
# 发送 【提示】 可查看所有可用工具的列表。
_TOOL_KEYWORDS = {
    # 具体工具放通用工具前面，避免子串抢匹配（如 "播放状态" 含 "播放"）
    "get_music_status":   ["放什么歌", "播放状态", "现在在放", "什么歌", "正在放", "当前播放"],
    "get_music_playlist": ["歌单有什么", "有什么歌", "歌单", "播放列表"],
    "control_music":      ["播放音乐", "下一首", "上一首", "音量", "随机播放", "循环播放", "暂停播放", "播放", "暂停", "下一曲", "上一曲", "放歌", "听歌"],
    "save_memory":        ["记住", "记下来"],
    "list_todos":         ["待办列表", "查看待办", "有什么待办", "列出待办"],
    "complete_todo":      ["完成待办", "标记完成"],
    "add_todo":           ["提醒我", "添加待办", "记一下", "别忘了", "添加提醒", "提醒"],
    "open_app":           ["打开", "启动", "运行", "帮我开", "帮我打开"],
    "get_current_time":   ["几点", "现在几", "几点了", "今天几号", "星期几", "农历", "日期", "时间", "查看时间", "现在时间", "几时"],
    "web_search":         ["搜索", "查一下", "查查", "查一查", "搜一下", "查新闻", "搜", "查询", "百度"],
    "get_balance":        ["余额", "还剩多少钱", "还有多少钱", "欠费"],
    "toggle_proactive_chat": ["开启主动聊天", "关闭主动聊天", "启用主动聊天", "停用主动聊天"],
    "send_file_to_qq":    ["发给我", "发送文件", "文件发到", "发文件", "把文件", "传文件", "发到qq", "文件传给", "发一个文件"],
    "list_directory":     ["查看目录", "里面都有什么", "里面都有啥", "文件夹里有什么", "列出文件", "看文件夹", "查看文件夹"],
    "write_diary":        ["写日记", "写一篇日记", "生成日记", "记日记", "写日记吧", "重新写日记"],
    "read_diary":         ["读日记", "看看日记", "日记写了什么", "最近日记", "看日记"],
    # 肩部外设（排前面，避免 "水平" 等通用字被非肩部工具抢匹配）
    "shoulder_pan":       ["水平舵机", "左右舵机"],
    "shoulder_tilt":      ["竖直舵机", "上下舵机", "垂直舵机"],
    "shoulder_center":    ["复位", "归位", "肩膀复位", "云台复位"],
    "shoulder_status":    ["肩膀状态", "外设状态", "肩部状态"],
    "shoulder_temp":      ["肩膀温度", "肩膀湿度", "外设温度", "外设湿度"],
    "shoulder_servo":     ["水平", "竖直", "垂直", "pan", "tilt"],
    "shoulder_observe":  ["拍照", "看看", "观察", "拍张照", "看一看", "照张相"],
    "shoulder_human_track": ["人体跟踪", "跟踪人", "跟随人", "启动跟踪", "开始跟踪"],
    "stop_human_tracking": ["停止跟踪", "关闭跟踪", "结束跟踪", "退出跟踪"],
}


# ── 工具函数 ─────────────────────────────────────────────

def _extract_plain_text(msg_data) -> str:
    """
    从 OneBot 消息中提取纯文本。
    msg_data 可能是字符串或数组（消息段列表）。
    """
    if isinstance(msg_data, str):
        return msg_data.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
    if isinstance(msg_data, list):
        parts = []
        for seg in msg_data:
            if seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
        return "".join(parts).encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
    return str(msg_data).encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


def _extract_at_qqs(msg_data) -> list:
    """从 OneBot 消息中提取所有被 @ 的 QQ 号。"""
    if not isinstance(msg_data, list):
        return []
    qqs = []
    for seg in msg_data:
        if seg.get("type") == "at":
            qq = seg.get("data", {}).get("qq", "")
            if qq:
                qqs.append(qq)
    return qqs


def _extract_images(msg_data) -> list:
    """从 OneBot 消息中提取所有图片信息。返回 [{"url": str, "file_id": str, "file": str}, ...]。"""
    if not isinstance(msg_data, list):
        return []
    images = []
    for seg in msg_data:
        if seg.get("type") == "image":
            data = seg.get("data", {})
            images.append({
                "url": data.get("url", ""),
                "file_id": data.get("file_id", ""),
                "file": data.get("file", ""),
            })
    return images


def _extract_voices(msg_data) -> list:
    """从 OneBot 消息中提取所有语音消息。NapCatQQ 可能只有 file_id 无 url。"""
    if not isinstance(msg_data, list):
        return []
    voices = []
    for seg in msg_data:
        stype = seg.get("type", "")
        if stype in ("voice", "record"):
            url = seg.get("data", {}).get("url", "")
            file_id = seg.get("data", {}).get("file_id", "")
            if url or file_id:
                voices.append({"url": url, "file_id": file_id})
    return voices


def _extract_forwards(msg_data) -> list:
    """从 OneBot 消息中提取所有合并转发消息段。保留完整 data 供 XML 回退。"""
    if not isinstance(msg_data, list):
        return []
    forwards = []
    for seg in msg_data:
        if seg.get("type") == "forward":
            data = seg.get("data", {})
            fid = data.get("id", "") or data.get("resId", "")
            if fid:
                forwards.append({"id": fid, "_raw_data": data})
    return forwards


def _resolve_file_path(raw: str) -> str:
    """将 file:// URL 或混合路径转为 Windows 本地绝对路径。"""
    p = raw.strip()
    if p.startswith("file://"):
        p = p[7:]
        if p.startswith("/") and len(p) > 2 and p[2] == ":":
            p = p[1:]
        elif p.startswith("/"):
            p = p[1:]
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        return p.replace("/", "\\")
    return p


def _extract_files(msg_data) -> list:
    """从 OneBot 消息中提取所有文件信息。

    NapCatQQ file 段实际格式（2026实测）：
    {"type": "file", "data": {"file": "仅文件名.docx", "file_id": "uuid...", "file_size": "12345"}}

    file 字段不含本地路径，需通过 file_id 调用 get_file API 获取真实路径。
    返回 [{"path": str, "file_id": str, "name": str, "size": int}, ...]
    """
    if not isinstance(msg_data, list):
        return []
    files = []
    for seg in msg_data:
        if seg.get("type") == "file":
            data = seg.get("data", {})
            fname = data.get("file", "")    # 仅文件名，非路径
            fid   = data.get("file_id", "")  # 唯一文件标识符
            if not fname and not fid:
                continue
            files.append({
                "path": fname,
                "file_id": fid,
                "name": fname,
                "size": int(data.get("file_size", 0)) if data.get("file_size") else 0,
            })
    return files


def _strip_bot_mention(msg_data, bot_qq: str) -> str:
    """
    提取消息中的文本，但去掉对机器人的 @。
    例如 "[@bot] 你好" → "你好"
    """
    if isinstance(msg_data, str):
        return msg_data
    if isinstance(msg_data, list):
        parts = []
        for seg in msg_data:
            if seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
            elif seg.get("type") == "at":
                qq = str(seg.get("data", {}).get("qq", ""))
                if qq != str(bot_qq):
                    parts.append(f"@{qq}")
        return "".join(parts).strip()
    return str(msg_data)


def _strip_roleplay(text: str) -> str:
    """去除回复中的角色扮演描写（全角括号内的中文），保留半角颜文字。

    例如：
    "（伸个懒腰）唔…博士" → "唔…博士"
    "(｀・ω・´) 你好呀"  → "(｀・ω・´) 你好呀"（半角括号保留）
    """
    import re
    # 全角（）内的内容视作角色扮演描写，去掉
    return re.sub(r'（[^）]*?）', '', text).strip()


_POKE_FALLBACKS = [
    "呀！被你拍到啦～(*/ω＼*)",
    "拍我一下是要引起我注意吗？",
    "哎哟，被拍到啦，轻点嘛～",
    "怎么突然拍我呀？",
    "这一下我可记住啦～",
    "唔……拍我可以，但要记得说点好听的哦～",
]


def _build_reply_msg(text: str, msg: dict, bot_qq: str, is_first: bool = True) -> list:
    """
    构建回复消息段列表。
    - 群聊（首段）：自动 @ 发送者
    - 群聊（后续段）：纯文本，不重复 @
    - 私聊：普通文本
    """
    if msg.get("message_type") == "group":
        if is_first:
            return [
                {"type": "at", "data": {"qq": msg["user_id"]}},
                {"type": "text", "data": {"text": f" {text}"}},
            ]
        else:
            return [{"type": "text", "data": {"text": text}}]
    return [{"type": "text", "data": {"text": text}}]


# ── 桥接 Worker ──────────────────────────────────────────

class QQBridgeWorker(QThread):
    """在后台线程中运行 QQ 桥接，通过 WebSocket 连接 NapCatQQ。"""

    connected = pyqtSignal()
    disconnected = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    debug_log = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        cfg = get_qq_bridge_config()
        self._ws_url = cfg.get("ws_url", "ws://127.0.0.1:3001")
        self._bot_qq = str(cfg.get("qq_account", ""))
        self._running = False
        self._ws = None
        self._sessions = {}           # session_key -> AgentCore
        self._lock = Lock()
        self._poke_last_at = {}   # target_id -> float（拍一拍冷却时间）
        self._poke_lock = Lock()
        self._last_reply_time = {}    # session_key -> float (time.monotonic)
        self._rate_limit_count = {}   # 用于日志记录限速次数
        self._daily_counts = {}       # user_id -> 当天已回复条数（按用户隔离）
        self._daily_counts_date = time.localtime().tm_yday  # 当前日期（年积日）
        self._send_failures = 0       # 连续发送失败次数
        self._fast_reply_enabled = False  # 仅主人私聊；运行期设置，不持久化
        self._silent_override = False  # 深夜静默：是否被"醒醒"唤醒
        self._session_map = {}        # session_key -> db session_id（持久化映射）
        self._last_global_send = 0.0  # 上次成功发送消息的时间戳 (time.monotonic)
        self._send_lock = Lock()      # 发送消息的互斥锁（线程安全）

        # ── OneBot API 请求-响应同步（用于 upload_private_file 等） ──
        self._pending_api_calls = {}        # echo -> threading.Event
        self._pending_api_results = {}      # echo -> response dict
        self._api_echo_counter = 0
        self._api_lock = Lock()

        # ── 分段发送状态（莲心 → 用户） ────────────────────
        self._segment_queue = []    # 待发送的分段文本列表
        self._segment_msg = None    # 分段对应的原始消息（用于构建回复）
        self._segment_user = ""     # 分段对应的用户 ID
        self._segment_session_key = ""  # 分段所属会话，避免其他会话误打断
        self._segment_active = False  # 是否有分段正在后台发送中
        self._segment_has_sent = False  # 后台是否已发出至少一段（用于群聊 @ 判断）
        self._segment_clear_count = 0  # 队列被清空的次数（用于检测旧分段是否应丢弃）
        self._segment_lock = Lock()   # 分段状态的互斥锁
        self._request_generations = {}  # session_key -> 最新请求代数
        self._request_generation_lock = Lock()

# ── 肩部云台状态缓存（用于自然语言角度注入） ──────
        self._shoulder_state = {"pan": None, "tilt": None}
        self._is_direct_cmd = False   # 标记当前为【指令】直接执行模式

        # ── 观察模式引用 ─────────────────────────────────
        self._obs_worker = None       # ObservationModeWorker 实例
        self._track_worker = None     # TrackWorker 实例

        # ── 分段接收状态（用户 → 莲心，由"。。"触发） ────
        self._pending_buffer = {}     # session_key -> {"fragments": [str], "original_msg": dict, "user_id": str}
        self._pending_timer = None    # threading.Timer 超时自动合并
        self._pending_deferred = []   # 等待期间其他用户的消息 [(session_key, text, msg, user_id), ...]
        self._pending_lock = Lock()

        # 加载定时参数配置
        self._load_timing_config()

        # 从配置加载主人信息（支持热重载）
        self._owner_qq = cfg.get("owner_qq", "") or ""
        self._owner_name = cfg.get("owner_name", "主人") or "主人"
        self._voice_reply_enabled = cfg.get("voice_reply_enabled", True)
        self._segmented_reply_enabled = cfg.get("segmented_reply_enabled", True)

        # ── QQ 端表情包状态 ──
        self._pending_emotion_q = None      # 待发送的表情包情绪
        self._pending_emotion_img = None    # 待发送的表情包图片路径

        # 加载 QQ 会话映射（确保映射文件存在）
        self._load_session_map()

        # ── 群聊【莲心】前缀的最近文件/图片缓存 ──────────
        self._recent_cache = {}       # session_key -> items
        self._recent_cache_lock = Lock()

        # ── 群成员名片缓存（session_key -> {nickname, card, role}）──
        self._member_info_cache = {}

        # ── 群聊上下文旁听缓存（group_id -> [{"name", "text"}]）──
        self._group_context = {}

        # ── QQ 好友列表缓存（get_friend_list，供主人查询） ──
        self._friend_list_cache = None      # list[dict] 或 None
        self._friend_list_cache_time = 0.0  # time.time()
        self._friend_list_lock = Lock()

    @property
    def _user_display_name(self) -> str:
        """获取统一用户称呼（全局设置优先，回退到 QQ 配置）。"""
        try:
            return get_settings().user_name
        except Exception:
            return self._owner_name or "博士"

    # ── 线程主循环 ────────────────────────────────────────

    def run(self):
        self._running = True
        self._log(f"QQ 桥接启动，连接目标: {self._ws_url}")

        # 启动后台分段发送线程
        seg_thread = Thread(target=self._segment_worker, daemon=True)
        seg_thread.start()

        while self._running:
            try:
                self._connect_and_serve()
            except Exception as e:
                self._log(f"[!] 连接异常: {e}")
                self.error_occurred.emit(str(e))

            if self._running:
                self._log("[*] 5 秒后尝试重连...")
                time.sleep(5)

        self._cleanup_sessions()

    def stop(self):
        """安全停止桥接（从主线程调用）。"""
        self._running = False
        if self._ws:
            self._ws.close()
        self._log("QQ 桥接已停止")

    # ── WebSocket 生命周期 ───────────────────────────────

    def _connect_and_serve(self):
        ws = websocket.WebSocketApp(
            self._ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws = ws
        # run_forever 会阻塞直到连接关闭
        ws.run_forever(
            ping_interval=30,
            ping_timeout=10,
            reconnect=0,  # 不自动重连，由外层循环处理
        )

    def _on_open(self, ws):
        self._log("[✓] 已连接到 NapCatQQ")
        self.connected.emit()
        # 异步获取机器人信息（不阻塞事件循环）
        self._fetch_bot_info()

    def _on_close(self, ws, close_status_code, close_msg):
        reason = close_msg or f"状态码 {close_status_code}"
        self._log(f"[*] 连接断开: {reason}")
        self.disconnected.emit(reason)

    def _on_error(self, ws, error):
        err_str = str(error)
        if "Connection refused" in err_str:
            self._log("[!] NapCatQQ 未启动（连接被拒绝），等待重连...")
        else:
            self._log(f"[!] WebSocket 错误: {err_str}")

    def _on_message(self, ws, raw):
        """处理来自 NapCatQQ 的消息。"""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return

        if payload.get("post_type") == "message":
            # 派发到后台线程处理，避免阻塞 WebSocket 事件循环（心跳超时断连）
            Thread(target=self._handle_message, args=(payload,), daemon=True).start()
        elif payload.get("post_type") == "notice":
            # 派发 notice 事件（如拍一拍），同样走后台线程避免阻塞
            Thread(target=self._handle_notice, args=(payload,), daemon=True).start()
        # API 响应：匹配 echo 字段唤醒等待的请求
        echo = payload.get("echo")
        if echo is not None:
            with self._api_lock:
                event = self._pending_api_calls.pop(echo, None)
                if event is not None:
                    self._pending_api_results[echo] = payload
                    event.set()

    # ── 拍一拍（poke）处理 ──────────────────────────────

    def _handle_notice(self, payload: dict):
        """处理 OneBot notice 事件，目前仅处理「拍一拍」。"""
        try:
            if payload.get("notice_type") == "notify" and payload.get("sub_type") == "poke":
                self._handle_poke(payload)
        except Exception as e:
            self._log(f"[拍一拍] notice 处理异常: {e}")

    def _handle_poke(self, payload: dict):
        """处理「拍一拍」：回应（LLM 优先/萌语兜底）+ 概率反拍。"""
        try:
            # OneBot v11: user_id=被拍者(机器人)，target_id=发起者
            target_id = str(payload.get("target_id", "") or "")
            self_id = str(payload.get("user_id", "") or "")
            group_id = str(payload.get("group_id", "") or "")
            if not target_id or not self_id:
                return
            if self_id != self._bot_qq:
                # 不是拍莲心，忽略
                return
            if target_id == self._bot_qq:
                return

            cfg = get_qq_bridge_config()
            if not cfg.get("poke_enabled", True):
                return

            # ── 冷却保护，防刷屏 ──
            cooldown = float(cfg.get("poke_cooldown_seconds", 30) or 30)
            now = time.time()
            with self._poke_lock:
                last = self._poke_last_at.get(target_id, 0.0)
                if now - last < cooldown:
                    self._log(f"[拍一拍] {target_id} 冷却中（{cooldown:.0f}秒），忽略")
                    return
                self._poke_last_at[target_id] = now

            is_group = bool(group_id)
            is_owner = target_id == self._owner_qq
            self._log(
                f"[拍一拍] {target_id} 拍了拍莲心"
                + ("（群聊）" if is_group else "（私聊）")
                + ("（主人）" if is_owner else "")
            )

            # ── 生成回应（LLM 优先，失败用萌语兜底）──
            if cfg.get("poke_llm", True):
                text = self._generate_poke_reply(target_id, group_id, is_group, is_owner)
            else:
                text = ""
            if not text:
                text = random.choice(_POKE_FALLBACKS)

            # ── 发送回应 ──
            synthetic = {
                "message_type": "group" if is_group else "private",
                "user_id": int(target_id),
                "group_id": int(group_id) if group_id else None,
            }
            self._send_quick_reply(synthetic, text)

            # ── 概率反拍（默认 60%，用户拍后延迟 X 秒）──
            probability = float(cfg.get("poke_poke_back_probability", 0.6) or 0.6)
            if random.random() < probability:
                delay = float(cfg.get("poke_poke_back_delay_seconds", 2.0) or 2.0)
                Timer(delay, self._poke_back, args=(target_id, group_id)).start()
        except Exception as e:
            self._log(f"[拍一拍] 处理异常: {e}")

    def _poke_back(self, target_id: str, group_id: str):
        """反拍回去（由延迟线程调用）。"""
        try:
            params = {"user_id": int(target_id)}
            if group_id:
                params["group_id"] = int(group_id)
            result = self._send_onebot_action("send_poke", params, timeout=5.0)
            self._log(f"[拍一拍] 反拍 {target_id}: {result}")
        except Exception as e:
            self._log(f"[拍一拍] 反拍失败: {e}")

    def _build_poke_prompt(self, target_id: str, is_group: bool, is_owner: bool) -> str:
        """构建拍一拍 LLM 提示词（复用桌面端拍一拍回答风格）。"""
        if is_owner:
            user_name = self._owner_name or "主人"
        else:
            session_key = f"qq_group_{target_id}_{target_id}" if is_group else f"qq_private_{target_id}"
            info = self._member_info_cache.get(session_key, {})
            user_name = info.get("card", "") or info.get("nickname", "") or f"QQ{target_id}"
        hour = time.localtime().tm_hour
        time_ctx = "深夜" if (hour >= 23 or hour < 6) else "普通日期"
        return (
            "事实不可改变：\n"
            f"- 发起者：{user_name}\n"
            "- 对象：莲心\n"
            "- 动作：拍一拍\n"
            f"- 事件：{user_name}刚刚拍了拍莲心的头像。\n"
            "请以莲心第一人称写 1 到 2 句自然、口语化的回应短句。\n"
            "可以自然回应被拍到的感受，保持调戏和玩笑感。\n"
            "不要把动作方向写反，也不要编造莲心主动拍了对方。\n"
            "不要提到系统、模型、提示词、事件日志，不要调用工具，不要输出标题或标签。\n"
            f"当前时间语境：{time_ctx}\n"
            "这是QQ聊天，回复尽量简短（1~2句）。\n"
            "这些数据只用于调整语气，不要在回复中直接复述。"
        )

    def _generate_poke_reply(self, target_id: str, group_id: str, is_group: bool, is_owner: bool) -> str:
        """走真实 LLM 链路生成拍一拍回应（不写入正常会话），失败返回空串由萌语兜底。"""
        from brain.agent import AgentCore

        class _EphemeralHistory:
            db_path = ":memory:"
            def sync_legacy_channel_maps(self): return None
            def new_session(self, *args, **kwargs): return 0
            def update_title(self, *args, **kwargs): return None
            def save_message(self, *args, **kwargs): return 0
            def get_latest_session_id(self, *args, **kwargs): return None
            def get_messages(self, *args, **kwargs): return []
            def get_latest_message_id(self, *args, **kwargs): return 0
            def get_latest_compression_snapshot(self, *args, **kwargs): return None

        prompt = self._build_poke_prompt(target_id, is_group, is_owner)
        last_error = None
        for attempt in range(1, 4):
            try:
                isolated = AgentCore(
                    disable_tools=True,
                    track_emotion=False,
                    owner_scope=False,
                    source_channel="qq_poke",
                    history_manager=_EphemeralHistory(),
                )
                isolated.history = []
                isolated._session_titled = True
                isolated._conversation_summary = ""
                text = (isolated.chat(prompt, disable_tools=True) or "").strip()
                lowered = text.lower()
                error_markers = (
                    "api", "调用失败", "请求失败", "服务异常", "网络异常",
                    "no user query", "authenticationerror", "connection slots",
                )
                if text and not any(m in lowered for m in error_markers):
                    self._log(f"[拍一拍] LLM 回应成功 attempt={attempt}, len={len(text)}")
                    return text[:180]
                last_error = text or "LLM 返回空文本"
                self._log(f"[拍一拍] LLM 未生成有效文本 attempt={attempt}: {last_error}")
            except Exception as exc:
                last_error = exc
                self._log(f"[拍一拍] LLM 调用异常 attempt={attempt}: {exc}")
        self._log(f"[拍一拍] LLM 最终失败，转入萌语兜底: {last_error}")
        return ""

    # ── 消息处理 ─────────────────────────────────────────

    def _handle_message(self, msg: dict):
        msg_type = msg.get("message_type")
        user_id = str(msg.get("user_id", ""))

        # 忽略机器人自己的消息
        if user_id == self._bot_qq:
            return

        # 构造会话 key（提前构造，后续多处用到）
        if msg_type == "private":
            session_key = f"qq_private_{user_id}"
        elif msg_type == "group":
            group_id = str(msg.get("group_id", ""))
            session_key = f"qq_group_{group_id}_{user_id}"
        else:
            return

        # ── 群聊：无条件缓存文件/图片（即使不回复也缓存，供【莲心】前缀使用） ──
        if msg_type == "group":
            cache_files = _extract_files(msg.get("message", []))
            cache_images = _extract_images(msg.get("message", []))
            if cache_files or cache_images:
                self._cache_recent_item(session_key, cache_files, cache_images)

            # 获取群成员名片（仅首次，后续复用缓存）
            if session_key not in self._member_info_cache:
                info = self._fetch_member_info(group_id, user_id)
                if info:
                    self._member_info_cache[session_key] = info

        # 判断是否需要回复
        if not self._should_reply(msg):
            # 群聊中不回复的消息→记录到旁听缓存
            if msg_type == "group":
                raw_text = _extract_plain_text(msg.get("message", []))
                if raw_text.strip():
                    info = self._member_info_cache.get(session_key, {})
                    name = info.get("card", "") or info.get("nickname", "") or f"QQ{user_id}"
                    self._log_group_context(group_id, name, raw_text.strip()[:200])
            return

        # 新消息一旦被接受，就立即废弃同会话尚未发完的旧回复。
        request_generation = self._begin_request(session_key)

        # ── 分段接收期间，其他用户的消息排队等候 ──────────
        with self._pending_lock:
            if self._pending_buffer and session_key not in self._pending_buffer:
                self._pending_deferred.append((session_key, "", msg, user_id))
                self._log(f"[分段] [{session_key}] 排队等待（其他用户正在分段发送中）")
                return

        # ── 每日计数器重置（跨日自动重置所有计数） ──────────
        today = time.localtime().tm_yday
        if today != self._daily_counts_date:
            self._daily_counts_date = today
            self._daily_counts.clear()
            self._log("[*] 新的一天，每日回复计数已重置")

        # 提取消息纯文本（用于暗号判断和后续处理）
        if msg_type == "private":
            text = _extract_plain_text(msg.get("message", ""))
        else:
            text = _strip_bot_mention(msg.get("message", ""), self._bot_qq)

        # 提取图片URL（OneBot v11 image 消息段）
        image_urls = _extract_images(msg.get("message", []))

        # 提取文件信息（OneBot v11 file 消息段）
        files_info = _extract_files(msg.get("message", []))
        voice_urls = _extract_voices(msg.get("message", []))
        forward_urls = _extract_forwards(msg.get("message", []))

        if not text.strip() and not image_urls and not files_info and not voice_urls and not forward_urls:
            # 纯非文本消息（如语音/视频等），记录类型供调试
            seg_types = [s.get("type", "?") for s in (msg.get("message", []) if isinstance(msg.get("message"), list) else [])]
            if seg_types:
                self._log(f"[跳过] 未处理的消息段类型: {seg_types}")
            return

        # ── 【观察模式】指令处理（必须以【观察模式】开头） ──────
        clean_text = text.strip()
        if clean_text.startswith("【观察模式】"):
            self._handle_observation_cmd(clean_text, msg)
            return

        # ── 【跟踪】指令处理（必须以【跟踪】开头） ──────────
        if clean_text.startswith("【跟踪】"):
            self._handle_tracking_cmd(clean_text, msg)
            return

        # ── 普通消息 + 观察模式激活 → 排队等待 ──────────────
        if self._is_observation_active():
            from brain.observation_mode import get_observation_state
            obs_state = get_observation_state()
            obs_state.enqueue_message({
                "session_key": session_key,
                "user_id": user_id,
                "text": clean_text,
                "msg": msg,
                "image_urls": image_urls,
                "files_info": files_info,
                "voice_urls": voice_urls,
                "forward_urls": forward_urls,
                "has_cmd": False,
                "cmd_text": "",
            })
            self._log(f"[观察模式] [{session_key}] 消息已排队: {clean_text[:40]}…")
            self._send_quick_reply(msg, "等等哦～我先看完这一圈再回你(｀・ω・´)")
            return

        # ── 人体跟踪模式拦截 ─────────────────────────────
        if self._is_human_tracking_active():
            from brain.human_tracking import get_track_manager
            manager = get_track_manager()
            manager.refresh_cmd_time()
            self._send_quick_reply(msg, "人体跟踪中～等一下再聊哦(｀・ω・´)")
            return

        # ── 分段接收检测（用户用"。。"分段发送） ──────────────
        text_stripped = text.strip()
        if "【指令】" not in text_stripped and self._has_pending_suffix(text_stripped):
            # 去掉末尾"。。"后缓存，等后续合并
            self._handle_pending_segment(
                session_key, text_stripped[:-2].rstrip(), msg, user_id,
                request_generation,
            )
            return

        # 检查该会话是否有缓存的分段待合并
        with self._pending_lock:
            pending = self._pending_buffer.pop(session_key, None)
        if pending:
            pending["fragments"].append(text_stripped)
            self._process_merged_segments(pending, session_key, request_generation)
            return

        # ── 深夜静默 ────────────────────────────────────────
        hour = time.localtime().tm_hour
        in_silent = SILENT_HOUR_START <= hour < SILENT_HOUR_END

        if not in_silent:
            self._silent_override = False

        if in_silent:
            if not self._silent_override:
                if text.strip() in ("醒醒", "【醒醒】"):
                    self._silent_override = True
                    self._log(f"[唤醒] [{session_key}] 博士唤醒了莲心")
                    self._send_quick_reply(msg, f"唔…{self._user_display_name}这么晚还没睡呀？")
                    return
                else:
                    self._log(f"[静默] [{session_key}] 当前时段 ({hour}:00) 不回复")
                    return
            else:
                if text.strip() in ("晚安好梦", "【晚安好梦】"):
                    self._silent_override = False
                    self._log(f"[静默] [{session_key}] 博士说晚安，继续静默")
                    self._send_quick_reply(msg, f"晚安，{self._user_display_name}~好梦。")
                    return

        # ── 每日上限检查（按用户隔离） ──────────────────────
        limit = self._daily_limit_other
        current = self._daily_counts.get(user_id, 0)

        if self._limit_enabled and user_id != self._owner_qq and current >= limit:
            if current == limit:
                # 刚好达到上限：发送提醒，标记为已提醒（limit+1）
                self._daily_counts[user_id] = limit + 1
                self._log(f"[上限] [{session_key}] 达到上限 ({limit} 条)，发送提醒")
                self._send_quick_reply(msg, "您的今日对话次数已达到今日上限了喵~")
            return

        # ── 限速检查 ──────────────────────────────────────
        now = time.monotonic()
        last_time = self._last_reply_time.get(session_key, 0.0)
        if (not self._is_fast_owner_private(msg)
                and now - last_time < self._min_reply_interval):
            self._rate_limit_count[session_key] = self._rate_limit_count.get(session_key, 0) + 1
            if self._rate_limit_count[session_key] <= 3:
                self._log(f"[限速] [{session_key}] 发送过快，已忽略")
            return
        self._rate_limit_count[session_key] = 0

        self._log(f"[收到] [{session_key}] {text[:50]}{'…' if len(text) > 50 else ''}")

        # ── 图片分析（QQ 消息中的图片先下载并视觉识别） ──────
        if image_urls:
            self._log(f"[图片] [{session_key}] 收到 {len(image_urls)} 张图片，开始分析...")
            text = self._analyze_images(image_urls, text, session_key)
            self._log(f"[图片] [{session_key}] 分析完成，上下文共 {len(text)} 字")

        # ── 文件读取（QQ 收到的文件提取文字内容） ────────────
        if files_info:
            self._log(f"[文件] [{session_key}] 收到 {len(files_info)} 个文件，开始读取...")
            text = self._read_received_files(files_info, text, session_key)
            self._log(f"[文件] [{session_key}] 读取完成，上下文共 {len(text)} 字")

        # ── 语音转录（QQ 收到的语音消息 SILK→文字）────────────
        if voice_urls:
            self._log(f"[语音] [{session_key}] 收到 {len(voice_urls)} 条语音，开始转录...")
            text = self._process_voice(voice_urls, text, session_key)
            self._log(f"[语音] [{session_key}] 转录完成，上下文共 {len(text)} 字")

        # ── 合并转发消息展开 ────────────────────────────────
        if forward_urls:
            self._log(f"[转发] [{session_key}] 收到 {len(forward_urls)} 条合并转发消息，开始展开...")
            text = self._process_forward(forward_urls, text, session_key)
            self._log(f"[转发] [{session_key}] 展开完成，上下文共 {len(text)} 字")

        # ── 【莲心】前缀：注入缓存的最近文件/图片 ────────────
        if msg_type == "group" and text.strip().startswith("【莲心】"):
            text = text.strip()[4:].strip()  # 去掉【莲心】前缀
            cached = self._pop_cached_recent(session_key)
            if cached["files"] or cached["images"]:
                self._log(f"[莲心] [{session_key}] 注入缓存: {len(cached['files'])} 文件 + {len(cached['images'])} 图片")
                if cached["files"]:
                    text = self._read_received_files(cached["files"], text, session_key)
                if cached["images"]:
                    text = self._analyze_images(cached["images"], text, session_key)
            else:
                self._log(f"[莲心] [{session_key}] 无缓存内容")

        # ── 【语音】前缀：标记需要语音回复，去掉前缀后正常处理 ──
        should_voice_reply = bool(voice_urls)  # 收到语音自动回复语音
        if text.strip().startswith("【语音】"):
            text = text.strip()[4:].strip()
            should_voice_reply = True
            self._log(f"[语音] [{session_key}] 【语音】前缀，将以语音回复")

        # ── 【提示】帮助：列出所有可用工具 ────────────────────
        clean_text = text.strip()
        if clean_text in ("【提示】", "【指令】提示"):
            help_text = self._format_tool_help()
            self._send_quick_reply(msg, help_text)
            return

        # ── 群聊上下文注入：让 AI 知道没被 @ 期间群友聊了什么 ──
        if msg_type == "group":
            bg = self._build_group_context(group_id)
            if bg:
                text = f"{bg}\n---\n{text}"
                self._log(f"[上下文] [{group_id}] 注入背景 ({len(self._group_context.get(group_id, []))} 条)")

        # 获取 AgentCore 实例并对话
        agent = self._get_or_create_agent(session_key, user_id)

        # ── 收集工具调用信息，用于在回复时显示调用状态 ──────
        tool_calls_made = []

        def _on_tool_call(name, args):
            tool_calls_made.append(name)

        def _on_tool_result(name, result, is_error=False, elapsed_ms=0.0):
            """Deliver captured media to QQ as soon as the tool completes."""
            if is_error or name not in ("capture_desktop", "capture_from_camera"):
                return
            if self._send_observation_image(msg):
                self._log(f"[图片] [{session_key}] 已发送 {name} 采集的图片")
            else:
                self._log(f"[图片] [{session_key}] {name} 已完成，但图片发送失败")

        # ── 设置日记消息源（供 write_diary 工具使用） ──────
        from brain.tools import set_diary_message_source
        set_diary_message_source(lambda: self._get_session_messages(session_key))

        # ── 【指令】前缀检测 ──────────────────────────────────
        has_cmd, cmd_text = self._strip_command_prefix(text)

        if has_cmd:
            # 【指令】主人校验：仅主人可触发（owner_qq 为空时暂不拦截）
            if self._owner_qq and user_id != self._owner_qq:
                self._log(f"[指令] 非主人({user_id})尝试使用指令，已拒绝")
                self._send_quick_reply(msg, f"只有{self._user_display_name}才能对我发指令哦~")
                return

            # 命令模式：匹配工具关键词 → 直接执行，不走模型
            forced = self._match_forced_tool(cmd_text)
            if forced:
                from brain.tools import execute_tool
                args = self._extract_tool_args(forced, cmd_text)
                tool_calls_made.append(forced)
                self._log(f"[指令] 直接执行: {forced}")
                self._is_direct_cmd = True
                # 写日记耗时较长（AI 生成），先告知用户
                if forced == "write_diary":
                    self._send_quick_reply(msg, "正在整理今天的聊天记录，请稍候…大概需要十秒钟左右(｀・ω・´)")
                try:
                    from brain.tools import set_cross_session_context
                    set_cross_session_context(agent._session_id, agent.get_history_manager())
                    result = execute_tool(forced, args)
                    response = _strip_roleplay(str(result))
                    self._update_shoulder_state(forced, args)
                    if forced in ("capture_desktop", "capture_from_camera"):
                        self._send_observation_image(msg)
                    self._log(f"[指令] 执行成功: {forced}")
                except Exception as e:
                    response = f"（{forced} 执行失败：{e}）"
                    self._log(f"[指令] 执行失败: {forced} -> {e}")
            else:
                # 【指令】但未匹配工具 → 去掉前缀后走模型正常回复
                self._log(f"[指令] 未匹配工具，转模型处理")
                try:
                    response = _strip_roleplay(agent.chat(
                        cmd_text,
                        on_tool_call=_on_tool_call,
                        on_tool_result=_on_tool_result,
                        response_guard=lambda: self._is_request_current(
                            session_key, request_generation
                        ),
                    ))
                except Exception as e:
                    response = f"（莲心思考时出了点小问题… {e}）"
                    self._log(f"[!] AgentCore 错误: {e}")
        else:
            # 普通聊天模式：先判断路由，再选择工具/纯聊模式
            # 日记关键词注入（提醒 LLM 必须调用对应工具）
            read_diary_kw = ["读日记", "日记里", "回忆一下日记", "看看日记", "日记写了什么", "最近日记"]
            write_diary_kw = ["写日记", "写一篇日记", "生成日记", "记日记", "重新写日记"]
            if any(kw in text for kw in read_diary_kw):
                text = "[重要：你必须调用 read_diary 工具来获取日记内容，不要直接回答。]\n" + text
            elif any(kw in text for kw in write_diary_kw):
                text = "[重要：你必须调用 write_diary 工具来生成日记，不要直接说'已写好'。]\n" + text
            # 肩部云台方向关键词注入（强制触发工具调用）
            shoulder_kw = ["看看最左边", "看看最右边", "看看最上面", "看看最下面",
                           "看看左边", "看看右边", "看看上面", "看看下面",
                           "看左边", "看右边", "看上面", "看下面",
                           "左上方", "右上方", "左下方", "右下方",
                           "往左", "往右", "往上", "往下",
                           "转过去", "转头", "向左转", "向右转",
                           "抬起头", "低下头", "抬头", "低头",
                           "看看周围", "环顾四周", "扫视一下"]
            if any(kw in text for kw in shoulder_kw):
                text = self._inject_shoulder_state(text)
                text = (
                    "[重要：你必须调用 shoulder_pan/shoulder_tilt/shoulder_servo 工具来控制云台角度，"
                    "不要只描述画面或说你会去做。立即调用对应工具。]\n"
                ) + text
            try:
                route = decide(text)
                is_chat = route == "chat"
                self._log(
                    f"[路由] [{session_key}] 旧路由建议={'纯聊天' if is_chat else 'Agent'}；"
                    "实际工具决策交由 AgentCore"
                )
                response = _strip_roleplay(agent.chat(
                    text,
                    on_tool_call=_on_tool_call,
                    on_tool_result=_on_tool_result,
                    disable_tools=False,
                    response_guard=lambda: self._is_request_current(
                        session_key, request_generation
                    ),
                ))
            except Exception as e:
                response = f"（莲心思考时出了点小问题… {e}）"
                self._log(f"[!] AgentCore 错误: {e}")

        # 若调用了工具（包含命令模式和模型自动调用），在回复正文前追加调用提示
        if tool_calls_made:
            call_msgs = [f"【成功调用：{t}】" for t in tool_calls_made]
            prefix = " ".join(call_msgs) + "\n"
            # 如果 AI 回复也提到了工具调用（例如"好的，已打开..."），把提示放在前面
            response = prefix + response
            self._log(f"[工具] 调用了: {', '.join(tool_calls_made)}")

        # ── 解析并剥离情绪标签（response 返回干净文本，情绪存于 self） ──
        response = self._parse_qq_emotion(response, agent)

        if not self._is_request_current(session_key, request_generation):
            self._log(f"[打断] [{session_key}] 丢弃已过期的旧回复")
            return

        # ── 思考延迟（8~15 秒，模拟人类思考） ────────────────
        if not self._is_direct_cmd and not self._is_fast_owner_private(msg):
            think = random.uniform(*self._think_delay)
            self._log(f"[思考] [{session_key}] 思考 {think:.1f} 秒...")
            self._sleep_with_check(think)
        else:
            self._is_direct_cmd = False  # 用完清除标志

        if not self._is_request_current(session_key, request_generation):
            self._log(f"[打断] [{session_key}] 思考期间收到新消息，旧回复不再发送")
            return

        # ── 语音回复分支 ─────────────────────────────────────
        voice_handled = False
        if self._voice_reply_enabled and should_voice_reply:
            voice_ok = self._send_voice_reply(response, msg)
            if voice_ok:
                voice_handled = True
                self._last_reply_time[session_key] = time.monotonic()
                self._daily_counts[user_id] = self._daily_counts.get(user_id, 0) + 1
                self._log(f"[语音回复] [{session_key}] {response[:50]}{'…' if len(response) > 50 else ''}")
            else:
                self._log(f"[语音回复] [{session_key}] 语音发送失败，退回文字")

        if not voice_handled:
            # ── 分段处理回复（文字）─────────────────────────
            segments = self._split_response(response) if self._segmented_reply_enabled else [response]
            if not segments:
                self._log(f"[回复] [{session_key}] 空回复，跳过发送")
                return

            if len(segments) <= 1:
                typing_time = 0.0 if self._is_fast_owner_private(msg) else self._calc_typing_time(segments[0])
                if not self._is_direct_cmd:
                    self._log(f"[打字] [{session_key}] 输入 {len(segments[0])} 字，约需 {typing_time:.1f} 秒...")
                    self._sleep_with_check(typing_time)
                else:
                    self._is_direct_cmd = False

                if not self._is_request_current(session_key, request_generation):
                    self._log(f"[打断] [{session_key}] 打字期间收到新消息，旧回复不再发送")
                    return

                reply_msg = _build_reply_msg(segments[0], msg, self._bot_qq)
                self._send_msg({
                    "message_type": msg_type,
                    "user_id": msg.get("user_id"),
                    "group_id": msg.get("group_id"),
                    "message": reply_msg,
                })
                self._last_reply_time[session_key] = time.monotonic()
                self._daily_counts[user_id] = self._daily_counts.get(user_id, 0) + 1
                self._log(f"[回复] [{session_key}] {segments[0][:50]}{'…' if len(segments[0]) > 50 else ''}")
                # ── 发送表情包图片（短回复） ──
                self._send_qq_emotion_image(msg)
            else:
                # 长回复：交由后台线程分段发送
                self._log(f"[分段] [{session_key}] {len(segments)} 段，共 {len(response)} 字")

                # 中断正在进行的旧分段 + 排队新分段（原子操作）
                self._queue_segmented_response(segments, msg, user_id, session_key)

                self._last_reply_time[session_key] = time.monotonic()
                self._daily_counts[user_id] = self._daily_counts.get(user_id, 0) + 1

    def _should_reply(self, msg: dict) -> bool:
        """判断是否应该回复这条消息。"""
        msg_type = msg.get("message_type")

        if msg_type == "private":
            return True

        if msg_type == "group":
            # 群聊：被 @ 时回复
            at_qqs = _extract_at_qqs(msg.get("message", []))
            if self._bot_qq in at_qqs:
                return True
            # 【莲心】前缀：群聊中消息以【莲心】开头也回复
            raw_text = _extract_plain_text(msg.get("message", []))
            raw_stripped = raw_text.strip()
            if raw_stripped.startswith("【莲心】") or raw_stripped.startswith("【语音】"):
                return True
            # 【醒醒】唤醒：静默时段群聊唤醒也放行
            if raw_stripped in ("【醒醒】", "醒醒"):
                return True
            return False

        return False

    # ── 群聊【莲心】前缀缓存 ────────────────────────────────

    _CACHE_TTL = 600  # 缓存有效期（秒，10分钟）
    _MAX_CACHE_PER_USER = 5  # 每用户最多缓存条数

    def _cache_recent_item(self, session_key: str, files_info: list, image_urls: list):
        """缓存群聊用户最近发送的文件/图片信息，供【莲心】前缀使用。"""
        with self._recent_cache_lock:
            if session_key not in self._recent_cache:
                self._recent_cache[session_key] = []
            now = time.time()
            cache = self._recent_cache[session_key]
            for f in files_info:
                cache.append({"type": "file", "data": f, "time": now})
            for img in image_urls:
                cache.append({"type": "image", "data": img, "time": now})
            if len(cache) > self._MAX_CACHE_PER_USER:
                cache[:] = cache[-self._MAX_CACHE_PER_USER:]
            self._log(f"[缓存] [{session_key}] +{len(files_info)}文件 +{len(image_urls)}图片 (共{len(cache)}条)")

    def _pop_cached_recent(self, session_key: str) -> dict:
        """弹出该用户的缓存条目（10分钟内有效）。返回 {"files": [...], "images": [...]}。"""
        with self._recent_cache_lock:
            cache = self._recent_cache.pop(session_key, [])
        if not cache:
            return {"files": [], "images": []}
        now = time.time()
        result = {"files": [], "images": []}
        for item in cache:
            if now - item["time"] > self._CACHE_TTL:
                continue
            if item["type"] == "file":
                result["files"].append(item["data"])
            elif item["type"] == "image":
                result["images"].append(item["data"])
        self._log(f"[缓存] [{session_key}] 取出 {len(result['files'])}文件 + {len(result['images'])}图片")
        return result

    # ── 群聊上下文旁听 ──────────────────────────────────────

    _MAX_CONTEXT_LEN = 30  # 每群最多缓存消息数
    _MAX_MSG_LEN = 200     # 单条消息最多保存字符数

    def _log_group_context(self, group_id: str, name: str, text: str):
        """记录一条群聊消息到上下文缓存（仅未被回复的消息）。"""
        if group_id not in self._group_context:
            self._group_context[group_id] = []
        ctx = self._group_context[group_id]
        ctx.append({"name": name, "text": text[:self._MAX_MSG_LEN]})
        if len(ctx) > self._MAX_CONTEXT_LEN:
            ctx[:] = ctx[-self._MAX_CONTEXT_LEN:]

    def _build_group_context(self, group_id: str) -> str:
        """构造近期群聊背景文本，用于注入到 AI 回复的上下文中。"""
        msgs = self._group_context.get(group_id, [])
        if not msgs:
            return ""
        parts = ["[近期群聊背景]"]
        for m in msgs:
            parts.append(f"[{m['name']}]: {m['text']}")
        return "\n".join(parts)

    # ── AgentCore 会话管理（持久化独立会话）────────────────

    def _load_session_map(self):
        """从 JSON 文件加载 QQ 会话 -> DB session_id 映射。"""
        try:
            if _SESSION_MAP_PATH.exists():
                data = json.loads(_SESSION_MAP_PATH.read_text(encoding="utf-8"))
                self._session_map = {k: int(v) for k, v in data.items()}
                self._log(f"[*] 加载 QQ 会话映射，共 {len(self._session_map)} 个")
        except Exception as e:
            self._log(f"[!] 加载 QQ 会话映射失败: {e}")
            self._session_map = {}

    def _save_session_map(self):
        """将 QQ 会话映射写入 JSON 文件。"""
        try:
            _SESSION_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SESSION_MAP_PATH.write_text(
                json.dumps(self._session_map, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            self._log(f"[!] 保存 QQ 会话映射失败: {e}")

    def _get_or_create_agent(self, session_key: str, user_id: str = "") -> AgentCore:
        with self._lock:
            is_owner = user_id == self._owner_qq
            if session_key in self._sessions:
                cached_agent = self._sessions[session_key]
                if bool(getattr(cached_agent, "_owner_scope", False)) == is_owner:
                    return cached_agent
                self._log(
                    f"[*] 会话身份权限已变化，重建 Agent: {session_key} "
                    f"(owner_scope={is_owner})"
                )
                del self._sessions[session_key]

            # ── 确定用户上下文（让 AI 知道在跟谁说话） ─────
            disable_tools = not is_owner

            # ── 判断是群聊还是私聊 ──────────────────────────
            is_group = session_key.startswith("qq_group_")
            group_id = session_key.split("_")[2] if is_group and len(session_key.split("_")) >= 4 else ""

            qq_platform_note = (
                "\n\n【QQ平台聊天规范】\n"
                "- 这是在QQ平台上聊天，对方的手机屏幕有限，请尽量让回复简洁。\n"
                "- 如果内容较长，系统会按完整段落和句子自动拆分成多条消息发送。\n"
                "- 你每段消息末尾若带有「..」标记，表示你还有话没说完。\n"
                "- 若对方中途发来新消息，系统会中断剩余分段并优先回复对方。"
                "\n\n【聊天风格铁则】\n"
                "- **绝对禁止在回复中添加「（动作/表情/神态描写）」**。例如不要写「（愣了一下）」「（推了推眼镜）」「（微笑）」。这只针对全角括号（）内的内容，不影响【表情：XXX】标签——后者是必须遵守的格式要求，不能省略。\n"
                "- 像正常人打字聊天一样简短。日常聊天控制在 1~3 句话内。\n"
                "- 只有以下情况可以适当展开：对方明确说「详细点」「多说点」「展开说说」；或者对方发来了文件/图片要求你分析。\n"
                "- 如果只是日常问候、闲聊、简单问答，越短越好。例如对方说「吃了吗」→ 回「还没，你要请客？～」即可，不用展开。"
            )

            # 群聊专属上下文
            member_display = ""
            if is_group:
                info = self._member_info_cache.get(session_key, {})
                name = info.get("card", "") or info.get("nickname", "")
                if name:
                    member_display = f"\n- 对方在群里的昵称是「{name}」，用这个昵称称呼对方。"
            group_note = (
                f"\n\n【群聊环境】\n"
                f"- 你正在QQ群（群号{group_id}）中回复消息，群里的其他成员也能看到你的回复。\n"
                f"- 对方通过 @你 来与你对话，你的回复首段会自动 @对方。\n"
                f"- 保持回答简洁得体，因为群聊中其他成员也在看。\n"
                f"- 注意保护隐私：不要透露主人的身份、联系方式或任何个人信息，"
                f"- 也不要透露主人与你之间的聊天内容或记忆；被问及时委婉拒绝，这是你和主人之间的秘密。"
                f"- 你没有参与回复期间，群友的聊天内容会以「[近期群聊背景]」的形式在顶部展示，让你知道群里发生了什么。"
                f"{member_display}"
                f"\n\n【莲心指令 — 重要】\n"
                f"- 群聊消息以「【莲心】」开头时，系统会自动把该发送者最近发过的文件/图片内容注入到上下文中。\n"
                f"- 这个机制解决了手机端无法同时 @ 和发送附件的问题。对方可以分两步操作：\n"
                f"  ① 先发送文件/图片（不用 @）② 再发送「【莲心】+ 需求」（不用 @）\n"
                f"- 或者在同一条消息里同时发文件和「【莲心】+ 需求」也可。\n"
                f"- 如果对方的需求涉及阅读文件、分析内容，你应当根据注入的文件内容进行回答。"
            ) if is_group else ""

            if is_owner:
                user_name = get_settings().user_name
                user_desc = (
                    f"你正在与你的主人「{self._owner_name}」（QQ号{self._owner_qq}）对话。"
                    f"请像对待主人一样回应她，称呼她为「{user_name}」。"
                    f"不要在其他QQ好友面前混淆她的身份。"
                    f"{group_note}"
                    f"\n\n【重要：工具调用规则】\n"
                    f"- 你有完整的工具使用权（打开App、查时间、搜索、读取文件、生成文档等）。\n"
                    f"- 当主人要求你执行操作时，必须调用对应的工具——不要用文字假装执行。\n"
                    f"- 例如：主人说「打开网易云」→ 调用 open_app；说「现在几点」→ 调用 get_current_time。\n"
                    f"- 如果主人要求把文件发到QQ上（如'把这份文档发给我'），调用 send_file_to_qq 工具。\n"
                    f"- 只有工具返回成功结果后，你才能告知主人操作已完成。\n"
                    f"- 【严禁胡编目录内容】当主人询问某个文件夹里有什么文件时，必须先调用 list_directory 或 glob_files 查看真实结果。\n"
                    f"-   ⚠ 绝对禁止凭记忆或猜测列出文件名——那是欺骗主人。如果工具调用失败，如实报告错误。\n"
                    f"- 【严禁擅自发文件】send_file_to_qq 只在主人明确要求发送文件时才调用。禁止自作主张把文件发给主人。"
                    f"\n\n【QQ 文件接收】\n"
                    f"- 当用户在 QQ 上直接向你发送文件（如 .doc/.docx/.txt/.pdf/.xlsx 等），系统会自动读取文件内容并注入到对话上下文中。\n"
                    f"- 你会看到文件内容和文件名，可以直接针对内容进行回答、总结或分析。\n"
                    f"- 长文件会显示前 5000 字符，超出部分会标注已截断。如需阅读完整文件，可以主动调用 read_file 工具。\n"
                    f"- Excel 表格（.xlsx）会以「工作表名 + 制表符分隔」的形式展示所有行。"
                    f"\n\n【记忆规则 — 必须执行】\n"
                    f'- 当主人向你介绍某人时（如"他叫XX""他是XX""称呼他为XX"），必须立即调用 save_memory 保存到长期记忆。\n'
                    f'- 绝对禁止只说"记住了"而不调用 save_memory——那只是在假装记住。\n'
                    f"\n【快捷指令】\n"
                    f"- 在QQ聊天中，如果在消息最开头写入「【指令】」，例如「【指令】打开网易云」，"
                    f"系统会直接执行工具并返回结果，不走模型推理。\n"
                    f"- 如果主人想知道如何使用，可以告诉她：在消息最前面加上【指令】就行。\n"
                    f"- 发送「【提示】」可查看所有可用指令的列表。"
                    f"{qq_platform_note}"
                )
            else:
                user_desc = (
                    f"你正在与一位QQ好友（{user_id}）对话。"
                    f"请以友好礼貌的态度回应，但注意对方不是你的主人。"
                    f"对方不是你的主人；禁止透露主人的姓名、账号、联系方式和私人信息，"
                    f"也禁止透露主人与你（莲心）之间的聊天内容、记忆或个人档案。"
                    f"如果对方询问主人或你与主人之间的隐私，请委婉拒绝，可以说「这是我和主人之间的秘密」。"
                    f"注意：你无法为对方使用任何工具（如打开软件、搜索网页、读写文件等），只能进行纯文本聊天。"
                    f"{group_note}"
                    f"{qq_platform_note}"
                )

            # ── 查找该 QQ 用户是否已有关联的 DB session ──
            db_session_id = self._session_map.get(session_key)
            source_channel = "qq_group" if is_group else "qq_private"

            if db_session_id is not None:
                # 已有映射：恢复该会话
                agent = AgentCore(
                    session_id=db_session_id,
                    user_desc=user_desc,
                    disable_tools=disable_tools,
                    track_emotion=is_owner,
                    source_channel=source_channel,
                    participant_id=user_id,
                    owner_scope=is_owner,
                )
                self._log(f"[*] 恢复会话: {session_key} (session_id={db_session_id})")
            else:
                # 新用户：创建全新 AgentCore（自动新建 DB session）
                agent = AgentCore(
                    user_desc=user_desc,
                    disable_tools=disable_tools,
                    track_emotion=is_owner,
                    source_channel=source_channel,
                    participant_id=user_id,
                    owner_scope=is_owner,
                )
                db_session_id = agent._session_id
                self._session_map[session_key] = db_session_id
                self._save_session_map()
                self._log(f"[*] 创建新会话: {session_key} (session_id={db_session_id})")

            self._sessions[session_key] = agent
            return agent

    def _get_session_messages(self, session_key: str) -> list:
        """获取指定会话今天的所有消息记录，供写日记使用。"""
        from datetime import datetime
        agent = self._sessions.get(session_key)
        if not agent:
            return []
        mgr = agent.get_history_manager()
        today_str = datetime.now().strftime("%Y-%m-%d")
        all_msgs = mgr.get_messages(agent._session_id)
        result = []
        for m in all_msgs:
            ts = m.get("timestamp", "")
            role = m.get("role", "")
            content = m.get("content", "")
            if ts.startswith(today_str):
                result.append({"role": role, "content": content, "timestamp": ts})
        return result

    def _cleanup_sessions(self):
        with self._lock:
            self._sessions.clear()
        self._log("[*] 已清理所有会话")

    # ── 工具强制调用匹配 ─────────────────────────────────────

    def _format_tool_help(self) -> str:
        """生成【指令】可用工具的帮助文本。"""
        user_name = self._user_display_name
        return (
            "===== 【指令】可用工具 =====\n\n"
            "◆ 打开应用\n"
            "  关键词：打开、启动、运行\n"
            "  示例：【指令】打开网易云\n\n"
            "◆ 时间日期\n"
            "  关键词：几点、时间、日期、星期\n"
            "  示例：【指令】现在几点\n\n"
            "◆ 联网搜索\n"
            "  关键词：搜索、查一下、百度\n"
            "  示例：【指令】搜索今天天气\n\n"
            "◆ 查询余额\n"
            "  关键词：余额、还剩多少钱\n"
            "  示例：【指令】余额\n\n"
            "◆ 添加待办\n"
            "  关键词：提醒我、提醒\n"
            "  示例：【指令】提醒我明天开会\n\n"
            "◆ 查看待办\n"
            "  关键词：待办列表、查看待办\n"
            "  示例：【指令】待办列表\n\n"
            "◆ 完成待办\n"
            "  关键词：完成待办、标记完成\n"
            "  示例：【指令】完成待办买牛奶\n\n"
            "◆ 发送文件到QQ\n"
            "  关键词：发给我、发送文件、把文件发到\n"
            "  示例：【指令】把桌面上的周报.docx发给我\n\n"
            "◆ 音乐控制\n"
            "  关键词：播放、下一首、暂停、音量\n"
            "  示例：【指令】播放音乐\n\n"
            "◆ 查看歌单\n"
            "  关键词：歌单、有什么歌\n"
            "  示例：【指令】歌单有什么\n\n"
            "◆ 长期记忆\n"
            "  关键词：记住、记下来\n"
            "  示例：【指令】记住我的生日是5月1日\n\n"
            "◆ 主动聊天开关\n"
            "  关键词：开启主动聊天、关闭主动聊天\n"
            "  示例：【指令】开启主动聊天\n\n"
            "◆ 发送文件到QQ\n"
            "  关键词：发给我、把文件、发送文件\n"
            "  示例：【指令】把E:\\path\\file.doc发给我\n\n"
            "◆ 查看目录内容\n"
            "  关键词：查看目录、里面都有什么\n"
            "  示例：【指令】查看目录 E:\\path\n\n"
            "◆ 肩部云台 - 双轴同时控制\n"
            "  关键词：水平、竖直\n"
            "  示例：【指令】水平30，竖直45\n\n"
            "◆ 肩部云台 - 水平\n"
            "  关键词：水平舵机、左右舵机\n"
            "  示例：【指令】水平舵机30度\n\n"
            "◆ 肩部云台 - 竖直\n"
            "  关键词：竖直舵机、上下舵机\n"
            "  示例：【指令】竖直舵机45度\n\n"
            "◆ 肩部云台 - 复位\n"
            "  关键词：复位、归位\n"
            "  示例：【指令】复位\n\n"
            "◆ 肩部外设 - 状态\n"
            "  关键词：肩膀状态、外设状态\n"
            "  示例：【指令】肩膀状态\n\n"
            "◆ 肩部外设 - 温湿度\n"
            "  关键词：肩膀温度、外设温度\n"
            "  示例：【指令】肩膀温度\n\n"
            "◆ 显示本帮助\n"
            "  关键词：提示\n"
            "  示例：【提示】\n\n"
            "━━━━━━━━━━━━━━━━\n"
            f"只有{user_name}才能使用【指令】哦~"
        )

    @staticmethod
    def _strip_command_prefix(text: str) -> tuple[bool, str]:
        """
        检测消息是否以 【指令】 开头（前后允许有空白）。
        若是则返回 (True, 去掉前缀及空白后的文本)；否则返回 (False, 原文本)。
        """
        t = text.strip()
        if t.startswith("【指令】"):
            return True, t[len("【指令】"):].strip()
        return False, text

    @staticmethod
    def _match_forced_tool(text: str) -> str | None:
        """
        检测文本是否命中工具关键词。由 【指令】 命令模式调用，普通聊天不会触发。
        返回匹配关键词最多的工具名，未命中返回 None。
        """
        text_lower = text.lower()
        best_tool = None
        best_count = 0
        for tool_name, keywords in _TOOL_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text_lower)
            if count > 0 and count > best_count:
                best_count = count
                best_tool = tool_name
        return best_tool

    @staticmethod
    def _extract_tool_args(tool_name: str, text: str) -> dict:
        """
        从用户消息中提取工具的调用参数。
        对每个工具做针对性提取，提取失败时使用原文兜底。
        """
        if tool_name == "open_app":
            # 1. 优先在消息中查找已知应用名称（避免 "重新启动它吗" 这类误提取）
            _KNOWN_APPS = [
                "网易云音乐", "网易云", "cloudmusic", "CloudMusic",
                "steam", "Steam",
                "微信", "WeChat", "wechat",
                "qq", "QQ",
                "记事本", "notepad",
                "计算器", "calc",
                "画图", "mspaint",
                "资源管理器", "文件管理器", "我的电脑", "explorer",
                "命令行", "cmd",
                "任务管理器", "taskmgr",
                "控制面板", "control",
                "截图", "截图工具", "snippingtool",
            ]
            text_lower = text.lower()
            for app in _KNOWN_APPS:
                if app.lower() in text_lower:
                    return {"name": app}
            # 2. 用前缀提取（长的优先匹配）
            for prefix in ["帮我重新打开", "帮我重新启动", "重新打开", "重新启动",
                           "帮我打开", "帮我开", "打开", "启动", "运行"]:
                if prefix in text:
                    name = text.split(prefix, 1)[-1].strip()
                    # 去掉尾部的语气词/标点
                    for p in "吗吧嘛啊呀呢了哦哈？！?.!~~":
                        if name.endswith(p):
                            name = name.rstrip(p).strip()
                    if name:
                        return {"name": name}
            return {"name": text.strip()}
        elif tool_name == "get_current_time":
            return {}
        elif tool_name == "web_search":
            for prefix in ["搜索", "查查", "查一查", "查一下", "搜一下"]:
                if prefix in text:
                    query = text.split(prefix, 1)[-1].strip()
                    if query:
                        return {"query": query, "max_results": 5}
            return {"query": text.strip(), "max_results": 5}
        elif tool_name == "get_balance":
            return {}
        elif tool_name == "add_todo":
            for prefix in ["提醒我", "添加待办", "记一下", "别忘了", "添加提醒", "提醒"]:
                if prefix in text:
                    title = text.split(prefix, 1)[-1].strip()
                    if title:
                        return {"title": title[:50]}
            return {"title": text.strip()[:50]}
        elif tool_name == "control_music":
            if "暂停" in text:
                return {"action": "pause"}
            elif "下一首" in text or "下一曲" in text:
                return {"action": "next"}
            elif "上一首" in text or "上一曲" in text:
                return {"action": "prev"}
            elif "音量" in text:
                if "大" in text or "高" in text:
                    return {"action": "volume_up"}
                elif "小" in text or "低" in text:
                    return {"action": "volume_down"}
                return {"action": "volume_up"}
            elif "随机" in text or "循环" in text:
                return {"action": "loop"}
            return {"action": "play"}
        elif tool_name == "list_todos":
            return {}
        elif tool_name == "complete_todo":
            for prefix in ["完成待办", "标记完成"]:
                if prefix in text:
                    title = text.split(prefix, 1)[-1].strip()
                    if title:
                        return {"title_keyword": title}
            return {"title_keyword": text.strip()}
        elif tool_name == "save_memory":
            for prefix in ["记住", "记下来"]:
                if prefix in text:
                    fact = text.split(prefix, 1)[-1].strip()
                    if fact:
                        return {"fact": fact}
            return {"fact": text.strip()}
        elif tool_name in ("get_music_status", "get_music_playlist"):
            return {}
        elif tool_name == "toggle_proactive_chat":
            if any(kw in text for kw in ["开启", "启用"]):
                return {"action": "enable"}
            else:
                return {"action": "disable"}
        elif tool_name == "list_directory":
            # 提取目录路径：绝对路径优先，否则取"查看目录"后面的文本
            abs_match = re.search(r'([A-Za-z]:[\\/][^\s<>"|?*“”]*)', text)
            if abs_match:
                raw = abs_match.group(1).strip().rstrip('.,;:！？，。；：里面的')
                return {"path": raw}
            for kw in ["查看目录", "查看文件夹", "列出文件", "看文件夹"]:
                if kw in text:
                    after = text.split(kw, 1)[-1].strip()
                    if after:
                        raw = after.rstrip('，。；：！？里的')
                        return {"path": raw}
            return {"path": ""}
        elif tool_name == "send_file_to_qq":
            # 1. 绝对路径（带扩展名），排除中英文引号避免吞掉中文后缀
            abs_match = re.search(
                r'([A-Za-z]:[\\/][^\s<>"|?*“”]*\.[a-zA-Z0-9]{2,5})', text
            )
            if abs_match:
                return {"path": abs_match.group(1).strip()}
            # 2. 目录 + 引号内文件名（如 E:\path\里的"file.doc"）
            dir_match = re.search(
                r'([A-Za-z]:[\\/](?:[^\s<>"|?*“”\\/]+[\\/])*'
                r'[^\s<>"|?*“”]*)', text
            )
            q_match = re.search(r'[“”"]([^“”"]+)[“”"]', text)
            if dir_match and q_match:
                dir_path = dir_match.group(1).strip()
                filename = q_match.group(1).strip()
                for filler in ["里面的", "中的", "里面", "里"]:
                    if dir_path.endswith(filler):
                        dir_path = dir_path[:-len(filler)]
                        break
                sep = "\\" if not dir_path.endswith(("\\", "/")) else ""
                return {"path": dir_path + sep + filename}
            # 3. 仅引号内文件名（无目录上下文）
            if q_match:
                return {"path": q_match.group(1).strip()}
            # 4. "把XXX发给" 句式
            for prefix in ["把", "将"]:
                if prefix in text:
                    after = text.split(prefix, 1)[-1].strip()
                    for suffix in ["发给我", "发到我的qq", "发到qq", "发到", "发给", "发送给", "发送到", "发送"]:
                        if suffix in after:
                            desc = after.split(suffix, 1)[0].strip().rstrip('的')
                            if desc:
                                return {"path": desc}
            # 5. "发送文件" / "发文件" 后面的文本
            for kw in ["发送文件", "发文件"]:
                if kw in text:
                    after = text.split(kw, 1)[-1].strip()
                    if after:
                        return {"path": after}
            return {"path": text.strip()}
        elif tool_name == "write_diary":
            args = {}
            # 尝试提取消息数量：写日记最近20条 / 用最近50条消息写日记
            import re as _re
            m = _re.search(r'(\d+)\s*(?:条|条消息)', text)
            if m:
                args["message_count"] = int(m.group(1))
            if any(kw in text for kw in ["重写", "重新", "覆盖"]):
                args["force"] = True
            return args
        elif tool_name == "read_diary":
            return {}
        # ── 肩部外设 ─────────────────────────────────────────
        elif tool_name == "shoulder_servo":
            # 解析 "水平30，竖直45" 或 "pan30 tilt45" 格式
            pan = None
            tilt = None
            # 中文：水平(\d+) 或 pan[=:]?(\d+)
            m_pan = re.search(r'(?:水平|pan)[=:：]?\s*(\d+)', text, re.I)
            if m_pan:
                pan = int(m_pan.group(1))
            m_tilt = re.search(r'(?:竖直|垂直|tilt)[=:：]?\s*(\d+)', text, re.I)
            if m_tilt:
                tilt = int(m_tilt.group(1))
            if pan is None and tilt is not None:
                pan = 90
            if tilt is None and pan is not None:
                tilt = 90
            if pan is not None and tilt is not None:
                return {"pan": max(0, min(180, pan)), "tilt": max(0, min(180, tilt))}
            # 兜底：尝试提取两个纯数字（用逗号/空格分隔）
            nums = re.findall(r'(\d+)', text)
            if len(nums) >= 2:
                return {"pan": max(0, min(180, int(nums[0]))), "tilt": max(0, min(180, int(nums[1])))}
            return {}
        elif tool_name == "shoulder_pan":
            m = re.search(r'(?:水平|pan)[=:：]?\s*(\d+)', text, re.I)
            if m:
                return {"angle": max(0, min(180, int(m.group(1))))}
            m = re.search(r'(\d+)', text)
            if m:
                return {"angle": max(0, min(180, int(m.group(1))))}
            return {}
        elif tool_name == "shoulder_tilt":
            m = re.search(r'(?:竖直|垂直|tilt)[=:：]?\s*(\d+)', text, re.I)
            if m:
                return {"angle": max(0, min(180, int(m.group(1))))}
            m = re.search(r'(\d+)', text)
            if m:
                return {"angle": max(0, min(180, int(m.group(1))))}
            return {}
        elif tool_name in ("shoulder_center", "shoulder_status", "shoulder_temp"):
            return {}
        return {}

    # ── 肩部云台状态跟踪 ────────────────────────────────────

    def _update_shoulder_state(self, tool_name: str, args: dict):
        """根据工具调用参数更新缓存的云台角度。"""
        s = self._shoulder_state
        if tool_name == "shoulder_servo":
            if "pan" in args:
                s["pan"] = args["pan"]
            if "tilt" in args:
                s["tilt"] = args["tilt"]
        elif tool_name == "shoulder_pan":
            if "angle" in args:
                s["pan"] = args["angle"]
        elif tool_name == "shoulder_tilt":
            if "angle" in args:
                s["tilt"] = args["angle"]
        elif tool_name == "shoulder_center":
            s["pan"] = 90
            s["tilt"] = 90

    def _inject_shoulder_state(self, text: str) -> str:
        """如果缓存了云台角度且消息涉及肩部控制，在消息前注入当前状态。"""
        s = self._shoulder_state
        if s["pan"] is not None and s["tilt"] is not None:
            return f"[当前云台：水平={s['pan']}°, 垂直={s['tilt']}°]\n{text}"
        return text

    # ── 观察模式 ────────────────────────────────────────────

    def _is_observation_active(self) -> bool:
        """检查观察模式是否激活。"""
        try:
            from brain.observation_mode import get_observation_state
            return get_observation_state().is_active
        except Exception:
            return False

    def _handle_observation_cmd(self, text: str, msg: dict):
        """处理【观察模式】开启/关闭 指令。"""
        from brain.observation_mode import get_observation_state
        from workers.observation_mode_worker import ObservationModeWorker

        if "开启" in text or "启动" in text:
            if self._is_observation_active():
                self._send_quick_reply(msg, "已经在观察模式啦～我正看着周围呢(｀・ω・´)")
                return

            state = get_observation_state()
            state.set_qq_bridge(self)
            state.activate()

            # 启动 Worker
            self._obs_worker = ObservationModeWorker(state)
            self._obs_worker.pending_messages.connect(
                self._on_observation_pending_messages
            )
            self._obs_worker.mode_exited.connect(self._on_observation_mode_exit)
            self._obs_worker.start()

            self._send_quick_reply(msg, "好嘞！让我看看周围有什么有趣的东西～(^-^)")
            self._log("[观察模式] 已开启")

        elif "关闭" in text or "退出" in text or "停止" in text:
            state = get_observation_state()
            if not state.is_active:
                self._send_quick_reply(msg, "观察模式本来就是关闭的哦(｀・ω・´)")
                return

            state.deactivate()  # Worker 会在下次检查时自动退出
            self._obs_worker = None
            self._send_quick_reply(msg, "好的，我乖乖待着～(^-^)")
            self._log("[观察模式] 已关闭")

        else:
            self._send_quick_reply(msg, "要用【观察模式】开启 或 【观察模式】关闭 来告诉我哦(｀・ω・´)")

    def _on_observation_pending_messages(self, msgs: list):
        """处理观察模式期间排队的用户消息。每条在独立 daemon 线程中处理。"""
        total = len(msgs)
        self._log(f"[观察模式] 开始处理 {total} 条排队消息")

        from brain.observation_mode import get_observation_state
        state = get_observation_state()
        if not state.is_active:
            return

        done_counter = {"n": 0}
        done_lock = Lock()

        def _process_one(item):
            try:
                self._process_observation_pending(item)
            except Exception as e:
                self._log(f"[观察模式] 消息处理失败: {e}")
            finally:
                with done_lock:
                    done_counter["n"] += 1
                    if done_counter["n"] >= total:
                        state.notify_processing_done()

        for item in msgs:
            if not state.is_active:
                state.notify_processing_done()
                return
            Thread(target=_process_one, args=(item,), daemon=True).start()

    def _process_observation_pending(self, item: dict):
        """处理单条观察模式排队消息。"""
        text = item.get("text", "")
        if not text.strip():
            return

        session_key = item.get("session_key", "")
        user_id = item.get("user_id", "")
        original_msg = item.get("msg", {})

        # 获取 AgentCore 实例
        agent = self._get_or_create_agent(session_key, user_id)
        from brain.tools import set_diary_message_source
        set_diary_message_source(lambda: self._get_session_messages(session_key))
        from brain.tools import _register_qq_bridge
        _register_qq_bridge(self)

        has_cmd, cmd_text = self._strip_command_prefix(text)

        if has_cmd:
            # 【指令】模式
            forced = self._match_forced_tool(cmd_text)
            self._is_direct_cmd = True
            if forced:
                args = self._extract_tool_args(forced, cmd_text)
                try:
                    from brain.tools import execute_tool, set_cross_session_context
                    set_cross_session_context(agent._session_id, agent.get_history_manager())
                    result = execute_tool(forced, args)
                    response = str(result)
                    self._update_shoulder_state(forced, args)
                except Exception as e:
                    response = f"（{forced} 执行失败：{e}）"
            else:
                try:
                    from brain.agent import AgentCore
                    response = agent.chat(cmd_text)
                except Exception as e:
                    response = f"（处理失败：{e}）"

            self._send_quick_reply(original_msg, response)
            self._log(f"[观察模式-指令] [{session_key}] 回复: {response[:50]}…")
        else:
            # 普通聊天
            try:
                response = agent.chat(text)
                self._send_quick_reply(original_msg, response)
                self._log(f"[观察模式-聊天] [{session_key}] 回复: {response[:50]}…")
            except Exception as e:
                self._log(f"[观察模式-聊天] 失败: {e}")

    def _on_observation_mode_exit(self, reason: str):
        """观察模式退出回调。"""
        self._obs_worker = None
        self._log(f"[观察模式] 已退出: {reason}")

    # ── 人体跟踪模式 ────────────────────────────────────────

    def _handle_tracking_cmd(self, text: str, msg: dict):
        """处理【跟踪】指令。"""
        cmd = text[len("【跟踪】"):].strip()

        if cmd in ("停止", "关闭", "退出", "结束"):
            from brain.tools import _stop_human_tracking
            result = _stop_human_tracking()
            self._send_quick_reply(msg, result)
        elif cmd in ("人", "开始", "启动", "") or cmd.startswith("人"):
            from brain.tools import _start_human_tracking
            result = _start_human_tracking()
            self._send_quick_reply(msg, result)
        else:
            self._send_quick_reply(
                msg,
                "【跟踪】后面可以接「人」开始人体跟踪，或「停止」结束哦~"
            )

    def _is_human_tracking_active(self) -> bool:
        """人体跟踪是否正在运行。"""
        from brain.human_tracking import get_track_manager
        return get_track_manager().is_active

    def _on_human_tracking_exit(self, reason: str):
        """人体跟踪退出回调。"""
        self._track_worker = None
        self._log(f"[人体跟踪] 已退出: {reason}")

    # ── 分段接收（用户用"。。"分段发送消息） ────────────────

    def _has_pending_suffix(self, text: str) -> bool:
        """检查消息末尾是否有"。。"标记（表示还有话没说完）"""
        return text.rstrip().endswith("。。")

    def _handle_pending_segment(self, session_key: str, text: str, msg: dict,
                                user_id: str, request_generation: int):
        """缓存一段待接收的文本片段，等待后续合并"""
        with self._pending_lock:
            # 检查是否有其他用户的 pending 会话
            existing_keys = list(self._pending_buffer.keys())
            if existing_keys and existing_keys[0] != session_key:
                # 有其他用户正在分段发送，排队等候
                self._pending_deferred.append((session_key, text, msg, user_id))
                self._log(f"[分段] [{session_key}] 排队等待（{existing_keys[0]} 正在分段发送中）")
                return

            if session_key in self._pending_buffer:
                self._pending_buffer[session_key]["fragments"].append(text)
                self._pending_buffer[session_key]["generation"] = request_generation
                n = len(self._pending_buffer[session_key]["fragments"])
                self._log(f"[分段] [{session_key}] 已缓存第 {n} 段（共 {len(text)} 字）")
            else:
                self._pending_buffer[session_key] = {
                    "fragments": [text],
                    "original_msg": msg,
                    "user_id": user_id,
                    "generation": request_generation,
                }
                self._log(f"[分段] [{session_key}] 开始等待后续片段（第1段，{len(text)} 字）")

            # 重置超时定时器
            self._reset_pending_timer()

    def _reset_pending_timer(self):
        """重置超时定时器（每次收到新片段刷新 60 秒）"""
        if self._pending_timer and self._pending_timer.is_alive():
            self._pending_timer.cancel()
        self._pending_timer = Timer(SEGMENT_WAIT_TIMEOUT, self._on_pending_timeout)
        self._pending_timer.daemon = True
        self._pending_timer.start()

    def _on_pending_timeout(self):
        """超时（60 秒未收到下一段），自动合并已收到的片段并处理"""
        with self._pending_lock:
            if not self._pending_buffer:
                return
            session_key = next(iter(self._pending_buffer))
            pending = self._pending_buffer.pop(session_key, None)

        if pending:
            self._log(f"[分段] [{session_key}] 等待超时 ({SEGMENT_WAIT_TIMEOUT}s)，自动合并处理")
            self._process_merged_segments(
                pending, session_key, pending.get("generation", 0)
            )

    def _process_merged_segments(self, pending: dict, session_key: str,
                                 request_generation: int):
        """合并分段文本后走正常处理流程"""
        merged_text = "\n".join(pending["fragments"])
        msg = pending["original_msg"]
        user_id = pending["user_id"]

        self._log(f"[分段] [{session_key}] 合并 {len(pending['fragments'])} 段（共 {len(merged_text)} 字）→ 处理")

        # ── 图片分析（从第一段消息中提取图片） ────────────
        image_urls = _extract_images(msg.get("message", []))
        if image_urls:
            self._log(f"[图片] [{session_key}] 合并消息含 {len(image_urls)} 张图片，开始分析...")
            merged_text = self._analyze_images(image_urls, merged_text, session_key)

        # ── 文件读取 ─────────────────────────────────────
        file_info = _extract_files(msg.get("message", []))
        if file_info:
            self._log(f"[文件] [{session_key}] 合并消息含 {len(file_info)} 个文件，开始读取...")
            merged_text = self._read_received_files(file_info, merged_text, session_key)

        # ── 语音转录 ────────────────────────────────────────
        voice_urls = _extract_voices(msg.get("message", []))
        if voice_urls:
            self._log(f"[语音] [{session_key}] 合并消息含 {len(voice_urls)} 条语音，开始转录...")
            merged_text = self._process_voice(voice_urls, merged_text, session_key)

        # ── 合并转发消息展开 ────────────────────────────────
        forward_urls = _extract_forwards(msg.get("message", []))
        if forward_urls:
            self._log(f"[转发] [{session_key}] 合并消息含 {len(forward_urls)} 条合并转发，开始展开...")
            merged_text = self._process_forward(forward_urls, merged_text, session_key)

        # ── 【莲心】前缀：注入缓存的最近文件/图片 ────────────
        if session_key.startswith("qq_group_") and merged_text.strip().startswith("【莲心】"):
            merged_text = merged_text.strip()[4:].strip()
            cached = self._pop_cached_recent(session_key)
            if cached["files"] or cached["images"]:
                self._log(f"[莲心] [{session_key}] 注入缓存: {len(cached['files'])} 文件 + {len(cached['images'])} 图片")
                if cached["files"]:
                    merged_text = self._read_received_files(cached["files"], merged_text, session_key)
                if cached["images"]:
                    merged_text = self._analyze_images(cached["images"], merged_text, session_key)
            else:
                self._log(f"[莲心] [{session_key}] 无缓存内容")

        # ── 【语音】前缀 ────────────────────────────────────
        should_voice_reply = bool(voice_urls)
        if merged_text.strip().startswith("【语音】"):
            merged_text = merged_text.strip()[4:].strip()
            should_voice_reply = True
            self._log(f"[语音] [{session_key}] 【语音】前缀，将以语音回复")

        # ── 每日上限检查 ──────────────────────────────────
        limit = self._daily_limit_other
        current = self._daily_counts.get(user_id, 0)
        if self._limit_enabled and user_id != self._owner_qq and current >= limit:
            if current == limit:
                self._daily_counts[user_id] = limit + 1
                self._send_quick_reply(msg, "您的今日对话次数已达到今日上限了喵~")
            self._after_pending_flush()
            return

        # ── 【提示】帮助 ──────────────────────────────────
        clean = merged_text.strip()
        if clean in ("【提示】", "【指令】提示"):
            self._send_quick_reply(msg, self._format_tool_help())
            self._after_pending_flush()
            return

        # ── 群聊上下文注入 ──────────────────────────────────
        if session_key.startswith("qq_group_"):
            parts = session_key.split("_")
            if len(parts) >= 4:
                gid = parts[2]
                bg = self._build_group_context(gid)
                if bg:
                    merged_text = f"{bg}\n---\n{merged_text}"

        agent = self._get_or_create_agent(session_key, user_id)
        tool_calls_made = []

        def _on_tool_call(name, args):
            tool_calls_made.append(name)

        def _on_tool_result(name, result, is_error=False, elapsed_ms=0.0):
            if is_error or name not in ("capture_desktop", "capture_from_camera"):
                return
            if self._send_observation_image(msg):
                self._log(f"[图片] [{session_key}] 已发送 {name} 采集的图片")
            else:
                self._log(f"[图片] [{session_key}] {name} 已完成，但图片发送失败")

        # ── 【指令】前缀检测 ──────────────────────────────
        has_cmd, cmd_text = self._strip_command_prefix(merged_text)

        if has_cmd:
            if self._owner_qq and user_id != self._owner_qq:
                self._send_quick_reply(msg, f"只有{self._user_display_name}才能对我发指令哦~")
                self._after_pending_flush()
                return
            forced = self._match_forced_tool(cmd_text)
            if forced:
                from brain.tools import execute_tool
                args = self._extract_tool_args(forced, cmd_text)
                tool_calls_made.append(forced)
                self._is_direct_cmd = True
                try:
                    from brain.tools import set_cross_session_context
                    set_cross_session_context(agent._session_id, agent.get_history_manager())
                    result = execute_tool(forced, args)
                    response = _strip_roleplay(str(result))
                    self._update_shoulder_state(forced, args)
                    if forced in ("capture_desktop", "capture_from_camera"):
                        self._send_observation_image(msg)
                except Exception as e:
                    response = f"（{forced} 执行失败：{e}）"
            else:
                try:
                    response = _strip_roleplay(agent.chat(
                        cmd_text,
                        on_tool_call=_on_tool_call,
                        on_tool_result=_on_tool_result,
                        response_guard=lambda: self._is_request_current(
                            session_key, request_generation
                        ),
                    ))
                except Exception as e:
                    response = f"（莲心思考时出了点小问题… {e}）"
        else:
            try:
                route = decide(merged_text)
                is_chat = route == "chat"
                self._log(
                    f"[路由] [{session_key}] 旧路由建议={'纯聊天' if is_chat else 'Agent'}（分段合并）；"
                    "实际工具决策交由 AgentCore"
                )
                response = _strip_roleplay(agent.chat(
                    merged_text,
                    on_tool_call=_on_tool_call,
                    on_tool_result=_on_tool_result,
                    disable_tools=False,
                    response_guard=lambda: self._is_request_current(
                        session_key, request_generation
                    ),
                ))
            except Exception as e:
                response = f"（莲心思考时出了点小问题… {e}）"

        # 工具调用提示
        if tool_calls_made:
            call_msgs = [f"【成功调用：{t}】" for t in tool_calls_made]
            prefix = " ".join(call_msgs) + "\n"
            response = prefix + response

        # ── 解析并剥离情绪标签（合并路径） ──
        response = self._parse_qq_emotion(response, agent)

        if not self._is_request_current(session_key, request_generation):
            self._log(f"[打断] [{session_key}] 丢弃已过期的合并消息回复")
            self._after_pending_flush()
            return

        # ── 思考延迟 ─────────────────────────────────────
        if not self._is_direct_cmd and not self._is_fast_owner_private(msg):
            think = random.uniform(*self._think_delay)
            self._log(f"[思考] [{session_key}] 思考 {think:.1f} 秒...")
            self._sleep_with_check(think)
        else:
            self._is_direct_cmd = False

        if not self._is_request_current(session_key, request_generation):
            self._log(f"[打断] [{session_key}] 思考期间收到新消息，合并回复不再发送")
            self._after_pending_flush()
            return

        # ── 语音回复分支 ─────────────────────────────────
        voice_handled = False
        if self._voice_reply_enabled and should_voice_reply:
            voice_ok = self._send_voice_reply(response, msg)
            if voice_ok:
                voice_handled = True
                self._last_reply_time[session_key] = time.monotonic()
                self._daily_counts[user_id] = self._daily_counts.get(user_id, 0) + 1
                self._log(f"[语音回复] [{session_key}] {response[:50]}{'…' if len(response) > 50 else ''}")
            else:
                self._log(f"[语音回复] [{session_key}] 语音发送失败，退回文字")

        if not voice_handled:
            # ── 分段发送回复（文字）─────────────────────────
            segments = self._split_response(response) if self._segmented_reply_enabled else [response]
            if not segments:
                self._log(f"[回复] [{session_key}] 空回复，跳过发送")
                self._after_pending_flush()
                return
            if len(segments) <= 1:
                typing_time = 0.0 if self._is_fast_owner_private(msg) else self._calc_typing_time(segments[0])
                if not self._is_direct_cmd:
                    self._log(f"[打字] [{session_key}] 输入 {len(segments[0])} 字，约需 {typing_time:.1f} 秒...")
                    self._sleep_with_check(typing_time)
                else:
                    self._is_direct_cmd = False
                if not self._is_request_current(session_key, request_generation):
                    self._log(f"[打断] [{session_key}] 打字期间收到新消息，合并回复不再发送")
                    self._after_pending_flush()
                    return
                reply_msg = _build_reply_msg(segments[0], msg, self._bot_qq)
                self._send_msg({
                    "message_type": msg.get("message_type"),
                    "user_id": msg.get("user_id"),
                    "group_id": msg.get("group_id"),
                    "message": reply_msg,
                })
                self._last_reply_time[session_key] = time.monotonic()
                self._log(f"[回复] [{session_key}] {segments[0][:50]}{'…' if len(segments[0]) > 50 else ''}")
                # ── 发送表情包图片（合并路径短回复） ──
                self._send_qq_emotion_image(msg)
            else:
                # 中断正在进行的旧分段 + 排队新分段（原子操作）
                self._queue_segmented_response(segments, msg, user_id, session_key)

        # 记录每日计数
        if not voice_handled:
            self._last_reply_time[session_key] = time.monotonic()
            self._daily_counts[user_id] = self._daily_counts.get(user_id, 0) + 1

        # ── 处理排队消息 ─────────────────────────────────
        self._after_pending_flush()

    def _after_pending_flush(self):
        """分段接收结束后，处理等待期间排队的消息"""
        deferred = []
        with self._pending_lock:
            self._pending_buffer.clear()
            if self._pending_timer:
                try:
                    self._pending_timer.cancel()
                except Exception:
                    pass
                self._pending_timer = None
            deferred = list(self._pending_deferred)
            self._pending_deferred.clear()

        # 把排队消息重新注入处理流程
        for sk, txt, m, uid in deferred:
            self._log(f"[排队] [{sk}] 开始处理排队消息")
            # 走完整的 _handle_message 流程
            self._handle_message(m)

    # ── 定时参数加载 ─────────────────────────────────────────

    def _load_timing_config(self):
        """从配置文件加载定时参数，使用默认值补全缺失字段。"""
        timing = get_qq_timing_config()
        self._think_delay = (timing["think_delay_min"], timing["think_delay_max"])
        self._type_speed = (timing["type_speed_min"], timing["type_speed_max"])
        self._segment_interval = (timing["segment_interval_min"], timing["segment_interval_max"])
        self._segment_pending = ".."
        self._global_send_interval = (timing["global_send_interval_min"], timing["global_send_interval_max"])
        self._min_reply_interval = timing["min_reply_interval"]
        self._daily_limit_other = timing["daily_limit_other"]
        self._limit_enabled = bool(timing.get("limit_enabled", True))

    def set_fast_reply_enabled(self, enabled: bool):
        """Enable artificial-delay bypass for the owner's private chat only."""
        self._fast_reply_enabled = bool(enabled)
        state = "开启" if self._fast_reply_enabled else "关闭"
        self._log(f"[*] 主人私聊极速回复已{state}")

    def _is_fast_owner_private(self, msg_ctx: dict | None) -> bool:
        if not self._fast_reply_enabled or not msg_ctx:
            return False
        return (
            msg_ctx.get("message_type") == "private"
            and str(msg_ctx.get("user_id", "")) == str(self._owner_qq)
        )

    def reload_timing_config(self):
        """从配置文件重新加载定时参数（供设置面板热重载调用）。"""
        self._load_timing_config()
        self._log("[*] 定时参数已从配置重新加载")

    def reload_bridge_config(self):
        """从配置文件重新加载桥接参数（主人信息等，供 ApiConfigDialog 保存后调用）。"""
        cfg = get_qq_bridge_config()
        self._ws_url = cfg.get("ws_url", "ws://127.0.0.1:3001")
        self._bot_qq = str(cfg.get("qq_account", ""))
        self._owner_qq = cfg.get("owner_qq", "") or ""
        self._owner_name = cfg.get("owner_name", "主人") or "主人"
        self._voice_reply_enabled = cfg.get("voice_reply_enabled", True)
        self._segmented_reply_enabled = cfg.get("segmented_reply_enabled", True)
        self._log("[*] 桥接参数已从配置重新加载")

    # ── OneBot API 调用 ─────────────────────────────────

    def _send_onebot_action(self, action: str, params: dict, timeout: float = 30.0) -> str:
        """发送 OneBot API 动作并等待响应，返回执行结果文本。"""
        # 生成唯一 echo 标识
        with self._api_lock:
            self._api_echo_counter += 1
            echo = f"lianxin_{self._api_echo_counter}_{int(time.monotonic() * 1000)}"
            event = Event()
            self._pending_api_calls[echo] = event

        payload = json.dumps({
            "action": action,
            "params": params,
            "echo": echo,
        }, ensure_ascii=False)
        try:
            if self._ws and self._ws.sock and self._ws.sock.connected:
                self._ws.send(payload)
            else:
                with self._api_lock:
                    self._pending_api_calls.pop(echo, None)
                return f"执行 {action} 失败：WebSocket 未连接"
        except Exception as e:
            with self._api_lock:
                self._pending_api_calls.pop(echo, None)
            self._send_failures += 1
            self._log(f"[!] OneBot API 调用失败 ({action}): {e}")
            return f"执行 {action} 失败：{e}"

        # 等待响应
        ok = event.wait(timeout)
        with self._api_lock:
            self._pending_api_calls.pop(echo, None)
            resp = self._pending_api_results.pop(echo, None)

        self._send_failures = 0

        if not ok:
            return f"执行 {action} 超时（{timeout}秒未收到响应）"

        status = resp.get("status")
        retcode = resp.get("retcode")
        msg = resp.get("message", "")
        if status == "ok" and retcode == 0:
            return f"已执行 {action}"
        else:
            return f"执行 {action} 失败（retcode={retcode}, status={status}）: {msg}"

    def _call_onebot_api(self, action: str, params: dict, timeout: float = 15.0) -> dict | None:
        """发送 OneBot API 动作并返回响应 data 字段。失败返回 None。"""
        with self._api_lock:
            self._api_echo_counter += 1
            echo = f"lianxin_{self._api_echo_counter}_{int(time.monotonic() * 1000)}"
            event = Event()
            self._pending_api_calls[echo] = event

        payload = json.dumps({
            "action": action,
            "params": params,
            "echo": echo,
        }, ensure_ascii=False)
        try:
            if self._ws and self._ws.sock and self._ws.sock.connected:
                self._ws.send(payload)
            else:
                with self._api_lock:
                    self._pending_api_calls.pop(echo, None)
                self._log(f"[转发] {action} 失败：WebSocket 未连接")
                return None
        except Exception as e:
            with self._api_lock:
                self._pending_api_calls.pop(echo, None)
            self._log(f"[转发] {action} 异常: {e}")
            return None

        ok = event.wait(timeout)
        with self._api_lock:
            self._pending_api_calls.pop(echo, None)
            resp = self._pending_api_results.pop(echo, None)

        if not ok or not resp:
            self._log(f"[转发] {action} 超时或无响应")
            return None

        if resp.get("status") == "ok" and resp.get("retcode") == 0:
            return resp.get("data", {})
        self._log(f"[转发] {action} 返回错误: retcode={resp.get('retcode')}, status={resp.get('status')}")
        return None

    # ── QQ 表情包 ──────────────────────────────────────────

    def _parse_qq_emotion(self, text: str, agent=None) -> str:
        """从 agent._last_emotion 获取情绪（文本中标签已被 agent.chat() 剥离）。
        返回原文本不变（已为干净文本）。
        """
        from utils.emotion_manager import get_random_emotion_image
        import random

        self._pending_emotion_q = None
        self._pending_emotion_img = None

        # 从 agent 读取情绪（标签已在 agent.chat() 中剥离并存储）
        emotion = getattr(agent, '_last_emotion', None) if agent else None
        if emotion:
            try:
                from utils.settings import get_settings
                prob = get_settings().emotion_probability
            except Exception:
                prob = 0.6
            if random.random() < prob:
                img = get_random_emotion_image(emotion)
                if img:
                    self._pending_emotion_q = emotion
                    self._pending_emotion_img = img
        else:
            # 诊断日志：LLM 未输出情绪标签（仅首次出现时记录）
            raw = getattr(agent, '_last_raw_response', '') if agent else ''
            if raw and not getattr(self, '_warned_no_emotion', False):
                self._warned_no_emotion = True
                tail = raw[-80:] if len(raw) > 80 else raw
                self._log(f"[诊断] LLM 未输出情绪标签, raw末尾: {repr(tail)}")
        return text  # text 已是干净文本，直接返回

    def _send_qq_emotion_image(self, msg_ctx: dict):
        """发送表情包图片到 QQ（如果有待发送的情绪图片）。"""
        img_path = self._pending_emotion_img
        emotion = self._pending_emotion_q
        if not img_path:
            return
        self._pending_emotion_q = None
        self._pending_emotion_img = None

        normalized = str(Path(img_path).resolve()).replace("\\", "/")
        params = {
            "message_type": msg_ctx.get("message_type", "private"),
            "user_id": msg_ctx.get("user_id"),
            "message": [{"type": "image", "data": {"file": f"file:///{normalized}"}}],
        }
        if msg_ctx.get("group_id"):
            params["group_id"] = msg_ctx.get("group_id")

        self._log(f"[表情包] 发送 {emotion or '?'} -> {img_path}")
        self._send_msg(params)

    def _send_observation_image(self, msg_ctx: dict) -> bool:
        """Send the most recent desktop/camera capture as a QQ image message."""
        try:
            from brain.tools import get_observation_image
            observation = get_observation_image()
            image_path = observation.get("path") if observation else None
            if not image_path or not Path(image_path).is_file():
                self._log(f"[图片] 采集图片不存在: {image_path}")
                return False

            normalized = str(Path(image_path).resolve()).replace("\\", "/")
            params = {
                "message_type": msg_ctx.get("message_type", "private"),
                "user_id": msg_ctx.get("user_id"),
                "message": [{"type": "image", "data": {"file": f"file:///{normalized}"}}],
            }
            if msg_ctx.get("group_id"):
                params["group_id"] = msg_ctx["group_id"]
            return self._send_msg(params)
        except Exception as exc:
            self._log(f"[图片] 发送采集图片异常: {exc}")
            return False

    def send_file_to_qq(self, file_path: str, name: str = "", user_id: str = "") -> str:
        """将本地文件发送到主人的 QQ 私聊。
        返回执行结果文本，供工具函数返回给 AI。
        """
        path = Path(file_path)
        if not path.exists():
            return f"文件不存在：{file_path}"
        if not path.is_file():
            return f"路径不是文件：{file_path}"

        target = user_id or self._owner_qq
        if not target:
            return "发送失败：未配置主人 QQ 号"

        display_name = name or path.name
        # 将路径转为正斜杠（NapCatQQ 兼容性）
        normalized_path = str(path.resolve()).replace("\\", "/")
        return self._send_onebot_action("upload_private_file", {
            "user_id": int(target),
            "file": normalized_path,
            "name": display_name,
        })

    def get_qq_friend_list(self, refresh: bool = False, keyword: str = "") -> str:
        """获取主人绑定 QQ 的好友列表（OneBot get_friend_list）。

        返回格式化文本，供工具函数返回给 AI。默认使用最近缓存，refresh=True 强制刷新。
        """
        now = time.time()
        with self._friend_list_lock:
            cache_valid = (
                self._friend_list_cache is not None
                and now - self._friend_list_cache_time < 60
            )
            if refresh or not cache_valid:
                data = self._call_onebot_api("get_friend_list", {}, timeout=10.0)
                if not isinstance(data, list):
                    return "获取 QQ 好友列表失败：NapCat 未连接或返回异常，请稍后重试。"
                self._friend_list_cache = list(data)
                self._friend_list_cache_time = now
            friends = list(self._friend_list_cache)

        kw = str(keyword or "").strip()
        if kw:
            friends = [
                f for f in friends
                if kw.lower() in str(f.get("nickname", "") or "").lower()
                or kw.lower() in str(f.get("remark", "") or "").lower()
                or kw.lower() in str(f.get("user_id", "") or "")
            ]
        if not friends:
            if kw:
                return f"没有找到昵称、备注或 QQ 号包含「{kw}」的好友。"
            return "QQ 好友列表为空。"

        lines = [f"你绑定的 QQ 共有 {len(friends)} 位好友："]
        for f in sorted(friends, key=lambda x: str(x.get("nickname", "") or "")):
            uid = f.get("user_id", "")
            nick = f.get("nickname", "") or ""
            remark = f.get("remark", "") or ""
            tag = f"（备注：{remark}）" if remark and remark != nick else ""
            lines.append(f"- {nick}（{uid}）{tag}")
        lines.append("请如实转述；好友较多时可按需列出主要几位并说明总数。")
        return "\n".join(lines)

    def _send_msg(self, params: dict) -> bool:
        """发送消息，等待 NapCat 确认送达。返回 True 表示成功，False 表示失败。"""
        # ── 全局限速（在锁外完成，避免长时间持锁阻塞其他发送者） ──
        now = time.monotonic()
        elapsed = now - self._last_global_send
        if self._last_global_send > 0 and not self._is_fast_owner_private(params):
            required_gap = random.uniform(*self._global_send_interval)
            if elapsed < required_gap:
                extra = required_gap - elapsed
                self._log(f"[全局限速] 距上条消息仅 {elapsed:.1f} 秒，额外等待 {extra:.1f} 秒...")
                self._sleep_with_check(extra)

        # 连续失败过多则直接拒绝，避免无效阻塞
        if self._send_failures >= 3:
            self._log(f"[!] 连续发送失败 {self._send_failures} 次，暂停发送")
            return False

        # 注册 echo 用于等待 NapCat 的 API 响应
        with self._api_lock:
            self._api_echo_counter += 1
            echo = f"send_{self._api_echo_counter}_{int(time.monotonic() * 1000)}"
            event = Event()
            self._pending_api_calls[echo] = event

        # WebSocket 写操作（持锁，但仅限网络 IO 本身）
        with self._send_lock:
            payload = json.dumps({
                "action": "send_msg",
                "params": params,
                "echo": echo,
            }, ensure_ascii=False)
            try:
                if not (self._ws and self._ws.sock and self._ws.sock.connected):
                    raise ConnectionError("WebSocket 未连接")
                # 设置写超时，防止 TCP 缓冲区满导致无限阻塞
                old_timeout = self._ws.sock.gettimeout()
                self._ws.sock.settimeout(10)
                try:
                    self._ws.send(payload)
                finally:
                    self._ws.sock.settimeout(old_timeout)
            except Exception as e:
                self._send_failures += 1
                with self._api_lock:
                    self._pending_api_calls.pop(echo, None)
                self._log(f"[!] 发送消息失败 (连续{self._send_failures}次): {e}")
                return False

        # 等待 NapCat 确认（锁外，不阻塞其他线程）
        ok = event.wait(10.0)
        with self._api_lock:
            self._pending_api_calls.pop(echo, None)
            result = self._pending_api_results.pop(echo, None)

        if ok and result and result.get("status") == "ok":
            self._send_failures = 0
            self._last_global_send = time.monotonic()
            return True
        else:
            self._send_failures += 1
            self._log(f"[!] NapCat 未确认发送 (连续{self._send_failures}次){': ' + str(result)[:100] if result else ''}")
            return False

    def _send_quick_reply(self, msg: dict, text: str):
        """发送一条简单回复（不经过 AgentCore，用于暗号响应）。"""
        reply = _build_reply_msg(text, msg, self._bot_qq)
        self._send_msg({
            "message_type": msg.get("message_type"),
            "user_id": msg.get("user_id"),
            "group_id": msg.get("group_id"),
            "message": reply,
        })

    def send_to_owner(self, text: str):
        """向主人发送一条主动消息（私聊），由主线程或其他线程调用。"""
        if not self._owner_qq:
            self._log("[!] 未配置主人QQ号，无法发送主动消息")
            return
        message = [{"type": "text", "data": {"text": text}}]
        self._send_msg({
            "message_type": "private",
            "user_id": int(self._owner_qq),
            "message": message,
        })

    def _fetch_bot_info(self):
        """连接后记录日志（配置中已有 QQ 号，无需从 API 获取）。"""
        self._log(f"[✓] 已连接到 NapCatQQ，QQ: {self._bot_qq}")
        self._log("[✓] QQ 桥接就绪，等待消息中...")

    # ── 辅助 ────────────────────────────────────────────

    def _log(self, msg: str):
        """发射调试日志。含"失败/错误/异常/Error"的错误消息不限频，普通消息限频 100ms。"""
        is_error = any(kw in msg for kw in ("失败", "错误", "异常", "Error"))
        if not is_error:
            now = time.monotonic()
            if hasattr(self, '_last_log_time') and now - self._last_log_time < 0.1:
                return  # 限频：同 100ms 内重复信号丢弃
            self._last_log_time = now
        self.debug_log.emit(msg)

    # ── 分段发送后台线程 ──────────────────────────────────

    def _begin_request(self, session_key: str) -> int:
        """登记新请求并立即打断同会话尚未发完的旧回复。"""
        with self._request_generation_lock:
            generation = self._request_generations.get(session_key, 0) + 1
            self._request_generations[session_key] = generation
        self._interrupt_segment_delivery(session_key)
        return generation

    def _is_request_current(self, session_key: str, generation: int) -> bool:
        with self._request_generation_lock:
            return self._request_generations.get(session_key, 0) == generation

    def _interrupt_segment_delivery(self, session_key: str) -> bool:
        """清除同会话旧分段，并使已弹出的旧分段代数失效。"""
        with self._segment_lock:
            if not self._segment_active or self._segment_session_key != session_key:
                return False
            remaining = len(self._segment_queue)
            self._segment_queue.clear()
            self._segment_active = False
            self._segment_has_sent = False
            self._segment_session_key = ""
            self._segment_clear_count += 1
        self._log(f"[打断] [{session_key}] 新消息到达，丢弃旧回复剩余 {remaining} 段")
        return True

    def _queue_segmented_response(self, segments: list[str], msg: dict,
                                  user_id: str, session_key: str):
        with self._segment_lock:
            self._segment_queue.clear()
            self._segment_has_sent = False
            self._segment_clear_count += 1
            self._segment_queue = list(segments)
            self._segment_msg = msg
            self._segment_user = user_id
            self._segment_session_key = session_key
            self._segment_active = True

    def _segment_worker(self):
        """后台线程：逐段发送长回复，并在新消息到达时中断。"""
        while self._running:
            # 检查是否有待发送的分段
            with self._segment_lock:
                has_work = bool(self._segment_queue) and self._segment_active

            if not has_work:
                time.sleep(0.3)
                continue

            # 取出一个分段
            with self._segment_lock:
                if not self._segment_queue:
                    self._segment_active = False
                    continue
                segment = self._segment_queue.pop(0)
                is_last = len(self._segment_queue) == 0
                msg_ctx = self._segment_msg
                user_id = self._segment_user
                popped_gen = self._segment_clear_count  # 记录取出时的代数

            # 非末段末尾加上「..」标记
            if not is_last:
                segment = segment.rstrip() + f" {self._segment_pending}"

            # 按字符数计算打字时间
            typing_time = 0.0 if self._is_fast_owner_private(msg_ctx) else self._calc_typing_time(segment)
            self._log(f"[分段打字] 输入 {len(segment)} 字，约需 {typing_time:.1f} 秒...")

            # 打字等待（期间可被中断）
            interrupted = self._sleep_with_interrupt(typing_time, popped_gen)
            if interrupted or not self._running:
                continue

            # 检查分段是否已失效（队列被清空过）
            with self._segment_lock:
                if self._segment_clear_count != popped_gen:
                    self._log("[分段] 检测到中断，丢弃已过期的旧分段")
                    continue

            # 首段群聊带 @，后续段纯文本
            with self._segment_lock:
                is_first = not self._segment_has_sent
                if is_first:
                    self._segment_has_sent = True

            reply_msg = _build_reply_msg(segment, msg_ctx, self._bot_qq, is_first=is_first)
            ok = self._send_msg({
                "message_type": msg_ctx.get("message_type"),
                "user_id": msg_ctx.get("user_id"),
                "group_id": msg_ctx.get("group_id"),
                "message": reply_msg,
            })

            if not ok:
                # 发送失败，清空剩余分段并释放状态，恢复就绪
                self._log(f"[分段] 发送失败，取消剩余 {len(self._segment_queue)} 段，恢复就绪")
                with self._segment_lock:
                    self._segment_queue.clear()
                    self._segment_active = False
                    self._segment_has_sent = False
                    self._segment_session_key = ""
                break

            self._log(f"[分段] 已发 ({len(segment)}字){' 未完..' if not is_last else ' 完毕'}")

            if is_last:
                # 全部发完
                with self._segment_lock:
                    self._segment_active = False
                    self._segment_has_sent = False
                    self._segment_session_key = ""
                # 分段发送完毕，检查是否需要发送表情包图片
                self._send_qq_emotion_image(msg_ctx)
            else:
                # 段间间隔（期间可被中断）
                delay = 0.0 if self._is_fast_owner_private(msg_ctx) else random.uniform(*self._segment_interval)
                self._log(f"[分段] 段间等待 {delay:.1f} 秒...")
                interrupted = self._sleep_with_interrupt(delay, popped_gen)
                if interrupted:
                    # 队列已被 _handle_message 清空或替换，回主循环重新判断
                    with self._segment_lock:
                        self._segment_has_sent = False
                    self._log("[分段] 被新消息中断，剩余分段取消")
                    continue

    def _sleep_with_interrupt(self, seconds: float, initial_clear_count: int) -> bool:
        """
        休眠同时检测中断条件。返回 True 表示被中断（队列被 _handle_message 替换）。
        用于后台分段发送线程。
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end and self._running:
            with self._segment_lock:
                if self._segment_clear_count != initial_clear_count:
                    return True  # 队列被主线程替换 → 中断
            time.sleep(0.3)
        return False

    # ── 图片处理 ────────────────────────────────────────────

    def _get_image_via_api(self, file_id_or_name: str) -> str | None:
        """通过 OneBot get_image API 获取图片本地路径。"""
        with self._api_lock:
            self._api_echo_counter += 1
            echo = f"getimg_{self._api_echo_counter}_{int(time.monotonic() * 1000)}"
            event = Event()
            self._pending_api_calls[echo] = event

        payload = json.dumps({
            "action": "get_image",
            "params": {"file_id": file_id_or_name},
            "echo": echo,
        }, ensure_ascii=False)
        try:
            if self._ws and self._ws.sock and self._ws.sock.connected:
                self._ws.send(payload)
            else:
                with self._api_lock:
                    self._pending_api_calls.pop(echo, None)
                return None
        except Exception:
            with self._api_lock:
                self._pending_api_calls.pop(echo, None)
            return None

        ok = event.wait(10.0)
        with self._api_lock:
            self._pending_api_calls.pop(echo, None)
            resp = self._pending_api_results.pop(echo, None)

        if not ok or not resp:
            return None
        data = resp.get("data", {})
        file_path = data.get("file", "") or data.get("url", "")
        if file_path:
            resolved = _resolve_file_path(file_path)
            if Path(resolved).is_file():
                return resolved
        return None

    def _download_image(self, img_info: dict | str) -> str | None:
        """获取图片本地路径。优先 API / 本地路径，回退 URL 下载。返回路径或 None。"""
        url = img_info.get("url", "") if isinstance(img_info, dict) else str(img_info)
        file_id = img_info.get("file_id", "") if isinstance(img_info, dict) else ""
        file_field = img_info.get("file", "") if isinstance(img_info, dict) else ""
        file_repr = repr(file_field[:120]) if file_field else "无"
        self._log(f"[图片] 字段详情: url={'有' if url else '无'}, file_id={'有' if file_id else '无'}, file={file_repr}")

        # 方式1：有 file 字段 → file:// 转本地路径，或尝试 get_image API
        if file_field:
            # 1a: file:// URL 转本地路径
            if file_field.startswith("file://"):
                fp = _resolve_file_path(file_field)
                if Path(fp).is_file():
                    self._log(f"[图片] file:// 解析到本地文件: {fp}")
                    return fp
            # 1b: 直接作为 get_image 的参数（NapCatQQ 可能接受 file 值）
            resolved = self._get_image_via_api(file_field)
            if resolved:
                self._log(f"[图片] get_image API 解析到: {resolved}")
                return resolved
            # 1c: 直接作为本地路径检查
            if Path(file_field).is_file():
                self._log(f"[图片] 本地路径: {file_field}")
                return file_field

        # 方式2：有 file_id → get_file API
        if file_id:
            try:
                resolved = self._resolve_file_via_api(file_id, "image.jpg")
                if resolved and Path(resolved).is_file():
                    size = Path(resolved).stat().st_size
                    if size > 1000:
                        self._log(f"[图片] get_file 解析到: {resolved} ({size/1024:.0f}KB)")
                        return resolved
            except Exception as e:
                self._log(f"[图片] get_file 异常: {e}")

        # 方式3：URL 下载
        if url:
            headers_list = [
                {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                 "Referer": "https://multimedia.nt.qq.com.cn/"},
                {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                 "Referer": "https://qq.com/"},
                {"User-Agent": "Mozilla/5.0"},
            ]
            for headers in headers_list:
                try:
                    resp = requests.get(url, timeout=15, headers=headers)
                    resp.raise_for_status()
                    if len(resp.content) < 500:
                        continue
                    suffix = ".jpg"
                    if "png" in resp.headers.get("Content-Type", ""):
                        suffix = ".png"
                    elif "gif" in resp.headers.get("Content-Type", ""):
                        suffix = ".gif"
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                    tmp.write(resp.content)
                    tmp.close()
                    self._log(f"[图片] URL 下载成功: ({len(resp.content)/1024:.0f}KB)")
                    return tmp.name
                except requests.RequestException as e:
                    resp_body = e.response.text[:150] if hasattr(e, "response") and e.response is not None else "无响应体"
                    self._log(f"[图片] URL 异常 ({resp_body})")
            self._log(f"[图片异常] URL 下载失败（三种 header 均过期）")

        return None

    def _analyze_images(self, image_urls: list, text: str, session_key: str) -> str:
        """下载并分析图片，返回合并了图片描述的上下文文本。"""
        from config import get_siliconflow_config
        cfg = get_siliconflow_config()
        if not cfg.get("api_key"):
            self._log(f"[图片] 未配置视觉API，跳过分析")
            if text.strip():
                return f"[用户发了一张图片]\n{text}"
            return "[用户发了一张图片]\n请回应：你看到了一张图片，但暂时无法分析其内容。"

        from brain.vision import describe_image

        descriptions = []
        for i, img_info in enumerate(image_urls):
            img_path = self._download_image(img_info)
            if img_path is None:
                descriptions.append("[一张图片（下载失败）]")
                continue
            try:
                desc = describe_image(img_path)
                descriptions.append(desc)
                self._log(f"[图片] 分析完成 ({i+1}/{len(image_urls)}): {desc[:50]}...")
            except Exception as e:
                descriptions.append(f"[图片分析失败: {e}]")
                self._log(f"[图片] 分析失败: {e}")
            finally:
                try:
                    os.unlink(img_path)
                except Exception:
                    pass

        parts = []
        for d in descriptions:
            parts.append(f"[用户发了一张图片，视觉分析结果如下]\n{d}")
        if text.strip():
            parts.append(text)
        return "\n\n".join(parts)

    # ── 语音处理 ────────────────────────────────────────────

    def _download_voice(self, voice_info: dict) -> str | None:
        """获取 QQ 语音文件路径，失败返回 None。

        策略：
        1. file_id → get_file API 拿本地路径（最可靠）
        2. url 为本地文件路径 → 复制到临时目录（NapCatQQ 部分版本）
        3. url 为 HTTP 链接 → requests 下载
        """
        import shutil

        url = voice_info.get("url", "")
        file_id = voice_info.get("file_id", "")

        self._log(f"[语音] voice_info: url={'有' if url else '无'} file_id={'有' if file_id else '无'} 类型={'path' if url and not url.startswith('http') else 'http' if url and url.startswith('http') else 'none'}")

        # 方式1：有 file_id → get_file API 拿本地路径（不走网络，无格式问题）
        if file_id:
            try:
                resolved = self._resolve_file_via_api(file_id, "voice.silk")
                if resolved and Path(resolved).is_file():
                    size = Path(resolved).stat().st_size
                    if size > 100:  # >100B 才可能是有效语音
                        self._log(f"[语音] API 解析到本地文件: {resolved} ({size}B)")
                        return resolved
                    else:
                        self._log(f"[语音] API 解析文件过小 ({size}B)，尝试其他方式")
                else:
                    self._log(f"[语音] API 解析失败或文件不存在: {resolved}")
            except Exception as e:
                self._log(f"[语音] API 解析异常: {e}")

        # 方式2：url 为本地文件路径（NapCatQQ 部分版本返回 file:// 或本地绝对路径）
        if url:
            # 统一处理 file:// 前缀和混合路径 -> Windows 本地路径
            local_path = _resolve_file_path(url)
            url_path = Path(local_path)

            # 文件可能还在写入中，最多等 3.5 秒（0.5s → 1s → 2s 三级重试）
            for retry_delay in (0.5, 1.0, 2.0):
                if url_path.is_file():
                    fmt = url_path.suffix.lower()  # .amr / .silk / .wav
                    self._log(f"[语音] 本地文件路径: {url_path} ({url_path.stat().st_size}B)")
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=fmt or ".amr")
                    tmp.close()
                    shutil.copy2(str(url_path), tmp.name)
                    self._log(f"[语音] 已复制到临时文件: {tmp.name}")
                    return tmp.name
                self._log(f"[语音] 等待文件落盘 ({retry_delay:.1f}s)...")
                time.sleep(retry_delay)

            self._log(f"[语音] 文件始终不存在: path={local_path}")

            # 方式3：url 是 HTTP 链接 → 下载兜底
            if url.startswith("http://") or url.startswith("https://"):
                try:
                    resp = requests.get(url, timeout=30, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "*/*",
                    })
                    resp.raise_for_status()
                    if len(resp.content) < 100:
                        self._log(f"[语音] URL 返回内容过小 ({len(resp.content)}B)，可能非有效语音")
                        return None
                    # 诊断：检查 SILK 文件头
                    header_hex = resp.content[:16].hex()
                    header_ascii = resp.content[:16]
                    self._log(f"[语音] 音频文件头: {header_hex} -> {header_ascii[:5]!r}")
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".silk")
                    tmp.write(resp.content)
                    tmp.close()
                    self._log(f"[语音] URL 下载成功: {url[:60]}... -> {tmp.name} ({len(resp.content)}B)")
                    return tmp.name
                except Exception as e:
                    self._log(f"[语音] URL 下载失败: {url[:60]}... -> {e}")

        return None

    def _process_voice(self, voice_urls: list, text: str, session_key: str) -> str:
        """下载语音 → SILK→WAV → Whisper 转文字 → 合并到 text。"""
        from brain.audio_utils import convert_voice_to_text

        # 等 150ms 让 _log 的 100ms 限频窗口过去，确保语音日志不被吞掉
        time.sleep(0.15)

        transcriptions = []
        for i, v in enumerate(voice_urls):
            silk_path = self._download_voice(v)
            if silk_path is None:
                transcriptions.append("[一段语音（下载失败）]")
                continue
            try:
                transcribed = convert_voice_to_text(silk_path, debug_log=self._log)
                if transcribed:
                    transcriptions.append(f"[用户发来语音，转文字: {transcribed}]")
                else:
                    transcriptions.append("[一段语音（未识别到内容）]")
                self._log(f"[语音] 转录完成 ({i+1}/{len(voice_urls)}): {transcribed[:80] if transcribed else '(空)'}")
            except Exception as e:
                transcriptions.append(f"[一段语音（转录失败: {e}）]")
                self._log(f"[语音] 转录失败: {e}")
            finally:
                try:
                    os.unlink(silk_path)
                except Exception:
                    pass

        parts = transcriptions + ([text] if text.strip() else [])
        return "\n\n".join(parts)

    # ════════════════════════════════════════════════════════
    # 合并转发消息解析
    # ════════════════════════════════════════════════════════

    def _process_forward(self, forward_urls: list, text: str, session_key: str) -> str:
        """处理合并转发消息，展开为结构化文本后注入原消息文本。"""
        self._forward_img_count = 0  # 每轮转发重置图片计数
        parts = []
        for info in forward_urls:
            fid = info["id"]
            raw_data = info.get("_raw_data", {})
            content = self._get_forward_content(fid, depth=0, session_key=session_key, raw_data=raw_data)
            if content:
                parts.append(content)
            else:
                parts.append("[收到一条合并转发消息，但无法获取详细内容]")

        if text.strip():
            parts.append(text)
        return "\n\n".join(parts)

    def _get_forward_content(self, forward_id: str, depth: int, session_key: str, raw_data: dict = None) -> str | None:
        """调用 get_forward_msg API → 遍历节点 → 格式化输出。API 失败时尝试 XML 回退。"""

        data = self._call_onebot_api("get_forward_msg", {"id": forward_id}, timeout=20.0)
        if not data:
            self._log(f"[转发] get_forward_msg API 失败，尝试 XML 回退")
            if raw_data:
                fallback = self._parse_forward_xml_fallback(raw_data, depth, session_key)
                if fallback:
                    return fallback
            return None

        nodes = data.get("messages", [])
        if not nodes:
            self._log(f"[转发] messages 列表为空")
            return None

        total = len(nodes)
        truncated = total > MAX_FORWARD_NODES
        if truncated:
            nodes = nodes[:MAX_FORWARD_NODES]

        # ── 统计参与者和消息类型 ──────────────────────────
        senders_set = {}
        group_id_seen = None
        for n in nodes:
            s = n.get("sender", {})
            uid = str(s.get("user_id", ""))
            nick = s.get("nickname", "") or f"QQ{uid}"
            if uid and uid not in senders_set:
                senders_set[uid] = nick
            # 识别转发来源（是否为群聊）
            if group_id_seen is None and n.get("message_type") == "group":
                group_id_seen = n.get("group_id", "") or ""

        # ── 头部信息 ──────────────────────────────────────
        lines = []
        if group_id_seen:
            info = f"来自群聊{group_id_seen}，{len(senders_set)} 人参与"
        else:
            names = list(senders_set.values())[:5]
            info = "、".join(names)
            if len(senders_set) > 5:
                info += f"等 {len(senders_set)} 人"
        lines.append(f"[合并转发聊天记录：{info}，共 {total} 条消息]")
        if truncated:
            lines.append(f"[仅展示前 {MAX_FORWARD_NODES} 条]")
        lines.append("─" * 30)

        # ── 逐条格式化 ────────────────────────────────────
        total_chars = 0
        for node in nodes:
            line = self._format_node(node, depth, session_key)
            if not line:
                continue
            total_chars += len(line)
            if total_chars > MAX_FORWARD_CHARS:
                lines.append("[后续消息过长，已截断]")
                break
            lines.append(line)

        lines.append("─" * 30)
        lines.append("[以上是合并转发的对话内容，请基于这些内容回答用户的问题]")

        return "\n".join(lines)

    def _parse_forward_xml_fallback(self, raw_data: dict, depth: int, session_key: str) -> str | None:
        """当 get_forward_msg API 失败时，从原始消息段的 XML 预览中提取文字摘要。"""
        import xml.etree.ElementTree as ET

        xml_str = raw_data.get("xmlContent", "") or raw_data.get("content", "") or raw_data.get("xml", "")
        if not xml_str:
            all_keys = list(raw_data.keys())
            self._log(f"[转发] XML 回退无数据，raw_data keys: {all_keys}")
            return None

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError as e:
            self._log(f"[转发] XML 解析失败: {e}")
            return None

        source_el = root.find(".//source")
        source_name = source_el.get("name", "") if source_el is not None else ""
        t_sum = root.get("tSum", "?")

        titles = root.findall(".//title")
        lines = []
        title_text = source_name or "聊天记录"
        lines.append(f"[合并转发聊天记录：{title_text}，共 {t_sum} 条消息（文字摘要）]")
        lines.append("─" * 30)

        msg_lines = []
        for title_el in titles:
            text = (title_el.text or "").strip()
            if not text:
                continue
            # size="34" 是大标题，跳过
            if title_el.get("size", "") == "34":
                continue
            msg_lines.append(text)

        if msg_lines:
            total_chars = 0
            for line in msg_lines:
                total_chars += len(line)
                if total_chars > MAX_FORWARD_CHARS:
                    lines.append("[后续消息过长，已截断]")
                    break
                lines.append(line)
        else:
            lines.append("[无法解析消息内容]")

        lines.append("─" * 30)
        lines.append("[以上是合并转发的文字摘要，完整内容中的图片/语音/文件暂无法识别]")

        return "\n".join(lines)

    # ── 转发内辅助：图片/文件/语音真实处理 ────────────────

    def _describe_forward_image(self, img_data: dict, session_key: str) -> str | None:
        """下载转发中的图片并用 Vision API 描述。失败返回 None。"""
        MAX_IMAGES = 10
        if self._forward_img_count >= MAX_IMAGES:
            return "[图片]"
        self._forward_img_count += 1

        try:
            img_path = self._download_image(img_data)
            if not img_path:
                return None
            from config import get_siliconflow_config
            cfg = get_siliconflow_config()
            if not cfg.get("api_key"):
                return None
            from brain.vision import describe_image
            desc = describe_image(img_path)
            try:
                os.unlink(img_path)
            except Exception:
                pass
            return f"[图片: {desc[:120]}]" if desc else None
        except Exception as e:
            self._log(f"[转发] 图片分析失败: {e}")
            return None

    def _read_forward_file(self, file_data: dict, session_key: str) -> str | None:
        """读取转发中的文件内容。失败返回 None。"""
        MAX_CHARS = 3000
        fname = file_data.get("file", "") or file_data.get("name", "")
        fid = file_data.get("file_id", "")

        try:
            path = None
            if fid:
                resolved = self._resolve_file_via_api(fid, fname or "file")
                if resolved:
                    p = Path(resolved)
                    if p.is_file():
                        path = str(p)
            if not path and fname:
                p = Path(fname)
                if p.is_file():
                    path = str(p)
            if not path:
                return None

            from brain.tools import _extract_full_text
            content, err = _extract_full_text(Path(path))
            if err:
                self._log(f"[转发] 文件读取失败: {fname} -> {err}")
                return None
            if not content:
                return None
            if len(content) > MAX_CHARS:
                content = content[:MAX_CHARS] + "\n…（文件较长已截断）"
            return f"[文件: {fname}]\n内容: {content}" if fname else f"[文件内容: {content}]"
        except Exception as e:
            self._log(f"[转发] 文件处理异常: {e}")
            return None

    def _transcribe_forward_voice(self, voice_data: dict) -> str | None:
        """下载并转录转发中的语音消息。失败返回 None。"""
        try:
            self._log(f"[转发] 语音段数据 keys: {list(voice_data.keys())}, url={'有' if voice_data.get('url') else '无'}, file_id={'有' if voice_data.get('file_id') else '无'}, file={'有' if voice_data.get('file') else '无'}")
            voice_path = self._download_voice(voice_data)
            if not voice_path:
                self._log(f"[转发] 语音下载失败: _download_voice 返回 None")
                return None
            from brain.audio_utils import convert_voice_to_text
            text = convert_voice_to_text(voice_path, debug_log=self._log)
            try:
                os.unlink(voice_path)
            except Exception:
                pass
            return f"[语音转文字: {text[:150]}]" if text else None
        except Exception as e:
            self._log(f"[转发] 语音转录失败: {e}")
            return None

    def _format_node(self, node: dict, depth: int, session_key: str) -> str | None:
        """格式化单个转发节点（发信人 + 时间 + 消息内容）。"""

        sender = node.get("sender", {})
        uid = str(sender.get("user_id", ""))
        nick = sender.get("nickname", "") or f"QQ{uid}"

        # ── 时间 ──────────────────────────────────────────
        msg_time = ""
        ts = node.get("time", 0)
        if ts:
            try:
                from datetime import datetime
                dt = datetime.fromtimestamp(ts)
                msg_time = dt.strftime("%m-%d %H:%M")
            except Exception:
                pass

        # ── 消息内容解析 ──────────────────────────────────
        message = node.get("message", [])
        if not isinstance(message, list):
            message = [{"type": "text", "data": {"text": str(message)}}]

        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            st = seg.get("type", "")
            sd = seg.get("data", {})

            if st == "text":
                txt = sd.get("text", "").strip()
                if txt:
                    parts.append(txt)
            elif st == "image":
                desc = self._describe_forward_image(sd, session_key)
                parts.append(desc if desc else "[图片]")
            elif st == "face":
                parts.append("[表情]")
            elif st == "file":
                content = self._read_forward_file(sd, session_key)
                parts.append(content if content else f"[文件: {sd.get('file', '') or sd.get('name', '') or '文件'}]")
            elif st in ("voice", "record"):
                trans = self._transcribe_forward_voice(sd)
                parts.append(trans if trans else "[语音]")
            elif st == "video":
                parts.append("[视频]")
            elif st == "reply":
                parts.append("[引用回复]")
            elif st == "forward":
                if depth < MAX_FORWARD_DEPTH:
                    nested_id = sd.get("id", "")
                    if nested_id:
                        nested = self._get_forward_content(nested_id, depth + 1, session_key)
                        if nested:
                            indented = "\n".join("  " + ln for ln in nested.split("\n"))
                            parts.append(f"[嵌套转发:\n{indented}\n  /嵌套转发]")
                        else:
                            parts.append("[嵌套转发（无法展开）]")
                    else:
                        parts.append("[嵌套转发]")
                else:
                    parts.append("[嵌套转发（已达最大深度）]")
            elif st in ("markdown", "miraiCode"):
                txt = sd.get("data", "") or sd.get("content", "") or ""
                parts.append(str(txt)[:200])
            else:
                for k in ("text", "content", "data", "title"):
                    v = sd.get(k, "")
                    if v:
                        parts.append(str(v)[:200])
                        break
                else:
                    parts.append(f"[{st}]")

        if not parts:
            return None

        content = " ".join(parts)
        label = f"[{nick}(QQ{uid}){msg_time}]" if uid and uid != "0" else f"[{nick}{msg_time}]"
        return f"{label}: {content}"

    def _send_voice_reply(self, text: str, msg: dict):
        """生成语音回复并发送。

        动态截断策略（TTS → WAV → SILK → base64 内联发送）：
        - ≤ 100 字：全量语音，不附文字
        - 100~300 字：语音说前 100 字 + "..."，后附完整文字（另发一条消息）
        - > 300 字：语音说前 120 字 + "..."，后附完整文字（另发一条消息）
        """
        from brain.audio_utils import convert_text_to_voice
        import base64

        text_len = len(text)

        # ── 动态截断：决定语音内容 ──
        if text_len <= 100:
            voice_text = text
        elif text_len <= 300:
            voice_text = text[:100].rstrip() + "..."
        else:
            voice_text = text[:120].rstrip() + "..."

        # 去掉颜文字（如 (｀・ω・´) 会被 TTS 读出 "omega"，很出戏）
        voice_text = re.sub(r'\([^)]*\)', '', voice_text).strip()
        if not voice_text:
            voice_text = text[:80]
            voice_text = re.sub(r'\([^)]*\)', '', voice_text).strip() or text[:80]

        # 生成 SILK 文件
        silk_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".silk")
        silk_path = silk_tmp.name
        silk_tmp.close()

        try:
            ok = convert_text_to_voice(voice_text, silk_path, debug_log=self._log)
            if not ok:
                self._log("[语音] TTS→SILK 转换失败，退回文字回复")
                return False

            # 读取 SILK 文件并 base64 编码
            with open(silk_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("ascii")
            file_data = f"base64://{b64_data}"

            # 先发语音（纯 record，不混文字段）
            voice_msg = [{"type": "record", "data": {"file": file_data}}]
            result = self._send_onebot_action("send_msg", {
                "message_type": msg.get("message_type"),
                "user_id": msg.get("user_id"),
                "group_id": msg.get("group_id"),
                "message": voice_msg,
            }, timeout=15.0)
            self._log(f"[语音] 发送结果: {result}")

            if "成功" not in result and "已执行" not in result:
                self._log(f"[语音] NapCatQQ 返回错误，退回文字")
                return False

            # 语音发送成功 → 如果文字被截断，另发一条消息附完整文字
            if text_len > 100:
                self._send_msg({
                    "message_type": msg.get("message_type"),
                    "user_id": msg.get("user_id"),
                    "group_id": msg.get("group_id"),
                    "message": [{"type": "text", "data": {"text": text}}],
                })
                self._log(f"[语音] 已附完整文字 ({text_len} 字)")

            return True

        except Exception as e:
            self._log(f"[语音] 发送失败: {e}")
            return False
        finally:
            import threading
            threading.Thread(target=lambda: self._cleanup_voice(silk_path), daemon=True).start()

    @staticmethod
    def _cleanup_voice(path: str):
        """延迟清理语音临时文件（等发送完成后再删）。"""
        import time
        time.sleep(5)
        try:
            os.unlink(path)
        except Exception:
            pass

    # ── 文件接收与读取 ────────────────────────────────────────

    _MAX_FILE_CHARS = 5000  # 文件注入上下文的最大字符数

    def _resolve_file_via_api(self, file_id: str, fname: str) -> str:
        """通过 OneBot get_file API 获取文件的本地路径。"""
        with self._api_lock:
            self._api_echo_counter += 1
            echo = f"getfile_{self._api_echo_counter}_{int(time.monotonic() * 1000)}"
            event = Event()
            self._pending_api_calls[echo] = event

        payload = json.dumps({
            "action": "get_file",
            "params": {"file_id": file_id},
            "echo": echo,
        }, ensure_ascii=False)
        try:
            if self._ws and self._ws.sock and self._ws.sock.connected:
                self._ws.send(payload)
            else:
                with self._api_lock:
                    self._pending_api_calls.pop(echo, None)
                return ""
        except Exception:
            with self._api_lock:
                self._pending_api_calls.pop(echo, None)
            return ""

        ok = event.wait(10.0)
        with self._api_lock:
            self._pending_api_calls.pop(echo, None)
            resp = self._pending_api_results.pop(echo, None)

        if not ok or not resp:
            self._log(f"[文件] get_file API 超时或无响应")
            return ""

        data = resp.get("data", {}) if isinstance(resp, dict) else {}
        file_path = data.get("file", "") or data.get("url", "")
        if file_path:
            resolved = _resolve_file_path(file_path)
            self._log(f"[文件] get_file 解析到路径: {resolved}")
            return resolved
        return ""

    def _fetch_member_info(self, group_id: str, user_id: str) -> dict:
        """调用 OneBot get_group_member_info 获取群成员名片。返回 {nickname, card, role}，失败返回空字典。"""
        with self._api_lock:
            self._api_echo_counter += 1
            echo = f"mem_{self._api_echo_counter}_{int(time.monotonic() * 1000)}"
            event = Event()
            self._pending_api_calls[echo] = event

        payload = json.dumps({
            "action": "get_group_member_info",
            "params": {"group_id": int(group_id), "user_id": int(user_id)},
            "echo": echo,
        }, ensure_ascii=False)
        try:
            if self._ws and self._ws.sock and self._ws.sock.connected:
                self._ws.send(payload)
            else:
                with self._api_lock:
                    self._pending_api_calls.pop(echo, None)
                return {}
        except Exception:
            with self._api_lock:
                self._pending_api_calls.pop(echo, None)
            return {}

        ok = event.wait(5.0)
        with self._api_lock:
            self._pending_api_calls.pop(echo, None)
            resp = self._pending_api_results.pop(echo, None)

        if not ok or not resp:
            return {}

        data = resp.get("data", {}) if isinstance(resp, dict) else {}
        info = {
            "nickname": data.get("nickname", ""),
            "card": data.get("card", ""),
            "role": data.get("role", ""),
        }
        self._log(f"[名片] 群{group_id}/{user_id}: 昵称={info['nickname']}, 名片={info['card']}, 角色={info['role']}")
        return info

    def _read_received_files(self, files_info: list, text: str, session_key: str) -> str:
        """读取 QQ 收到的文件内容并注入到消息文本中。"""
        from brain.tools import _extract_full_text

        file_sections = []
        for info in files_info:
            fpath = info["path"]        # NapCatQQ 填的只是文件名，不是路径
            fname = info["name"]
            fid   = info.get("file_id", "")
            ext = Path(fname).suffix.lower()

            # 跳过图片文件（已有视觉通道处理）
            if ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"):
                continue

            # ── 定位实际文件路径 ──────────────────────────
            real_path = None
            fp = Path(fpath)

            # 1) 直接检查 fpath（可能恰好是绝对路径或 CWD 下的文件）
            if fp.is_file():
                real_path = fp
            # 2) 有 file_id → 调用 get_file API 解析真实路径
            elif fid:
                self._log(f"[文件] [{session_key}] 通过 file_id 解析路径: {fname}")
                resolved = self._resolve_file_via_api(fid, fname)
                if resolved and Path(resolved).is_file():
                    real_path = Path(resolved)

            if real_path is None:
                self._log(f"[文件] [{session_key}] 无法定位文件: {fname} (fpath={fpath})")
                file_sections.append(f"[收到文件：{fname}（文件无法定位）]")
                continue

            # ── 读取文件内容 ──────────────────────────────
            self._log(f"[文件] [{session_key}] 读取文件: {fname} -> {real_path}")
            try:
                content, err = _extract_full_text(real_path)
                if err:
                    self._log(f"[文件] [{session_key}] 读取失败: {err}")
                    file_sections.append(f"[收到文件：{fname}（读取失败: {err}）]")
                    continue

                if not content:
                    file_sections.append(f"[收到文件：{fname}（内容为空）]")
                    continue

                # 长文件截断
                if len(content) > self._MAX_FILE_CHARS:
                    content = content[:self._MAX_FILE_CHARS]
                    footer = f"\n\n…（文件较长，仅显示前 {self._MAX_FILE_CHARS} 字符）"
                else:
                    footer = ""

                file_sections.append(
                    f"[用户发来文件：{fname}]\n{content}{footer}"
                )
                self._log(f"[文件] [{session_key}] 读取成功: {fname} ({len(content)} 字符)")

                # 自动清理 NapCatQQ temp 缓存文件
                self._cleanup_temp_file(real_path, fname)

            except Exception as e:
                self._log(f"[文件] [{session_key}] 读取异常: {e}")
                file_sections.append(f"[收到文件：{fname}（读取异常: {e}）]")

        if not file_sections:
            return text

        file_sections.append(text if text.strip() else "")
        return "\n\n".join(file_sections)

    @staticmethod
    def _cleanup_temp_file(path: Path, fname: str):
        """自动删除 NapCatQQ temp 缓存目录下的文件。"""
        try:
            p = str(path)
            if "\\temp\\" in p or "/temp/" in p:
                path.unlink(missing_ok=True)
                print(f"  [文件] 已清理临时文件: {fname}")
        except Exception:
            pass

    # ── 文本分段与打字时间计算 ────────────────────────────

    def _split_response(self, text: str) -> list:
        from utils.text_segmentation import split_semantic_text
        return split_semantic_text(text)

    def _calc_typing_time(self, text: str) -> float:
        """根据文本长度计算模拟打字耗时（秒）。"""
        char_count = len(text)
        if char_count <= 0:
            return 0
        speed = random.uniform(*self._type_speed)  # 字/分钟
        return (char_count / speed) * 60.0

    # ── 工具方法 ────────────────────────────────────────────

    def _sleep_with_check(self, seconds: float):
        """休眠但可被 stop() 中断，通过每 0.5 秒检查 _running 状态。"""
        end = time.monotonic() + seconds
        while time.monotonic() < end and self._running:
            remaining = end - time.monotonic()
            time.sleep(min(0.5, max(0.05, remaining)))

async def _send_message(self, target_id: int, text: str, is_group: bool = False):
    """发送消息到QQ（私聊或群聊）"""
    # 解析表情包标签
    clean_text, emotion = parse_emotion_tag(text)
    if clean_text:
        text = clean_text
    
    # 发送文本消息
    if is_group:
        # 群聊需要 @ 发送者，这里假设调用方已经处理了 @
        await self._send_group_msg(target_id, text)
    else:
        await self._send_private_msg(target_id, text)
    
    # 概率发送图片
    import random
    if emotion and random.random() < 0.6:
        img_path = get_random_emotion_image(emotion)
        if img_path:
            # OneBot 发送图片的 CQ 码格式: [CQ:image,file=本地路径]
            image_cq = f"[CQ:image,file={img_path}]"
            if is_group:
                await self._send_group_msg(target_id, image_cq)
            else:
                await self._send_private_msg(target_id, image_cq)
