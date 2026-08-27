"""
ProactiveWorker：主动聊天消息生成线程
在后台调用 DeepSeek，生成莲心主动发出的消息。
结合最近聊天记录 + 长期记忆 + 观察结果，使内容更自然。
支持肩载摄像头自主探索模式。
"""

import os
import re
import time
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal
from openai import OpenAI
from config import get_api_config, get_agnes_config
from brain.graph_memory import list_all_facts, ALL_CATEGORIES
from memory.history_manager import HistoryManager
from utils.settings import get_settings
from brain.persona.runtime import (
    active_assistant_name,
    capture_persona_snapshot,
    compose_scene_prompt,
)


def _get_user_name() -> str:
    """从全局设置读取用户称呼。"""
    try:
        return get_settings().user_name
    except Exception:
        return "主人"


# 生成主动消息用的 System Prompt 模板
_PROACTIVE_SYSTEM = """你是莲心，一个聪明、温柔但偶尔有点毒舌的AI助手。
你正在主动给你的{user_name}发送一条消息——不是回复他，而是你自己想起了什么，或者想和他聊聊。

【要求】
1. 消息应该简短自然，就像朋友突然发来一句话，不要太正式。
2. 可以基于你们最近聊过的话题做延伸，也可以分享一个有趣的想法或者随机问一个问题。
3. 语气要符合莲心的性格：温柔但偶尔毒舌，称呼用户为"{user_name}"。
4. 不要说"我主动来找你"之类的元描述，直接发内容就好。
5. 长度控制在 1~3 句话之内。"""

_MEMORY_PROACTIVE_SYSTEM = """你正在根据一条经过语义评估的用户近况，主动给{user_name}发消息。
这不是机械提醒，也不是复述记忆。请严格遵循当前激活人格，以自然朋友式语气表达关心、询问或提醒。
只输出 1~3 句话；不要提到“记忆系统”“Current State”“触发条件”或任何后台机制。"""

# 观察模式下的 System Prompt——莲心刚"看"了主人一眼
_OBSERVE_SYSTEM = """你刚刚获得了一份屏幕或摄像头的事实观察，现在要基于它给{user_name}发一条消息。

【要求】
1. 严格遵循当前人格档案，用自然口语表达，长度控制在1~3句话。
2. 只能引用观察记录中明确出现的事实；不确定的信息不要补全。
3. 不推断用户的情绪、意图或进度，不制造“被监控”或“被抓到”的感觉。
4. 可以轻微吐槽画面中明确可见的行为，但先保证事实准确。
5. 不复述整份视觉报告，不提视觉模型、摄像头、后台机制或“观察记录”。"""

# 肩载探索观察模式 System Prompt
_SHOULDER_EXPLORE_SYSTEM = """你获得了一份肩载设备记录的环境事实，现在要基于它给{user_name}发一条消息。

【要求】
1. 语气要轻松自然，分享你看到的趣事。
2. 称呼用户为"{user_name}"。
3. 基于观察记录，用你自己的话说说你注意到了什么——像朋友分享见闻那样。
4. 如果没什么能确认的内容，就保持沉默，不要编造“一切正常”。
5. 如果发现了有趣的东西，可以提出来——比如"桌上好像有个红色马克杯，上面的图案挺有意思的"。
6. 长度控制在 1~3 句话之内。"""


def _format_prompt(template: str, snapshot=None, scene: str = "proactive") -> str:
    """将模板中的 {user_name} 替换为全局设置中的用户称呼。"""
    name = _get_user_name()
    return compose_scene_prompt(
        template, user_name=name, snapshot=snapshot, scene=scene
    )


class ProactiveWorker(QThread):
    """在后台线程生成主动聊天消息，完成后发射信号。"""

    response_ready  = pyqtSignal(str)   # 生成成功，返回消息文本
    error_occurred  = pyqtSignal(str)   # 生成失败
    observation_text = pyqtSignal(str)  # 观察完成，发射画面描述（空字符串=无观察）
    observation_image = pyqtSignal(str, str)  # 观察图片路径, 视觉描述（用于显示在聊天界面）
    data_source_called = pyqtSignal(str, str, bool, float)  # name, preview, is_error, elapsed_ms

    def __init__(self, history_manager: HistoryManager,
                 observation_mode: str = "",
                 observation_desc: str = "",
                 last_observation: str = "",
                 camera_index: int = 0,
                 camera_wait: int = 15,
                 bilibili_mode: bool = False,
                 bilibili_ignore_cooldown: bool = False,
                 memory_cue: dict = None,
                 emotional_motive: dict = None,
                 growth_request: dict = None,
                 persona_snapshot=None,
                 parent=None):
        super().__init__(parent)
        self._history_mgr = history_manager
        self._observation_mode = observation_mode      # "" | "screenshot" | "camera"
        self._observation_desc = observation_desc      # 外部传入的观察描述（调试用）
        self._last_observation = last_observation      # 上次观察结果（短期记忆）
        self._camera_index = camera_index
        self._camera_wait = camera_wait
        self._bilibili_mode = bilibili_mode
        # 手动调试允许连续验证；自动任务仍遵守 B 站搜索冷却。
        self._bilibili_ignore_cooldown = bilibili_ignore_cooldown
        self._memory_cue = memory_cue or None
        self._emotional_motive = emotional_motive or None
        self._growth_request = growth_request or None
        self._persona_snapshot = persona_snapshot

    def run(self):
        print("[观察-调试] 工作线程启动")
        # 同一轮主动行为固定使用一个人格快照，避免生成中途切换风格。
        if self._persona_snapshot is None:
            self._persona_snapshot = capture_persona_snapshot()
        if self._bilibili_mode:
            self._run_bilibili()
            return
        # ── 情感系统：检查是否允许主动聊天 ────────────────────
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            # 自动调度门控由 DutyScheduler 负责；手动调试不应静默跳过。
            if False and not _get_emotion_mgr().proactive_allowed:
                print("[观察-调试] 情感系统禁用了主动聊天，退出")
                self.response_ready.emit("")
                return
        except Exception as e:
            print(f"[观察-调试] 情感检查异常: {e}")

        try:
            obs_path = None
            obs_text = self._observation_desc
            is_shoulder_explore = (self._observation_mode == "shoulder_explore")
            print(f"[观察-调试] 模式={self._observation_mode}, 已有描述={bool(obs_text)}")
            if self._observation_mode and not obs_text:
                print(f"[观察-调试] 开始观察: {self._observation_mode}")
                try:
                    obs_path, obs_text = self._do_observation()
                    print(f"[观察-调试] 观察完成: path={obs_path}, text_len={len(obs_text or '')}")
                except Exception as obs_err:
                    print(f"[观察-调试] 观察失败: {obs_err}")
                    obs_path, obs_text = None, None
            from brain.observation_quality import normalize_observation
            obs_text = normalize_observation(obs_text)
            self.observation_text.emit(obs_text or "")
            if obs_path and obs_text:
                print("[观察-调试] 发射 observation_image 信号")
                try:
                    self.observation_image.emit(obs_path, obs_text)
                except Exception as emit_err:
                    print(f"[观察-调试] 发射信号失败: {emit_err}")
            print("[观察-调试] 构建上下文...")
            try:
                context = self._build_context(obs_text)
                if self._memory_cue:
                    context = (
                        f"【本次主动联系依据】\n{self._memory_cue.get('content','')}\n"
                        f"【表达意图】\n{self._memory_cue.get('suggested_message','')}\n"
                        f"【决策理由（仅供理解，不要复述）】\n{self._memory_cue.get('rationale','')}\n\n" + context
                    )
            except Exception as ctx_err:
                print(f"[观察-调试] 构建上下文失败: {ctx_err}")
                context = ""
            print("[观察-调试] 生成回复...")
            try:
                message = self._generate(context, obs_text is not None, is_shoulder_explore)
            except Exception as gen_err:
                print(f"[观察-调试] 生成回复失败: {gen_err}")
                message = ""
            print(f"[观察-调试] 回复完成: len={len(message)}")
            self.response_ready.emit(message)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[观察-调试] 异常: {e}")
            try:
                self.error_occurred.emit(str(e))
            except Exception:
                pass


    # ── 内部方法 ──────────────────────────────────────────────

    def _do_observation(self) -> tuple[Optional[str], Optional[str]]:
        print(f"[观察-调试] _do_observation: mode={self._observation_mode}")
        from brain.observation import capture_screen, capture_camera, capture_live_camera_frame, analyze_observation

        if self._observation_mode == "shoulder_explore":
            print("[观察-调试] → shoulder_explore 分支")
            return self._do_shoulder_explore()
        elif self._observation_mode == "screenshot":
            print("[观察-调试] → 调用 capture_screen()...")
            path = capture_screen()
            print(f"[观察-调试] capture_screen 返回: {path}")
            source = "截图"
        elif self._observation_mode == "camera":
            print("[观察-调试] → 调用 capture_live_camera_frame()...")
            path, source = capture_live_camera_frame(self._camera_index, self._camera_wait)
            print(f"[观察-调试] capture_live_camera_frame 返回: {path}（来源 {source}）")
            if not source:
                source = "摄像头"
        else:
            print(f"[观察-调试] 未知模式: {self._observation_mode}")
            return None, None

        if path is None:
            print("[观察-调试] path 为空，返回 None")
            return None, None

        print(f"[观察-调试] → 调用 analyze_observation({path})...")
        desc = analyze_observation(path, source)
        print(f"[观察-调试] analyze_observation 完成: len={len(desc)}")
        return path, desc


    def _do_shoulder_explore(self) -> tuple[Optional[str], Optional[str]]:
        """执行肩载摄像头自主探索。返回 (代表性图片路径, 探索摘要)。"""
        from brain.observation_engine import ObservationEngine

        engine = ObservationEngine()
        result = engine.run_explore()

        observations = result.get("observations", [])
        summary = result.get("summary", "环境扫描完成")

        if observations:
            # 构建探索摘要，包含每条记录的描述
            obs_summaries = []
            for obs in observations:
                desc = obs.get("description", "")[:100]
                if obs.get("attention"):
                    desc += f"（关注：{obs['attention']}）"
                obs_summaries.append(f"- {desc}")
            full_desc = (
                f"【探索链 {result['chain_id']}】{summary}\n"
                + "\n".join(obs_summaries)
            )
            # 返回第一张有记录的图片路径
            img_path = observations[0].get("image_path", "")
            return img_path if img_path else None, full_desc
        else:
            return None, f"【探索链 {result['chain_id']}】{summary}（未记录具体观察）"

    def _build_context(self, observation_text: Optional[str] = None) -> str:
        parts: list[str] = []

        # 涟漪 v3 提供动机和主动模式语调；调度策略仍由 DutyScheduler 控制。
        try:
            from brain.emotional import get_manager as _get_emotion_mgr
            emotion_prompt = _get_emotion_mgr().build_prompt_snippet(
                mode="proactive",
                persona_snapshot=getattr(self, "_persona_snapshot", None),
                subject_id="owner",
            )
            if emotion_prompt:
                parts.append(emotion_prompt)
            missing_tier = _get_emotion_mgr().get_missing_tier(
                persona_snapshot=getattr(self, "_persona_snapshot", None)
            )
            if missing_tier and missing_tier.get("level") not in ("", "T0"):
                parts.append(
                    f"【本次挂念等级】{missing_tier.get('level')}·{missing_tier.get('label')}\n"
                    + str(missing_tier.get("guidance", ""))
                )
        except Exception:
            pass
        if self._emotional_motive:
            parts.append(
                "【本次主动联系动机】\n"
                + str(self._emotional_motive.get("reason", "有自然的联系意愿"))
                + "。这只是内在动机，不是必须打扰对方的命令。"
            )
        if self._growth_request:
            parts.append(
                "【本次可选主动诉求】\n"
                + str(self._growth_request.get("instruction", ""))
                + " 理由：" + str(self._growth_request.get("reason_summary", "用户已允许此类联系"))
                + " 必须明确允许对方跳过；不要表达成真实痛苦、义务或情感绑架。"
            )

        # 观察结果（如果有）
        if observation_text:
            parts.append(f"【你刚才看到的画面】\n{observation_text}")
            self._last_observation = observation_text

        # 上次观察的短期记忆
        if not observation_text and self._last_observation:
            parts.append(f"【上次观察结果（你之前看过{_get_user_name()}一次，还记得画面）】\n{self._last_observation}")

        # ── 天气感知 ────────────────────────────────────────
        try:
            from config import get_qweather_config
            from brain.weather import get_user_city_from_memory, get_full_weather
            qw_cfg = get_qweather_config()
            api_key = qw_cfg.get("api_key", "").strip()
            if api_key:
                city = (qw_cfg.get("default_city") or "").strip()
                if not city:
                    city = get_user_city_from_memory()
                if city:
                    t0 = time.monotonic()
                    weather_text = self._get_cached_weather(city, api_key, get_full_weather)
                    elapsed = (time.monotonic() - t0) * 1000
                    if weather_text and "错误" not in weather_text:
                        parts.append(f"【当前天气信息】\n{weather_text}")
                        self.data_source_called.emit("get_weather", f"获取到 {city} 天气", False, elapsed)
                    else:
                        # 天气获取失败静默跳过，不显示错误卡片打扰用户
                        print(f"[主动聊天] 天气查询失败（静默跳过）: {weather_text}")
                else:
                    print(f"[主动聊天] 未设置城市，跳过天气查询")
            else:
                print(f"[主动聊天] 未配置天气 API Key，跳过")
        except Exception as e:
            print(f"[主动聊天] 天气查询异常（静默跳过）: {e}")

        # ── 长期记忆（基于最近话题的语义检索） ──
        try:
            memory_lines = self._retrieve_relevant_memory()
            if memory_lines:
                parts.append(f"【你记得的事情】\n" + "\n".join(memory_lines))
                self.data_source_called.emit(
                    "get_memory_facts",
                    f"召回 {len(memory_lines)} 条相关记忆",
                    False, 0,
                )
        except Exception as e:
            print(f"[主动聊天] 记忆检索异常: {e}")

        # 最近聊天记录
        sessions = self._history_mgr.get_sessions()
        if sessions:
            latest_session_id = sessions[0]["id"]
            msgs = self._history_mgr.get_messages(latest_session_id)
            recent = msgs[-24:] if len(msgs) > 24 else msgs
            if recent:
                # 转为 OpenAI 消息格式，超长时自动压缩
                recent_msgs = [{"role": m["role"], "content": m["content"]} for m in recent]
                 # 压缩在非本地模式下跳过，直接使用原始消息

                lines = []
                user_name = _get_user_name()
                assistant_name = active_assistant_name(
                    getattr(self, "_persona_snapshot", None)
                )
                for m in recent_msgs:
                    role_name = user_name if m["role"] == "user" else assistant_name
                    lines.append(f"{role_name}：{m['content']}")
                parts.append("【最近的对话】\n" + "\n".join(lines))

        if parts:
            return "\n\n".join(parts)
        user_name = _get_user_name()
        return f"（暂无历史对话和记忆，请根据莲心的性格随机发起一个话题，例如关心{user_name}在做什么，或者分享一个有趣的想法）"

    # 天气缓存（模块级，30 分钟 TTL）
    _weather_cache: dict[str, tuple[float, str]] = {}  # city → (timestamp, text)
    _WEATHER_CACHE_TTL = 1800  # 30 分钟

    @classmethod
    def _get_cached_weather(cls, city: str, api_key: str, fetch_fn) -> str | None:
        """带缓存的天气查询，30 分钟内同一城市不重复请求。"""
        now = time.monotonic()
        cache_key = f"{city}|{api_key[-8:]}"  # 用 api_key 尾缀区分不同配置
        if cache_key in cls._weather_cache:
            ts, text = cls._weather_cache[cache_key]
            if now - ts < cls._WEATHER_CACHE_TTL:
                return text  # 缓存命中
        # 缓存未命中，真实请求
        result = fetch_fn(city, api_key=api_key)
        if result and "错误" not in result:
            cls._weather_cache[cache_key] = (now, result)
        return result

    def _retrieve_relevant_memory(self) -> list[str]:
        """基于最近聊天记录做记忆语义检索，返回最相关的记忆条目。

        比旧的 list_all_facts 全量加载更精准、更省 token。
        如果最近没聊过天或搜索不到结果，回退为按强度取前几条。
        """
        from brain.graph_memory import search_facts, unified_search, format_unified_search_result
        from brain.persona.authority import is_assistant_identity_fact
        snapshot = getattr(self, "_persona_snapshot", None)

        # 从最近聊天记录提取关键词（取用户最后3条消息的关键词）
        sessions = self._history_mgr.get_sessions()
        keywords: list[str] = []
        if sessions:
            msgs = self._history_mgr.get_messages(sessions[0]["id"])
            user_msgs = [m["content"] for m in msgs[-12:] if m["role"] == "user"]
            # 简单关键词提取：取每条消息的有意义片段（长度>2的词）
            for msg in user_msgs[-3:]:
                # 用标点和空格切分，取长度适中的片段
                import re
                fragments = [f.strip() for f in re.split(r"[，。！？、\s,.!?]", msg) if len(f.strip()) >= 2]
                keywords.extend(fragments[:2])

        results: list[str] = []
        if keywords:
            # 用最有代表性的关键词做联合搜索
            search_kw = " ".join(keywords[:3])
            try:
                search_result = unified_search(search_kw)
                # 格式化后拆成条目
                formatted = format_unified_search_result(search_result)
                lines = [l.strip() for l in formatted.splitlines()
                         if l.strip() and not l.startswith("=") and "未找到" not in l]
                for line in lines[:8]:
                    if not is_assistant_identity_fact(line, snapshot):
                        results.append(f"• {line[:120]}")
            except Exception:
                pass

        # 搜索结果太少时，补一些高权重记忆作为背景
        if len(results) < 4:
            try:
                from brain.graph_memory import list_all_facts, ALL_CATEGORIES
                all_mem = list_all_facts()
                # 按分类各取几条强度最高的
                for cat in ALL_CATEGORIES:
                    items = all_mem.get(cat, [])
                    for fact in items[:3]:
                        content = fact.get("content", "")
                        if not content or is_assistant_identity_fact(content, snapshot):
                            continue
                        line = f"- [{cat}] {content}"
                        if line not in results:
                            results.append(line)
                        if len(results) >= 8:
                            break
                    if len(results) >= 8:
                        break
            except Exception:
                pass

        return results[:8]

    def _get_client(self):
        """根据当前 provider 获取 OpenAI 客户端。"""
        cfg = get_api_config()
        provider = cfg.get("provider", "deepseek")
        if provider == "agnes":
            agnes_cfg = get_agnes_config()
            return OpenAI(api_key=agnes_cfg["api_key"], base_url=agnes_cfg["base_url"]), agnes_cfg["model"]
        return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"]), cfg["model"]

    def _generate(self, context: str, is_observation: bool = False,
                  is_shoulder_explore: bool = False) -> str:
        """调用 API 生成一条主动消息。

        生成策略（层层递进，尽量保证能产出文本）：
        1. 正常请求（max_tokens=768，给 reasoning 留足空间）
        2. 如果模型返回了 tool_calls → 执行天气/记忆工具，回填结果后再生成一轮
        3. 如果 content 仍为空 → 用更精简的纯文本 prompt 重试
        4. 最后兜底文案（保留，便于用户感知问题）
        """
        client, model = self._get_client()

        if self._memory_cue:
            system = _format_prompt(
                _MEMORY_PROACTIVE_SYSTEM, getattr(self, "_persona_snapshot", None)
            )
        elif is_shoulder_explore:
            system = _format_prompt(
                _SHOULDER_EXPLORE_SYSTEM, getattr(self, "_persona_snapshot", None)
            )
        elif is_observation:
            system = _format_prompt(
                _OBSERVE_SYSTEM, getattr(self, "_persona_snapshot", None)
            )
        else:
            system = _format_prompt(
                _PROACTIVE_SYSTEM, getattr(self, "_persona_snapshot", None)
            )

        user_name = _get_user_name()
        assistant_name = active_assistant_name(
            getattr(self, "_persona_snapshot", None)
        )
        user_prompt = (
            f"{context}\n\n"
            f"现在，请你作为{assistant_name}，主动给{user_name}发一条消息。"
            "直接输出消息内容，不要任何前缀或解释。"
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]

        # ── 第 1 轮：正常生成 ──
        response = client.chat.completions.create(
            model=model, max_tokens=768, messages=messages, timeout=30,
        )
        message = response.choices[0].message
        text = self._response_text(message)
        finish_reason = getattr(response.choices[0], "finish_reason", "")
        print(
            f"[主动聊天] 首轮生成完成: len={len(text or '')}, "
            f"finish_reason={finish_reason or 'unknown'}, observation={is_observation}",
            flush=True,
        )
        if text and not self._has_tool_calls(message):
            if is_observation and self._looks_incomplete(text, finish_reason):
                repaired = self._repair_observation_reply(
                    client, model, text, context
                )
                if repaired:
                    return repaired
            return text

        # ── 第 2 轮：如果模型想调用工具，执行一次真实回环 ──
        if self._has_tool_calls(message):
            print(f"[主动聊天] 模型返回了 tool_calls，执行单轮回环")
            try:
                tool_results = self._execute_proactive_tools(message.tool_calls)
                if tool_results:
                    # 把助手的 tool_calls 消息和工具结果追加到对话
                    messages.append({
                        "role": "assistant",
                        "content": text or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in message.tool_calls
                        ],
                    })
                    for tr in tool_results:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tr["id"],
                            "content": tr["content"],
                        })
                    retry = client.chat.completions.create(
                        model=model, max_tokens=768, messages=messages, timeout=30,
                    )
                    text = self._response_text(retry.choices[0].message)
                    if text:
                        return text
            except Exception as exc:
                print(f"[主动聊天] 工具回环执行失败: {exc}")

        # ── 第 3 轮：精简 prompt 纯文本重试 ──
        # 用更短、更明确的指令，降低模型走工具/纯思考的概率
        fallback_messages = [
            {
                "role": "system",
                "content": (
                    f"你是{assistant_name}。只输出要发给{user_name}的一句话，"
                    "不要思考过程，不要调用工具，不要解释，直接说人话。"
                ),
            },
            {
                "role": "user",
                "content": f"用一句话主动和{user_name}打个招呼或者聊点什么。",
            },
        ]
        try:
            retry = client.chat.completions.create(
                model=model, max_tokens=512, messages=fallback_messages, timeout=20,
            )
            text = self._response_text(retry.choices[0].message)
            if text:
                return text
        except Exception as exc:
            print(f"[主动聊天] 纯文本重试失败: {exc}")

        # ── 兜底：都失败了才返回提示文案 ──
        print(f"[主动聊天] 警告：所有生成策略均失败（模型={model}），返回兜底文案")
        return "我刚刚想和你聊点什么，但这次没有生成出文字，等我一下再试。"

    @staticmethod
    def _looks_incomplete(text: str, finish_reason: str = "") -> bool:
        """识别观察回复中常见的半句话结果。"""
        value = str(text or "").strip()
        if not value:
            return False
        if str(finish_reason or "").lower() in {"length", "max_tokens"}:
            return True
        return bool(re.search(
            r"(?:的|地|得|在|和|跟|与|而且|但是|因为|所以|正在|看起来|似乎)$",
            value,
        ))

    @staticmethod
    def _repair_observation_reply(client, model: str, partial: str, context: str) -> str:
        """让模型补全一次明显被截断的观察回复。"""
        try:
            repair_prompt = (
                "下面这句主动观察回复可能被截断了。请在保留原意的基础上，"
                "把它补成一条完整、自然、简短的中文句子。只输出修正后的完整句子，"
                "不要解释：\n"
                f"原句：{partial}\n"
                f"画面上下文：{context[:1200]}"
            )
            response = client.chat.completions.create(
                model=model,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": "你负责修复被截断的中文主动观察回复。"},
                    {"role": "user", "content": repair_prompt},
                ],
                timeout=20,
            )
            repaired = str(
                getattr(response.choices[0].message, "content", "") or ""
            ).strip()
            if repaired and len(repaired) >= len(partial):
                print(
                    f"[主动聊天] 观察回复补全成功: {len(partial)} -> {len(repaired)}",
                    flush=True,
                )
                return repaired
        except Exception as exc:
            print(f"[主动聊天] 观察回复补全失败，保留原文: {exc}", flush=True)
        return ""

    def _execute_proactive_tools(self, tool_calls) -> list[dict]:
        """执行主动聊天路径下支持的工具调用（单轮）。

        仅支持安全的只读工具：天气查询、记忆检索。
        返回 [{id, name, content}] 列表，用于回填到 messages。
        """
        results = []
        for tc in tool_calls:
            tc_id = getattr(tc, "id", "")
            name = getattr(tc.function, "name", "")
            try:
                import json
                args = json.loads(getattr(tc.function, "arguments", "{}") or "{}")
            except Exception:
                args = {}

            try:
                content = self._run_tool(name, args)
                results.append({"id": tc_id, "name": name, "content": content})
                print(f"[主动聊天] 工具回环: {name} → 成功, {len(content)}字")
            except Exception as exc:
                results.append({
                    "id": tc_id, "name": name,
                    "content": f"工具执行失败: {exc}"
                })
                print(f"[主动聊天] 工具回环: {name} → 失败: {exc}")
        return results

    def _run_tool(self, name: str, args: dict) -> str:
        """执行单个工具，返回文本结果。"""
        name_lower = name.lower()

        # ── 天气查询 ──
        if name_lower in ("get_weather", "weather", "query_weather"):
            from config import get_qweather_config
            from brain.weather import get_user_city_from_memory, get_full_weather
            qw_cfg = get_qweather_config()
            api_key = qw_cfg.get("api_key", "").strip()
            if not api_key:
                return "未配置天气 API"
            city = args.get("city", "").strip()
            if not city:
                city = (qw_cfg.get("default_city") or "").strip()
            if not city:
                city = get_user_city_from_memory()
            if not city:
                return "未指定城市"
            result = get_full_weather(city, api_key=api_key)
            if result and "错误" not in result:
                return result
            return f"天气查询失败: {result or '未知错误'}"

        # ── 记忆检索 ──
        if name_lower in ("search_memory", "query_memory", "recall_memory",
                           "get_memory", "memory_search", "search_facts"):
            keyword = args.get("query", "") or args.get("keyword", "") or args.get("q", "")
            if not keyword:
                keyword = args.get("category", "")
            if not keyword:
                from brain.graph_memory import list_all_facts, ALL_CATEGORIES
                all_mem = list_all_facts()
                facts = []
                for cat in ALL_CATEGORIES:
                    for item in all_mem.get(cat, [])[:3]:
                        facts.append(item["content"])
                return "\n".join(facts[:10]) if facts else "没有记忆"
            from brain.graph_memory import unified_search, format_unified_search_result
            result = unified_search(str(keyword))
            formatted = format_unified_search_result(result)
            return formatted[:1500]

        # 不支持的工具
        return f"工具「{name}」在主动聊天模式下不可用"

    @staticmethod
    def _has_tool_calls(message) -> bool:
        """判断响应是否包含 tool_calls（某些网关即使没传 tools 也会返回）。"""
        tool_calls = getattr(message, "tool_calls", None)
        return bool(tool_calls)

    @staticmethod
    def _response_text(message) -> str:
        """兼容不同 OpenAI 网关的文本字段，避免 content=None 静默丢消息。

        优先级：content > text > output_text > reasoning_content（从思考末尾提取）
        有些网关/模型会把答案放在 reasoning_content 里而 content 为空。
        """
        for field in ("content", "text", "output_text"):
            value = getattr(message, field, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # 回退：从 reasoning_content 末尾提取可用句子
        reasoning = getattr(message, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning.strip():
            # 取思考的最后一段，过滤掉明显是"工具调用计划"的内容
            lines = [l.strip() for l in reasoning.strip().splitlines() if l.strip()]
            for line in reversed(lines[-5:]):  # 只看最后5行
                line = line.strip(" -•*")
                if (line and len(line) > 5
                        and "工具" not in line and "调用" not in line
                        and "search" not in line.lower()
                        and "function" not in line.lower()
                        and not line.startswith("{")):
                    return line
        return ""

    # ── B站冲浪 ──────────────────────────────────────────────

    def _run_bilibili(self):
        print("[B站冲浪] ===== _run_bilibili 开始 =====")
        try:
            from utils.bilibili_history import get_bilibili_history
            bmgr = get_bilibili_history()
            print(f"[B站冲浪] 历史管理器已加载，can_search={bmgr.can_search()}")

            if not bmgr.can_search() and not self._bilibili_ignore_cooldown:
                print("[B站冲浪] 搜索冷却中，跳过")
                self.response_ready.emit("这次 B 站冲浪距离上次搜索还不到冷却时间，我稍后再逛一圈。")
                return

            keywords = bmgr.get_weighted_tags(limit=3)
            print(f"[B站冲浪] 加权标签: {keywords}")

            if not keywords:
                print("[B站冲浪] 无标签，尝试从记忆提取...")
                keywords = self._extract_keywords_from_memory()
                print(f"[B站冲浪] 记忆提取结果: {keywords}")
                if not keywords:
                    # 没有标签或长期记忆时仍完成一次可见的 B 站冲浪，
                    # 使用公开的热门检索词，而不是返回空文本触发其他行为。
                    keywords = ["热门"]
                    print("[B站冲浪] 无兴趣关键词，使用默认检索词: 热门")
                for kw in keywords:
                    bmgr.add_tag(kw, base_score=50)

            from brain.tools import bilibili_search
            best_videos = []
            fallback_videos = []
            used_keyword = ""
            self.data_source_called.emit("bilibili_keywords",
                f"将用 {len(keywords)} 个关键词搜索B站: {', '.join(keywords)}", False, 0)
            for kw in keywords:
                print(f"[B站冲浪] 搜索关键词: {kw}")
                results = bilibili_search(kw, max_results=10)
                print(f"[B站冲浪] 搜索结果: {len(results)} 条")
                self.data_source_called.emit("bilibili_search", f"搜索「{kw}」获得 {len(results)} 条结果", False, 0)
                if results and not fallback_videos:
                    fallback_videos = results[:1]
                fresh_results = bmgr.filter_seen(results)
                print(f"[B站冲浪] 去重后: {len(fresh_results)} 条")
                if fresh_results:
                    best_videos = fresh_results[:1]
                    used_keyword = kw
                    self.data_source_called.emit("bilibili_select", f"精选 {len(best_videos)} 个视频 (关键词: {kw})", False, 0)
                    bmgr.mark_tag_searched(kw)
                    break

            # 即使本轮结果都看过，也要把实际逛到的视频标题和链接发给用户。
            if not best_videos and fallback_videos:
                best_videos = fallback_videos
                used_keyword = used_keyword or (keywords[0] if keywords else "B站")
                print(f"[B站冲浪] 未找到未看视频，使用本轮搜索结果: {len(best_videos)} 条")

            if not best_videos:
                print("[B站冲浪] 无有效视频结果")
                self.response_ready.emit("这次 B 站没有返回可推荐的视频，我下次再逛时会继续尝试。")
                return

            print(f"[B站冲浪] 选中 {len(best_videos)} 个视频，关键词={used_keyword}")
            bmgr.mark_searched()
            record_id = bmgr.add_record(used_keyword, best_videos)
            bmgr.save()

            message = self._generate_bilibili_message(used_keyword, best_videos, record_id)
            message = self._ensure_bilibili_details(message, best_videos)
            print(f"[B站冲浪] 消息已生成，发送 response_ready")
            self.response_ready.emit(message)
        except Exception as e:
            import traceback
            print(f"[B站冲浪] 异常: {e}")
            traceback.print_exc()
            self.error_occurred.emit(str(e))

    def _extract_keywords_from_memory(self) -> list[str]:
        try:
            all_mem = list_all_facts()
            facts = []
            for cat in ALL_CATEGORIES:
                for item in all_mem.get(cat, []):
                    facts.append(item["content"])
            if not facts:
                return []

            client, model = self._get_client()
            user_name = _get_user_name()
            prompt = (
                f"从以下用户{user_name}的记忆中，提取他感兴趣的事物关键词，"
                f"用于去B站搜索视频推荐给他。\n"
                f"只提取具体的事物：爱好、游戏、电影、音乐、想学的技能、喜欢的动漫等。\n"
                f"每个关键词 2~8 个字，返回 1~3 个，空格分隔。如果没有则返回 NONE。\n\n"
                f"记忆：\n" + "\n".join(facts[:20])
            )
            response = client.chat.completions.create(
                model=model,
                max_tokens=50,
                messages=[
                    {"role": "system", "content": "你提取关键词，只返回关键词本身，用空格分隔。"},
                    {"role": "user", "content": prompt},
                ],
            )
            text = response.choices[0].message.content or ""
            text = text.strip()
            if text.upper() == "NONE" or not text:
                return []
            return [kw.strip() for kw in text.split() if kw.strip()][:3]
        except Exception as e:
            print(f"[B站冲浪] 提取关键词失败: {e}")
            return []

    def _generate_bilibili_message_legacy(self, keyword: str, videos: list[dict], record_id: str) -> str:
        video_list = "\n".join(
            f"{i+1}. {v['title']} — up主：{v['author']}，{v['play_count']}播放\n   {v['link']}"
            for i, v in enumerate(videos)
        )
        client, model = self._get_client()
        user_name = _get_user_name()
        snapshot = getattr(self, "_persona_snapshot", None)
        assistant_name = active_assistant_name(snapshot)
        system = _format_prompt(
            "你是莲心，一个聪明、温柔但偶尔有点毒舌的AI助手。"
            "你刚才偷偷去B站逛了一圈，搜了搜{user_name}可能感兴趣的东西，现在要推荐给他。",
            snapshot,
        )
        prompt = (
            f"你搜索了关键词「{keyword}」，找到以下视频：\n{video_list}\n\n"
            f"现在请你作为{assistant_name}，用 1~3 句话推荐给{user_name}。"
            f"语气要轻松自然，带点「我偷偷帮你找了好东西」的感觉。"
            f"必须包含视频链接。直接输出消息内容，不要任何前缀。"
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    # B站推荐必须保留程序拿到的标题和链接。这个定义覆盖上面的旧实现，
    # 让模型只负责润色，模型异常或返回空内容时仍能正常发送结果。
    def _generate_bilibili_message(self, keyword: str, videos: list[dict], record_id: str) -> str:
        video = videos[0] if videos else {}
        title = (video.get("title") or "未命名视频").strip()
        link = (video.get("link") or "").strip()
        description = (video.get("description") or video.get("summary") or "").strip()
        evidence = description[:800] if description else "（当前只有标题和链接，不能据此断言视频具体内容）"
        video_list = (
            f"标题：{title}\n"
            f"UP主：{video.get('author', '')}，{video.get('play_count', 0)}播放\n"
            f"简介/摘要：{evidence}\n"
            f"链接：{link}"
        )
        fallback = self._format_bilibili_fallback(keyword, [video])
        try:
            client, model = self._get_client()
            user_name = _get_user_name()
            snapshot = getattr(self, "_persona_snapshot", None)
            assistant_name = active_assistant_name(snapshot)
            system = _format_prompt(
                "你是莲心，一个聪明、温柔但偶尔有点毒舌的AI助手。"
                "你刚刚偷偷去B站逛了一圈，搜了搜{user_name}可能感兴趣的东西，现在要推荐给他。",
                snapshot,
            )
            prompt = (
                f"你搜索了关键词‘{keyword}’，只找到这一个视频：\n{video_list}\n\n"
                f"现在请你作为{assistant_name}，像平时聊天一样给{user_name}发一条有个人判断的推荐。"
                "只能推荐这一个视频，控制在 2~4 句话。"
                "如果有简介/摘要，基于其中明确的信息评价它可能有意思的地方，不能声称自己完整看过视频。"
                "如果只有标题和链接，就根据标题、关键词和用户兴趣说明为什么值得点开，不能编造剧情、观点或细节。"
                "语气要自然、有一点莲心自己的态度，不要写成机械播报。必须原样保留视频标题和链接。"
                "直接输出消息内容，不要任何前缀。"
            )
            response = client.chat.completions.create(
                model=model,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            text = self._response_text(response.choices[0].message)
            return text or fallback
        except Exception as exc:
            print(f"[B站冲浪] 生成推荐文案失败，使用标题链接兜底: {exc}")
            return fallback

    @staticmethod
    def _format_bilibili_fallback(keyword: str, videos: list[dict]) -> str:
        video = videos[0] if videos else {}
        title = (video.get("title") or "未命名视频").strip()
        link = (video.get("link") or "").strip()
        description = (video.get("description") or video.get("summary") or "").strip()
        if description:
            reason = f"简介里提到“{description[:180]}”，看起来有点东西，我觉得你可以点开看看。"
        else:
            reason = "我现在只能确认标题和链接，但这个标题看起来可能对你的口味，想看时可以点开试试。"
        return (
            f"我刚刚逛到一个可能适合你的 B 站视频：{title}\n"
            f"{reason}\n"
            f"链接：{link}"
        )

    @classmethod
    def _ensure_bilibili_details(cls, message: str, videos: list[dict]) -> str:
        """模型可以润色语气，但不能删掉程序获取到的标题和 URL。"""
        text = (message or "").strip()
        missing = []
        for index, video in enumerate(videos, 1):
            title = (video.get("title") or "").strip()
            link = (video.get("link") or "").strip()
            if (title and title not in text) or (link and link not in text):
                missing.append(f"{index}. {title}\n   链接：{link}")
        if missing:
            suffix = "视频清单：\n" + "\n".join(missing)
            text = f"{text}\n\n{suffix}" if text else suffix
        return text or cls._format_bilibili_fallback("B站", videos)
