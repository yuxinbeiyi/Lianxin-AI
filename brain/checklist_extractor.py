"""
checklist_extractor.py — 对话结束后 LLM 自动提取待办事项
借鉴 NagaAgent DogTag: 回顾对话→提取[ADD_ITEM]/[DONE_ITEM]→集成到TodoManager
"""

import re
import logging
import threading
from typing import Optional

logger = logging.getLogger("ChecklistExtractor")

# 待办只能来自用户明确的安排意图；纠错建议、莲心自我承诺和行为规则
# 不属于 Todo，避免后台摘要模型把“以后先搜索”误转成提醒。
_TODO_INTENT_RE = re.compile(
    "\\u63d0\\u9192\\u6211|\\u6dfb\\u52a0\\u5f85\\u529e|\\u52a0\\u5165\\u5f85\\u529e|\\u8bb0\\u4e00\\u4e0b|\\u8bb0\\u4e0b\\u6765|\\u522b\\u5fd8\\u4e86|\\u5230\\u65f6\\u5019\\u63d0\\u9192|\\u8bbe\\u4e2a\\u63d0\\u9192|\\u8bbe\\u7f6e\\u63d0\\u9192|\\u5e2e\\u6211\\u5b89\\u6392"
)


def has_explicit_todo_intent(conversation_text: str) -> bool:
    """Return whether the user explicitly asked to create or remember a todo."""
    for line in str(conversation_text or "").splitlines():
        if line.startswith("[\u7528\u6237]") and _TODO_INTENT_RE.search(line):
            return True
    return False

_EXTRACT_SYSTEM = """你是莲心AI的对话回顾助手。一段对话刚刚结束，请回顾内容，提取用户提到的事项。

你的任务：
1. 识别用户提到要做、要记住、要跟进的事情 → 用 [ADD_ITEM] 标记
2. 识别用户提到已经完成、不需要再关注的事情 → 用 [DONE_ITEM] 标记
3. 如果没有需要关注的事项，只回复 CHECKLIST_OK

格式要求：
- [ADD_ITEM] 事项简述 — 每条一行，简洁明了
- [DONE_ITEM] 事项关键词 — 与现有待办匹配的关键词

注意：
- 只提取用户明确提出的事项，不要臆测
- 忽略闲聊、寒暄、纯信息问询（如"今天天气怎么样"）
- [ADD_ITEM] 最多3条"""


def extract_checklist(
    conversation_text: str,
    api_key: str,
    api_base: str,
    model: str,
) -> Optional[dict]:
    """对话结束后调用 LLM 提取待办事项。

    Args:
        conversation_text: 最近 N 轮对话的文本表示
        api_key/base/model: LLM 配置

    Returns:
        {"add": ["事项1", "事项2"], "done": ["关键词1"]} 或 None（无事）
    """
    try:
        import litellm
        response = litellm.completion(
            model=model,
            max_tokens=300,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"请回顾这段对话：\n\n{conversation_text}"},
            ],
            api_key=api_key,
            api_base=api_base,
            timeout=20,
        )
    except Exception as e:
        logger.debug(f"Checklist LLM 调用失败: {e}")
        return None

    text = response.choices[0].message.content or ""
    return _parse_response(text)


def _parse_response(text: str) -> Optional[dict]:
    """解析 LLM 返回的 checklist 指令。"""
    text = text.strip()

    if "CHECKLIST_OK" in text.upper() and len(text) < 50:
        return None

    add_items = []
    done_items = []

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "[ADD_ITEM]" in line.upper():
            item = re.sub(r'\[ADD_ITEM\]', '', line, flags=re.IGNORECASE).strip()
            if item:
                add_items.append(item[:100])
        elif "[DONE_ITEM]" in line.upper():
            kw = re.sub(r'\[DONE_ITEM\]', '', line, flags=re.IGNORECASE).strip()
            if kw:
                done_items.append(kw[:50])

    if not add_items and not done_items:
        return None

    return {"add": add_items, "done": done_items}


def apply_checklist(
    result: dict,
    todo_manager,
    chat_widget=None,
) -> str:
    """应用提取结果到 TodoManager。

    Returns:
        展示给用户的摘要文本
    """
    parts = []
    if result.get("add"):
        for item in result["add"]:
            try:
                todo_manager.add_todo(item)
            except Exception:
                pass
        items_str = "、".join(result["add"][:3])
        parts.append(f"帮你记下了：{items_str}")

    if result.get("done"):
        for kw in result["done"]:
            try:
                todo_manager.complete_todo(kw)
            except Exception:
                pass

    return "。".join(parts) if parts else ""


def run_checklist_async(
    conversation_text: str,
    api_key: str,
    api_base: str,
    model: str,
    todo_manager,
    chat_widget=None,
    callback=None,
):
    """后台线程：提取+应用 checklist，不阻塞主对话。

    如果提供了 callback(result)，则仅提取不自动 apply，
    由 callback 负责后续处理（如弹窗确认）。
    """
    def _run():
        try:
            result = extract_checklist(conversation_text, api_key, api_base, model)
            if not result:
                return
            if result.get("add") and not has_explicit_todo_intent(conversation_text):
                logger.info("[Checklist] 丢弃 %d 条候选：未发现用户明确待办意图", len(result["add"]))
                result = {"add": [], "done": result.get("done", [])}
                if not result["done"]:
                    return
            if callback:
                callback(result)
            else:
                summary = apply_checklist(result, todo_manager, chat_widget)
                if summary and chat_widget:
                    logger.info(f"Checklist: {summary}")
        except Exception as e:
            logger.debug(f"Checklist 后台执行异常: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
