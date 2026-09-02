"""
Skill Manager：莲心AI 技能系统
负责技能的发现、加载、激活/停用管理。

技能目录：项目根目录下的 skills/
每个技能是一个子目录，包含 SKILL.md（元数据+知识内容）和可选的 tools.py（自定义工具）。

两类技能：
  1. 纯知识注入 — 只有 SKILL.md，激活后注入 system prompt
  2. 带自定义工具 — 有 tools.py，激活后注册新工具到全局调度表
"""

from pathlib import Path
from typing import Optional
import json
import importlib.util
import sys
import logging

logger = logging.getLogger("SkillManager")

# ── 持久化配置 ─────────────────────────────────────────
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "utils" / "paths.py"
_CONFIG_DIR = None  # 延迟初始化
_SKILL_CONFIG_FILE = None

def _get_config_dir() -> Path:
    global _CONFIG_DIR
    if _CONFIG_DIR is None:
        from utils.paths import get_user_data_dir
        _CONFIG_DIR = get_user_data_dir()
    return _CONFIG_DIR

def _get_config_path() -> Path:
    global _SKILL_CONFIG_FILE
    if _SKILL_CONFIG_FILE is None:
        _SKILL_CONFIG_FILE = _get_config_dir() / "skill_config.json"
    return _SKILL_CONFIG_FILE

# 内存中的禁用列表（启动时从文件加载，运行时由 UI 同步）
_disabled_skills: set[str] = set()

def _load_skill_config():
    """从文件加载禁用的技能列表"""
    global _disabled_skills
    try:
        cfg_path = _get_config_path()
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            _disabled_skills = set(data.get("disabled_skills", []))
    except Exception:
        _disabled_skills = set()

def save_skill_config():
    """保存当前禁用技能列表到文件"""
    try:
        cfg_path = _get_config_path()
        data = {"disabled_skills": sorted(_disabled_skills)}
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("保存技能配置失败: %s", e)

def get_disabled_skills() -> set[str]:
    return _disabled_skills.copy()


def is_skill_active(name: str) -> bool:
    """Return whether a discovered Skill is currently active."""
    return str(name or "") in _active_skills

# ── 技能注册表 ─────────────────────────────────────────
# _skill_registry[name] = {
#     "name": str,
#     "description": str,
#     "version": str,
#     "path": Path,
#     "knowledge": str,           # SKILL.md 正文（激活后注入 system prompt）
#     "tool_definitions": list,   # tools.py 导出的 TOOL_DEFINITIONS
#     "has_tools": bool,
# }
_skill_registry: dict[str, dict] = {}
_active_skills: set[str] = set()

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


# ── 公开接口 ───────────────────────────────────────────

def discover_skills():
    """扫描 skills/ 目录，发现所有可用技能。"""
    _skill_registry.clear()
    if not _SKILLS_DIR.exists():
        _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        return

    for entry in sorted(_SKILLS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        info = _parse_skill(entry)
        if info:
            _skill_registry[info["name"]] = info


def activate_skill(name: str) -> str:
    """激活指定名称的技能。返回结果消息。"""
    skill = _skill_registry.get(name)
    if not skill:
        return f"错误：未找到技能「{name}」。可用技能：{'、'.join(_skill_registry)}"

    if name in _active_skills:
        return f"技能「{name}」已处于激活状态。"

    # 如果技能有 tools.py，动态加载
    if skill["has_tools"]:
        err = _load_skill_tools(name, skill["path"] / "tools.py")
        if err:
            return err

    _active_skills.add(name)
    _disabled_skills.discard(name)  # 手动激活时移出禁用列表
    try:
        from brain.capability_knowledge import invalidate_capability_knowledge_cache
        invalidate_capability_knowledge_cache()
    except Exception:
        pass
    return f"技能「{name}」已激活。"


def deactivate_skill(name: str) -> str:
    """停用指定名称的技能。"""
    if name not in _active_skills:
        return f"技能「{name}」当前未激活。"

    skill = _skill_registry.get(name)
    if skill and skill["has_tools"]:
        _unload_skill_tools(name)

    _active_skills.discard(name)
    _disabled_skills.add(name)
    try:
        from brain.capability_knowledge import invalidate_capability_knowledge_cache
        invalidate_capability_knowledge_cache()
    except Exception:
        pass
    return f"技能「{name}」已停用。"


def uninstall_skill(name: str):
    """卸载技能：停用 + 从注册表删除 + 删除目录"""
    skill = _skill_registry.get(name)
    if not skill:
        return False, f"未找到技能「{name}」"

    skill_dir = skill["path"]

    if name in _active_skills:
        deactivate_skill(name)

    del _skill_registry[name]

    import shutil
    try:
        shutil.rmtree(skill_dir)
    except Exception as e:
        return False, f"删除目录失败: {e}"

    return True, f"技能「{name}」已卸载。"


def get_skill_list() -> str:
    """返回格式化的技能列表（含激活状态）。"""
    if not _skill_registry:
        return "暂无可用的技能。"

    lines = ["📦 可用技能列表："]
    for name, info in _skill_registry.items():
        status = "✅ 已激活" if name in _active_skills else "⏹ 未激活"
        has_tools = " [含工具]" if info["has_tools"] else ""
        desc = info["description"]
        lines.append(f"  {status}  {name}{has_tools} — {desc}")
    return "\n".join(lines)


def get_active_knowledge() -> list[str]:
    """获取所有已激活技能的知识内容列表（供注入 system prompt 使用）。"""
    return [_skill_registry[n]["knowledge"] for n in _active_skills if _skill_registry[n]["knowledge"]]


def get_active_skill_summary() -> str:
    """获取所有已激活技能的紧凑摘要（名称+一句话描述，每轮对话都注入）。
    格式：🔧 技能名 — 一句话描述
    """
    lines = []
    for name in sorted(_active_skills):
        info = _skill_registry.get(name)
        if not info:
            continue
        desc = info.get("description", "")
        # 截断过长描述
        if len(desc) > 80:
            desc = desc[:77] + "..."
        lines.append(f"🔧 {name} — {desc}")
    return "\n".join(lines) if lines else ""


def get_matching_knowledge(user_message: str) -> str:
    """根据用户消息关键词匹配，返回相关技能的完整 SKILL.md 知识。
    只返回匹配到的技能，不匹配则返回空字符串。
    """
    if not user_message:
        return ""
    msg_lower = user_message.lower()
    parts = []
    for name in sorted(_active_skills):
        info = _skill_registry.get(name)
        if not info or not info.get("knowledge"):
            continue
        # 提取技能关键词：名称 + 描述中的有效词
        keywords = {name.lower()}
        desc = info.get("description", "")
        for word in desc.replace("、", " ").replace("，", " ").replace(",", " ").split():
            word = word.strip().lower()
            if len(word) >= 2:
                keywords.add(word)
        # 任一关键词命中即匹配
        if any(kw in msg_lower for kw in keywords):
            parts.append(info["knowledge"])
    return "\n\n---\n\n".join(parts) if parts else ""


def get_active_tool_definitions() -> list[dict]:
    """获取所有已激活技能的自定义工具定义列表。"""
    result = []
    for name in _active_skills:
        skill = _skill_registry.get(name)
        if skill:
            result.extend(skill["tool_definitions"])
    return result


def get_tool_definitions_by_skills(skill_names: list[str]) -> list[dict]:
    """获取指定技能列表的工具定义（仅已激活的技能）。"""
    result = []
    for name in skill_names:
        skill = _skill_registry.get(name)
        if skill and name in _active_skills:
            result.extend(skill["tool_definitions"])
    return result


def activate_all_skills():
    """激活所有标记为 auto_activate 的技能（跳过用户手动禁用的）。"""
    _load_skill_config()
    activated = 0
    failed = 0
    skipped = 0
    for name, info in _skill_registry.items():
        if not info.get("auto_activate", True):
            continue
        if name in _disabled_skills:
            skipped += 1
            continue
        if name in _active_skills:
            if not info.get("tool_definitions"):
                if info["has_tools"]:
                    err = _load_skill_tools(name, info["path"] / "tools.py")
                    if err:
                        logger.warning("重载技能「%s」工具失败: %s", name, err)
            activated += 1
            continue

        if info["has_tools"]:
            err = _load_skill_tools(name, info["path"] / "tools.py")
            if err:
                logger.warning("自动激活技能「%s」失败: %s", name, err)
                failed += 1
                continue
        _active_skills.add(name)
        activated += 1

    if activated or skipped:
        logger.info("已自动激活 %d 个技能（失败 %d 个，跳过 %d 个）", activated, failed, skipped)
        # 技能变更后清除意图路由器的工具列表缓存
        try:
            from brain.intent_router import invalidate_tool_cache
            invalidate_tool_cache()
        except Exception:
            pass


def list_skill_resources(name: str) -> str:
    """列出技能目录下除 SKILL.md 和 tools.py 外的资源文件。"""
    skill = _skill_registry.get(name)
    if not skill:
        return f"错误：未找到技能「{name}」。"

    resources = []
    for f in skill["path"].iterdir():
        if f.is_file() and f.name not in ("SKILL.md", "tools.py"):
            resources.append(f.name)
    resources.sort()

    if not resources:
        return f"技能「{name}」没有附加资源文件。"
    return f"技能「{name}」的资源文件：\n" + "\n".join(f"  {r}" for r in resources)


def read_skill_resource(name: str, filename: str) -> str:
    """读取技能目录下的资源文件内容（仅文本文件）。"""
    skill = _skill_registry.get(name)
    if not skill:
        return f"错误：未找到技能「{name}」。"

    target = skill["path"] / filename
    if not target.exists() or not target.is_file():
        return f"错误：文件不存在 → {filename}"

    if target.suffix.lower() in (".py", ".pyc", ".exe", ".dll"):
        return "错误：不支持读取 Python / 二进制文件内容。"

    try:
        text = target.read_text(encoding="utf-8")
        if len(text) > 5000:
            text = text[:5000] + "\n\n... [内容过长，已截断]"
        return text
    except UnicodeDecodeError:
        return "错误：无法以文本方式读取该文件（非文本格式）。"
    except Exception as e:
        return f"读取失败：{e}"


# ── 内部实现 ───────────────────────────────────────────

def _parse_skill(skill_dir: Path) -> Optional[dict]:
    """解析技能目录，返回技能信息字典。"""
    skill_md = skill_dir / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")

    # 解析 YAML 前置元数据（--- 包裹）
    name = skill_dir.name
    description = ""
    version = "1.0"
    auto_activate = True  # 默认自动激活
    license = ""

    lines = content.splitlines()
    if len(lines) >= 2 and lines[0].strip() == "---":
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end:
            meta = _parse_simple_yaml(lines[1:end])
            name = meta.get("name", name)
            description = meta.get("description", "")
            version = meta.get("version", "1.0")
            license = meta.get("license", "")
            auto_activate_str = meta.get("auto_activate", "true")
            auto_activate = auto_activate_str.lower() in ("true", "yes", "1")
            knowledge_lines = lines[end + 1:]
        else:
            knowledge_lines = lines[1:]
    else:
        # 没有前置元数据，整篇内容作为知识
        knowledge_lines = lines

    # 去掉首尾空行
    knowledge = "\n".join(knowledge_lines).strip()
    # 如果 description 为空，取知识前 150 字
    if not description:
        description = knowledge[:150].replace("\n", " ").strip()

    has_tools = (skill_dir / "tools.py").exists()
    # 自我认知是按需、由用户手动启用的知识库；不能因为启动时扫描
    # Skill 就把它的详细说明和工具自动注入每轮上下文。
    if name == "自我认知功能":
        auto_activate = False

    return {
        "name": name,
        "description": description,
        "version": version,
        "license": license,
        "path": skill_dir,
        "knowledge": knowledge,
        "tool_definitions": [],
        "has_tools": has_tools,
        "auto_activate": auto_activate,
    }


def _parse_simple_yaml(lines: list[str]) -> dict:
    """
    简洁解析极简 YAML（只支持 key: value 格式），不依赖 pyyaml。
    示例输入：["name: my_skill", "description: 描述文字"]
    """
    result = {}
    for line in lines:
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and value:
                result[key] = value
    return result


def _load_skill_tools(name: str, tools_py: Path) -> Optional[str]:
    """加载技能的自定义工具，注册到全局 TOOL_EXECUTORS。"""
    try:
        spec = importlib.util.spec_from_file_location(f"_skill_{name}", str(tools_py))
        if spec is None or spec.loader is None:
            return f"技能「{name}」的 tools.py 加载失败。"

        module = importlib.util.module_from_spec(spec)
        # 记录已加载模块以便卸载
        sys.modules[f"_skill_{name}"] = module
        spec.loader.exec_module(module)

        # 读取工具定义
        definitions = getattr(module, "TOOL_DEFINITIONS", [])
        _skill_registry[name]["tool_definitions"] = definitions

        # 注册执行函数
        executors = getattr(module, "TOOL_EXECUTORS", {})
        if executors:
            from brain.tools import TOOL_EXECUTORS as core_executors
            for tool_name, executor in executors.items():
                if tool_name in core_executors and tool_name not in _get_skill_tool_names():
                    return (
                        f"冲突：技能「{name}」的工具「{tool_name}」与核心工具重名。"
                        "请修改技能中的工具名称。"
                    )
                core_executors[tool_name] = executor

        return None  # 成功
    except Exception as e:
        return f"加载技能「{name}」的 tools.py 时出错：{e}"


def _get_skill_tool_names() -> set[str]:
    """返回所有已注册技能的工具名称集合（用于重名检测）。"""
    names = set()
    for info in _skill_registry.values():
        for td in info.get("tool_definitions", []):
            fn = td.get("function", {})
            n = fn.get("name")
            if n:
                names.add(n)
    return names


def _unload_skill_tools(name: str):
    """卸载技能注册的工具。"""
    skill = _skill_registry.get(name)
    if not skill:
        return

    # 从全局 TOOL_EXECUTORS 移除本技能注册的工具
    from brain.tools import TOOL_EXECUTORS as core_executors
    for td in skill["tool_definitions"]:
        fn = td.get("function", {})
        tool_name = fn.get("name")
        if tool_name and tool_name in core_executors:
            del core_executors[tool_name]

    skill["tool_definitions"] = []

    # 清理 sys.modules
    module_name = f"_skill_{name}"
    if module_name in sys.modules:
        del sys.modules[module_name]


# ── 初始化：启动时自动发现技能 ────────────────────────
discover_skills()
