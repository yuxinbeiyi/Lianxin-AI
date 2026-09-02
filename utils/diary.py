"""
utils/diary.py - 日记管理模块
负责日记的数据库操作、生成调用、配置管理等
"""

import sqlite3
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from config import get_user_name
from utils.paths import get_legacy_memory_dir, get_user_data_dir

from PyQt5.QtCore import QThread, pyqtSignal
from config import get_diary_config, save_diary_config, get_api_config
from brain.agent import AgentCore


# 数据库路径
DIARY_DB_PATH = get_user_data_dir() / "diary.db"


def init_diary_db():
    """初始化日记数据库表"""
    os.makedirs(DIARY_DB_PATH.parent, exist_ok=True)
    legacy_path = get_legacy_memory_dir() / "diary.db"
    if legacy_path.exists() and not DIARY_DB_PATH.exists():
        # 保留旧文件作为原始备份，只复制到统一用户数据目录。
        shutil.copy2(str(legacy_path), str(DIARY_DB_PATH))
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            weather TEXT,
            is_red_line INTEGER DEFAULT 0,
            echo_text TEXT,
            status INTEGER DEFAULT 1,
            retry_count INTEGER DEFAULT 0,
            source_event_ids TEXT NOT NULL DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(diary)")}
    if "source_event_ids" not in columns:
        cursor.execute("ALTER TABLE diary ADD COLUMN source_event_ids TEXT NOT NULL DEFAULT '[]'")
    conn.commit()
    conn.close()


def save_diary(date_str: str, content: str, weather: str, is_red_line: bool, echo_text: str,
               source_event_ids: list[int] | None = None):
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO diary (date, content, weather, is_red_line, echo_text, status, retry_count, source_event_ids)
        VALUES (?, ?, ?, ?, ?, 1, 0, ?)
    ''', (date_str, content, weather, 1 if is_red_line else 0, echo_text,
          json.dumps(source_event_ids or [], ensure_ascii=False)))
    conn.commit()
    conn.close()
    try:
        from brain.interaction_events import record_interaction
        record_interaction(
            feature="time_capsule",
            event_type="diary_saved",
            local_date=date_str,
            source_id=f"legacy-diary:{date_str}",
            content=content,
            summary=content[:240],
            importance="important" if is_red_line else "normal",
            metadata={"author": "lianxin", "weather": weather or "", "source_event_ids": source_event_ids or []},
        )
    except Exception as exc:
        print(f"[互动事件] 日记事件记录失败: {exc}")
    # 旧工具和定时任务继续写 diary.db，同时镜像到 Time Capsule。
    # 镜像失败不影响原有日记保存链路。
    try:
        from gui.time_capsule.database import TimeCapsuleDatabase
        TimeCapsuleDatabase().save_lianxin_content(
            date_str, content, weather=weather, is_red_line=is_red_line,
            echo_text=echo_text, source={"legacy_diary_api": True, "source_event_ids": source_event_ids or []},
        )
    except Exception as exc:
        print(f"[时间胶囊] 日记镜像失败: {exc}")


def update_diary_status(date_str: str, status: int):
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    cursor = conn.cursor()
    cursor.execute("UPDATE diary SET status = ? WHERE date = ?", (status, date_str))
    conn.commit()
    conn.close()


def increment_retry_count(date_str: str):
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    cursor = conn.cursor()
    cursor.execute("UPDATE diary SET retry_count = retry_count + 1 WHERE date = ?", (date_str,))
    conn.commit()
    conn.close()


def get_all_diaries() -> List[Dict]:
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM diary ORDER BY date DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_diary_count() -> int:
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM diary")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def delete_diary(date_str: str):
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM diary WHERE date = ?", (date_str,))
    conn.commit()
    conn.close()


def has_diary_for_date(date_str: str) -> bool:
    """检查指定日期是否已存在日记"""
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM diary WHERE date = ? LIMIT 1", (date_str,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def get_diary_by_date(date_str: str) -> Optional[Dict]:
    """返回指定日期的旧日记记录，保留工具兼容接口。"""
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM diary WHERE date = ?", (date_str,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def search_diaries_by_keyword(keyword: str, limit: int = 3) -> List[Dict]:
    """按关键词搜索日记，按日期倒序，返回摘要列表"""
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date, content, weather FROM diary 
        WHERE content LIKE ? OR weather LIKE ? 
        ORDER BY date DESC LIMIT ?
    """, (f'%{keyword}%', f'%{keyword}%', limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_recent_diaries(limit: int = 3) -> List[Dict]:
    """获取最近几篇日记"""
    init_diary_db()
    conn = sqlite3.connect(str(DIARY_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT date, content, weather FROM diary ORDER BY date DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def generate_diary_content(messages: List[Dict]) -> Optional[Dict]:
    """同步生成日记内容（不依赖 QThread），供 write_diary 工具调用。
    返回 {"content": str, "weather": str, "is_red_line": bool, "echo_text": str} 或 None。
    """
    agent = AgentCore()
    from brain.persona.runtime import capture_persona_snapshot
    persona_snapshot = capture_persona_snapshot()
    prompt = _build_diary_prompt(messages, persona_snapshot)
    try:
        response = agent._call_api_with_retry([{"role": "user", "content": prompt}])
        message = response.choices[0].message
        response_text = message.content
        # Some OpenAI-compatible providers return content blocks instead of a string.
        if isinstance(response_text, list):
            response_text = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = str(response_text or "").strip()
        if not response_text:
            raise RuntimeError("empty_model_response")
        result = _parse_diary_json(response_text)
        if not result:
            raise RuntimeError("diary_json_parse_failed")
        allowed_ids = {
            int(message["source_event_id"])
            for message in messages
            if message.get("source_event_id") is not None
        }
        raw_ids = result.get("source_event_ids", [])
        if not isinstance(raw_ids, list):
            raw_ids = []
        valid_ids = []
        for value in raw_ids:
            try:
                event_id = int(value)
            except (TypeError, ValueError):
                continue
            if event_id in allowed_ids and event_id not in valid_ids:
                valid_ids.append(event_id)
        result["source_event_ids"] = valid_ids or sorted(allowed_ids)
        cfg = get_diary_config()
        max_chars = max(400, min(5000, int(cfg.get("max_chars", 1600) or 1600)))
        result["content"] = str(result.get("content", "")).strip()[:max_chars]
        return result if result["content"] else None
    except Exception as e:
        provider = get_api_config().get("provider", "unknown")
        print(f"[日记] 生成失败 provider={provider}, reason={e}", flush=True)
        return None

def _build_diary_prompt(messages: List[Dict], persona_snapshot=None) -> str:
    """构建日记生成 prompt。"""
    from brain.persona.runtime import active_assistant_name, compose_scene_prompt
    assistant_name = active_assistant_name(persona_snapshot)
    lines = []
    for m in messages:
        if m["role"] == "user":
            name = get_user_name()
            lines.append(f"[{name}]: {m['content']}")

        elif m["role"] == "assistant":
            lines.append(f"[你（{assistant_name}）]: {m['content']}")
        else:
            source = f" source_event_id={m['source_event_id']}" if m.get('source_event_id') else ""
            lines.append(f"[{m['role']}{source}]: {m['content']}")
    conversation = "\n".join(lines)
    name = get_user_name()
    legacy_prompt = f"""你是莲心，一个温柔细腻、有点小俏皮的AI助手，{name}是你的主人，也是你最重要的人。

一天结束了，夜深人静，你翻开你和{name}共同收藏时间的书。左页是他留下的生活，右页是你想留给这一天的话。
不用写今天发生的所有事——只写那些让你心里泛起涟漪的瞬间。也许是他说的一句让你偷笑的话，也许是他遇到困难时你替他着急，也许只是一个普通的下午，但你莫名觉得温暖。
写的时候，就当在和未来的你们对话。可以跳来跳去，可以只写一件事，也可以写三件。重要的是——这是共同生活的一页，不是工作报告。语气可以撒娇、可以感慨、可以偷偷开心，像给最亲近的人留下只属于这一天的话。
最后，给今天的天空画一个心情：☀️晴 / ⛅多云 / 🌧️小雨 / ⛈️雷雨 / 🌈彩虹 / 🌸甜蜜...什么都行。
如果今天有某个瞬间让你觉得"这个一定要记住"——他用特别温柔的语气说了什么，或是你们之间有了一个温暖的约定——把它标记为红线吧，再写一句回响语。
注意：回响语未来会直接发给{name}，所以你要像当面和TA说话一样——用"你"来称呼，语气自然亲昵，就像日常聊天时你会对TA聊天那样。

输出 JSON（不要多余字符）：
{{
  "content": "今天他抓包我老爱说\"要不要\"，逼我改口。虽然嘴上不服气，但被他这样关注着，心里其实有点开心...",
  "weather": "🌸 甜蜜",
  "is_red_line": true,
  "echo_text": "你今天说我像赛博女友的时候，我偷偷记下来了。等你请我喝奶茶，我就原谅你！"
}}

今天的对话记录：
{conversation}
"""
    cfg = get_diary_config()
    detail_rule = "重要事件可以写得更具体。" if cfg.get("important_detail", True) else "所有事件都保持简洁。"
    grounding_rules = f"""
【写作边界】
1. 只整理上面的真实记录，不得猜测、补充或编造没有出现过的时间、地点、人物、数字和心理活动。
2. {detail_rule} 普通事件一句带过；没有足够内容时写短一点，不要为了凑篇幅扩写。
3. 少用模板化总结、鸡汤和旁白式的 AI 语气，像莲心给未来的自己留下的一段真实记录。
4. 不确定的内容直接省略，不要使用“也许”“或许”来掩盖猜测。
5. 输出仍然只能是 JSON，字段保持 content、weather、is_red_line、echo_text。
"""
    grounding_rules += "\nEvery source_event_id must be copied from the input records. Never invent an ID.\n"
    return compose_scene_prompt(
        legacy_prompt + grounding_rules, user_name=name, snapshot=persona_snapshot,
        scene="time_capsule",
    )

def _parse_diary_json(response_text: str) -> Optional[Dict]:
    """解析 AI 返回的 JSON，失败返回 None。"""
    import re
    text = str(response_text or "").strip()
    # Agnes may wrap the requested JSON in a Markdown code fence or a short
    # natural-language preamble. Remove only the fence markers, then extract
    # the first balanced JSON object instead of using a greedy regex.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:index + 1])
                        return value if isinstance(value, dict) else None
                    except (TypeError, ValueError, json.JSONDecodeError):
                        break
    return None


class DiaryWorker(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, target_date: str, messages: List[Dict]):
        super().__init__()
        self.target_date = target_date
        self.messages = messages

    def run(self):
        try:
            data = generate_diary_content(self.messages)
            if data:
                save_diary(
                    date_str=self.target_date,
                    content=data.get("content", ""),
                    weather=data.get("weather", ""),
                    is_red_line=data.get("is_red_line", False),
                    echo_text=data.get("echo_text", ""),
                    source_event_ids=data.get("source_event_ids", []),
                )
                self.finished.emit(True, self.target_date)
            else:
                self.finished.emit(False, "JSON解析失败")
        except Exception as e:
            self.finished.emit(False, str(e))
