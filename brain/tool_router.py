"""
工具路由器：按需注入工具定义，减少 token 消耗。

三层设计：
  L1 核心工具（12个）— 始终注入完整定义，覆盖记忆/时间/文件等高频操作
  L2 领域工具（52个）— 按用户消息关键词匹配，命中才注入完整定义
  L3 工具目录（~300 token）— 始终注入，列出所有工具名+一句话描述，
      让模型知道"我有哪些武器"，需要时可主动申请激活

激活流程：
  用户消息 → 关键词匹配领域 → 核心+命中领域(完整定义) + 目录(全部)
  若模型回复中暗示需要未激活的工具 → 自动重试，全量注入
"""

from typing import List, Dict, Set, Tuple

from brain.request_router import RequestRoute

# ── 核心工具（始终加载完整定义）────────────────────────────
CORE_TOOLS: Set[str] = {
    # 记忆系统（仅保留高频 CRUD，其余按需激活）
    "save_memory", "update_current_state", "review_memory_conflict", "explain_memory_quality",
    "search_graph_memory", "update_memory", "delete_memory",
    "discover_connections",  # 图谱关系发现
    # 时间
    "get_current_time",
    # 余额
    "get_balance",
    # 技能系统
    "list_skills", "activate_skill", "deactivate_skill",
    # 跨端搜索
    "search_conversation_history", "search_cross_session",
    # 文件操作（最高频入口，始终可用避免模型绕弯路）
    "search_files_everything", "read_file",
    "read_diary", "write_diary",
}

# ── 领域工具分类 ────────────────────────────────────────

CATEGORY_TOOLS: Dict[str, Set[str]] = {
    "file": {
        "read_file", "read_file_chunk", "read_file_lines",
        "write_file", "edit_file", "list_directory",
        "get_file_info_everything",
        "glob_files", "grep_file", "diff_files",
    },
    "code": {
        "run_python_code", "search_code", "run_command", "run_shell",
        "code_structure", "code_goto_def", "code_find_refs",
        "code_diagnostics", "git_status",
    },
    "web": {
        "web_search", "fetch_webpage", "fetch_webpage_browser",
        "fetch_webpage_via_api", "fetch_webpage_stealth",
        "configure_network_tools",
    },
    "github": {
        "github_search_repositories", "github_get_readme",
        "github_get_file", "github_list_directory", "github_list_commits",
    },
    "bilibili": {
        "bilibili_search", "bilibili_add_tag", "bilibili_list_tags",
    },
    "office": {
        "read_excel", "write_excel", "copy_excel_content",
        "write_docx", "format_document",
    },
    "media": {
        "ocr_image", "ocr_batch", "describe_image",
        "capture_from_camera", "capture_desktop",
        "generate_image", "generate_video",
    },
    "auto": {
        "open_app", "get_clipboard", "send_file_to_qq",
        "toggle_proactive_chat", "plan_tasks", "delegate_task", "track_tasks",
    },
    "todo": {
        "add_todo", "list_todos", "complete_todo",
    },
    "weather": {
        "get_weather", "set_user_city",
    },
}

# ── 关键词 → 领域映射 ────────────────────────────────────

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "file": [
        "文件", "读取", "写入", "编辑", "目录", "文件夹", "路径",
        "保存", "创建文件", "新建文件", "修改文件", "文档内容",
        "打开文件", "查看文件", "看看文件", "帮我写", "帮我改",
        "read", "write", "edit", "file", "directory", "folder",
    ],
    "code": [
        "代码", "编程", "python", "运行", "调试", "bug", "函数",
        "程序", "脚本", "script", "run", "重构", "定义", "引用",
        "git", "commit", "分支", "仓库", "命令行", "终端", "shell",
    ],
    "web": [
        "搜索", "网页", "网络", "上网", "查一下", "百度", "谷歌",
        "fetch", "抓取", "链接", "网址", "http://", "https://",
        "帮我搜", "查查", "最新", "新闻", "search", "web", "internet",
    ],
    "github": [
        "github", "github.com", "repository", "repo", "readme",
        "commit", "requirements.txt", "pyproject.toml",
    ],
    "bilibili": [
        "bilibili", "b站", "哔哩哔哩", "b站标签", "b站视频",
    ],
    "office": [
        "excel", "表格", "word", "文档", "docx", "xlsx",
        "排版", "格式化", "office", "办公", "报告",
    ],
    "media": [
        "图片", "图像", "照片", "截图", "拍照", "相机",
        "ocr", "识别文字", "提取文字", "生成图", "生成视频",
        "画一张", "画个", "生成一张", "做一张", "image", "photo", "video",
    ],
    "auto": [
        "打开", "启动", "应用", "程序", "剪贴板", "复制的内容",
        "QQ", "发到QQ", "计划", "自动化", "定时", "桌面", "屏幕",
    ],
    "todo": [
        "待办", "提醒", "todo", "task", "清单", "记一下", "帮我记", "别忘了",
    ],
    "weather": [
        "天气", "温度", "下雨", "晴天", "weather", "气候",
        "冷不冷", "热不热", "刮风", "下雪",
    ],
}

# ── 工具简短描述（用于目录） ──────────────────────────────

TOOL_DESCRIPTIONS: Dict[str, str] = {
    "read_file": "读取文件",
    "read_file_chunk": "读取长文件分块",
    "read_file_lines": "按行号范围读取",
    "write_file": "写入/覆盖文件",
    "edit_file": "精确替换内容",
    "list_directory": "列出目录",
    "search_files_everything": "全盘极速搜索",
    "get_file_info_everything": "文件元数据",
    "glob_files": "模式匹配文件",
    "grep_file": "文件内容搜索",
    "diff_files": "对比文件差异",
    "run_python_code": "沙箱执行Python",
    "search_code": "正则搜索代码",
    "run_command": "执行系统命令",
    "run_shell": "Shell命令增强",
    "code_structure": "列出代码结构",
    "code_goto_def": "跳转到定义",
    "code_find_refs": "查找引用",
    "code_diagnostics": "检查代码错误",
    "git_status": "查看Git状态",
    "web_search": "联网搜索",
    "fetch_webpage": "提取网页内容",
    "fetch_webpage_browser": "浏览器提取网页",
    "fetch_webpage_via_api": "Jina解析网页",
    "fetch_webpage_stealth": "TLS伪装提取",
    "configure_network_tools": "查看或调整联网工具顺序与启停状态",
    "bilibili_search": "B站搜索视频",
    "bilibili_add_tag": "添加B站标签",
    "bilibili_list_tags": "查看B站标签",
    "read_excel": "读取Excel",
    "write_excel": "写入Excel",
    "copy_excel_content": "复制Excel内容",
    "write_docx": "写入Word",
    "format_document": "Markdown转Word",
    "ocr_image": "图片文字识别",
    "ocr_batch": "批量文字识别",
    "describe_image": "理解图片内容",
    "capture_from_camera": "摄像头拍照",
    "capture_desktop": "截取屏幕",
    "generate_image": "AI生成图片",
    "generate_video": "AI生成视频",
    "open_app": "打开应用程序",
    "get_clipboard": "读取剪贴板",
    "send_file_to_qq": "发文件到QQ",
    "toggle_proactive_chat": "开关QQ主动聊天",
    "plan_tasks": "分解复杂任务",
    "delegate_task": "委派子代理",
    "track_tasks": "追踪任务进度",
    "add_todo": "添加待办",
    "list_todos": "列出待办",
    "complete_todo": "完成待办",
    "get_weather": "查询天气",
    "set_user_city": "设置用户城市",
}

# ── 领域中文名 ──────────────────────────────────────────

CATEGORY_NAMES: Dict[str, str] = {
    "file": "文件操作",
    "code": "代码开发",
    "web": "网页搜索",
    "bilibili": "B站互动",
    "office": "办公文档",
    "media": "图像媒体",
    "auto": "系统自动化",
    "todo": "待办清单",
    "weather": "天气查询",
}

# 按目录显示顺序
CATEGORY_ORDER = ["file", "code", "web", "bilibili", "office", "media", "auto", "todo", "weather"]


def match_categories(user_message: str) -> Set[str]:
    """根据用户消息关键词匹配需要加载的领域。

    每个领域只要命中任意一个关键词即激活。
    """
    if not user_message:
        return set()

    msg_lower = user_message.lower()
    matched: Set[str] = set()

    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in msg_lower:
                matched.add(category)
                break

    return matched


def get_active_tool_names(user_message: str) -> Set[str]:
    """获取当前应该加载完整定义的工具名集合。

    包括：核心工具 + 关键词命中的领域工具
    """
    categories = match_categories(user_message)
    needed = set(CORE_TOOLS)
    for cat in categories:
        needed.update(CATEGORY_TOOLS.get(cat, set()))
    if is_diary_request(user_message):
        # A diary/time-capsule question is a structured database query, not a
        # filesystem search. Keep the exact tools available and remove the
        # two broad file fallbacks that previously hijacked this intent.
        needed.update({"read_diary", "write_diary"})
        needed.discard("read_file")
        needed.discard("search_files_everything")
    return needed


def is_diary_request(user_message: str) -> bool:
    text = str(user_message or "").lower()
    return any(token in text for token in (
        "日记", "时间胶囊", "共同书页", "昨天写了什么", "前天写了什么",
        "昨天的日记", "前天的日记", "上周的日记", "记得我写的",
    ))


def filter_builtin_tools(all_tools: List[dict], user_message: str) -> List[dict]:
    """从全部内置工具中筛选出当前需要的工具。

    Args:
        all_tools: TOOL_DEFINITIONS 完整列表
        user_message: 用户最后一条消息

    Returns:
        筛选后的工具列表（仅核心+命中领域）
    """
    active = get_active_tool_names(user_message)
    selected = [t for t in all_tools
                if t.get("function", {}).get("name", "") in active]
    from brain.request_tool_policy import filter_definitions_for_request
    return filter_definitions_for_request(selected, user_message)


def filter_builtin_tools_for_route(all_tools: List[dict], route: RequestRoute,
                                   user_message: str) -> List[dict]:
    """按请求模式只注入本轮确实开放的工具，取代核心工具常驻。"""
    active = set(route.tool_names)
    selected = [
        item for item in all_tools
        if item.get("function", {}).get("name", "") in active
    ]
    from brain.request_tool_policy import filter_definitions_for_request
    return filter_definitions_for_request(selected, user_message)


def select_contextual_external_tools(definitions: List[dict], current_text: str,
                                     recent_context: str = "") -> List[dict]:
    """只在当前请求点名服务，或明确承接上一轮服务建议时注入 MCP。"""
    current = str(current_text or "").lower()
    recent = str(recent_context or "").lower()
    continuation = any(token in current for token in (
        "那就用", "就用你推荐的", "用它试试", "开始试试", "执行测试", "跑一下",
        "go ahead", "try it",
    ))
    selected = []
    for item in definitions:
        name = item.get("function", {}).get("name", "").lower()
        parts = name.split("__", 2)
        service = parts[1] if len(parts) >= 3 and parts[0] == "mcp" else ""
        if service and (service in current or (continuation and service in recent)):
            selected.append(item)
    return selected


def get_activation_tool_names(response_text: str, request_text: str = "") -> Set[str]:
    """只激活模型明确点名或当前语义类别需要的工具，禁止全量展开。"""
    text = str(response_text or "")
    categories = match_categories(f"{request_text}\n{text}")
    names: Set[str] = set()
    for category in categories:
        names.update(CATEGORY_TOOLS.get(category, set()))

    # 工具目录会让模型知道名称；只有正文明确点名时才补充该定义。
    all_known = set(CORE_TOOLS)
    for tools in CATEGORY_TOOLS.values():
        all_known.update(tools)
    lowered = text.lower()
    names.update(name for name in all_known if name.lower() in lowered)

    from brain.request_tool_policy import request_tool_allowlist
    allowlist = request_tool_allowlist(request_text)
    if allowlist is not None:
        names.intersection_update(allowlist)
    return names


def build_tool_catalog(loaded_categories: Set[str],
                       skill_tool_names: list = None,
                       mcp_tool_names: list = None,
                       disabled_tool_names: set = None,
                       skill_mcp_active: bool = False) -> str:
    """构建工具目录 system 消息。

    ✅ = 已加载完整定义，可直接调用
    📋 = 仅目录可见，需在回复中说出工具名才会激活
    skill_mcp_active=True 时技能/MCP 也标为 ✅（重试全量注入模式）
    """
    disabled = disabled_tool_names or set()
    skill_names = skill_tool_names or []
    mcp_names = mcp_tool_names or []

    lines = [
        "【工具目录 — 内部参考，严禁在对话中提及】✅=已加载 📋=说出工具名即可激活",
    ]

    # 核心工具
    core_names = sorted(name for name in CORE_TOOLS if name not in disabled)
    lines.append(f"✅ 核心({len(core_names)}): {', '.join(core_names)}")

    # 领域工具
    for cat in CATEGORY_ORDER:
        tools = sorted(CATEGORY_TOOLS[cat])
        tools = [t for t in tools if t not in disabled]
        if not tools:
            continue
        count = len(tools)
        name = CATEGORY_NAMES[cat]
        prefix = "✅" if cat in loaded_categories else "📋"
        lines.append(f"{prefix} {name}({count}): {', '.join(tools)}")

    # 技能工具
    if skill_names:
        skill_names = [n for n in skill_names if n not in disabled]
        if skill_names:
            prefix = "✅" if skill_mcp_active else "📋"
            lines.append(f"{prefix} 技能({len(skill_names)}): {', '.join(skill_names)}")

    # MCP 工具
    if mcp_names:
        mcp_names = [n for n in mcp_names if n not in disabled]
        if mcp_names:
            prefix = "✅" if skill_mcp_active else "📋"
            lines.append(f"{prefix} MCP({len(mcp_names)}): {', '.join(mcp_names)}")

    # 已禁用工具（仅列名称，告诉模型不可调用但存在，用户可在设置中开启）
    all_known = set(CORE_TOOLS)
    for ct in CATEGORY_TOOLS.values():
        all_known.update(ct)
    all_known.update(skill_names)
    all_known.update(mcp_names)
    disabled_shown = sorted(disabled & all_known)
    if disabled_shown:
        lines.append(f"🚫 已禁用({len(disabled_shown)}): {', '.join(disabled_shown)} — 不可调用，需用户在设置中开启")

    return "\n".join(lines)


def detect_tool_request(response_text: str) -> bool:
    """检测模型回复是否暗示需要未激活的工具。

    触发条件：
    1. 模型明确说"我没有这个工具"、"需要...工具"等
    2. 模型的回复很短（<100字）且表达了工具使用意图
       注意：长回复（>100字）说明模型已经做了完整回答，跳过意图检测。
    """
    if not response_text:
        return False

    import re

    # 长回答已经形成完整答复，其中的“没有权限”等自然语言不能触发工具重试。
    if len(response_text) >= 100:
        return False

    # 明确表达缺少工具（短回复才可能是工具缺失，长回复中可能是正常语气）
    missing_patterns = [
        "我没有这个工具", "我没有对应的工具", "没有这个功能",
        "需要.*工具", "缺少.*工具", "无法调用",
        "没有.*权限", "没有.*能力",
        "请激活", "需要激活",
        "暂时没有", "目前没有",
    ]
    for p in missing_patterns:
        if re.search(p, response_text):
            return True

    # "我无法/我不能/我没法" 这类语气词在角色扮演中很常见，只在极短回复中判定为工具缺失
    if len(response_text) < 50:
        short_missing = ["我无法", "我不能", "我没法"]
        for p in short_missing:
            if p in response_text:
                return True

    # 表达了工具使用意图（但短回复才可能真的是被工具限制截断，
    # 长回复说明模型已经完成回答了，不应重试）
    intent_keywords = [
        "让我搜索", "我来搜索", "帮你搜索", "搜一下",
        "让我查", "我来查", "帮你查", "查一下",
        "让我打开", "我来打开", "帮你打开",
        "让我看看", "我看一下", "让我拍",
        "让我读取", "我来读取", "读取文件",
        "让我写", "我来写", "写入文件",
        "浏览器", "打开网页", "截图",
        "执行命令", "运行代码",
    ]
    for kw in intent_keywords:
        if kw in response_text:
            return True

    return False
