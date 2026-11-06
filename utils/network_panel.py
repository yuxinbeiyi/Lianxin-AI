import copy

import config
from utils.config_panel_store import _load, _write, _lock, merge, public, revision, PanelConflict


def state():
    from brain.network_router import tool_catalog, SEARCH_IDS, FETCH_IDS
    result = public("network", _load())
    for kind, defaults in (("search", SEARCH_IDS), ("fetch", FETCH_IDS)):
        order = result["sections"]["network_tool_order"][f"{kind}_order"]
        result["sections"]["network_tool_order"][f"{kind}_order"] = list(dict.fromkeys([item for item in order if item in defaults] + list(defaults)))
    result["catalog"] = {kind: tool_catalog(kind) for kind in ("search", "fetch")}
    return result


def _switches(values):
    from brain.network_router import SEARCH_IDS, FETCH_IDS
    if not isinstance(values, dict) or set(values) - set((*SEARCH_IDS, *FETCH_IDS)) or any(type(value) is not bool for value in values.values()):
        raise ValueError("联网启用配置无效")
    if "browser_search" in values and "browser_fetch" in values and values["browser_search"] != values["browser_fetch"]:
        raise ValueError("两项浏览器工具共用同一启用源，不能设置相反状态")


def request(action, payload):
    from brain.network_router import set_tool_enabled, SEARCH_IDS, FETCH_IDS
    with _lock:
        if action == "state":
            return state()
        full = _load()
        if action == "reset":
            if payload.get("confirmed") is not True:
                raise ValueError("恢复默认需要确认")
            if payload.get("revision") != revision("network", full):
                raise PanelConflict("联网配置已变化，请重新加载")
            updated = copy.deepcopy(full)
            updated["network_tool_order"] = {**full.get("network_tool_order", {}), **config._NETWORK_TOOL_ORDER_DEFAULTS}
            switches = {name: True for name in (*SEARCH_IDS, *FETCH_IDS)}
        elif action == "save":
            updated = merge("network", full, payload)
            switches = payload.get("switches", {})
        else:
            raise LookupError("未知网络设置动作")
        _switches(switches)
        _write(updated)
        for name, enabled in switches.items():
            set_tool_enabled(name, enabled)
        from brain.capability_knowledge import invalidate_capability_knowledge_cache
        invalidate_capability_knowledge_cache()
        return state()
