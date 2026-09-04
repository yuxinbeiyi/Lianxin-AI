"""请求模式、强信号路由与渐进式能力发现。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from brain.request_context import parse_request_context


class RequestMode(str, Enum):
    CHAT_LIGHT = "CHAT_LIGHT"
    CHAT_MEMORY = "CHAT_MEMORY"
    TASK_DIRECT = "TASK_DIRECT"
    TASK_DISCOVERY = "TASK_DISCOVERY"
    TASK_CONTINUATION = "TASK_CONTINUATION"


CAPABILITY_TO_TOOLS: dict[str, set[str]] = {
    "self_knowledge": {"query_self_knowledge", "query_self_status", "inspect_self_capability"},
    "memory_read": {"search_graph_memory", "search_conversation_history", "search_cross_session",
                    "discover_connections", "explain_memory_quality"},
    "memory_write": {"save_memory", "update_memory", "delete_memory", "review_memory_conflict",
                     "update_current_state"},
    "contacts": {"query_recent_contacts", "query_qq_friend_list"},
    "time": {"get_current_time"},
    "web_search": {"web_search"},
    "web_fetch": {"fetch_webpage", "configure_network_tools"},
    "github": {
        "github_search_repositories", "github_get_readme",
        "github_get_file", "github_list_directory", "github_list_commits",
    },
    # 复合网页任务不是新的网络工具，而是把“搜索证据”和“后续交接”
    # 绑定成一条有顺序的工作流。执行层会先强制 web_search，再根据
    # 用户意图交给 fetch_webpage 或浏览器工具。
    "web_research": {
        "web_search", "fetch_webpage",
        "browser_navigate", "browser_snapshot", "browser_click",
        "browser_fill", "browser_press", "browser_scroll",
        "browser_wait", "browser_tabs", "browser_screenshot",
        "browser_connect", "browser_disconnect",
    },
    "browser": {
        "browser_navigate", "browser_snapshot", "browser_click",
        "browser_fill", "browser_press", "browser_scroll",
        "browser_wait", "browser_tabs", "browser_screenshot",
        "browser_connect", "browser_disconnect",
    },
    "file_read": {"read_file", "read_file_chunk", "read_file_lines", "search_files_everything",
                  "list_directory", "get_file_info_everything", "glob_files", "grep_file",
                  "diff_files"},
    "file_write": {"write_file", "edit_file"},
    "code": {"search_code", "code_structure", "code_goto_def", "code_find_refs", "code_diagnostics",
             "run_python_code", "run_command", "run_shell", "git_status"},
    "office": {"read_excel", "write_excel", "copy_excel_content", "write_docx", "format_document"},
    "image": {"ocr_image", "ocr_batch", "describe_image", "capture_from_camera", "capture_desktop",
              "generate_image", "generate_video", "look_at_camera"},
    "system": {"open_app", "get_clipboard", "send_file_to_qq", "plan_tasks", "delegate_task",
               "track_tasks", "toggle_proactive_chat", "get_balance", "list_skills",
               "activate_skill", "deactivate_skill", "query_capabilities"},
    "todo": {"add_todo", "list_todos", "complete_todo", "set_reminder"},
    "weather": {"get_weather", "set_user_city"},
    "bilibili": {"bilibili_search", "bilibili_add_tag", "bilibili_list_tags"},
    "time_capsule": {"read_diary", "write_diary"},
    "embodied": {"navigate_to_marker", "move_snake", "cancel_embodied_task", "get_embodied_status"},
    "hardware": {
        "shoulder_photo", "shoulder_pan", "shoulder_tilt", "shoulder_center",
        "shoulder_status", "shoulder_temp", "shoulder_servo", "shoulder_observe",
        "start_shoulder_explore", "shoulder_human_track", "stop_human_tracking",
        "shoulder_face_track", "stop_face_tracking",
        "start_observation_mode", "stop_observation_mode",
    },
}

# 用户点名工具名 → 能力组。机制型工具（query_capabilities 等）不属于任何
# 能力组，单独映射到 system，保证用户直接点名时路由层能接住。
_ORPHAN_TOOL_CAPABILITIES: dict[str, str] = {
    "get_balance": "system",
    "list_skills": "system",
    "activate_skill": "system",
    "deactivate_skill": "system",
    "query_capabilities": "system",
}

_TOOL_NAME_TO_CAPABILITY: dict[str, str] = {}
for _capability, _tool_names in CAPABILITY_TO_TOOLS.items():
    for _tool_name in _tool_names:
        _TOOL_NAME_TO_CAPABILITY.setdefault(_tool_name, _capability)
_TOOL_NAME_TO_CAPABILITY.update(_ORPHAN_TOOL_CAPABILITIES)

_TOOL_NAME_PATTERN = re.compile(
    r"\b("
    + "|".join(re.escape(name) for name in sorted(_TOOL_NAME_TO_CAPABILITY, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


CAPABILITY_DESCRIPTIONS = {
    "memory_read": "读取已确认的长期记忆或历史会话",
    "memory_write": "按用户明确要求写入或修改长期记忆",
    "contacts": "回顾近期与莲心互动过的联系人或查询 QQ 好友列表（仅主人可见）",
    "time": "查询精确日期、时间、农历或节日",
    "web_search": "搜索实时网络资料",
    "web_fetch": "读取指定网页正文",
    "github": "使用 GitHub 专用接口搜索仓库、读取 README/源码和查看提交记录",
    "web_research": "先搜索并核验来源，再读取网页或交给浏览器继续操作",
    "browser": "使用浏览器打开网页并进行点击、输入、滚动或截图",
    "file_read": "查找并读取本地文件",
    "file_write": "创建或修改本地文件",
    "code": "分析、运行、修改或测试代码",
    "office": "处理 Word、Excel 等办公文档",
    "image": "识别、理解或生成图像媒体",
    "system": "操作应用、剪贴板或执行复杂任务",
    "todo": "管理待办清单",
    "weather": "查询实时天气",
    "bilibili": "搜索或管理 B 站内容",
    "time_capsule": "读取或写入时间胶囊日记",
    "embodied": "在莲心虚拟世界中导航、移动或查询贪吃蛇执行状态",
}

_URL_RE = re.compile(r"https?://\S+", re.I)
_GITHUB_REPO_RE = re.compile(r"https?://(?:www\.)?github\.com/[^/\s]+/[^/\s#?]+", re.I)
_GITHUB_SLUG_RE = re.compile(r"(?<![\w-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?![\w-])")
_GITHUB_RECENT_RE = re.compile(
    r"(?:最近|最新).{0,10}(?:更新|提交|commit)|(?:更新|提交).{0,10}(?:仓库|项目)|"
    r"(?:recent|latest).{0,12}(?:commits?|updates?)",
    re.I,
)
_GITHUB_FILE_RE = re.compile(
    r"(?:读取|查看|打开|分析|解释|读).{0,30}(?:文件|源码|代码)|"
    r"(?:read|open|inspect|analy[sz]e).{0,30}(?:file|source|code)|"
    r"\b(?:requirements(?:-[\w]+)?\.txt|pyproject\.toml|package\.json|[\w./-]+\.(?:py|js|ts|go|java|json|yaml|yml|md))\b",
    re.I,
)
_GITHUB_NEGATED_FILE_RE = re.compile(
    r"(?:不需要|不要|无需|不必).{0,24}(?:源码|代码|文件|source|code|file)",
    re.I,
)
_GITHUB_DIRECTORY_RE = re.compile(
    r"(?:列出|查看|浏览).{0,80}(?:目录|文件树|项目结构)|"
    r"(?:list|show|browse).{0,80}(?:directory|tree|structure)",
    re.I,
)
_GITHUB_SEARCH_RE = re.compile(
    r"(?:搜索|搜|找|比较|推荐).{0,30}(?:github|仓库|项目)|"
    r"(?:github|仓库|repo|repository).{0,30}(?:搜索|搜|找|比较|推荐)|"
    r"(?:github|repositories?|repos?).{0,30}(?:search|compare|find|recommend)|"
    r"(?:按|根据).{0,10}(?:star|stars|星标)",
    re.I,
)
_GITHUB_README_RE = re.compile(r"(?:readme|README|项目说明|说明文档)", re.I)
_WINDOWS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\n\r\t]+")
_FILE_EXT_RE = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|md|txt|csv|json|py|js|ts|html|log|db|sqlite)\b", re.I)
_CONTINUATION_RE = re.compile(r"^(?:那就|就按|继续|接着|再试|换一个|第二个|用它|开始吧|执行吧|试试)")
_NEGATED_SEARCH_RE = re.compile(r"(?:不想|不用|不要|别|停止|取消).{0,4}(?:搜|查|联网|上网)")
_SOCIAL_RE = re.compile(
    r"^(?:早安|早上好|上午好|中午好|下午好|晚上好|晚安|你好|嗨|哈喽|莲心|在吗|"
    r"谢谢|辛苦了|抱抱|想你了|我回来了|你怎么样|今天心情怎么样)[呀啊哦呢嘛吗~～！!。,.， ]*$"
)

# 主人询问近期互动联系人或 QQ 好友列表（社交回顾类问题）。这类问题必须
# 进入 TASK 路由并注入联系人工具，否则模型手中无工具只能凭记忆/幻觉回答。
_CONTACTS_INQUIRY_RE = re.compile(
    r"(?:"
    r"最近.{0,12}(?:谁|什么人|有人|哪些人|几个人).{0,12}(?:找过?|聊过?|说过话|找过你|跟你聊)"
    r"|(?:谁|哪些人|什么人).{0,10}(?:找你|跟你|和你|找过你).{0,10}(?:聊天|说话|聊过)"
    r"|(?:我|你|莲心).{0,6}(?:的)?(?:QQ|qq)?好友(?:列表|名单|都有谁|有哪些|有几个|多少人|多少位)?[？?。，,、.\s]*$"
    r")",
)

# 用户直接点名工具或其服务提供方时，不应再退回到模糊能力发现。
_DIRECT_WEB_SEARCH_RE = re.compile(
    r"(?:web[_\s-]?search|tavily|使用.{0,8}(?:搜索工具|联网工具)|调用.{0,8}(?:搜索工具|联网工具))",
    re.IGNORECASE,
)
_DIRECT_WEB_FETCH_RE = re.compile(
    r"(?:fetch[_\s-]?webpage|firecrawl|使用.{0,8}(?:抓取工具|网页工具)|调用.{0,8}(?:抓取工具|网页工具))",
    re.IGNORECASE,
)

_WEB_REREAD_RE = re.compile(
    r"(?:(?:重新(?:读取|阅读|查看|抓取|核对|核实)|再次(?:读取|阅读|查看|抓取|核对|核实)|"
    r"再(?:读|看|抓取|核对)一?(?:遍|次)?|重读|重看).{0,36}"
    r"(?:网页|页面|原文|文章|报道|新闻|链接|网址|刚才|之前|上次)|"
    r"(?:网页|页面|原文|文章|报道|新闻|链接|网址|刚才|之前|上次).{0,20}"
    r"(?:重新(?:读取|阅读|查看|抓取|核对|核实)|再次(?:读取|阅读|查看|抓取|核对|核实)|"
    r"再(?:读|看|抓取|核对)一?(?:遍|次)?|重读|重看))",
    re.IGNORECASE,
)

# 明确要求浏览器交互时，必须让浏览器技能在首轮进入工具定义。
# 纯“搜索/读取网页”仍走 web_search / fetch_webpage，不强行启动可见浏览器。
_BROWSER_INTERACTION_RE = re.compile(
    r"(?:"
    r"用浏览器|浏览器(?:打开|访问|进入|操作|点击|输入|填写|登录|截图|滚动)|"
    r"打开网页|网页上(?:点击|输入|填写|登录|搜索|滚动)|"
    r"页面上(?:点击|输入|填写|登录|选择|滚动)|"
    r"网页(?:点击|填表|自动登录|自动化)|"
    r"浏览器自动化|接管(?:我)?(?:已经|已)?打开的浏览器|接管浏览器|CDP"
    r")",
    re.IGNORECASE,
)
_BROWSER_CDP_RE = re.compile(
    r"接管(?:我)?(?:已经|已)?打开的浏览器|接管浏览器|连接(?:本机|本地)?(?:浏览器|CDP)|CDP",
    re.IGNORECASE,
)

# “搜索并打开/操作”与单独搜索、单独读取网页是不同的任务：前者需要
# 保留搜索证据，再把选中的 URL 交给下一层执行。这里只识别明确的
# 顺序关系，避免把普通“打开网页并搜索”误判成复合研究任务。
_WEB_RESEARCH_RE = re.compile(
    r"(?:"
    r"(?:先|先去|先帮我)?(?:搜|搜索|查|查找|检索|联网搜索|上网搜索)"
    r".{0,48}(?:再|然后|之后|随后|并(?:且)?).{0,18}"
    r"(?:打开|访问|进入|浏览器|接管|点击|操控|操作|查看正文|读取正文|阅读|总结)"
    r"|"
    r"(?:搜|搜索|查找|检索).{0,48}(?:并|然后|再).{0,12}"
    r"(?:打开|访问|进入|浏览器|接管|点击|操控|操作|查看|读取|阅读)"
    r")",
    re.IGNORECASE,
)
_WEB_RESEARCH_READ_RE = re.compile(
    r"(?:查看正文|读取正文|阅读原文|打开后(?:看看|阅读|总结)|搜索后(?:看看|阅读|总结)|"
    r"搜索.*(?:并|然后|再).*(?:查看|读取|阅读|总结))",
    re.IGNORECASE,
)

# 这些能力对应的操作没有歧义：用户一旦明确提出，就必须先取得真实执行记录。
_PRIMARY_EXECUTION_TOOLS = {
    # 明确的浏览器交互优先于“URL 读取”，否则“用浏览器打开 URL”会被
    # 同时命中的 web_fetch 能力抢先固定为 fetch_webpage。
    "browser": "browser_navigate",
    "web_fetch": "fetch_webpage",
    "web_search": "web_search",
    "web_research": "web_search",
    "weather": "get_weather",
    "time": "get_current_time",
    "embodied": "navigate_to_marker",
}


@dataclass(frozen=True)
class RequestRoute:
    mode: RequestMode
    capabilities: frozenset[str] = frozenset()
    reason: str = ""

    @property
    def tool_names(self) -> set[str]:
        names: set[str] = set()
        for capability in self.capabilities:
            names.update(CAPABILITY_TO_TOOLS.get(capability, set()))
        return names

    @property
    def is_light(self) -> bool:
        return self.mode == RequestMode.CHAT_LIGHT

    @property
    def uses_memory_context(self) -> bool:
        return self.mode == RequestMode.CHAT_MEMORY or "memory_read" in self.capabilities


@dataclass
class ToolSessionState:
    active: bool = False
    capabilities: set[str] = field(default_factory=set)
    opened_tool_names: set[str] = field(default_factory=set)
    denied_enablements: set[str] = field(default_factory=set)
    last_intent: str = ""

    def begin(self, route: RequestRoute, message: str) -> None:
        if route.mode != RequestMode.TASK_CONTINUATION:
            self.capabilities.clear()
            self.opened_tool_names.clear()
            self.denied_enablements.clear()
        self.active = route.mode in {
            RequestMode.TASK_DIRECT, RequestMode.TASK_DISCOVERY, RequestMode.TASK_CONTINUATION,
        }
        self.capabilities.update(route.capabilities)
        self.opened_tool_names.update(route.tool_names)
        self.last_intent = str(message or "")[:500]


def required_execution_tool(route: RequestRoute, available_tool_names: Iterable[str],
                            request_text: str = "") -> str | None:
    """Return the deterministic first tool for an explicit external task.

    ``None`` deliberately means that the model may choose among several valid
    tools.  A returned name is safe to force through the provider tool API.
    """
    if route.mode not in {RequestMode.TASK_DIRECT, RequestMode.TASK_CONTINUATION}:
        return None
    available = set(available_tool_names)
    request_text = parse_request_context(request_text).routing_text

    if "self_knowledge" in route.capabilities and is_self_knowledge_request(request_text):
        if "inspect_self_capability" in available:
            return "inspect_self_capability"

    if "memory_read" in route.capabilities and is_verifiable_recall_request(request_text):
        if "search_conversation_history" in available:
            return "search_conversation_history"

    if "github" in route.capabilities:
        github_tool = _github_primary_tool(request_text)
        if github_tool in available:
            return github_tool

    # 复合任务的第一步永远是搜索。后续步骤由 WebResearchTaskState
    # 根据搜索结果和目标模式切换，不在这里提前固定浏览器动作。
    if "web_research" in route.capabilities and "web_search" in available:
        return "web_search"

    # 浏览器任务的第一步取决于是否带有 URL：
    # - URL + browser：先导航；
    # - 仅 browser：先观察当前页面，不能凭空要求 browser_navigate 的 url 参数。
    if "browser" in route.capabilities:
        if _BROWSER_CDP_RE.search(str(request_text or "")):
            if "browser_connect" in available:
                return "browser_connect"
        browser_first = (
            "browser_navigate"
            if "web_fetch" in route.capabilities
            else "browser_snapshot"
        )
        if browser_first in available:
            return browser_first

    for capability, tool_name in _PRIMARY_EXECUTION_TOOLS.items():
        if capability == "browser":
            continue
        if capability in route.capabilities and tool_name in available:
            return tool_name
    return None


def _github_primary_tool(text: str) -> str:
    """Choose the deterministic first GitHub operation for an explicit task."""
    if _GITHUB_DIRECTORY_RE.search(text):
        return "github_list_directory"
    if _GITHUB_SEARCH_RE.search(text) and not _GITHUB_FILE_RE.search(text):
        return "github_search_repositories"
    if _GITHUB_RECENT_RE.search(text):
        return "github_list_commits"
    if _GITHUB_FILE_RE.search(text):
        return "github_get_file"
    if _GITHUB_README_RE.search(text):
        return "github_get_readme"
    return "github_get_readme"


def _looks_like_action(text: str) -> bool:
    return bool(re.search(
        r"(?:帮我|请你|能不能|可以帮|替我|给我|需要你|想让你|麻烦你|怎么查|如何找|"
        r"找一下|看看有没有|有没有人|外面怎么|处理一下|做一个|弄一下)", text
    ))


def _image_caption_requests_action(text: str) -> bool:
    """判断图片消息的配文是否明确要求了需要工具的动作。

    只有显式动作（帮我/请你…）或点名网络/浏览器能力/带 URL 时才保留
    任务路由；单纯描述性配文（“这就是我的电路板”“看看这张图”）一律
    走纯文本图片回应。
    """
    return bool(
        _looks_like_action(text)
        or _DIRECT_WEB_SEARCH_RE.search(text)
        or _DIRECT_WEB_FETCH_RE.search(text)
        or _BROWSER_INTERACTION_RE.search(text)
        or _WEB_RESEARCH_RE.search(text)
        or _URL_RE.search(text)
    )


def _recent_text(messages: Iterable[dict]) -> str:
    """Return a bounded transcript used only for follow-up intent detection."""
    return "\n".join(
        str(item.get("content", ""))[:600]
        for item in list(messages or ())[-6:]
        if isinstance(item, dict)
    )


def _is_city_recall_for_weather(text: str, recent_messages: Iterable[dict]) -> bool:
    """Recognize a short memory follow-up that supplies a city for a weather ask."""
    if not re.search(r"(?:城市|哪[个里]|所在地|住在|地方)", text):
        return False
    return bool(re.search(r"(?:天气|气温|温度|下雨|降水|预报)", _recent_text(recent_messages)))


# 强任务词：消息里出现这些词时，“记得/回忆”更可能是祈使用法（如“记得附上链接”）
# 或混合任务，不应劫持整条路由进入纯回忆模式。
_STRONG_TASK_HINT_RE = re.compile(
    r"(?:搜索|搜一?下|帮我搜|新闻|资讯|链接|网址|附上|天气|气温|截屏|打开|图片|文件"
    r"|PPT|excel|表格|查一?下|帮我查|找一下)"
)

_SELF_KNOWLEDGE_RE = re.compile(
    r"(?:你有(?:什么|哪些)(?:功能|能力|工具|技能)|你会什么|介绍一下你自己|"
    r"你能做什么|莲心(?:的)?(?:功能|能力|系统|架构|状态)|"
    r"(?:涟漪情感|棱镜记忆|音乐空间|主动聊天|能力中枢|人格枢控|星图|"
    r"时间胶囊|日记|树洞|纸条|备忘本|自习室|视觉理解|语音转录|语音合成|"
    r"视频语音通话|上网冲浪|QQ聊天|肩载设备|人脸追踪|具身智能).{0,24}"
    r"(?:做什么|是什么|怎么工作|如何工作|怎么用|如何用|架构|状态|启用|激活|正常|最近|最后一次|能不能|是否))",
    re.IGNORECASE,
)


def is_self_knowledge_request(text: str) -> bool:
    """判断用户是否在询问莲心自身能力、架构或运行状态。"""
    value = str(text or "").strip()
    if not value:
        return False
    return bool(_SELF_KNOWLEDGE_RE.search(value)) or bool(
        re.search(r"(?:我的|你最近).{0,8}(?:自习|保存记忆|活动|日记|备忘).{0,8}(?:多久|什么时候|什么|哪条|哪篇)", value)
    )

# 这类问题要求从持久化记录中确认事实，不能只依赖当前上下文或模型补全。
_RECALL_HISTORY_RE = re.compile(
    r"(?:聊天记录|历史记录|原聊天|原记录|对话记录|日志时间|时间戳|原话|"
    r"当时|那次|那件事|那一回|那段对话|具体(?:的)?时间|准确时间|"
    r"哪天|几号|几点|什么时候|说了什么|发生(?:在|的)?时间|"
    r"不是今天|不是昨天)",
    re.IGNORECASE,
)


def is_verifiable_recall_request(text: str) -> bool:
    """判断是否必须用真实聊天记录核验历史事件。"""
    value = str(text or "").strip()
    if not value or not _RECALL_HISTORY_RE.search(value):
        return False
    # “现在几点”“今天是几号”属于实时钟表查询；只有带明确历史语境时
    # 才走聊天记录，避免“几号/时间”这个词把实时问题升级成历史检索。
    if re.search(
        r"(?:现在|当前|此刻|今天|明天|后天|昨天).{0,8}(?:几点|时间|日期|几号|星期)",
        value,
    ) and not re.search(
        r"(?:聊天记录|历史记录|当时|那次|那件事|原话|说了什么|不是今天|不是昨天)",
        value,
    ):
        return False
    return True


def is_explicit_web_reread_request(text: str) -> bool:
    """判断用户是否明确要求重新取得网页原文。"""
    value = parse_request_context(text).routing_text
    return bool(_WEB_REREAD_RE.search(value))


def classify_request(message: str, *, recent_messages: Iterable[dict] = (),
                     forced_tool: str | None = None,
                     session_state: ToolSessionState | None = None) -> RequestRoute:
    request_context = parse_request_context(message)
    text = request_context.routing_text
    lowered = text.lower()
    if forced_tool:
        capabilities = {
            capability for capability, names in CAPABILITY_TO_TOOLS.items()
            if forced_tool in names
        }
        return RequestRoute(
            RequestMode.TASK_DIRECT, frozenset(capabilities), "用户从界面手动指定工具"
        )

    if session_state and session_state.active and _CONTINUATION_RE.search(text):
        return RequestRoute(
            RequestMode.TASK_CONTINUATION,
            frozenset(session_state.capabilities),
            "承接上一轮尚未结束的工具任务",
        )

    if is_verifiable_recall_request(text):
        return RequestRoute(
            RequestMode.TASK_DIRECT,
            frozenset({"memory_read"}),
            "要求核验历史聊天记录中的具体事件、时间或原话",
        )

    if is_explicit_web_reread_request(text):
        return RequestRoute(
            RequestMode.TASK_DIRECT,
            frozenset({"web_fetch"}),
            "用户明确要求重新读取或核对网页原文",
        )

    if is_self_knowledge_request(text):
        return RequestRoute(
            RequestMode.TASK_DIRECT,
            frozenset({"self_knowledge"}),
            "用户询问莲心自身的功能、架构或运行状态",
        )

    if any(token in lowered for token in ("时间胶囊", "日记", "共同书页")) and any(
        token in lowered for token in ("记得", "昨天", "前天", "查看", "读", "写", "日记")
    ):
        caps = {"time_capsule"}
        if any(token in lowered for token in ("记得", "回忆", "之前")):
            caps.add("memory_read")
        return RequestRoute(RequestMode.CHAT_MEMORY, frozenset(caps), "时间胶囊或日记回忆")

    if re.search(r"(?:记得|还记得|之前说过|以前聊过|回忆|昨天.*说|前天.*说)", text):
        if _is_city_recall_for_weather(text, recent_messages):
            return RequestRoute(
                RequestMode.TASK_DIRECT,
                frozenset({"memory_read", "weather"}),
                "回忆地点并延续近期天气查询",
            )
        # “记得附上链接”这类祈使用法或混合任务不劫持路由：落入下方能力扫描，
        # 让搜索等真实需求拿到对应工具（扫描中会补 memory_read）。
        # 仅纯回忆（无强任务词）才进入纯回忆路由。
        if not _STRONG_TASK_HINT_RE.search(text):
            return RequestRoute(RequestMode.CHAT_MEMORY, frozenset({"memory_read"}), "明确回忆历史")
    if re.search(r"(?:请|帮我|你要)?记住|保存到长期记忆|删掉.{0,8}记忆|修改.{0,8}记忆", text):
        return RequestRoute(RequestMode.TASK_DIRECT, frozenset({"memory_write"}), "明确修改长期记忆")

    if request_context.is_quote_ack:
        return RequestRoute(
            RequestMode.CHAT_LIGHT,
            frozenset(),
            "引用回复确认型消息，不启动工具",
        )

    # 识图消息的默认意图是“回应图片”。视觉描述已作为回答素材注入消息
    # （routing_text 中已剥离），配文没有明确操作意图时不进入任务模式，
    # 避免描述/配文里的设备词汇诱发无关工具调用（如发电路板照片却去查
    # shoulder_status）。配文明确要求动作时仍照常进入下方能力扫描。
    if request_context.has_image_blocks and not _image_caption_requests_action(text):
        return RequestRoute(
            RequestMode.CHAT_LIGHT,
            frozenset(),
            "图片回应：基于已注入的视觉描述作答，不启动工具",
        )

    capabilities: set[str] = set()
    reasons: list[str] = []
    composite_web_task = bool(_WEB_RESEARCH_RE.search(text))
    # 已经给出明确 URL 的“读取正文”请求仍走原有 fetch_webpage 直读路径，
    # 不为了“搜索并读取”几个字额外增加一次搜索，保持兼容与低延迟。
    if composite_web_task and _URL_RE.search(text) and _WEB_RESEARCH_READ_RE.search(text):
        composite_web_task = False
    if composite_web_task:
        capabilities.add("web_research")
        reasons.append("用户要求先搜索再读取或交给浏览器继续操作")
    if not _NEGATED_SEARCH_RE.search(text) and _DIRECT_WEB_SEARCH_RE.search(text):
        capabilities.add("web_search")
        reasons.append("用户明确点名联网搜索工具")
    if _DIRECT_WEB_FETCH_RE.search(text):
        capabilities.add("web_fetch")
        reasons.append("用户明确点名网页读取工具")
    if _BROWSER_INTERACTION_RE.search(text):
        capabilities.add("browser")
        reasons.append("用户明确要求浏览器交互")
    github_url = bool(_GITHUB_REPO_RE.search(text))
    github_reference = github_url or bool(
        re.search(r"(?:github|仓库|repo|repository)", text, re.I)
        or _GITHUB_SLUG_RE.search(text)
    )
    github_specific_task = github_reference and bool(
        _GITHUB_RECENT_RE.search(text)
        or _GITHUB_DIRECTORY_RE.search(text)
        or (_GITHUB_FILE_RE.search(text) and not _GITHUB_NEGATED_FILE_RE.search(text))
        or _GITHUB_SEARCH_RE.search(text)
        or _GITHUB_README_RE.search(text)
    )
    if github_specific_task:
        capabilities.add("github")
        reasons.append("明确要求 GitHub 仓库数据或文件内容")
        capabilities.discard("web_fetch")
    elif github_reference:
        capabilities.add("web_search")
        reasons.append("提及仓库/项目但无具体 GitHub 操作，需要联网搜索")
    if _URL_RE.search(text) and not github_specific_task:
        capabilities.add("web_fetch")
        reasons.append("包含 URL")
    if _WINDOWS_PATH_RE.search(text) or _FILE_EXT_RE.search(text):
        capabilities.add("file_read")
        reasons.append("包含文件路径或扩展名")
        if re.search(r"(?:修改|编辑|写入|保存|创建|新建|覆盖)", text):
            capabilities.add("file_write")
        if re.search(r"\.(?:docx?|xlsx?|pptx?|csv)\b", text, re.I):
            capabilities.add("office")
    if re.search(r"(?:几点|几号|星期几|周几|农历|节气|什么日期|距离.{0,12}(?:多久|几天))", text):
        capabilities.add("time")
        reasons.append("明确精确时间问题")
    weather_question = re.search(
        r"(?:查|看|告诉我|预报|今天|明天|后天|现在|当地|外面|北京|上海|广州|深圳|杭州|成都|重庆|武汉|西安)"
        r".{0,8}(?:天气|气温|温度|会下雨|会下雪|空气质量)|"
        r"(?:天气|气温|温度|空气质量).{0,8}(?:怎么样|如何|多少|预报|查询|查一下|会不会)",
        text,
    )
    if weather_question:
        capabilities.add("weather")
        reasons.append("明确天气问题")
    if not _NEGATED_SEARCH_RE.search(text) and (
        re.search(r"(?:联网搜索|上网搜索|搜一下|帮我搜|搜索.{0,10}(?:新闻|资料|信息)|查最新|最新新闻|实时消息)", text)
        or re.search(r"(?:给出|附上|提供).{0,5}(?:来源|链接)", text)
    ):
        capabilities.add("web_search")
        reasons.append("明确联网搜索或来源要求")

    # 复合网页任务已经包含搜索与交接能力；保留具体能力标签便于旧逻辑
    # 和工具目录继续工作，但不让它们改变第一步的确定性顺序。
    if composite_web_task:
        if _WEB_RESEARCH_READ_RE.search(text):
            capabilities.add("web_fetch")
        else:
            capabilities.add("browser")
    if re.search(r"(?:这段代码|代码块|函数|脚本|仓库|git |commit|单元测试|调试|修复.{0,8}(?:bug|代码)|运行.{0,8}(?:代码|脚本))", lowered):
        capabilities.add("code")
        reasons.append("明确代码任务")
    if re.search(r"(?:待办|todo|提醒我|加入清单|闹钟|倒计时|定时提醒|设置提醒|定个提醒)", lowered):
        capabilities.add("todo")
        reasons.append("明确待办/提醒任务")
    if re.search(r"(?:b站|哔哩哔哩|bilibili)", lowered):
        capabilities.add("bilibili")
        reasons.append("明确 B 站任务")
    # 图像能力：截屏/看屏幕/摄像头画面/图像识别。此前没有任何正则触发
    # image 组，导致"你可以截屏观察一下我的电脑吗"这类请求落入
    # CHAT_LIGHT（模型手中无截屏工具，只能回答"没有能力"）。
    if re.search(
        r"(?:截屏|截图|屏幕截图|拍屏|截个图|"
        r"(?:看看?|观察|识别|描述|读一下).{0,8}(?:我的)?(?:屏幕|显示器|桌面)|"
        r"(?:屏幕|显示器|桌面).{0,6}(?:上|里|中).{0,4}(?:是什么|有什么|显示))",
        text,
    ):
        capabilities.add("image")
        reasons.append("要求截屏、观察屏幕或图像识别")

    # 图像生成：画图/自画像/生成图片（含用户点名 Agnes 图像 API）。
    if re.search(
        r"(?:帮我?画|画个|画一张|画一幅|画一下|画出来|画一个|画幅).{0,20}(?:图|画|自画像|像)"
        r"|(?:生成|制作).{0,16}(?:图片|图像|图|画|自画像)"
        r"|(?:图片|图像).{0,6}(?:生成|制作|生成器)"
        r"|(?:自画像|画图|图像生成|文生图|Agnes)",
        text,
    ):
        capabilities.add("image")
        reasons.append("要求生成或绘制图像")
    # 系统自动化：此前整组不可达（open_app 等工具从未被路由开放）。
    if re.search(
        r"(?:打开|启动|运行).{0,6}(?:应用|程序|软件)"
        r"|(?:读取|查看|看看?).{0,4}剪贴板|剪贴板内容"
        r"|(?:发送|发).{0,6}(?:到|给)\s*qq|把.{0,10}文件.{0,6}发.{0,3}qq"
        r"|自动化任务|计划任务|任务分解|委派任务",
        lowered,
    ):
        capabilities.add("system")
        reasons.append("系统自动化操作（打开应用/剪贴板/QQ发文件/任务编排）")
    # 办公文档：不再依赖消息中出现文件扩展名。
    if re.search(
        r"(?:ppt|幻灯片|excel|表格|word文档|docx|排版|周报)"
        r"|(?:做|写|生成|整理|处理).{0,4}(?:表格|文档|ppt)"
        r"|(?:写|撰写|生成).{0,4}(?:报告|周报)",
        lowered,
    ):
        capabilities.add("office")
        reasons.append("办公文档处理")
    # 文件操作泛化：不再依赖消息中出现路径或扩展名。
    if re.search(
        r"(?:新建|创建).{0,4}(?:文件夹|文件)"
        r"|(?:整理|分类|删除|删掉|移动|复制|重命名).{0,6}文件"
        r"|(?:保存|写入|存).{0,8}(?:到|进).{0,4}(?:文件|文件夹)"
        r"|(?:读取|读一下?|看看?|打开).{0,4}文件",
        lowered,
    ):
        capabilities.add("file_read")
        reasons.append("文件操作（泛化匹配）")
        if re.search(r"(?:写入|保存|创建|新建|删除|删掉|移动|重命名|整理)", lowered):
            capabilities.add("file_write")

    # 本地文件/目录查找：找…文件/目录/在哪里/路径，或用系统命令/命令行搜索。
    if re.search(
        r"(?:找|查找|找找|找一下|搜一?下|搜索|查一下|帮我找).{0,24}(?:文件|目录|文件夹|在哪里|在哪儿|路径|位置)"
        r"|(?:系统命令|命令行|终端|cmd|powershell).{0,12}(?:搜索|查找|搜|查)"
        r"|(?:文件|目录|文件夹).{0,4}(?:在哪里|在哪儿|位置|路径)",
        text,
    ):
        capabilities.add("file_read")
        reasons.append("本地文件或目录查找")
        if re.search(r"(?:系统命令|命令行|终端|cmd|powershell)", lowered):
            capabilities.add("code")
            reasons.append("用户要求使用系统命令")
    # 用户直接点名工具名时按工具所属能力组路由（高精度，零泛化误判）。
    for _mentioned in _TOOL_NAME_PATTERN.findall(text):
        _mentioned_cap = _TOOL_NAME_TO_CAPABILITY.get(_mentioned.lower())
        if _mentioned_cap:
            capabilities.add(_mentioned_cap)
            reasons.append(f"点名工具 {_mentioned}")
    # 混合任务里的回忆成分：“还记得…吗，帮我搜下…”这类消息在扫描时补上
    # memory_read，让记忆工具与搜索工具同轮可用。
    if re.search(r"(?:记得|还记得|回忆|之前说过|以前聊过|记忆|昨天.{0,4}说|前天.{0,4}说)", text):
        capabilities.add("memory_read")
        reasons.append("消息附带回忆或记忆检索需求")

    # 记忆写意图：删除/修改/保存/忘记 记忆 → memory_write（delete_memory/update_memory/save_memory）。
    if re.search(
        r"(?:删除|删掉|移除|去掉|修改|更新|改写|覆盖|保存|记住|写入|存|忘掉|忘记|遗忘).{0,10}(?:记忆|回忆|长期记忆|这条)",
        text,
    ):
        capabilities.add("memory_write")
        reasons.append("用户要求写入/修改/删除记忆")
    if any(token in lowered for token in (
        "坦克", "贪吃蛇", "虚拟世界", "地图标记", "食物", "标记的位置", "标记点", "前往标记", "到达标记",
        "左转", "右转", "急停", "取消任务",
    )):
        capabilities.add("embodied")
        reasons.append("虚拟世界具身任务")

    if _CONTACTS_INQUIRY_RE.search(text):
        capabilities.add("contacts")
        reasons.append("主人询问近期互动联系人或QQ好友列表")

    if re.search(
        r"(?:ESP32|ESP32-CAM|肩载|肩部外设|肩膀|云台|舵机|肩部摄像|肩载摄像|"
        r"肩膀状态|肩部状态|肩载状态|拍一张照片|拍照|看看周围|观察周围|"
        r"人脸追踪|人脸跟踪|追踪人脸|跟踪人脸)",
        text,
        re.IGNORECASE,
    ):
        capabilities.add("hardware")
        reasons.append("肩载设备或 ESP32-CAM 操作")

    if capabilities:
        return RequestRoute(RequestMode.TASK_DIRECT, frozenset(capabilities), "；".join(reasons))
    if _SOCIAL_RE.fullmatch(text) or (len(text) <= 18 and not _looks_like_action(text)):
        return RequestRoute(RequestMode.CHAT_LIGHT, frozenset(), "短问候或日常交流")
    if _looks_like_action(text) and not _NEGATED_SEARCH_RE.search(text):
        if request_context.has_image_blocks:
            # 泛化的“帮我看看”没有指向具体能力，图片回应优先用已注入
            # 的视觉描述作答，而不是进入能力发现流程。
            return RequestRoute(
                RequestMode.CHAT_LIGHT,
                frozenset(),
                "图片回应：基于已注入的视觉描述作答，不启动工具",
            )
        return RequestRoute(RequestMode.TASK_DISCOVERY, frozenset(), "存在操作意图但领域不确定")
    return RequestRoute(RequestMode.CHAT_LIGHT, frozenset(), "无外部能力强信号，先按纯文本交流")


def is_contacts_inquiry(text: str) -> bool:
    """判断消息是否属于主人询问近期互动联系人或 QQ 好友列表的社交回顾类问题。"""
    return bool(_CONTACTS_INQUIRY_RE.search(str(text or "").strip()))


def normalize_capabilities(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for value in values or ():
        key = str(value or "").strip().lower()
        if key in CAPABILITY_TO_TOOLS and key not in normalized:
            normalized.append(key)
    return normalized[:3]


def format_capability_result(capabilities: Iterable[str]) -> str:
    selected = normalize_capabilities(capabilities)
    if not selected:
        return "没有识别到可开放的能力。请改用自然语言回答，或用更准确的能力类别重试一次。"
    lines = ["已为当前任务开放以下能力："]
    for key in selected:
        lines.append(f"- {key}：{CAPABILITY_DESCRIPTIONS[key]}")
    lines.append("请立即使用已开放的真实工具继续任务，不要只描述调用计划。")
    return "\n".join(lines)


REQUEST_TOOLS_DEFINITION = {
    "type": "function",
    "function": {
        "name": "request_tools",
        "description": (
            "纯文本不足以完成当前任务时，按语义申请最多三个能力类别。"
            "闲聊不要调用；申请后必须继续执行真实工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "description": "准备完成的具体任务。"},
                "capabilities": {
                    "type": "array", "items": {"type": "string", "enum": sorted(CAPABILITY_TO_TOOLS)},
                    "maxItems": 3,
                },
                "reason": {"type": "string", "description": "为什么纯文本不足。"},
            },
            "required": ["intent", "capabilities", "reason"],
        },
    },
}
