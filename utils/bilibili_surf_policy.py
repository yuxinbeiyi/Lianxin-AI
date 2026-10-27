"""Pure search policy and privacy-bounded local surf diagnostics."""

import json
import re
import threading
import time
import unicodedata
import uuid

from utils.paths import get_user_data_dir

SKIP_REASONS = {"off_topic", "insufficient_evidence", "low_value"}
REASON_MESSAGES = {
    "off_topic": "这轮搜索结果与兴趣主题不够相关，莲心没有评论。",
    "insufficient_evidence": "找到相关视频，但没有足够信息支持具体评论。",
    "low_value": "这轮没有找到值得占用你一次注意力的视频，莲心没有评论。",
    "duplicate_only": "这轮结果都已经推荐过，没有重复评论。",
    "empty_results": "这轮搜索没有返回视频，没有发送评论。",
    "search_error": "这轮视频搜索服务异常，没有发送评论。",
    "material_error": "这轮视频资料读取失败，没有发送评论。",
    "model_error": "这轮模型处理发生异常，没有发送评论。",
    "model_rate_limited": "模型服务暂时繁忙，这轮先不评论，稍后再试。",
    "model_quota_error": "模型服务提示额度不足，这轮没有评论，请检查接口额度。",
    "model_channel_error": "当前模型服务暂不可用，这轮没有评论，请检查模型渠道。",
    "model_auth_error": "模型接口授权未通过，这轮没有评论，请检查接口配置。",
    "model_timeout": "模型这次响应超时了，这轮先不评论。",
    "model_cancelled": "这轮冲浪已停止，没有发送评论。",
    "model_output_truncated": "这轮模型输出达到长度上限，未获得完整结果，没有发送评论。",
    "model_empty_output": "这轮模型没有返回有效正文，没有发送评论。",
    "invalid_output": "这轮筛选结果未通过校验，没有发送评论。",
    "cooldown": "搜索请求间隔未到，稍后再去 B 站看看。",
    "tags_paused": "兴趣标签目前都暂停了，先启用一个标签再去 B 站看看。",
    "selected": "已选中一个相关视频。",
    "run_error": "这轮冲浪发生技术异常，未继续发送评论。",
}
_DIAGNOSTIC_LOCK = threading.RLock()


def validate_text(value, name, maximum, allow_empty=True):
    if not isinstance(value, str):
        raise ValueError(f"{name}必须是文本")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"{name}不能包含控制字符")
    value = " ".join(value.split())
    if len(value) > maximum or (not value and not allow_empty):
        raise ValueError(f"{name}长度应为 {'0' if allow_empty else '1'}–{maximum} 个字符")
    return value


def normalize_search_query(value, fallback=""):
    query = validate_text(value, "搜索词", 80) or validate_text(fallback, "标签", 80)
    if re.search(r"(?:https?://|[a-z]:[\\/]|\\\\|(?:^|\s)[/~]|\buid\s*[:=]|\b\d{8,}\b|\S+@\S+\.\S+)", query, re.I):
        raise ValueError("搜索词应为公开兴趣词，不能包含账号 UID、链接、本地路径或联系方式")
    return query


def candidate_score(video, query, tag="", intent_note=""):
    terms = list(dict.fromkeys(re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", (query + " " + intent_note).casefold())))
    terms = [term for term in terms if len(term) > 1 or not term.isascii()]
    if tag and tag.casefold() not in terms:
        terms.append(tag.casefold())
    title = str(video.get("title") or "").casefold()
    tags = video.get("tags") or []
    tags = tags if isinstance(tags, str) else " ".join(map(str, tags))
    metadata = (str(video.get("description") or video.get("summary") or "") + " " + tags).casefold()
    def hit(term, text):
        if term.isascii():
            return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text))
        return term in text
    return sum((1 if term.isascii() and len(term) <= 2 else 4) * hit(term, title) + hit(term, metadata) for term in terms)


def popularity_count(value):
    if isinstance(value, bool):
        return 0
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([万亿]?)\s*", str(value or "").replace(",", ""))
    if not match:
        return 0
    try:
        return max(0, int(float(match.group(1)) * {"": 1, "万": 10000, "亿": 100000000}[match.group(2)]))
    except (ValueError, OverflowError):
        return 0


def rank_candidates(candidates, limit=1):
    ranked = []
    for rank, video in enumerate(candidates[:3], 1):
        item = dict(video)
        item["search_rank"] = item.get("search_rank", rank)
        ranked.append(item)
    return sorted(ranked, key=lambda item: (-popularity_count(item.get("play_count")),
        -popularity_count(item.get("like_count")), item["search_rank"]))[:max(0, min(limit, 1))]


def parse_selection(raw, candidates):
    invalid = {"status": "invalid_output", "reason": "invalid_output", "video": None}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return invalid
    if not isinstance(data, dict) or set(data) != {"decision", "bvid", "reason_code", "reason"}:
        return invalid
    decision, bvid, reason = data["decision"], data["bvid"], data["reason_code"]
    if not all(isinstance(value, str) for value in (decision, bvid, reason, data["reason"])):
        return invalid
    if len(data["reason"]) > 160 or any(unicodedata.category(char).startswith("C") for char in data["reason"]):
        return invalid
    if decision == "skip" and reason in SKIP_REASONS and bvid == "":
        return {"status": "skipped", "reason": reason, "video": None}
    if decision != "select" or reason != "selected" or not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
        return invalid
    video = next((item for item in candidates if item.get("bvid") == bvid), None)
    return {"status": "selected", "reason": reason, "video": video} if video else invalid


def parse_comment_decision(raw, video, mention_name):
    invalid = {"status": "invalid_output", "reason": "invalid_output", "video": None, "comment": ""}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return invalid
    if not isinstance(data, dict) or set(data) != {"decision", "bvid", "reason_code", "reason", "comment"}:
        return invalid
    comment = data.pop("comment")
    if not isinstance(comment, str):
        return invalid
    result = parse_selection(json.dumps(data, ensure_ascii=False), [video])
    if result["status"] == "skipped":
        return {**result, "comment": ""} if comment == "" else invalid
    if result["status"] != "selected" or not comment.strip():
        return invalid
    if any(unicodedata.category(char).startswith("C") and char not in "\n\r\t" for char in comment):
        return invalid
    comment = " ".join(comment.split())
    if (not mention_name or not comment.startswith(f"@{mention_name} ")
            or not comment[len(mention_name)+2:].strip()
            or re.search(r"cookie|sessdata|bili_jct|\buid\s*=|sk-[a-z0-9_-]+", comment, re.I)):
        return invalid
    return {**result, "comment": comment}


class SurfDiagnostics:
    def __init__(self, path=None, limit=100):
        self.path = path or (get_user_data_dir() / "bilibili_surf_diagnostics.json")
        self.limit = max(1, min(limit, 100))

    @staticmethod
    def _safe(record):
        safe = {"run_id": str(record.get("run_id") or uuid.uuid4().hex)[:64],
                "timestamp": record.get("timestamp", time.time())}
        for key, maximum in (("tag", 80), ("query", 80), ("query_source", 16),
                             ("selection_status", 32), ("reason", 40),
                             ("send_status", 48), ("share_status", 32), ("error_type", 80)):
            safe[key] = str(record.get(key) or "")[:maximum]
        safe["summary"] = REASON_MESSAGES.get(safe["reason"], "本轮已结束，请查看发送状态。")
        for key in ("returned_count", "fresh_count", "enriched_count", "duration_ms"):
            value = record.get(key, 0)
            safe[key] = max(0, int(value)) if isinstance(value, (int, float)) else 0
        safe["stage_ms"] = {key: max(0, int(value)) for key, value in record.get("stage_ms", {}).items()
                            if key in {"search", "material", "selection", "comment", "send", "share"} and isinstance(value, (int, float))}
        safe["model_errors"] = []
        from utils.bilibili_surf_model import safe_label
        for item in record.get("model_errors", [])[-4:]:
            if not isinstance(item, dict):
                continue
            entry = {key: safe_label(item.get(key), maximum) for key, maximum in (
                ("stage", 32), ("model", 120), ("api_host", 96), ("error_type", 80),
                ("error_code", 80), ("request_id", 96), ("reason", 40))}
            for key in ("http_status", "attempt", "elapsed_ms"):
                value = item.get(key, 0)
                entry[key] = max(0, int(value)) if isinstance(value, (int, float)) else 0
            safe["model_errors"].append(entry)
        receipt = record.get("comment_receipt")
        if safe["send_status"] in {"sent_verified", "sent_unverified"} and isinstance(receipt, dict):
            rpid = str(receipt.get("rpid") or "")
            text = str(receipt.get("text") or "")
            if re.fullmatch(r"[0-9]{1,30}", rpid) and not re.search(r"cookie|sessdata|bili_jct|uid\s*=", text, re.I):
                safe["comment_receipt"] = {"rpid": rpid, "text": text[:4000]}
        safe["candidates"] = []
        for item in record.get("candidates", [])[:10]:
            entry = {key: str(item.get(key) or "")[:maximum] for key, maximum in
                     (("bvid", 12), ("title", 200), ("subtitle_status", 24))}
            for key in ("rank", "score", "description_length", "subtitle_length"):
                value = item.get(key, 0)
                entry[key] = max(0, int(value)) if isinstance(value, (int, float)) else 0
            safe["candidates"].append(entry)
        return safe

    def record(self, record):
        safe = self._safe(record)
        with _DIAGNOSTIC_LOCK:
            rows = self._read()
            rows.insert(0, safe)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps(rows[:self.limit], ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return safe

    def _read(self):
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            return rows if isinstance(rows, list) else []
        except (OSError, ValueError):
            return []

    def latest(self):
        with _DIAGNOSTIC_LOCK:
            rows = self._read()
            return self._safe(rows[0]) if rows and isinstance(rows[0], dict) else None
