"""统一联网路由：把用户排序、启停状态和实际搜索/抓取执行放在同一处。"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Callable

logger = logging.getLogger("NetworkRouter")


class _QuotaExceeded(RuntimeError):
    """供应商明确返回额度耗尽；是否继续降级由用户配置决定。"""

SEARCH_IDS = ("tavily", "zhihu", "builtin_search", "browser_search")
FETCH_IDS = ("firecrawl", "http", "jina", "stealth", "browser_fetch")

_META = {
    "tavily": {
        "name": "Tavily AI 搜索", "mcp": "tavily_search",
        "description": "面向实时信息的高质量 AI 搜索，适合优先检索。",
    },
    "zhihu": {
        "name": "知乎全网搜索", "mcp": "global_search",
        "description": "搜索知乎问答、文章与热榜，适合中文经验和社区讨论。",
    },
    "builtin_search": {
        "name": "内建聚合搜索", "builtin": "builtin_search",
        "description": "依次尝试百度、DuckDuckGo 与 Bing，是无需额外 API Key 的降级搜索源。",
    },
    "browser_search": {
        "name": "浏览器搜索", "builtin": "fetch_webpage_browser",
        "description": "通过 Playwright 渲染搜索结果，可靠但较慢、占用资源更多。",
    },
    "firecrawl": {
        "name": "Firecrawl 网页爬虫", "mcp": "firecrawl",
        "description": "将已有 URL 的网页提取为干净 Markdown；用于读网页，不是关键词搜索源。",
    },
    "http": {
        "name": "普通 HTTP 抓取", "builtin": "fetch_webpage",
        "description": "直连读取静态网页，速度最快，适合普通文章页。",
    },
    "jina": {
        "name": "Jina 网页中转", "builtin": "fetch_webpage_via_api",
        "description": "通过 Jina Reader 转换网页，适合普通抓取失败后的降级。",
    },
    "stealth": {
        "name": "TLS 伪装抓取", "builtin": "fetch_webpage_stealth",
        "description": "使用更接近真实浏览器的网络特征，适合遇到基础反爬的网站。",
    },
    "browser_fetch": {
        "name": "浏览器网页读取", "builtin": "fetch_webpage_browser",
        "description": "可渲染 JavaScript 的完整浏览器读取，最可靠但最慢。",
    },
}


def _mcp_enabled(name: str) -> bool:
    try:
        from brain.mcp.mcp_registry import is_mcp_enabled
        return is_mcp_enabled(name)
    except Exception:
        return True


def tool_state(tool_id: str) -> tuple[bool, str]:
    """返回可否参与路由及界面状态文本；配置缺失不等同于错误。"""
    from config import get_builtin_tool_config, get_firecrawl_config, get_tavily_config, get_zhihu_config

    meta = _META[tool_id]
    if not tool_user_enabled(tool_id):
        return False, "已停用"
    if tool_id == "tavily":
        if not _mcp_enabled(meta["mcp"]):
            return False, "能力中枢已停用"
        return (True, "可用") if get_tavily_config().get("api_key", "").strip() else (False, "未配置 API Key")
    if tool_id == "zhihu":
        if not _mcp_enabled(meta["mcp"]):
            return False, "能力中枢已停用"
        return (True, "可用") if get_zhihu_config().get("access_secret", "").strip() else (False, "未配置 Access Secret")
    if tool_id == "firecrawl":
        if not _mcp_enabled(meta["mcp"]):
            return False, "能力中枢已停用"
        return (True, "可用") if get_firecrawl_config().get("api_key", "").strip() else (False, "未配置 API Key")
    return True, "可用"


def tool_user_enabled(tool_id: str) -> bool:
    """返回用户主动开关，不把 API Key 缺失误判成“已关闭”。"""
    from config import get_builtin_tool_config
    meta = _META[tool_id]
    if "builtin" in meta:
        return bool(get_builtin_tool_config().get(meta["builtin"], True))
    return _mcp_enabled(meta["mcp"])


def set_tool_enabled(tool_id: str, enabled: bool) -> None:
    """写入各自的唯一状态源，并与能力中枢的 MCP 开关保持一致。"""
    from config import get_builtin_tool_config, save_builtin_tool_config
    meta = _META[tool_id]
    if "builtin" in meta:
        cfg = get_builtin_tool_config()
        cfg[meta["builtin"]] = bool(enabled)
        save_builtin_tool_config(cfg)
        return
    if _mcp_enabled(meta["mcp"]) != bool(enabled):
        from brain.mcp.mcp_registry import toggle_mcp_enabled
        toggle_mcp_enabled(meta["mcp"])


def tool_catalog(kind: str) -> list[dict]:
    """供设置界面读取的稳定网络能力目录。"""
    ids = SEARCH_IDS if kind == "search" else FETCH_IDS
    result = []
    for tool_id in ids:
        enabled, state = tool_state(tool_id)
        result.append({
            "id": tool_id, **_META[tool_id], "enabled": enabled,
            "checked": tool_user_enabled(tool_id), "state": state,
        })
    return result


def configure_tools(action: str = "status", kind: str = "search",
                    tool_id: str = "", position: int | None = None) -> str:
    """供模型和设置页共用的联网路由配置入口。"""
    from config import get_network_tool_order_config, save_network_tool_order_config

    action = str(action or "status").lower()
    kind = "fetch" if str(kind).lower() == "fetch" else "search"
    valid_ids = FETCH_IDS if kind == "fetch" else SEARCH_IDS

    if action in {"enable", "disable"}:
        if tool_id not in valid_ids:
            return f"配置失败：{tool_id or '未指定工具'} 不属于 {kind} 工具。"
        set_tool_enabled(tool_id, action == "enable")
    elif action == "move":
        if tool_id not in valid_ids:
            return f"配置失败：{tool_id or '未指定工具'} 不属于 {kind} 工具。"
        cfg = get_network_tool_order_config()
        key = f"{kind}_order"
        order = [item for item in cfg.get(key, []) if item in valid_ids and item != tool_id]
        order += [item for item in valid_ids if item not in order and item != tool_id]
        target = max(0, min(len(order), int(position or 0)))
        order.insert(target, tool_id)
        cfg[key] = order
        save_network_tool_order_config(cfg)
    elif action == "reset":
        cfg = get_network_tool_order_config()
        cfg["search_order"] = list(SEARCH_IDS)
        cfg["fetch_order"] = list(FETCH_IDS)
        save_network_tool_order_config(cfg)
    elif action != "status":
        return "配置失败：action 仅支持 status、enable、disable、move、reset。"

    lines = ["联网工具配置："]
    for route_kind in ("search", "fetch"):
        items = {item["id"]: item for item in tool_catalog(route_kind)}
        labels = []
        for number, item_id in enumerate(_ordered(route_kind), 1):
            item = items[item_id]
            labels.append(f"{number}. {item['name']}（{item['state']}）")
        lines.append(f"{route_kind}: " + "；".join(labels))
    return "\n".join(lines)


def _ordered(kind: str) -> list[str]:
    from config import get_network_tool_order_config
    allowed = SEARCH_IDS if kind == "search" else FETCH_IDS
    saved = get_network_tool_order_config().get(f"{kind}_order", [])
    return [item for item in saved if item in allowed] + [item for item in allowed if item not in saved]


def _retry_count() -> int:
    from config import get_network_tool_order_config
    return int(get_network_tool_order_config().get("retry_count", 2))


def _run_chain(kind: str, invoke: Callable[[str], str | None]) -> str:
    from config import get_network_tool_order_config
    attempts: list[str] = []
    for tool_id in _ordered(kind):
        available, reason = tool_state(tool_id)
        if not available:
            attempts.append(f"{_META[tool_id]['name']}（跳过：{reason}）")
            continue
        for number in range(_retry_count() + 1):
            try:
                result = invoke(tool_id)
            except _QuotaExceeded:
                attempts.append(f"{_META[tool_id]['name']}（额度耗尽）")
                if not get_network_tool_order_config().get("fallback_on_quota", True):
                    return "网络请求未成功：当前优先工具额度已耗尽，且已关闭自动换下一个工具。"
                break
            except Exception as exc:
                result = None
                logger.info("网络工具 %s 第 %s 次失败：%s", tool_id, number + 1, exc)
            if result:
                return result
            if number < _retry_count():
                time.sleep(min(0.4 * (number + 1), 1.0))
        attempts.append(_META[tool_id]["name"])
    return "网络请求未成功。已尝试：" + "、".join(attempts or ["没有已启用的网络工具"])


def search_web(query: str, max_results: int = 5) -> str:
    """按用户设定的搜索来源顺序检索。"""
    handlers = {
        "tavily": lambda: _search_tavily(query, max_results),
        "zhihu": lambda: _search_zhihu(query, max_results),
        "builtin_search": lambda: _search_builtin(query, max_results),
        "browser_search": lambda: _search_browser(query, max_results),
    }
    return _run_chain("search", lambda tool_id: handlers[tool_id]())


def fetch_url(url: str, max_length: int, legacy_handlers: dict[str, Callable[[str, int], str]]) -> str:
    """按用户设定的读取顺序抓取已有 URL；旧实现作为可靠适配器复用。"""
    handlers = {
        "firecrawl": lambda: _fetch_firecrawl(url, max_length),
        "http": lambda: _usable(legacy_handlers["http"](url, max_length)),
        "jina": lambda: _usable(legacy_handlers["jina"](url, max_length)),
        "stealth": lambda: _usable(legacy_handlers["stealth"](url, max_length)),
        "browser_fetch": lambda: _usable(legacy_handlers["browser"](url, max_length)),
    }
    return _run_chain("fetch", lambda tool_id: handlers[tool_id]())


def _usable(result: str) -> str | None:
    text = str(result or "")
    if text.startswith(("错误", "访问失败", "网络连接失败", "获取网页失败", "未能提取", "处理网页时出错")):
        return None
    return text


def _search_tavily(query: str, max_results: int) -> str | None:
    import requests
    from config import get_tavily_config
    response = requests.post("https://api.tavily.com/search", json={
        "query": query, "max_results": min(max_results, 10), "search_depth": "basic",
    }, headers={"Authorization": f"Bearer {get_tavily_config()['api_key']}"}, timeout=15)
    if response.status_code in (402, 429):
        raise _QuotaExceeded()
    if not response.ok:
        return None
    rows = response.json().get("results", [])
    return _format_results("Tavily", query, rows, "title", "url", "content") if rows else None


def _search_zhihu(query: str, max_results: int) -> str | None:
    import requests
    from config import get_zhihu_config
    response = requests.get("https://developer.zhihu.com/api/v1/content/global_search", params={
        "Query": query, "Count": min(max_results, 10),
    }, headers={"Authorization": f"Bearer {get_zhihu_config()['access_secret']}", "X-Request-Timestamp": str(int(time.time()))}, timeout=15)
    if response.status_code in (402, 429):
        raise _QuotaExceeded()
    if not response.ok:
        return None
    rows = response.json().get("data", [])
    return _format_results("知乎", query, rows, "title", "url", "excerpt") if rows else None


def _search_builtin(query: str, max_results: int) -> str | None:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return _search_bing(query, max_results)
    with DDGS() as client:
        rows = list(client.text(query, max_results=min(max_results, 10)))
    return _format_results("DuckDuckGo", query, rows, "title", "href", "body") if rows else _search_bing(query, max_results)


def _search_bing(query: str, max_results: int) -> str | None:
    import requests
    from bs4 import BeautifulSoup
    response = requests.get("https://www.bing.com/search", params={"q": query, "count": min(max_results, 10)}, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    rows = []
    for item in soup.select(".b_algo"):
        link = item.select_one("h2 a")
        if link:
            rows.append({"title": link.get_text(strip=True), "href": link.get("href", ""), "body": (item.select_one(".b_caption p") or {}).get_text(strip=True) if item.select_one(".b_caption p") else ""})
    return _format_results("Bing", query, rows, "title", "href", "body") if rows else None


def _search_browser(query: str, max_results: int) -> str | None:
    from brain.browser_controller import get_browser
    page = get_browser()._ensure_page()
    page.goto("https://www.bing.com/search?q=" + urllib.parse.quote(query), timeout=30000, wait_until="domcontentloaded")
    rows = page.evaluate("""() => Array.from(document.querySelectorAll('.b_algo')).slice(0, 10).map(x => ({title:x.querySelector('h2 a')?.textContent?.trim() || '', href:x.querySelector('h2 a')?.href || '', body:x.querySelector('.b_caption p')?.textContent?.trim() || ''}))""")
    return _format_results("浏览器", query, rows[:max_results], "title", "href", "body") if rows else None


def _format_results(source: str, query: str, rows: list[dict], title: str, url: str, body: str) -> str:
    lines = [
        f"[搜索证据｜来源={source}｜查询={query}]",
        "说明：以下是搜索结果摘要，不等同于网页正文；涉及精确数字或结论时请继续读取原始链接。",
    ]
    seen_urls: set[str] = set()
    index = 0
    for item in rows:
        raw_url = str(item.get(url, "") or "").strip()
        normalized = raw_url.split("#", 1)[0].rstrip("/").lower()
        if not raw_url or normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        index += 1
        domain = urllib.parse.urlparse(raw_url).netloc or "未知来源"
        published = item.get("published_date") or item.get("published") or item.get("date") or "未提供"
        lines.append(
            f"\n证据 {index}\n"
            f"标题：{item.get(title, '无标题')}\n"
            f"来源域名：{domain}\n发布日期：{published}\n"
            f"原始链接：{raw_url}\n摘要：{str(item.get(body, '') or '')[:500]}"
        )
    if index == 0:
        return ""
    return "\n".join(lines)


_SPA_SHELL_MARKERS = (
    "Skip to content",
    "You signed in with another tab",
    "We read every piece of feedback",
    "Sign in",
    "Loading...",
)


def _is_spa_shell(markdown: str) -> bool:
    """检测 Firecrawl 返回的是否仅为 SPA 外壳（导航栏等），无实质内容。"""
    if not markdown or len(markdown.strip()) < 80:
        return True
    lines = [l.strip() for l in markdown.splitlines() if l.strip()]
    shell_count = sum(1 for l in lines if any(m in l for m in _SPA_SHELL_MARKERS))
    return shell_count >= 2 and len(lines) <= 20


def _fetch_firecrawl(url: str, max_length: int) -> str | None:
    import requests
    from config import get_firecrawl_config
    response = requests.post("https://api.firecrawl.dev/v1/scrape", json={"url": url, "formats": ["markdown"]}, headers={"Authorization": f"Bearer {get_firecrawl_config()['api_key']}"}, timeout=30)
    if response.status_code in (402, 429):
        raise _QuotaExceeded()
    if not response.ok:
        return None
    payload = response.json()
    markdown = str((payload.get("data") or {}).get("markdown") or "")
    if not markdown:
        return None
    if _is_spa_shell(markdown):
        logger.info("Firecrawl 返回 SPA 外壳（%d 行），视为空结果以触发降级", len(markdown.splitlines()))
        # GitHub 仓库页面常被 SPA 外壳包裹，自动降级到 raw README 重试一次。
        import re as _re
        _GITHUB_REPO_RE = _re.compile(r"https?://github\.com/([^/]+/[^/]+)(?:/.*)?$")
        _m = _GITHUB_REPO_RE.match(url.split("?")[0].split("#")[0])
        if _m:
            _repo = _m.group(1)
            for _branch in ("main", "master"):
                _raw_url = f"https://raw.githubusercontent.com/{_repo}/{_branch}/README.md"
                try:
                    _raw_resp = requests.get(_raw_url, timeout=10)
                    if _raw_resp.ok and len(_raw_resp.text.strip()) > 80:
                        return "[Firecrawl Markdown]\n\n" + _raw_resp.text[:max_length]
                except Exception:
                    pass
        return None
    return "[Firecrawl Markdown]\n\n" + markdown[:max_length]