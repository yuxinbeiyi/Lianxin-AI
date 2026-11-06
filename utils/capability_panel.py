from collections import deque
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import threading

from brain.browser_security import redact_browser_text, redact_url


def _name(payload):
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise ValueError("能力或扩展名称无效")
    return name


def _confirmed(payload):
    if payload.get("confirmed") is not True:
        raise ValueError("此操作需要明确确认")


def _plugin_path(path, root):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root) or not path.is_dir():
        raise ValueError("插件目录不在允许范围内，未执行操作")
    return path


def _summary(summary):
    return {**asdict(summary), "success_rate": summary.success_rate, "avg_duration_ms": summary.avg_duration_ms}


def _tools(definitions):
    return [item.get("function", {}) for item in definitions if isinstance(item, dict) and isinstance(item.get("function"), dict)]


class CapabilityPanelService:
    def __init__(self):
        self._lock = threading.RLock()
        self._logs = deque(maxlen=500)
        self._install_logs = deque(maxlen=200)
        self._last_refresh = ""
        self._observed_extensions = {}
        self._log("读取后端共享能力注册表；不会因打开窗口自动安装或执行工具")

    def _log(self, message, level="info", install=False):
        row = {"timestamp": datetime.now().isoformat(timespec="seconds"), "level": level,
               "message": redact_browser_text(message, max_chars=2000)}
        self._logs.append(row)
        if install:
            self._install_logs.append(row)

    def state(self):
        from brain.capability_catalog import CATEGORY_ORDER, list_capabilities
        from brain import skill_manager as skills
        from brain.mcp import mcp_registry as mcp
        from brain.mcp.mcp_manager import get_mcp_manager
        from brain.tool_usage import get_tool_usage_store
        from config import get_browser_config

        capabilities = list_capabilities(include_disabled=True)
        summaries = get_tool_usage_store().summaries(item.name for item in capabilities)
        skill_items = []
        for name, info in list(skills._skill_registry.items()):
            enabled = name in skills._active_skills
            if name == "浏览器自动化":
                enabled = enabled and bool(get_browser_config().get("enabled", True))
            skill_items.append({"name": name, "display_name": name, "description": info.get("description", ""),
                                "version": info.get("version", ""), "license": info.get("license", ""),
                                "enabled": enabled, "tools": _tools(info.get("tool_definitions", []))})
        mcp_items = []
        for name in dict.fromkeys([*mcp.MCP_REGISTRY, *mcp.MANIFEST_CACHE]):
            agent = mcp.MCP_REGISTRY.get(name)
            manifest = mcp.MANIFEST_CACHE.get(name, {})
            definitions = []
            error = ""
            try:
                if agent is None:
                    error = "服务清单已发现，但未成功加载；请检查依赖、凭据或连接后刷新"
                else:
                    definitions = agent.get_tool_definitions_openai()
            except Exception as exc:
                error = f"工具定义读取失败（{type(exc).__name__}）"
            external = manifest.get("agentType") == "external"
            mcp_items.append({"name": name, "display_name": manifest.get("displayName", name),
                              "description": manifest.get("description", getattr(agent, "description", "")),
                              "version": manifest.get("version", ""), "external": external,
                              "connected": bool(getattr(agent, "_connected", False)) if external else agent is not None,
                              "enabled": mcp.is_mcp_enabled(name), "tools": _tools(definitions), "error": error})
        ready = getattr(get_mcp_manager(), "_scan_ready", None)
        for kind, items in (("Skill", skill_items), ("MCP", mcp_items)):
            for item in items:
                status = (item["enabled"], item.get("connected"), len(item["tools"]), item.get("error", ""))
                key = (kind, item["name"])
                if self._observed_extensions.get(key) != status:
                    self._observed_extensions[key] = status
                    detail = item.get("error") or f"{'启用' if item['enabled'] else '停用'}，{len(item['tools'])} 个工具定义"
                    self._log(f"{kind} {item['display_name']}：{detail}", "error" if item.get("error") else "info")
        return {"items": [{**asdict(item), "status": item.status, "usage": _summary(summaries[item.name])} for item in capabilities],
                "skills": skill_items, "mcp": mcp_items, "categories": list(CATEGORY_ORDER),
                "mcpScanning": ready is not None and not ready.is_set(), "lastRefresh": self._last_refresh,
                "logs": list(self._logs), "installLogs": list(self._install_logs)}

    def request(self, action, payload):
        if not isinstance(payload, dict):
            raise ValueError("请求格式错误")
        with self._lock:
            try:
                return self._request(action, payload)
            except Exception as error:
                self._log(f"{action} 失败：{type(error).__name__} · {error}", "error", install=action in ("inspect", "install"))
                raise

    def _request(self, action, payload):
        from brain import skill_manager as skills
        from brain.mcp import mcp_registry as mcp
        from brain.capability_catalog import get_capability, toggle_favorite
        from brain.tool_usage import get_tool_usage_store

        if action == "state":
            return self.state()
        if action == "usage":
            from brain.capability_catalog import list_capabilities
            days = payload.get("days", 30)
            if days is not None and (type(days) is not int or days not in (1, 7, 30)):
                raise ValueError("统计范围无效")
            descriptors = {item.name: item for item in list_capabilities()}
            store = get_tool_usage_store()
            summaries = store.summaries(descriptors, days=days)
            rows = [{**_summary(value), "display_name": descriptors[name].display_name if name in descriptors else name,
                     "provider_name": descriptors[name].provider_name if name in descriptors else "未知来源"}
                    for name, value in summaries.items() if value.call_count]
            return {"overview": store.overview(days=days), "unused": sum(not item.call_count for item in summaries.values()),
                    "items": sorted(rows, key=lambda item: (-item["call_count"], item["tool_name"]))}
        if action == "browser-log":
            from brain.browser_task_log import read_recent
            keys = ("timestamp", "task_id", "event", "step", "tool", "url", "snapshot_id", "duration_ms", "error_code", "result_preview", "reason")
            return {"items": [{key: redact_browser_text(redact_url(str(row.get(key, ""))), max_chars=2000) for key in keys}
                              for row in read_recent(120)]}
        if action == "logs-clear":
            target = payload.get("target")
            if target not in ("global", "install"):
                raise ValueError("日志类型无效")
            (self._logs if target == "global" else self._install_logs).clear()
            return self.state()
        if action == "usage-reset":
            _confirmed(payload)
            get_tool_usage_store().reset()
            self._log("已清空全部本地工具使用统计")
            return self.state()
        if action == "favorite":
            name = _name(payload)
            if get_capability(name) is None:
                raise LookupError("能力不存在，请刷新")
            toggle_favorite(name)
            return self.state()
        if action == "toggle":
            name = _name(payload)
            enabled = payload.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("启用状态必须为布尔值")
            kind = payload.get("kind")
            if kind == "capability":
                item = get_capability(name)
                if item is None:
                    raise LookupError("能力不存在，请刷新")
                kind, name = item.source_kind, item.name if item.source_kind == "builtin" else item.provider_id
            if kind == "builtin":
                item = get_capability(name)
                if item is None or item.source_kind != "builtin":
                    raise LookupError("内置能力不存在")
                from config import get_builtin_tool_config, save_builtin_tool_config
                settings = get_builtin_tool_config()
                settings[name] = enabled
                save_builtin_tool_config(settings)
            elif kind == "skill":
                if name not in skills._skill_registry:
                    raise LookupError("Skill不存在，请刷新")
                message = skills.activate_skill(name) if enabled else skills.deactivate_skill(name)
                if (name in skills._active_skills) != enabled:
                    raise RuntimeError(message)
                skills.save_skill_config()
                if name == "浏览器自动化":
                    from config import get_browser_config, save_browser_config
                    settings = get_browser_config()
                    settings["enabled"] = enabled
                    save_browser_config(settings)
            elif kind == "mcp":
                if name not in mcp.MCP_REGISTRY and name not in mcp.MANIFEST_CACHE:
                    raise LookupError("MCP服务不存在，请刷新")
                if mcp.is_mcp_enabled(name) != enabled:
                    mcp.toggle_mcp_enabled(name)
            else:
                raise ValueError("能力来源无效")
            self._log(f"{name} 已{'启用' if enabled else '停用'}")
        elif action == "refresh":
            _confirmed(payload)
            from brain.mcp.mcp_manager import get_mcp_manager
            ready = getattr(get_mcp_manager(), "_scan_ready", None)
            if ready is not None and not ready.is_set():
                raise ValueError("启动扫描尚未完成，请稍后刷新")
            self._log("开始重新发现Skills并扫描MCP；该操作可能重新连接服务")
            skills.discover_skills()
            skills.activate_all_skills()
            mcp.unload_all()
            mcp.scan_mcp_services()
            self._last_refresh = datetime.now().isoformat(timespec="seconds")
            self._log(f"扫描完成：Skills {len(skills._skill_registry)}，MCP {len(mcp.MCP_REGISTRY)}；未加载的服务请检查清单与依赖")
        elif action == "mcp-test":
            name = _name(payload)
            agent = mcp.MCP_REGISTRY.get(name)
            if agent is None:
                raise LookupError("MCP服务不存在，请刷新")
            if mcp.MANIFEST_CACHE.get(name, {}).get("agentType") == "external":
                if not agent.connect_sync():
                    raise RuntimeError("外部MCP连接失败，请检查服务与配置")
                process = getattr(agent, "_process", None)
                if process is not None and process.poll() is not None:
                    raise RuntimeError("MCP进程已退出，请刷新重新连接")
            if hasattr(agent, "list_tools"):
                tools = list(agent.list_tools())
            elif hasattr(agent, "get_tool_definitions_openai"):
                tools = agent.get_tool_definitions_openai()
            else:
                tools = getattr(agent, "_tools", None)
            if tools is None:
                raise RuntimeError("服务已加载，但无法读取工具定义")
            self._log(f"MCP {name} 检查通过：{len(tools)} 个工具定义；未执行任何业务工具", "ok")
        elif action == "inspect":
            return self.inspect(payload)
        elif action == "install":
            _confirmed(payload)
            from brain.plugin_installer import install_plugin
            info = self.inspect(payload)
            if info["type"] not in ("skill", "mcp"):
                raise ValueError("无法识别插件类型")
            if info["name"] != payload.get("expectedName") or info["type"] != payload.get("expectedType"):
                raise ValueError("插件信息已变化，请重新识别后确认")
            success, message = install_plugin(info["source"])
            self._log(message, "ok" if success else "error", install=True)
            if not success:
                raise ValueError(message)
            self._log("文件已安装并请求热加载；是否可用以能力库和扩展状态为准", install=True)
        elif action == "uninstall":
            _confirmed(payload)
            name, kind = _name(payload), payload.get("kind")
            if kind == "skill":
                info = skills._skill_registry.get(name)
                if info is None:
                    raise LookupError("Skill已不存在")
                _plugin_path(info["path"], skills._SKILLS_DIR)
                success, message = skills.uninstall_skill(name)
                if success:
                    skills.save_skill_config()
            elif kind == "mcp":
                from brain.plugin_installer import uninstall_plugin
                if name not in mcp.MCP_REGISTRY and name not in mcp.MANIFEST_CACHE:
                    raise LookupError("MCP服务已不存在")
                folders = []
                for manifest in mcp._MCP_DIR.glob("*/mcp-manifest.json"):
                    try:
                        service_name = json.loads(manifest.read_text(encoding="utf-8")).get("name")
                    except (OSError, ValueError, AttributeError):
                        continue
                    if service_name == name:
                        folders.append(_plugin_path(manifest.parent, mcp._MCP_DIR))
                if len(folders) != 1:
                    raise ValueError("无法唯一确认MCP目录，未执行删除")
                success, message = uninstall_plugin(folders[0].name, "mcp")
                if success:
                    removed = mcp.MCP_REGISTRY.pop(name, None)
                    if removed is not None:
                        removed.cleanup()
                    mcp.MANIFEST_CACHE.pop(name, None)
                    mcp._rebuild_tool_cache()
            else:
                raise ValueError("只能卸载已发现的Skill或MCP")
            if not success:
                raise RuntimeError(message)
            self._log(message)
        elif action == "save":
            mcp._ensure_mcp_config_loaded()
            skills.save_skill_config()
            mcp.save_mcp_config()
        else:
            raise LookupError("未知能力中枢操作")
        from brain.capability_knowledge import invalidate_capability_knowledge_cache
        invalidate_capability_knowledge_cache()
        return self.state()

    def inspect(self, payload):
        from brain.plugin_installer import get_plugin_info, _SKILLS_DIR, _MCP_DIR
        source = payload.get("source")
        if not isinstance(source, str) or not source.strip() or len(source) > 4096:
            raise ValueError("请选择插件目录")
        path = Path(source.strip()).resolve()
        if not path.is_dir():
            raise ValueError("插件目录不存在")
        info = get_plugin_info(str(path))
        effective = Path(info.get("_source_path", str(path))).resolve()
        if info["type"] == "mcp" and not (effective / "mcp-manifest.json").exists():
            children = list(effective.glob("*/mcp-manifest.json"))
            if len(children) == 1:
                effective = children[0].parent.resolve()
                info = get_plugin_info(str(effective))
        target_root = _SKILLS_DIR if info["type"] == "skill" else _MCP_DIR
        if Path(target_root).resolve().is_relative_to(effective):
            raise ValueError("不能把插件安装目录的祖先目录作为源目录")
        return {"type": info["type"], "name": info["name"], "description": info.get("description", ""),
                "version": info.get("version", ""), "source": str(effective)}


_SERVICE = CapabilityPanelService()


def capability_panel_request(action, payload):
    return _SERVICE.request(action, payload)
