from utils.config_panel_store import _load, _lock, sections, FIELDS, SECRETS, merge


def request(domain, action, payload, bridge):
    if domain not in FIELDS or not isinstance(payload, dict):
        raise ValueError("配置请求格式错误")
    if action == "help":
        import webbrowser
        links = {"stt": {"volcano": "https://console.volcengine.com/asr", "aliyun-key": "https://ram.console.aliyun.com/manage/ak", "aliyun-project": "https://nls-portal.console.aliyun.com/"}, "network": {"tavily": "https://tavily.com/", "firecrawl": "https://firecrawl.org.cn/", "zhihu": "https://developer.zhihu.com/"}}
        url = links.get(domain, {}).get(payload.get("name"))
        if not url:
            raise ValueError("帮助入口不存在")
        if not webbrowser.open(url):
            raise RuntimeError("无法打开系统浏览器")
        return {"opened": True}
    if action == "reveal":
        identifier = payload.get("field", "")
        if not isinstance(identifier, str) or identifier not in SECRETS or identifier.split(".")[0] not in FIELDS[domain]:
            raise ValueError("凭据字段无效")
        with _lock:
            full = _load()
            merge(domain, full, {"revision": payload.get("revision")})
            name, field = identifier.split(".")
            return {"value": sections(domain, full)[name].get(field, "")}
    if domain == "stt":
        from utils.stt_panel import request as dispatch
        return dispatch(action, payload, bridge)
    if domain == "sound":
        from utils.sound_panel import request as dispatch
        return dispatch(action, payload, bridge)
    from utils.network_panel import request as dispatch
    return dispatch(action, payload)
