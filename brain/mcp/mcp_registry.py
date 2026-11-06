# brain/mcp/mcp_registry.py
"""MCP 注册表 — 扫描 mcp_servers/ 目录，加载 manifest，创建 Agent 实例"""

import json
import importlib
import importlib.util
import sys
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List


logger = logging.getLogger("MCPRegistry")

MCP_REGISTRY: Dict[str, Any] = {}
MANIFEST_CACHE: Dict[str, Dict] = {}
_all_mcp_tool_defs: list = []
_disabled_mcp: set = set()
_mcp_config_loaded = False

_MCP_DIR = Path(__file__).resolve().parent.parent.parent / "mcp_servers"

# ── 持久化配置 ─────────────────────────────────────────
_CONFIG_DIR = None
_MCP_CONFIG_FILE = None

def _get_config_dir() -> Path:
    global _CONFIG_DIR
    if _CONFIG_DIR is None:
        from utils.paths import get_user_data_dir
        _CONFIG_DIR = get_user_data_dir()
    return _CONFIG_DIR

def _get_config_path() -> Path:
    global _MCP_CONFIG_FILE
    if _MCP_CONFIG_FILE is None:
        _MCP_CONFIG_FILE = _get_config_dir() / "skill_config.json"
    return _MCP_CONFIG_FILE

def _load_mcp_config():
    """从文件加载禁用的 MCP 列表"""
    global _disabled_mcp, _mcp_config_loaded
    try:
        cfg_path = _get_config_path()
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            _disabled_mcp = set(data.get("disabled_mcp", []))
    except Exception:
        _disabled_mcp = set()
    _mcp_config_loaded = True


def _ensure_mcp_config_loaded():
    if not _mcp_config_loaded:
        _load_mcp_config()

def save_mcp_config():
    """保存当前 MCP 禁用列表到文件（与技能共用 skill_config.json）"""
    try:
        from utils.extension_config import save_extension_section
        cfg_path = _get_config_path()
        save_extension_section(cfg_path, "disabled_mcp", _disabled_mcp)
    except Exception as e:
        logger.warning("保存 MCP 配置失败: %s", e)
        raise


def scan_mcp_services(mcp_dir: str = None) -> List[str]:
    """扫描 mcp_servers/ 目录，加载所有 mcp-manifest.json

    Returns:
        成功注册的 service_name 列表
    """
    global _all_mcp_tool_defs
    _load_mcp_config()
    d = Path(mcp_dir) if mcp_dir else _MCP_DIR

    if not d.exists():
        print(f"[MCP] 目录不存在，跳过: {d}")
        return []

    registered = []
    for manifest_file in sorted(d.glob("*/mcp-manifest.json")):
        try:
            manifest = _load_manifest(manifest_file)
            if not manifest:
                continue

            service_name = manifest.get("name", "")
            if not service_name:
                logger.warning(f"[MCP] manifest 缺少 name: {manifest_file}")
                continue

            if service_name in MCP_REGISTRY:
                logger.warning(f"[MCP] 服务名冲突，跳过: {service_name}")
                continue

            MANIFEST_CACHE[service_name] = manifest

            try:
                agent = _create_agent(manifest, manifest_file.parent)
            except Exception as e:
                logger.error(f"[MCP] 创建 Agent 失败: {service_name} → {e}")
                continue

            if agent:
                MCP_REGISTRY[service_name] = agent
                registered.append(service_name)
                tool_count = len(agent._tools)
                print(
                    f"[MCP] 注册: {service_name} ({manifest.get('displayName', service_name)}) "
                    f"— {tool_count} 个工具"
                )

        except Exception as e:
            logger.error(f"[MCP] 处理失败: {manifest_file} → {e}")

    _rebuild_tool_cache()
    return registered


def _load_manifest(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[MCP] 加载失败: {path} → {e}")
        return None


def _create_agent(manifest: Dict, service_dir: Path) -> Optional[Any]:
    agent_type = manifest.get("agentType", "local")

    if agent_type in ("local", "mcp"):
        entry = manifest.get("entryPoint", {})
        module_path = entry.get("module", "")
        class_name = entry.get("class", "")

        if module_path:
            mod = importlib.import_module(module_path)
            agent_class = getattr(mod, class_name, None)
            if agent_class:
                return agent_class()
        else:
            agent_py = service_dir / "agent.py"
            if not agent_py.exists():
                logger.error(f"[MCP] 找不到 agent.py: {service_dir}")
                return None

            module_name = f"_mcp_{manifest.get('name', 'unknown')}"
            spec = importlib.util.spec_from_file_location(module_name, str(agent_py))
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            from brain.mcp.mcp_agent_base import MCPAgentBase

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, MCPAgentBase)
                    and attr is not MCPAgentBase
                ):
                    instance = attr()
                    if not instance.service_name:
                        instance.service_name = manifest.get("name", "")
                    if not instance.display_name:
                        instance.display_name = manifest.get("displayName", "")
                    if not instance.description:
                        instance.description = manifest.get("description", "")
                    return instance

            logger.error(f"[MCP] 未找到 MCPAgentBase 子类: {service_dir}")

    elif agent_type == "external":
        try:
            from brain.mcp.mcp_client import ExternalMCPClient
        except ImportError:
            logger.error("[MCP] 无法导入 ExternalMCPClient")
            return None

        connection = manifest.get("connection", {})
        cmd = _expand_external_command(connection.get("command", []))
        env = connection.get("env", {}).copy()
        
        # 如果是 tavily_search，从用户配置读取 API Key（优先级高于 manifest）
        if manifest["name"] == "tavily_search":
            from config import get_tavily_config
            tv_cfg = get_tavily_config()
            if tv_cfg.get("api_key", "").strip():
                env["TAVILY_API_KEY"] = tv_cfg["api_key"]

        # 如果是 firecrawl，从用户配置读取 API Key；未配置则跳过
        if manifest["name"] == "firecrawl":
            from config import get_firecrawl_config
            fc_cfg = get_firecrawl_config()
            api_key = fc_cfg.get("api_key", "").strip()
            if not api_key:
                logger.warning("[MCP] firecrawl 未配置 API Key，跳过注册")
                return None
            env["FIRECRAWL_API_KEY"] = api_key

    

        client = ExternalMCPClient(
            service_name=manifest["name"],
            command=cmd,
            env=env,

        )
        if not client.connect_sync():
            logger.error(f"[MCP] 外部服务连接失败: {manifest['name']}")
            return None
        return client


    return None


def _expand_external_command(command: list) -> list:
    """展开 MCP 清单中的跨机器路径占位符，避免提交个人目录。"""
    replacements = {
        "{project_root}": str(_MCP_DIR.parent),
        "{user_home}": str(Path.home()),
    }
    resolved = []
    for item in command:
        value = str(item)
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        resolved.append(value)
    # subprocess.Popen(..., shell=False) 无法执行 PowerShell 的 npx.ps1。
    # Windows 上优先使用同目录的 npx.cmd，确保外部 MCP 能被真正拉起。
    if resolved and os.name == "nt" and resolved[0].lower() == "npx":
        npx_cmd = shutil.which("npx.cmd")
        if npx_cmd:
            resolved[0] = npx_cmd
    return resolved


def _rebuild_tool_cache():
    global _all_mcp_tool_defs
    _all_mcp_tool_defs = []
    for name, agent in MCP_REGISTRY.items():
        if name in _disabled_mcp:
            continue
        if hasattr(agent, "get_tool_definitions_openai"):
            _all_mcp_tool_defs.extend(agent.get_tool_definitions_openai())


def get_all_mcp_tool_definitions() -> list:
    return _all_mcp_tool_defs


def get_mcp_agent(service_name: str):
    return MCP_REGISTRY.get(service_name)


def is_mcp_tool(tool_name: str) -> Optional[str]:
    """判断是否为 MCP 工具，返回 service_name 或 None"""
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        if len(parts) >= 3 and parts[1] in MCP_REGISTRY:
            return parts[1]
    return None


def unload_all():
    global _mcp_config_loaded
    for _name, agent in MCP_REGISTRY.items():
        try:
            agent.cleanup()
        except Exception:
            pass
    MCP_REGISTRY.clear()
    MANIFEST_CACHE.clear()
    _all_mcp_tool_defs.clear()
    _disabled_mcp.clear()
    _mcp_config_loaded = False


def toggle_mcp_enabled(name: str) -> bool:
    """切换 MCP 启用状态，返回 True=已启用 False=已停用"""
    _ensure_mcp_config_loaded()
    if name in _disabled_mcp:
        _disabled_mcp.discard(name)
        _rebuild_tool_cache()
        save_mcp_config()
        try:
            from brain.capability_knowledge import invalidate_capability_knowledge_cache
            invalidate_capability_knowledge_cache()
        except Exception:
            pass
        return True
    else:
        _disabled_mcp.add(name)
        _rebuild_tool_cache()
        save_mcp_config()
        try:
            from brain.capability_knowledge import invalidate_capability_knowledge_cache
            invalidate_capability_knowledge_cache()
        except Exception:
            pass
        return False


def is_mcp_enabled(name: str) -> bool:
    _ensure_mcp_config_loaded()
    return name not in _disabled_mcp


def get_enabled_mcp_names() -> list:
    _ensure_mcp_config_loaded()
    return [n for n in MCP_REGISTRY if n not in _disabled_mcp]


def get_disabled_mcp_names() -> list:
    _ensure_mcp_config_loaded()
    return list(_disabled_mcp)
