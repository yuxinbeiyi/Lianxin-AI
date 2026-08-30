"""
TodoManager：待办清单管理模块
支持添加、完成、删除、查询待办事项，持久化存储到用户目录/.lianxin/tasks.db
提供自然语言辅助解析（时间、优先级）
"""

import uuid
import re
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Callable

from brain.task_store import TaskStore, get_task_store
from utils.paths import get_user_data_dir

# 优先级映射和显示
PRIORITY_VALUES = ["high", "medium", "low"]
PRIORITY_DISPLAY = {
    "high": "🔴 高",
    "medium": "🟠 中",
    "low": "⚪ 低"
}

# 优先级正则匹配规则
_PRIORITY_PATTERNS = {
    "high": r"高优先级|紧急|重要|必须",
    "low": r"低优先级|不着急|有空"
}

# 尝试导入 dateparser，如果未安装则提示用户
try:
    import dateparser
    HAS_DATEPARSER = True
except ImportError:
    HAS_DATEPARSER = False

# ── 相对时间解析（"10分钟后" / "半小时后" / "2小时后"）─────────────
_RELATIVE_RE = re.compile(
    r"(\d+(?:\.\d+)?|半|[一二两三四五六七八九十]+)\s*个?\s*(小时|分钟|分|秒)\s*(?:之)?后"
)
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT_SECONDS = {"秒": 1, "分": 60, "分钟": 60, "小时": 3600}


def _cn_to_int(text: str) -> Optional[int]:
    """解析简体中文数字（一~九十九），失败返回 None。"""
    if not text:
        return None
    if text in _CN_DIGIT:
        return _CN_DIGIT[text]
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1
        ones = _CN_DIGIT.get(right, 0) if right else 0
        if (left and left not in _CN_DIGIT) or (right and right not in _CN_DIGIT):
            return None
        return tens * 10 + ones
    return None


def _relative_seconds(text: str) -> Optional[int]:
    m = _RELATIVE_RE.search(text)
    if not m:
        return None
    value_raw, unit = m.group(1), m.group(2)
    if value_raw == "半":
        value = 0.5
    else:
        try:
            value = float(value_raw)
        except ValueError:
            value = _cn_to_int(value_raw)
            if value is None:
                return None
    return int(value * _UNIT_SECONDS.get(unit if unit != "分" else "分钟", 0))


def parse_when(when: str, *, now: Optional[datetime] = None) -> datetime:
    """把 when 表达式解析为 datetime。

    支持四种形态（按优先级）：
    1. 相对时间："10分钟后" / "半小时后" / "2小时后"（含中文数字）
    2. ISO datetime（fromisoformat）
    3. "HH:MM" / "8点" / "8点30分"（今天已过则顺延到明天）
    4. 自然语言（dateparser，如"明早八点"）

    解析失败抛 ValueError，消息可直接展示给用户或交回模型追问。
    """
    now = now or datetime.now()
    raw = str(when or "").strip()
    if not raw:
        raise ValueError("没有提供提醒时间")

    seconds = _relative_seconds(raw)
    if seconds is not None and seconds > 0:
        return now + timedelta(seconds=seconds)

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass

    m = re.fullmatch(r"(\d{1,2})\s*[:：点]\s*(?:(\d{1,2})\s*分?)?", raw)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= hour < 24 and 0 <= minute < 60:
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            return dt
        raise ValueError(f"时间「{raw}」无效（小时 0-23，分钟 0-59）")

    if HAS_DATEPARSER:
        try:
            dt = dateparser.parse(raw, settings={"PREFER_DATES_FROM": "future"})
        except Exception:
            dt = None
        if dt:
            return dt

    raise ValueError(f"无法识别时间表达「{raw}」，请换个说法（如：10分钟后 / 08:00 / 明天下午3点）")


class TodoItem:
    """待办事项数据结构"""
    def __init__(self, title: str, due_time: Optional[str] = None,
                 priority: str = "medium", description: str = "",
                 completed: bool = False, created_at: Optional[str] = None,
                 todo_id: Optional[str] = None):
        self.id = todo_id or str(uuid.uuid4())
        self.title = title
        self.description = description
        self.due_time = due_time          # ISO格式字符串，如 "2026-04-21T15:00:00"
        self.priority = priority          # "high", "medium", "low"
        self.completed = completed
        self.created_at = created_at or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "due_time": self.due_time,
            "priority": self.priority,
            "completed": self.completed,
            "created_at": self.created_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TodoItem":
        return cls(
            todo_id=data.get("id"),
            title=data["title"],
            description=data.get("description", ""),
            due_time=data.get("due_time"),
            priority=data.get("priority", "medium"),
            completed=data.get("completed", False),
            created_at=data.get("created_at")
        )


class TodoManager:
    def __init__(self, store: TaskStore | None = None, *, workflow_audit: bool = True):
        self._store = store or get_task_store()
        self._workflow_audit = bool(workflow_audit)
        self._todos: List[TodoItem] = []
        self._observers: List[Callable] = []  # 观察者回调列表
        # 线程锁：LLM 工具线程与 GUI 线程共享本实例，这里保护数据结构读写。
        self._lock = threading.RLock()
        # 贪睡/已提醒状态（易失性运行状态，独立小文件持久化，重启不丢）。
        # 不进 tasks.db：todos 表为固定列，避免 schema 迁移风险。
        self._ack_path = get_user_data_dir() / "todo_ack.json"
        self._ack_state: Dict[str, Dict] = {}  # todo_id -> {last_reminded_date, snooze_until}
        self._load_ack()
        self._load()

    # ── 贪睡/已提醒状态（旁路文件）──────────────────────────

    def _load_ack(self):
        try:
            if self._ack_path.exists():
                import json
                self._ack_state = json.loads(self._ack_path.read_text(encoding="utf-8"))
            else:
                self._ack_state = {}
        except Exception:
            self._ack_state = {}

    def _save_ack(self):
        try:
            import json
            self._ack_path.parent.mkdir(parents=True, exist_ok=True)
            self._ack_path.write_text(
                json.dumps(self._ack_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[待办] 保存提醒状态失败: {e}")

    def _ack_of(self, todo_id: str) -> Dict:
        return self._ack_state.get(todo_id, {})

    # ── 持久化 ─────────────────────────────────────────────

    def _load(self):
        """从统一任务数据库加载待办事项。"""
        try:
            self._todos = [TodoItem.from_dict(item) for item in self._store.list_todos()]
            print(f"[待办] SQLite 加载成功，共 {len(self._todos)} 条待办")
        except Exception as e:
            print(f"[待办] SQLite 加载失败: {e}")
            self._todos = []

    def _save(self):
        """原子保存待办事项到统一任务数据库。"""
        try:
            self._store.replace_todos(t.to_dict() for t in self._todos)
            print(f"[待办] SQLite 保存成功，共 {len(self._todos)} 条待办")
            # 保存成功后通知所有观察者
            self._notify_observers()
        except Exception as e:
            print(f"[待办] SQLite 保存失败: {e}")

    def get_workflow_runs(self, todo_id: str) -> List[dict]:
        """返回与某条待办正式关联的 Workflow 运行。"""
        return self._store.list_workflows("todo", todo_id)

    def link_auto_task(self, todo_id: str, task_id: str, relation: str = "automates") -> None:
        """建立待办与自动化任务的可追溯关系。"""
        self._store.link_tasks("todo", todo_id, "auto_task", task_id, relation)

    def _notify_observers(self):
        """通知所有注册的观察者数据已变化"""
        for cb in self._observers:
            try:
                cb()
            except Exception as e:
                print(f"[待办] 通知观察者失败: {e}")

    def _record_workflow(self, todo: TodoItem, action: str) -> None:
        if not self._workflow_audit:
            return
        try:
            from brain.workflow import get_workflow_store
            workflow_store = get_workflow_store()
            run = workflow_store.begin_run(
                kind="todo", title=f"待办{action}：{todo.title}", channel="task_center",
                metadata={"todo_id": todo.id, "action": action,
                          "title": todo.title, "due_time": todo.due_time},
            )
            run_id = int(run["id"])
            workflow_store.finish_run(run_id, status="success", result_summary=f"待办已{action}")
            self._store.bind_workflow("todo", todo.id, run_id, action)
        except Exception:
            # Workflow 审计不可阻断待办的核心 CRUD。
            pass

    # ── 观察者管理 ─────────────────────────────────────────

    def register_observer(self, callback: Callable):
        """注册观察者，数据变化时调用 callback()"""
        if callback not in self._observers:
            self._observers.append(callback)

    def unregister_observer(self, callback: Callable):
        """注销观察者"""
        if callback in self._observers:
            self._observers.remove(callback)

    # ── 增删改查 ─────────────────────────────────────────

    def add_todo(self, title: str, due_time: Optional[str] = None,
                 priority: str = "medium", description: str = "") -> TodoItem:
        """添加待办事项，返回创建的 TodoItem"""
        if priority not in PRIORITY_VALUES:
            priority = "medium"
        todo = TodoItem(
            title=title,
            due_time=due_time,
            priority=priority,
            description=description
        )
        with self._lock:
            self._todos.append(todo)
            self._save()
        self._record_workflow(todo, "创建")
        return todo

    def get_todos(self, completed: bool = False) -> List[TodoItem]:
        """
        获取待办列表
        :param completed: True 获取所有（包括已完成），False 只获取未完成
        """
        if completed:
            return self._todos.copy()
        else:
            return [t for t in self._todos if not t.completed]

    def get_todo_by_id(self, todo_id: str) -> Optional[TodoItem]:
        for t in self._todos:
            if t.id == todo_id:
                return t
        return None

    def complete_todo(self, todo_id: str) -> bool:
        """标记待办为完成，返回是否成功"""
        with self._lock:
            todo = self.get_todo_by_id(todo_id)
            if todo and not todo.completed:
                todo.completed = True
                self._save()
                self._record_workflow(todo, "完成")
                return True
        return False

    def toggle_complete(self, todo_id: str) -> bool:
        """切换待办的完成状态（已完成→未完成，未完成→已完成），返回是否成功"""
        with self._lock:
            todo = self.get_todo_by_id(todo_id)
            if todo:
                todo.completed = not todo.completed
                self._save()
                self._record_workflow(todo, "完成" if todo.completed else "恢复")
                return True
        return False

    def delete_todo(self, todo_id: str) -> bool:
        """删除待办，返回是否成功"""
        with self._lock:
            for i, t in enumerate(self._todos):
                if t.id == todo_id:
                    del self._todos[i]
                    self._save()
                    self._record_workflow(t, "删除")
                    return True
        return False

    def update_todo(self, todo_id: str, **kwargs) -> bool:
        """更新待办字段，支持 title, due_time, priority, description"""
        with self._lock:
            todo = self.get_todo_by_id(todo_id)
            if not todo:
                return False
            for key, value in kwargs.items():
                if key in ["title", "due_time", "priority", "description"]:
                    setattr(todo, key, value)
            if "priority" in kwargs and kwargs["priority"] not in PRIORITY_VALUES:
                todo.priority = "medium"
            self._save()
            self._record_workflow(todo, "更新")
            return True

    def get_overdue_todos(self) -> List[TodoItem]:
        """返回所有未完成且截止时间已过的待办"""
        now = datetime.now()
        overdue = []
        for t in self._todos:
            if not t.completed and t.due_time:
                try:
                    due_dt = datetime.fromisoformat(t.due_time)
                    if due_dt < now:
                        overdue.append(t)
                except ValueError:
                    continue
        return overdue

    # ── 准点触发支持（配合 AutoTaskScheduler 30s 扫描）───────

    def get_due_todos(self, now: Optional[datetime] = None) -> List[TodoItem]:
        """返回"到期且应当提醒"的待办。

        判定：未完成、due_time <= now，且
        - 处于贪睡期（snooze_until > now）→ 不触发；
        - 贪睡到期（snooze_until <= now）→ 触发；
        - 无贪睡但今天已提醒过（last_reminded_date == 今天）→ 不触发；
        - 其余 → 触发。
        """
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        due: List[TodoItem] = []
        with self._lock:
            for t in self._todos:
                if t.completed or not t.due_time:
                    continue
                try:
                    due_dt = datetime.fromisoformat(t.due_time)
                except ValueError:
                    continue  # 历史脏数据：无效 due_time 不触发也不报错
                if due_dt > now:
                    continue
                ack = self._ack_of(t.id)
                snooze_raw = ack.get("snooze_until") or ""
                if snooze_raw:
                    try:
                        if datetime.fromisoformat(snooze_raw) > now:
                            continue  # 贪睡中
                    except ValueError:
                        pass  # 贪睡时间无效，按无贪睡处理
                elif ack.get("last_reminded_date") == today:
                    continue  # 今天已提醒过且未贪睡
                due.append(t)
        return due

    def mark_reminded(self, todo_id: str, *, now: Optional[datetime] = None) -> None:
        """记录某待办今日已提醒（清除贪睡）。"""
        now = now or datetime.now()
        with self._lock:
            self._ack_state[todo_id] = {
                "last_reminded_date": now.strftime("%Y-%m-%d"),
                "snooze_until": "",
            }
            self._save_ack()

    def snooze_todo(self, todo_id: str, minutes: int = 10) -> bool:
        """把待办提醒推迟 minutes 分钟，返回是否成功。"""
        todo = self.get_todo_by_id(todo_id)
        if not todo:
            return False
        now = datetime.now()
        with self._lock:
            self._ack_state[todo_id] = {
                "last_reminded_date": now.strftime("%Y-%m-%d"),
                "snooze_until": (now + timedelta(minutes=max(1, int(minutes)))).isoformat(),
            }
            self._save_ack()
        return True

    # ── 自然语言辅助解析（提供给外部使用）─────────────────

    @staticmethod
    def parse_time_from_text(text: str) -> Optional[str]:
        """
        从文本中解析出未来时间，返回 ISO 格式字符串，若解析失败返回 None
        """
        if not HAS_DATEPARSER:
            print("[待办] dateparser 未安装，无法解析自然语言时间。请运行 pip install dateparser")
            return None
        # 设置解析偏好为未来时间
        dt = dateparser.parse(text, settings={'PREFER_DATES_FROM': 'future'})
        if dt:
            return dt.isoformat()
        return None

    @staticmethod
    def parse_priority_from_text(text: str) -> str:
        """从文本中解析优先级，返回 'high', 'medium', 'low'"""
        text_lower = text.lower()
        for priority, pattern in _PRIORITY_PATTERNS.items():
            if re.search(pattern, text_lower):
                return priority
        return "medium"

    @staticmethod
    def extract_title_from_text(text: str, due_time_iso: Optional[str] = None) -> str:
        """
        从原始文本中提取待办标题（去掉时间和优先级关键词）
        简单实现：如果解析出了时间，则尝试移除时间部分；否则返回原文本截取前50字
        """
        if len(text) > 50:
            return text[:50] + "..."
        return text


_SHARED_MANAGER: Optional["TodoManager"] = None


def get_todo_manager() -> "TodoManager":
    """进程内共享实例（GUI、LLM 工具线程、调度线程共用同一份数据）。"""
    global _SHARED_MANAGER
    if _SHARED_MANAGER is None:
        _SHARED_MANAGER = TodoManager()
    return _SHARED_MANAGER
