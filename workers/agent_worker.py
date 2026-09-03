"""Agent 工作线程。

在后台线程中同步调用 AgentCore.chat()，
通过 Qt 信号将结果传递回主线程。

支持「中途插话」：GUI 线程通过 send_interrupt() 投递消息，
Worker 在工具调用间隙处理并报告进度。
"""
import queue
import threading
from PyQt5.QtCore import QThread, pyqtSignal
from brain.agent import AgentCore

_INTERRUPT_SYSTEM = """【用户中途插话】
用户刚刚说："{msg}"

请遵守以下规则回复：
1. 用1~2句话简要回复当前进度或回答用户问题（不超过50字）
2. 不要调用任何工具
3. 不要输出情绪标签
4. 如果用户说的是"取消""别做了""停下""不要了"等取消指令，
   回复"好的，已取消~"并在结尾加 [终止]
5. 如果用户说的是"改成""换个""用...代替"等修正指令，
   回复"好的，已修正~"并在结尾加 [修正]
6. 其他情况（如"进度如何""到哪了"）简要报告当前进展，
   并在结尾加 [继续]"""


class AgentWorker(QThread):
    response_ready = pyqtSignal(str)
    progress_update = pyqtSignal(str)   # 插话进度回复（仅追加文字，不改动画状态）
    activity = pyqtSignal(str)           # 内部阶段心跳，只用于请求看门狗续期
    tool_round_start = pyqtSignal(int)                          # round_num
    tool_called    = pyqtSignal(str, str, int)                  # name, args_json, round_num
    tool_result    = pyqtSignal(str, str, bool, int, float)     # name, preview, is_error, round_num, elapsed_ms
    tool_enable_requested = pyqtSignal(str, str, str, object)   # key, display_name, reason, request
    browser_confirmation_requested = pyqtSignal(str, str, str, object)  # tool, risk, reason, request
    observation_image = pyqtSignal(str, str)  # 观察图片 (path, desc) 用于气泡显示
    checklist_proposed = pyqtSignal(list)     # 后台提取的待办列表 [{"title":...,"due_time":...,"priority":...}]
    error_occurred = pyqtSignal(str)

    def __init__(self, agent: AgentCore, message: str, parent=None,
                 forced_tool: str = None, preferred_tool: str = None,
                 disable_tools: bool = False):
        super().__init__(parent)
        self.agent         = agent
        self.message       = message
        self.forced_tool   = forced_tool
        self.preferred_tool = preferred_tool
        self.disable_tools = disable_tools
        self.interrupt_queue: queue.Queue = queue.Queue()

    def send_interrupt(self, msg: str) -> bool:
        """GUI 线程调用：向工作线程发送一条插话消息。最多缓存 5 条。"""
        msg = msg.strip()
        if msg and self.interrupt_queue.qsize() < 5:
            self.interrupt_queue.put(msg)
            return True
        return False

    def _process_interrupt(self, interrupt_msg: str) -> str:
        """Worker 线程内调用：用 LLM 处理一条插话，返回简短回复。"""
        try:
            import litellm
            interrupt_messages = [
                {"role": "system",
                 "content": _INTERRUPT_SYSTEM.format(msg=interrupt_msg)},
            ]
            response = litellm.completion(
                model=self.agent._model,
                max_tokens=120,
                messages=interrupt_messages,
                api_key=self.agent._api_key,
                api_base=self.agent._api_base,
                timeout=12,
            )
            return response.choices[0].message.content or "（收到，正在继续…）"
        except Exception:
            return "（收到，正在处理中…）"

    def run(self):
        import json as _json
        try:
            current_round = [0]  # mutable closure for round tracking

            def on_round_start(round_num):
                current_round[0] = round_num
                self.activity.emit(f"round:{round_num}")
                self.tool_round_start.emit(round_num)

            # 设置待办提取回调
            def on_checklist_extracted(result):
                items = []
                for item in result.get("add", []):
                    items.append({"title": item, "due_time": None, "priority": "medium"})
                if items:
                    self.checklist_proposed.emit(items)
            self.agent._checklist_callback = on_checklist_extracted

            def on_tool_call(name, args):
                self.activity.emit(f"tool_start:{name}")
                safe_args = args or {}
                if str(name or "").startswith("browser_"):
                    try:
                        from brain.browser_security import redact_browser_args
                        safe_args = redact_browser_args(name, safe_args)
                    except Exception:
                        safe_args = {"redacted": True}
                args_json = _json.dumps(safe_args, ensure_ascii=False) if safe_args else "{}"
                self.tool_called.emit(name, args_json, current_round[0])

            def on_tool_result(name, result, is_error=False, elapsed_ms=0):
                self.activity.emit(f"tool_end:{name}")
                preview = (result or "")[:80].replace("\n", " ")
                preview += ("…" if len(result or "") > 80 else "")
                self.tool_result.emit(name, preview, is_error, current_round[0], elapsed_ms)
                # 截屏/摄像头 → 发射图片气泡信号
                if name in ("capture_desktop", "capture_from_camera"):
                    from brain.tools import get_observation_image
                    obs = get_observation_image()
                    if obs["path"]:
                        self.observation_image.emit(obs["path"], obs["desc"])

            def on_tool_enable_request(tool_key, display_name, reason):
                """工作线程等待主线程完成 Yes/No，最多等待两分钟。"""
                request = type("ToolEnableRequest", (), {})()
                request.event = threading.Event()
                request.approved = False
                self.tool_enable_requested.emit(tool_key, display_name, reason, request)
                if not request.event.wait(timeout=120):
                    return False
                return bool(request.approved)

            def on_browser_confirmation(tool_name, risk_level, reason, args):
                """高风险浏览器动作在 GUI 线程中确认，工作线程等待结果。"""
                request = type("BrowserConfirmationRequest", (), {})()
                request.event = threading.Event()
                request.approved = False
                request.remember = False
                request.args = args or {}
                self.browser_confirmation_requested.emit(
                    str(tool_name), str(risk_level), str(reason), request
                )
                if not request.event.wait(timeout=120):
                    return {"approved": False, "remember": False}
                return {
                    "approved": bool(request.approved),
                    "remember": bool(request.remember),
                }

            response = self.agent.chat(
                self.message,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                on_tool_enable_request=on_tool_enable_request,
                on_browser_confirmation=on_browser_confirmation,
                on_round_start=on_round_start,
                forced_tool=self.forced_tool,
                preferred_tool=self.preferred_tool,
                disable_tools=self.disable_tools,
                interrupt_queue=self.interrupt_queue,
                on_interrupt=self._process_interrupt,
                on_progress=lambda text: self.progress_update.emit(text),
                on_activity=lambda stage: self.activity.emit(str(stage)),
            )
            self.response_ready.emit(response)
        except Exception as e:
            import logging
            logging.getLogger("AgentWorker").exception(
                "AgentWorker failed: message=%r forced_tool=%s preferred_tool=%s",
                self.message[:300], self.forced_tool, self.preferred_tool,
            )
            self.error_occurred.emit(str(e))
