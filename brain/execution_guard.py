"""Prevent final answers from claiming external work that did not succeed."""

from __future__ import annotations

import re
from typing import Iterable

from brain.request_router import is_verifiable_recall_request


def _succeeded(audit: Iterable[dict], names: set[str]) -> bool:
    return any(
        item.get("name") in names and not item.get("is_error", False)
        for item in audit
    )


def _successful_memory_save(audit: Iterable[dict]) -> bool:
    """Require a real, non-error save_memory result before confirming storage."""
    failure_markers = (
        "失败", "错误", "不能写入", "不能为空", "被阻止", "不可写入",
        "failed", "error", "blocked", "refused",
    )
    for item in audit:
        if item.get("name") != "save_memory" or item.get("is_error", False):
            continue
        result = str(item.get("result", "") or "").lower()
        if result and not any(marker in result for marker in failure_markers):
            return True
    return False


def validate_execution_claims(content: str, request_text: str, audit: Iterable[dict],
                              *, capabilities: Iterable[str] = (), mode: str = "") -> str:
    """Replace only high-risk unsupported completion claims with an honest result.

    This deliberately does not police ordinary opinions or conversational wording.
    It is limited to web/file/memory/configuration operations the user explicitly asked for.
    """
    text = str(content or "")
    request = str(request_text or "")
    events = list(audit or ())
    capability_set = set(capabilities or ())

    # Memory claims are stricter than ordinary conversational wording.  The
    # model may acknowledge a fact in natural language, but it may only claim
    # durable storage after the audited save_memory tool succeeded.
    memory_request = re.search(
        r"(?:\u8bb0\u4f4f|\u8bb0\u4e0b\u6765|\u957f\u671f\u8bb0\u5fc6|\u6c38\u4e45\u8bb0\u5fc6|\u4fdd\u5b58.*\u8bb0\u5fc6|\u5199\u5165.*\u8bb0\u5fc6)",
        request,
        re.IGNORECASE,
    )
    memory_claim = re.search(
        r"(?:\u8bb0\u4f4f\u4e86|\u5df2\u7ecf\u8bb0\u4f4f|\u5df2(?:\u7ecf)?\u5199\u5165.*(?:\u957f\u671f\u8bb0\u5fc6|\u8bb0\u5fc6)|\u5df2(?:\u7ecf)?\u4fdd\u5b58.*(?:\u8bb0\u5fc6|\u8bb0\u5fc6\u5e93)|\u4ee5\u540e.*\u4f1a\u8bb0\u5f97)",
        text,
        re.IGNORECASE,
    )
    if memory_claim and (memory_request or memory_claim) and not _successful_memory_save(events):
        return "我还没有把这条信息写入长期记忆；如果你希望永久保存，请明确告诉我“请记住这件事”。"

    if re.search(r"(?:搜索|联网|查最新|查一下|资料|新闻)", request):
        claims = re.search(r"(?:我|已经|刚刚).{0,10}(?:搜索|查到|检索|浏览)", text)
        if claims and not _succeeded(events, {"web_search", "fetch_webpage"}):
            return "我这次没有成功取得联网检索结果，所以不能把内容说成已经查到。"

    if re.search(r"(?:读取|打开|查看|分析).{0,16}(?:文件|文档|pdf|docx|路径)", request, re.I):
        claims = re.search(r"(?:已经|我已|刚刚).{0,12}(?:读取|打开|查看|分析)", text)
        if claims and not _succeeded(events, {"read_file", "read_file_chunk", "read_file_lines"}):
            return "我这次没有成功读取到目标文件，不能假装已经看过它。"

    if re.search(r"(?:保存|写入|修改|编辑|创建).{0,16}(?:文件|记忆|配置|设置)", request):
        claims = re.search(r"(?:已经|我已|刚刚).{0,12}(?:保存|写入|修改|编辑|创建|启用|停用)", text)
        if claims and not _succeeded(events, {
            "write_file", "edit_file", "save_memory", "update_memory", "delete_memory",
            "request_enable_tool",
        }):
            return "这项修改没有成功执行，因此我不能把它说成已经保存或生效。"

    # A memory lookup can identify the city, but it cannot establish live weather.
    if "weather" in capability_set:
        weather_facts = re.search(
            r"(?:\d+\s*(?:℃|度)|降水|雷阵雨|暴雨|大雨|中雨|小雨|晴转|阴转|[东南西北]风)",
            text,
        )
        if weather_facts and not _succeeded(events, {"get_weather"}):
            return "我已取得地点相关的记忆，但本轮没有成功查询实时天气，不能直接给出温度、降雨或风力结论。"

    if mode == "TASK_DISCOVERY" or "web_search" in capability_set:
        search_claim = re.search(r"(?:我|已经|刚刚|这次).{0,12}(?:搜了一圈|搜索了|检索到|查到)", text)
        if search_claim and not _succeeded(events, {"web_search", "fetch_webpage"}):
            return "本轮没有成功完成联网检索，不能把内容说成是刚刚搜索到的结果。"

    # 历史核验必须有聊天记录工具的真实审计结果。这里同时拦截“正在查”
    # 这类过程性承诺，避免模型在无工具或工具失败时制造虚假的进度感。
    if is_verifiable_recall_request(request):
        history_ok = _succeeded(events, {"search_conversation_history"})
        history_claim = re.search(
            r"(?:我(?:正在|马上|已经|刚刚)?(?:查|核对|检索|查看)|"
            r"正在查|马上核对|刚调用|调用了|已经核对|根据(?:聊天记录|日志)|"
            r"查到了|找到.{0,12}(?:聊天记录|历史记录|原话)|"
            r"结果(?:马上|应该马上)出来)",
            text,
            re.IGNORECASE,
        )
        honest_no_result = re.search(r"(?:没有|未|没能|无法).{0,8}(?:找到|取得).{0,12}(?:聊天记录|历史记录|原话|结果)", text)
        if history_claim and not honest_no_result and not history_ok:
            return (
                "我这次还没有取得可验证的聊天记录结果，"
                "所以不能声称已经查到、正在查或已经核对出具体时间。"
                "你可以稍后让我重新检索，我会只依据真实记录回答。"
            )

    return text
