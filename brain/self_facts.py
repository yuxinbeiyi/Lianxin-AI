"""莲心的自我事实卡：关于“她自己”的客观事实，优先于长期记忆检索。

背景：用户问“帮我打开你的 github 仓库网页”时，莲心回答“我没有自己的 GitHub 仓库”，
而她自己的长期记忆里存着该链接。根因是自指事实既没有专属通道，也会被语义阈值
挡在门外。本模块提供一张受信的结构化事实表：自指问题先查这张卡，命中即用。

设计约束：
- 只做本地 JSON 存取与关键词匹配，不加载任何模型；
- 任何失败都必须静默降级，不能影响正常对话；
- 事实来源可追溯（source 字段）。
"""

from __future__ import annotations

import json
import logging
import platform
import re
import threading
from pathlib import Path

logger = logging.getLogger("SelfFacts")

_LOCK = threading.RLock()
_CACHE: "dict[str, dict] | None" = None
_FILE_NAME = "self_facts.json"

# ── 自指信号：问题在问“莲心自己”────────────────────────
_SELF_REFERENCE_RE = re.compile(
    r"(?:你自己|你的|你(?:有|是|叫|住|跑|用|的)|莲心(?:自己|的)|我是谁|你是谁)"
)

# ── 键 → 检索别名（用于把问题里的说法映射到事实键）──────
_KEY_ALIASES: "dict[str, tuple[str, ...]]" = {
    "self.name": ("名字", "叫什么", "名称", "称呼", "你是谁", "自我"),
    "self.owner": ("主人", "用户", "谁在养", "归属", "谁在用"),
    "self.runtime": ("跑在哪", "运行环境", "装在哪", "什么设备", "哪里运行", "本地", "机器"),
    "self.project_repo": ("github", "仓库", "repo", "项目地址", "开源", "代码仓库", "链接", "网址"),
    "self.version": ("版本", "version", "几版"),
}

_DISPLAY = {
    "self.name": "莲心的名字",
    "self.owner": "莲心的主人",
    "self.runtime": "莲心的运行环境",
    "self.project_repo": "莲心的项目仓库",
    "self.version": "莲心版本",
}


def _facts_path() -> Path:
    from utils.paths import get_user_data_dir
    return Path(get_user_data_dir()) / _FILE_NAME


def _read_file() -> dict:
    try:
        path = _facts_path()
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("self_facts 读取失败: %s", exc)
        return {}


def _write_file(data: dict) -> None:
    try:
        path = _facts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("self_facts 写入失败: %s", exc)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _detect_version() -> str:
    try:
        text = (_repo_root() / "CHANGELOG.md").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^##\s*\[([^\]]+)\]", text, re.MULTILINE)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return ""


def _detect_persona_name() -> str:
    try:
        from brain.persona import get_persona_manager
        snapshot = get_persona_manager().get_snapshot()
        name = str(getattr(getattr(snapshot, "profile", None), "assistant_name", "") or "").strip()
        if name:
            return name
    except Exception:
        pass
    return "莲心"


def _detect_owner() -> str:
    try:
        from config import get_user_name
        return str(get_user_name() or "").strip()
    except Exception:
        return ""


def _runtime_text() -> str:
    try:
        system = platform.system() or "未知系统"
        release = platform.release() or ""
        machine = platform.machine() or ""
        py = platform.python_version()
        parts = [part for part in (f"{system} {release}".strip(), machine) if part]
        return f"{' / '.join(parts)} · Python {py} · 本地部署（莲心跑在用户自己的机器上）"
    except Exception:
        return "本地部署"


def _default_facts() -> dict:
    """可推导的缺省事实；用户自定义值不会被覆盖。"""
    facts: dict = {}
    name = _detect_persona_name()
    if name:
        facts["self.name"] = {
            "value": name,
            "display": _DISPLAY["self.name"],
            "source": "人格档案",
            "confirmed_at": "",
        }
    owner = _detect_owner()
    if owner:
        facts["self.owner"] = {
            "value": owner,
            "display": _DISPLAY["self.owner"],
            "source": "用户配置",
            "confirmed_at": "",
        }
    facts["self.runtime"] = {
        "value": _runtime_text(),
        "display": _DISPLAY["self.runtime"],
        "source": "运行环境探测",
        "confirmed_at": "",
    }
    version = _detect_version()
    if version:
        facts["self.version"] = {
            "value": version,
            "display": _DISPLAY["self.version"],
            "source": "CHANGELOG.md",
            "confirmed_at": "",
        }
    return facts


def _scan_memory_for_repo(max_scan: int = 40) -> "dict | None":
    """从长期记忆里提升“莲心项目仓库”这类带 URL 的自指事实。

    只接受 github/仓库 关键词附近出现的 URL，避免把任意链接当成自我事实。
    """
    try:
        from brain.graph_memory import _get_conn
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, content, updated_at FROM memory_facts "
            "WHERE status='active' AND (content LIKE '%github%' OR content LIKE '%GitHub%' "
            "OR content LIKE '%仓库%' OR content LIKE '%repo%') "
            "ORDER BY updated_at DESC LIMIT ?",
            (max(1, int(max_scan)),),
        ).fetchall()
    except Exception as exc:
        logger.debug("自我事实记忆扫描不可用: %s", exc)
        return None

    preferred = None
    fallback = None
    for row in rows:
        content = str(row["content"] or "")
        match = re.search(
            r"(?:github|GitHub|仓库|项目|repo|开源)[^\n]{0,24}?(https?://[^\s，。；、）)\]]+)",
            content,
            re.IGNORECASE,
        )
        if not match:
            continue
        url = match.group(1).rstrip(".,;:!?")
        record = {
            "value": url,
            "display": _DISPLAY["self.project_repo"],
            "source": f"记忆#{row['id']}",
            "confirmed_at": str(row["updated_at"] or ""),
        }
        if "lianxin" in url.lower() and preferred is None:
            preferred = record
        if fallback is None:
            fallback = record
    return preferred or fallback


def get_facts(refresh: bool = False) -> dict:
    """返回自我事实表（缓存 + 缺省补齐 + 一次性记忆提升）。"""
    global _CACHE
    with _LOCK:
        if _CACHE is not None and not refresh:
            return _CACHE
        data = _read_file()
        defaults = _default_facts()
        changed = False
        for key, value in defaults.items():
            if not data.get(key, {}).get("value"):
                data[key] = value
                changed = True
        if not data.get("self.project_repo", {}).get("value"):
            promoted = _scan_memory_for_repo()
            if promoted:
                data["self.project_repo"] = promoted
                changed = True
        if changed:
            _write_file(data)
        _CACHE = data
        return data


def remember_self_fact(key: str, value: str, *, source: str = "", confirmed_at: str = "") -> dict:
    """写入/更新一条自我事实（供后续“记住这是我的仓库”之类的路径调用）。"""
    global _CACHE
    key = str(key or "").strip()
    value = str(value or "").strip()
    if not key or not value:
        return {}
    with _LOCK:
        data = get_facts()
        record = {
            "value": value,
            "display": _DISPLAY.get(key, key),
            "source": str(source or "手动"),
            "confirmed_at": str(confirmed_at or ""),
        }
        data[key] = record
        _write_file(data)
        _CACHE = data
        return record


def is_self_reference(text: str) -> bool:
    return bool(_SELF_REFERENCE_RE.search(str(text or "")))


def lookup_self_fact(text: str, limit: int = 3) -> "list[dict]":
    """按关键词匹配自我事实；自指问题或命中别名时返回。"""
    value = str(text or "").strip()
    if not value:
        return []
    lowered = value.lower()
    self_reference = is_self_reference(value)
    facts = get_facts()
    hits: list[dict] = []
    for key, record in facts.items():
        fact_value = str(record.get("value") or "").strip()
        if not fact_value:
            continue
        aliases = _KEY_ALIASES.get(key, ())
        matched = any(alias.lower() in lowered for alias in aliases)
        # 项目仓库这类事实必须同时具备自指，避免用户问别人仓库时误命中。
        if key == "self.project_repo" and not self_reference:
            matched = False
        if matched:
            hits.append({
                "key": key,
                "display": record.get("display") or _DISPLAY.get(key, key),
                "value": fact_value,
                "source": record.get("source") or "",
                "confirmed_at": record.get("confirmed_at") or "",
            })
    return hits[: max(1, int(limit))]


def format_self_fact_context(hits: "list[dict]") -> str:
    if not hits:
        return ""
    lines = ["【自我事实】（关于莲心自己的受信事实，优先于普通记忆）"]
    for hit in hits:
        source = hit.get("source") or ""
        suffix = f"（来源：{source}）" if source else ""
        lines.append(f"· {hit.get('display', '')}：{hit.get('value', '')}{suffix}")
    lines.append("上述事实属于你自己，可直接据此回答或执行操作；不得声称“我没有”或“不知道”。")
    return "\n".join(lines)
