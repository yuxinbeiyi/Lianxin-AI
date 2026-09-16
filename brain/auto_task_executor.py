# -*- coding: utf-8 -*-
"""
auto_task_executor.py — 自动化任务执行器（ReAct Agent 模式）

解析阶段只提取调度信息。执行阶段使用 ReAct Agent 循环，
LLM 在运行时根据任务描述和工具返回结果逐步决定和执行工具，
从根本上消除静态计划导致的占位内容、参数错误、级联失败等问题。
"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import Optional, Callable

import litellm

from config import get_api_config
from brain.auto_task_manager import get_auto_task_manager
from brain.tools import TOOL_DEFINITIONS, execute_tool
from brain.tool_router import dedupe_tool_definitions
from utils.auto_task_data import AutoTask

logger = logging.getLogger("AutoTaskExecutor")

# ── 模块级状态 ──────────────────────────────────────────

_running_tasks: set[str] = set()
_lock = threading.Lock()
_cancel_flags: dict[str, bool] = {}

_EXECUTION_TIMEOUT = 300  # 可通过 set_execution_timeout() 配置

# ── ReAct Agent 配置 ────────────────────────────────────

_REACT_SYSTEM_PROMPT = """你是莲心AI的自动化任务执行器。根据用户的任务描述，自主选择合适的工具来完成任务。

## 工作流程
1. 分析任务描述，确定需要执行的操作
2. 调用合适的工具（每次可调用多个非冲突工具）
3. 根据工具返回的结果判断是否需要继续
4. 所有步骤完成后，用一句话总结完成情况

## 重要规则
- 搜索类任务：先用搜索工具获取信息，再根据实际搜索结果生成具体内容写入文件，禁止写占位文本
- 写文件类工具：content 参数必须是你根据实际数据撰写的完整正文，不能是"从搜索结果提取"之类的描述
- 简单任务（删除文件、清理回收站）：直接调用工具一步完成，然后总结
- 工具返回错误时分析原因并尝试其他方法，不要用相同参数反复重试
- 如果是你无法完成的任务（无对应工具、权限不足等），直接说明原因
- 文件操作使用绝对路径；del 命令删除文件前确认路径正确"""

_REACT_MAX_ITERATIONS = 15
_REACT_DEAD_LOOP_THRESHOLD = 3

# ── 公开 API ────────────────────────────────────────────


def set_execution_timeout(seconds: int):
    """配置执行超时时间。"""
    global _EXECUTION_TIMEOUT
    _EXECUTION_TIMEOUT = max(seconds, 10)


def cancel_task(task_id: str) -> bool:
    """取消正在执行的任务。返回 True 表示已设置取消标志。"""
    with _lock:
        if task_id in _running_tasks:
            _cancel_flags[task_id] = True
            logger.info(f"任务 {task_id} 已标记取消")
            print(f"[AutoTaskExecutor] 任务 {task_id} 已标记取消")
            return True
        return False


def is_task_running(task_id: str) -> bool:
    """检查任务是否正在执行中。"""
    with _lock:
        return task_id in _running_tasks


def get_running_tasks() -> set[str]:
    """获取当前正在执行的任务 ID 集合。"""
    with _lock:
        return set(_running_tasks)


def execute_auto_task(task: AutoTask,
                      on_step: Callable[[int, str, bool], None] = None,
                      on_complete: Callable[[str, bool, str], None] = None):
    """在后台线程中执行自动化任务（ReAct Agent 模式）。"""
    thread = threading.Thread(
        target=_execute_sync,
        args=(task, on_step, on_complete),
        daemon=True,
    )
    thread.start()


# ── 内部辅助 ────────────────────────────────────────────


def _is_network_error(exc: Exception) -> bool:
    """判断异常是否为网络相关错误（可重试）。"""
    error_str = str(exc).lower()
    network_keywords = [
        "timeout", "connection", "refused", "reset", "network",
        "socket", "http", "rate limit", "too many requests",
        "service unavailable", "gateway", "proxy", "dns",
        "unreachable", "eof", "broken pipe", "ssl", "tls",
    ]
    if hasattr(exc, 'status_code'):
        status = getattr(exc, 'status_code', 0)
        if status in (429, 500, 502, 503, 504):
            return True
        if status == 400:
            return False
    for kw in network_keywords:
        if kw in error_str:
            return True
    return False


def _is_tool_success(result: str) -> bool:
    """判断工具执行结果是否成功。"""
    if not result:
        return False
    if result.startswith("[拒绝]"):
        return False
    if result.startswith("[ERROR]"):
        return False
    if "未知工具" in result:
        return False
    head = result[:30]
    if "失败" in head:
        return False
    if "异常" in head:
        return False
    return True


def _get_all_available_tools() -> list[dict]:
    """收集所有可用工具定义（本地 + skill + MCP），返回 OpenAI 格式列表。"""
    tools = list(TOOL_DEFINITIONS)
    try:
        from brain.skill_manager import get_active_tool_definitions
        tools.extend(get_active_tool_definitions())
    except Exception:
        pass
    try:
        from brain.mcp.mcp_registry import get_all_mcp_tool_definitions
        tools.extend(get_all_mcp_tool_definitions())
    except Exception:
        pass
    return dedupe_tool_definitions(tools)


# ── ReAct Agent 核心 ────────────────────────────────────


def _run_react_agent(task: AutoTask,
                     on_step: Optional[Callable[[int, str, bool], None]],
                     cancel_check: Callable[[], bool]) -> str:
    """ReAct Agent 循环：LLM 逐步决定和执行工具，直到任务完成。

    Args:
        task: 自动化任务（使用 task.description 作为执行意图）
        on_step: 每步回调，用于报告进度
        cancel_check: 返回 True 表示应取消执行

    Returns:
        最终结果字符串。以 [CANCELLED]/[API_ERROR]/[TIMEOUT] 开头表示失败。
    """
    from config import normalize_model_for_litellm
    api_cfg = get_api_config()
    model = normalize_model_for_litellm(
        api_cfg.get("model", "deepseek-v4-flash"),
        api_cfg.get("base_url", ""),
    )

    all_tools = _get_all_available_tools()
    if not all_tools:
        return "[ERROR] 没有可用的工具"

    step_index = 0
    last_tool_hashes: list[int] = []

    messages = [
        {"role": "system", "content": _REACT_SYSTEM_PROMPT},
        {"role": "user", "content": f"请执行以下任务：\n\n{task.description}"},
    ]

    print(f"[ReAct] 开始执行，可用工具 {len(all_tools)} 个，模型 {model}")
    print(f"   任务: {task.description[:120]}")

    for iteration in range(_REACT_MAX_ITERATIONS):
        # ── 取消检查 ──
        if cancel_check():
            print(f"[ReAct] 第{iteration+1}轮前检测到取消")
            return "[CANCELLED] 用户取消"

        # ── 调用 LLM ──
        llm_success = False
        llm_error = ""
        for retry in range(3):
            try:
                response = litellm.completion(
                    model=model,
                    messages=messages,
                    tools=all_tools,
                    tool_choice="auto",
                    api_key=api_cfg["api_key"],
                    api_base=api_cfg["base_url"],
                    temperature=0.2,
                    max_tokens=2000,
                    timeout=60,
                )
                llm_success = True
                break
            except Exception as e:
                llm_error = str(e)
                if retry < 2 and _is_network_error(e):
                    delay = 1.5 * (retry + 1)
                    print(f"   [ReAct] LLM 网络错误 (尝试{retry+1}/3)，{delay}s 后重试: {e}")
                    for _ in range(int(delay)):
                        time.sleep(1)
                        if cancel_check():
                            return "[CANCELLED] 用户取消"
                else:
                    break

        if not llm_success:
            print(f"   [ReAct] LLM 调用失败: {llm_error}")
            return f"[API_ERROR] LLM 调用失败: {llm_error[:200]}"

        choice = response.choices[0]
        msg = choice.message
        finish = choice.finish_reason

        # ── LLM 返回纯文本 → 任务完成 ──
        content = msg.content or ""
        tool_calls = getattr(msg, "tool_calls", None)

        if content and not tool_calls:
            print(f"   [ReAct] 第{iteration+1}轮 LLM 返回文本({len(content)}字): {content[:150]}")
            return content

        if finish == "stop" and not tool_calls:
            print(f"   [ReAct] 第{iteration+1}轮 finish=stop: {content[:150] if content else '(空)'}")
            return content if content else "任务完成"

        # ── 无 tool_calls 且无内容 → 尝试推进 ──
        if not tool_calls:
            print(f"   [ReAct] 第{iteration+1}轮无 tool_calls 无内容，finish={finish}")
            # 给 LLM 一个推动
            if finish == "length":
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({"role": "user", "content": "请继续完成剩余步骤。"})
                continue
            # 其他情况：LLM 可能认为完成了
            return content if content else "任务完成（无额外输出）"

        # ── 执行工具调用 ──
        tool_call_msgs = []
        for tc in tool_calls:
            func = tc.function
            tool_name = func.name
            try:
                tool_params = json.loads(func.arguments)
            except json.JSONDecodeError:
                tool_params = {}

            print(f"   [ReAct] 第{iteration+1}轮 调用: {tool_name}({json.dumps(tool_params, ensure_ascii=False)[:120]})")

            try:
                tool_result = execute_tool(tool_name, tool_params)
            except Exception as e:
                tool_result = f"[ERROR] 工具执行异常: {e}"
                print(f"   [ReAct] {tool_name} 异常: {e}")

            success = _is_tool_success(tool_result)
            status = "✅" if success else "❌"
            print(f"   {status} [ReAct] {tool_name} → {tool_result[:150]}")

            if on_step:
                step_index += 1
                on_step(step_index, f"{tool_name}: {tool_result[:100]}", success)

            tool_call_msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result[:2000],  # 截断防止 context overflow
            })

        # ── 追加到 messages ──
        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })
        messages.extend(tool_call_msgs)

        # ── 死循环检测 ──
        result_hash = hash(json.dumps(
            [[m.get("role"), str(m.get("content", ""))[:200]] for m in tool_call_msgs],
            sort_keys=True,
        ))
        last_tool_hashes.append(result_hash)
        if len(last_tool_hashes) > _REACT_DEAD_LOOP_THRESHOLD:
            last_tool_hashes.pop(0)
        if (len(last_tool_hashes) >= _REACT_DEAD_LOOP_THRESHOLD
                and len(set(last_tool_hashes)) == 1):
            print(f"   [ReAct] 检测到死循环，注入破圈提示")
            messages.append({
                "role": "user",
                "content": "你已经连续多次执行了相同的操作且结果相同。请换一种方法，或者如果任务确实无法完成，直接说明原因。",
            })
            last_tool_hashes.clear()

    # 达到最大迭代次数
    print(f"   [ReAct] 达到最大迭代次数 {_REACT_MAX_ITERATIONS}")
    return "[TIMEOUT] 达到最大执行轮数，任务可能未完成"


# ── 同步执行入口 ────────────────────────────────────────


def _execute_sync(task: AutoTask,
                  on_step: Optional[Callable[[int, str, bool], None]],
                  on_complete: Optional[Callable[[str, bool, str], None]]):
    """同步执行自动化任务（ReAct Agent 模式）。

    不再依赖预生成的静态工具链。直接以 task.description 为意图，
    通过 ReAct Agent 循环动态决定和执行工具。
    """
    task_id = task.task_id

    # 并发保护
    with _lock:
        if task_id in _running_tasks:
            logger.warning(f"任务 {task.name} 正在执行中，跳过")
            print(f"[AutoTaskExecutor] 任务「{task.name}」正在执行中，跳过重复触发")
            return
        _running_tasks.add(task_id)
        _cancel_flags.pop(task_id, None)

    manager = get_auto_task_manager()
    start_time = time.time()
    workflow_store = None
    workflow_run_id = 0
    try:
        from brain.workflow import get_workflow_store

        workflow_store = get_workflow_store()
        workflow_run = workflow_store.begin_run(
            kind="auto_task", title=task.name, channel="scheduler",
            metadata={"task_id": task_id, "description": task.description,
                      "schedule_type": task.schedule_type},
            retry_of_run_id=int(getattr(task, "_workflow_retry_of_run_id", 0) or 0) or None,
        )
        task._workflow_retry_of_run_id = 0
        workflow_run_id = int(workflow_run["id"])
        manager = get_auto_task_manager()
        manager.bind_workflow(task_id, workflow_run_id, "execution")
    except Exception as exc:
        logger.warning("自动任务 Workflow 初始化失败: %s", exc)

    print(f"\n{'='*60}")
    print(f"[AutoTaskExecutor] 开始执行任务「{task.name}」(ID:{task_id}) [ReAct模式]")
    print(f"   调度类型: {task.schedule_type} | 时间: {task.schedule_time}")
    print(f"   描述: {task.description[:120]}")
    print(f"{'='*60}")

    def cancel_check():
        with _lock:
            if _cancel_flags.get(task_id, False):
                return True
        if workflow_store and workflow_run_id and workflow_store.is_cancel_requested(workflow_run_id):
            return True
        if time.time() - start_time > _EXECUTION_TIMEOUT:
            return True
        return False

    try:
        from brain.workflow import workflow_context

        with workflow_context(workflow_run_id):
            result = _run_react_agent(task, on_step, cancel_check)

        # 判断成功/失败
        if result.startswith("[CANCELLED]"):
            all_success = False
            final_message = result
        elif result.startswith("[API_ERROR]"):
            all_success = False
            final_message = result
        elif result.startswith("[TIMEOUT]"):
            all_success = False
            final_message = result
        elif result.startswith("[ERROR]"):
            all_success = False
            final_message = result
        else:
            all_success = True
            final_message = result

        manager.mark_executed(task_id, all_success, final_message[:500])
        if workflow_store and workflow_run_id:
            workflow_store.finish_run(
                workflow_run_id,
                status="success" if all_success else (
                    "cancelled" if final_message.startswith("[CANCELLED]") else "failed"
                ),
                result_summary=final_message if all_success else "",
                error="" if all_success else final_message,
            )

    except Exception as e:
        all_success = False
        final_message = str(e)
        manager.add_log(task_id, -1, False, f"执行异常: {e}")
        manager.mark_executed(task_id, False, final_message[:500])
        if workflow_store and workflow_run_id:
            workflow_store.finish_run(workflow_run_id, status="failed", error=final_message)
        print(f"[AutoTaskExecutor] 执行异常: {e}")

    finally:
        elapsed = time.time() - start_time
        status_icon = "✅ 成功" if all_success else "❌ 失败"
        print(f"{'='*60}")
        print(f"[AutoTaskExecutor] 任务「{task.name}」执行完毕: {status_icon} (耗时 {elapsed:.1f}s)")
        print(f"{'='*60}\n")
        with _lock:
            _running_tasks.discard(task_id)
            _cancel_flags.pop(task_id, None)
        if on_complete:
            on_complete(task_id, all_success, final_message)
