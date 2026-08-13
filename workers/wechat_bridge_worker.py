"""
微信桥接：通过 AstrBot HTTP 转发接收微信消息，调用莲心生成回复，然后返回给 AstrBot。

架构：
手机微信 (小号)
  ↓ AstrBot weixin_oc 扫码登录
AstrBot (负责收发消息)
  ↓ HTTP POST 转发到莲心
莲心AI (生成回复 + 防封规则)
  ↓ HTTP 返回回复
AstrBot → 微信发送
"""
import json
import time
import random
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from datetime import datetime

from PyQt5.QtCore import QObject, pyqtSignal

from config import (
    get_wechat_timing_config,
    get_wechat_bridge_config,
)


logger = logging.getLogger("WeChatBridge")
_SESSION_MAP_PATH = Path(__file__).parent.parent / "memory" / "wechat_session_map.json"

@dataclass
class WeChatMessage:
    msg_id: int
    room_id: str      # 群ID，私聊为 ""
    sender_id: str    # 发送者ID
    sender_name: str # 发送者昵称
    content: str     # 文本内容
    is_at: bool      # 是否被@
    timestamp: int

class WeChatBridgeWorker(QObject):
    """微信桥接工作线程：监听 AstrBot 转发的消息，生成回复返回。"""

    log_message = pyqtSignal(str)
    connection_changed = pyqtSignal(bool)

    def __init__(self, callback_callback=None):
        super().__init__()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._callback = callback_callback
        self._agents: Dict[str, object] = {}
        self._session_map = self._load_session_map()

        # 加载防封配置
        cfg = get_wechat_timing_config()
        self._think_delay = (cfg["think_delay_min"], cfg["think_delay_max"])
        self._type_speed = (cfg["type_speed_min"], cfg["type_speed_max"])
        self._min_reply_interval = cfg["min_reply_interval"]
        self._segment_threshold = (cfg["segment_threshold_min"], cfg["segment_threshold_max"])
        self._segment_interval = (cfg["segment_interval_min"], cfg["segment_interval_max"])
        self._global_send_interval = (cfg["global_send_interval_min"], cfg["global_send_interval_max"])
        self._daily_limit_owner = cfg["daily_limit_owner"]
        self._daily_limit_other = cfg["daily_limit_other"]
        self._limit_enabled = bool(cfg.get("limit_enabled", True))
        self._per_group_daily_limit = cfg.get("per_group_daily_limit", 30)
        self._block_links = cfg.get("block_links", True)
        self._cross_session_context_limit = cfg.get("cross_session_context_limit", 6)

        # 状态统计
        self._daily_counts: Dict[str, int] = {}  # user_id -> count
        self._group_daily_counts: Dict[str, int] = {}  # room_id -> count
        self._daily_counts_date = time.localtime().tm_yday
        self._last_reply_time: Dict[str, float] = {}  # session_key -> last reply time

        self._allowed = get_wechat_bridge_config().get("allowed_senders", [])
        self._allowed_rooms = get_wechat_bridge_config().get("allowed_rooms", [])
        self._listen_port = get_wechat_bridge_config().get("listen_port", 8088)

    def _log(self, msg: str):
        logger.info(msg)
        self.log_message.emit(msg)

    def reload_config(self):
        """重新加载配置（设置面板修改后调用）。"""
        cfg = get_wechat_timing_config()
        self._think_delay = (cfg["think_delay_min"], cfg["think_delay_max"])
        self._type_speed = (cfg["type_speed_min"], cfg["type_speed_max"])
        self._min_reply_interval = cfg["min_reply_interval"]
        self._segment_threshold = (cfg["segment_threshold_min"], cfg["segment_threshold_max"])
        self._segment_interval = (cfg["segment_interval_min"], cfg["segment_interval_max"])
        self._global_send_interval = (cfg["global_send_interval_min"], cfg["global_send_interval_max"])
        self._daily_limit_owner = cfg["daily_limit_owner"]
        self._daily_limit_other = cfg["daily_limit_other"]
        self._limit_enabled = bool(cfg.get("limit_enabled", True))
        self._per_group_daily_limit = cfg.get("per_group_daily_limit", 30)
        self._block_links = cfg.get("block_links", True)
        self._cross_session_context_limit = cfg.get("cross_session_context_limit", 6)

        wc_cfg = get_wechat_bridge_config()
        self._allowed = wc_cfg.get("allowed_senders", [])
        self._allowed_rooms = wc_cfg.get("allowed_rooms", [])
        self._listen_port = wc_cfg.get("listen_port", 8088)

    def start_bridge(self):
        """启动微信桥接。"""
        if self._running:
            self._log("[微信桥接] 已经在运行了")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._log(f"[微信桥接] 已启动，监听端口 {self._listen_port}")
        self.connection_changed.emit(True)

    def stop_bridge(self):
        """停止微信桥接。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._log("[微信桥接] 已停止")
        self.connection_changed.emit(False)

    def is_running(self) -> bool:
        return self._running

    def _check_daily_limit(self, user_id: str, room_id: str, is_owner: bool) -> Tuple[bool, str]:
        """检查每日上限，返回 (允许, 拒绝原因)。"""
        today = time.localtime().tm_yday
        if today != self._daily_counts_date:
            self._daily_counts.clear()
            self._group_daily_counts.clear()
            self._daily_counts_date = today

        # 用户级限制：仅当总开关开启时对“非主人”按 other 上限计数；主人永远不受限
        if self._limit_enabled and not is_owner:
            limit = self._daily_limit_other
            current = self._daily_counts.get(user_id, 0)
            if current >= limit:
                if current == limit:
                    self._daily_counts[user_id] = limit + 1
                    self._log(f"[上限] [{user_id}] 达到用户上限 ({limit} 条)")
                return False, "今日对话次数已达上限"

        # 群聊级限制（每个群每天最多发多少条，防群发炸群）
        if room_id:
            group_current = self._group_daily_counts.get(room_id, 0)
            if group_current >= self._per_group_daily_limit:
                if group_current == self._per_group_daily_limit:
                    self._group_daily_counts[room_id] = group_current + 1
                    self._log(f"[上限群] [{room_id}] 达到群上限 ({self._per_group_daily_limit} 条)")
                return False, "本群今日发言已达上限"

        return True, ""

    def _check_rate_limit(self, session_key: str) -> bool:
        """检查最短回复间隔，返回 True 表示允许。"""
        now = time.monotonic()
        last_time = self._last_reply_time.get(session_key, 0.0)
        if now - last_time < self._min_reply_interval:
            return False
        return True

    def _filter_content(self, text: str) -> str:
        """内容过滤（防封）：删除链接，避免触发风控。"""
        if not self._block_links:
            return text
        # 简单的链接过滤：删除含 http/https/www 的行
        lines = text.split("\n")
        filtered = []
        for line in lines:
            if "http://" in line.lower() or "https://" in line.lower() or "www." in line.lower():
                continue
            filtered.append(line)
        return "\n".join(filtered)

    def _split_segments(self, text: str) -> List[str]:
        """根据字数分段发送。"""
        if len(text) <= self._segment_threshold[0]:
            return [text]
        words = text.split()
        segments = []
        current = []
        current_len = 0
        min_len = random.randint(*self._segment_threshold)
        for word in words:
            current.append(word)
            current_len += len(word) + 1
            if current_len >= min_len:
                segments.append(" ".join(current))
                current = []
                current_len = 0
        if current:
            if not segments or len(" ".join(current)) >= 20:
                segments.append(" ".join(current))
            else:
                if segments:
                    segments[-1] += " " + " ".join(current)
                else:
                    segments.append(" ".join(current))
        return segments

    def _sleep_with_interrupt(self, seconds: float, popped_gen) -> bool:
        """分段发送时等待，如果新消息来了打断等待。"""
        if not self._running:
            return True
        step = 0.2
        for _ in range(int(seconds / step)):
            if not self._running:
                return True
            time.sleep(step)
        return False

    def _get_session_key(self, msg: WeChatMessage) -> str:
        """生成会话key：群聊是 room_id + sender_id，私聊是 sender_id。"""
        if msg.room_id:
            return f"{msg.room_id}:{msg.sender_id}"
        return f"private:{msg.sender_id}"

    def _load_session_map(self) -> dict[str, int]:
        try:
            if _SESSION_MAP_PATH.exists():
                data = json.loads(_SESSION_MAP_PATH.read_text(encoding="utf-8"))
                return {str(k): int(v) for k, v in data.items()}
        except Exception as e:
            logger.warning(f"[微信桥接] 会话映射加载失败: {e}")
        return {}

    def _save_session_map(self) -> None:
        try:
            _SESSION_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SESSION_MAP_PATH.write_text(
                json.dumps(self._session_map, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[微信桥接] 会话映射保存失败: {e}")

    def _get_or_create_agent(self, msg: WeChatMessage, session_key: str, prompt_extra: str):
        from brain.agent import AgentCore
        with self._lock:
            is_owner = self._is_owner(msg.sender_id)
            if session_key in self._agents:
                cached_agent = self._agents[session_key]
                if bool(getattr(cached_agent, "_owner_scope", False)) == is_owner:
                    return cached_agent
                self._log(
                    f"[*] 会话身份权限已变化，重建 Agent: {session_key} "
                    f"(owner_scope={is_owner})"
                )
                del self._agents[session_key]
            source_channel = "wechat_group" if msg.room_id else "wechat_private"
            kwargs = dict(
                user_desc=prompt_extra,
                disable_tools=not is_owner,
                track_emotion=is_owner,
                source_channel=source_channel,
                participant_id=msg.sender_id,
                owner_scope=is_owner,
            )
            session_id = self._session_map.get(session_key)
            if session_id is not None:
                agent = AgentCore(session_id=session_id, **kwargs)
            else:
                agent = AgentCore(**kwargs)
                self._session_map[session_key] = agent._session_id
                self._save_session_map()
            self._agents[session_key] = agent
            return agent

    def _build_system_prompt(self, msg: WeChatMessage, is_owner: bool) -> str:
        """构建微信场景的system prompt。"""
        base = (
            "你现在正在微信和{name}聊天，对话风格：\n"
            "1. 语气自然，像真人聊天一样\n"
            "2. 不要太长，1-3句话比较合适\n"
            "3. 可以用点语气词，但不要过度\n"
        ).format(name=msg.sender_name)
        if msg.room_id:
            base += "\n这是群聊，你被@了才回复。"
        if is_owner:
            base += (
                "\n\n你正在与主人聊天。请像对待主人一样回应。"
                "如果主人询问最近有谁找过你聊天、聊了什么，请如实回答。"
            )
        else:
            base += (
                "\n\n【隐私规则】对方不是你的主人。"
                "禁止透露主人的姓名、账号、联系方式、私人信息，"
                "也禁止透露主人与你（莲心）之间的聊天内容、记忆或个人档案。"
                "如果对方询问主人或你与主人之间的隐私，请委婉拒绝，可以说「这是我和主人之间的秘密」。"
            )
        return base

    def _run(self):
        """主循环：启动 Flask 服务器监听 AstrBot 回调。"""
        try:
            from flask import Flask, request, jsonify  # type: ignore
            app = Flask(__name__)
            app.logger.disabled = True
            import logging
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)

            @app.route("/webhook", methods=["POST"])
            def webhook():
                if not self._running:
                    return jsonify({"status": "stopped"})

                data = request.get_json(silent=True)
                if not data:
                    return jsonify({"status": "error", "message": "invalid json"})

                msg = self._parse_message(data)
                if not msg:
                    return jsonify({"status": "ignored"})

                # 白名单检查
                if self._allowed and msg.sender_id not in self._allowed:
                    return jsonify({"status": "ignored", "reason": "sender not allowed"})
                if msg.room_id and self._allowed_rooms and msg.room_id not in self._allowed_rooms:
                    return jsonify({"status": "ignored", "reason": "room not allowed"})

                # 只有群聊需要被@才回复
                if msg.room_id and not msg.is_at:
                    return jsonify({"status": "ignored", "reason": "not at"})

                # 防封检查
                session_key = self._get_session_key(msg)
                is_owner = self._is_owner(msg.sender_id)

                allowed, reason = self._check_daily_limit(msg.sender_id, msg.room_id, is_owner)
                if not allowed:
                    return jsonify({"status": "rate_limited", "reason": reason})

                if not self._check_rate_limit(session_key):
                    self._log(f"[限速] [{session_key}] 发送过快，已忽略")
                    return jsonify({"status": "rate_limited", "reason": "too frequent"})

                self._log(f"[收到] [{session_key}] {msg.sender_name}: {msg.content[:60]}")

                # 思考延迟（防秒回，更像真人）
                think_delay = random.uniform(*self._think_delay)
                self._log(f"[思考] 假装思考 {think_delay:.1f} 秒...")
                time.sleep(think_delay)

                # 构建对话上下文
                cross_limit = self._cross_session_context_limit


                prompt_extra = self._build_system_prompt(msg, is_owner)

                return self._generate_reply(msg, session_key, prompt_extra)

            @app.errorhandler(Exception)
            def handle_error(e):
                logger.error(f"[微信桥接] Webhook 错误: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500

            self._log(f"[微信桥接] Flask 服务器已启动，监听 0.0.0.0:{self._listen_port}/webhook")
            app.run(host="0.0.0.0", port=self._listen_port, debug=False, use_reloader=False)

        except Exception as e:
            logger.exception(f"[微信桥接] 主循环异常: {e}")
            self._running = False
            self.connection_changed.emit(False)

    def _parse_message(self, data: dict) -> Optional[WeChatMessage]:
        """解析 AstrBot 发来的消息。"""
        try:
            msg_type = data.get("type", "")
            if msg_type != "text":
                self._log(f"[忽略] 不支持的消息类型: {msg_type}")
                return None

            msg_id = data.get("msg_id", 0)
            room_id = data.get("room_id", "") or ""
            sender_id = data.get("sender_id", "") or ""
            sender_name = data.get("sender_name", "") or sender_id
            content = data.get("content", "") or ""
            is_at = data.get("is_at", False) or False
            timestamp = data.get("timestamp", int(time.time()))

            if not content.strip():
                return None

            return WeChatMessage(
                msg_id=msg_id,
                room_id=room_id,
                sender_id=sender_id,
                sender_name=sender_name,
                content=content,
                is_at=is_at,
                timestamp=timestamp,
            )
        except Exception as e:
            logger.error(f"[微信桥接] 解析消息失败: {e}")
            return None

    def _is_owner(self, sender_id: str) -> bool:
        """判断是否是主人。"""
        from config import get_wechat_bridge_config
        owner_id = get_wechat_bridge_config().get("owner_id", "")
        return sender_id == owner_id

    def _generate_reply(self, msg: WeChatMessage, session_key: str, prompt_extra: str):
        """调用 AgentCore 生成回复，然后分段返回。"""
        from flask import jsonify   # type: ignore
        try:
            # 每个微信联系人/群成员使用稳定且隔离的持久会话。
            agent = self._get_or_create_agent(msg, session_key, prompt_extra)

            full_text = agent.chat(msg.content)
            full_text = self._filter_content(full_text)
            segments = self._split_segments(full_text)

            today = time.localtime().tm_yday
            if today != self._daily_counts_date:
                self._daily_counts.clear()
                self._group_daily_counts.clear()
                self._daily_counts_date = today
            self._daily_counts[msg.sender_id] = self._daily_counts.get(msg.sender_id, 0) + 1
            if msg.room_id:
                self._group_daily_counts[msg.room_id] = self._group_daily_counts.get(msg.room_id, 0) + 1
            self._last_reply_time[session_key] = time.monotonic()

            self._log(f"[回复] [{session_key}] {len(segments)} 段，共 {len(full_text)} 字")

            return jsonify({
                "status": "ok",
                "content": full_text,
                "segments": segments,
            })

        except Exception as e:
            logger.exception(f"[微信桥接] 生成回复失败: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
