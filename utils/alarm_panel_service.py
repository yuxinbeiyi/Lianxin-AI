# API-facing service for the native alarm panel.

from __future__ import annotations

import logging
import threading

from collections import deque
from datetime import datetime

from utils.alarm_manager import AlarmManager
from utils.alarm_runtime_lock import acquire, release
from utils.reminder_manager import get_reminder_manager
from utils.todo_manager import get_todo_manager
from brain.auto_task_manager import get_auto_task_manager

class AlarmPanelService:
    def __init__(self, bridge):
        self.bridge = bridge
        self.lock = threading.RLock()
        self.alarms = AlarmManager()
        self.reminders = get_reminder_manager()
        self.todos = get_todo_manager()
        self.auto_tasks = get_auto_task_manager()
        self.events = deque(maxlen=300)
        self.event_id = 0
        self.running = False
        self.owner = False
        self.thread = None
        self.auto_scheduler = None
        self.reminder_workers = set()
        self.stop_event = threading.Event()
        self.lifecycle_lock = threading.Lock()

    def start(self):
        with self.lifecycle_lock:
            if self.running:
                return self.owner
            if self.owner:
                return False
            self.owner = acquire()
            if not self.owner:
                return False
            self.running = True
            self.stop_event.clear()
            try:
                self.alarms._load()
                self.reminders._load()
                self.todos._load()
                self.auto_tasks._load()
                self._start_auto_scheduler()
                self.thread = threading.Thread(target=self._tick, name="alarm-panel-runtime", daemon=True)
                self.thread.start()
            except Exception:
                self.stop()
                raise
            return True

    def stop(self):
        self.running = False
        self.stop_event.set()
        if self.auto_scheduler:
            self.auto_scheduler.stop()
            if not self.auto_scheduler.wait(5000):
                return False
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                return False
        if any(worker.isRunning() for worker in self.reminder_workers):
            return False
        if self.owner:
            release()
            self.owner = False
        return True

    def _publish(self, kind, **payload):
        with self.lock:
            self.event_id += 1
            event = {**payload, 'id': self.event_id, 'type': kind, 'created_at': datetime.now().isoformat()}
            self.events.append(event)
            return event

    def events_after(self, after=0):
        with self.lock:
            items = [event for event in self.events if event['id'] > int(after)]
            return {'items': items, 'latest': self.event_id}

    def _start_auto_scheduler(self):
        from PyQt5.QtCore import Qt
        from brain.auto_task_scheduler import AutoTaskScheduler
        self.auto_scheduler = AutoTaskScheduler()
        self.auto_scheduler.task_due.connect(self._execute_auto_task, Qt.DirectConnection)
        self.auto_scheduler.task_missed.connect(self._on_missed_task, Qt.DirectConnection)
        self.auto_scheduler.todo_due.connect(self._on_due_todo, Qt.DirectConnection)
        self.auto_scheduler.start()

    def _execute_auto_task(self, task):
        from brain.auto_task_executor import execute_auto_task
        execute_auto_task(task, on_complete=self._on_auto_task_complete)

    def _on_auto_task_complete(self, task_id, success, result):
        task = self.auto_tasks.get_task(task_id)
        name = task.name if task else task_id
        message = f'🤖 自动化任务「{name}」执行完成' if success else f'⚠️ 自动化任务「{name}」执行失败：{str(result)[:180]}'
        self._deliver('auto_task', message, task_id=task_id, ok=bool(success), result=str(result))

    def _on_missed_task(self, task):
        self._deliver('auto_task_missed', f'主人，有个名为「{task.name}」的任务错过时间了。', task_id=task.task_id, name=task.name)

    def _on_due_todo(self, todo):
        self.todos.mark_reminded(todo.id)
        message = f'⏰ 提醒：{todo.title}'
        if todo.due_time:
            try:
                due_label = datetime.fromisoformat(todo.due_time).strftime('%H:%M')
                message += f'（原定时间 {due_label}）' 
            except (TypeError, ValueError): pass
        self._deliver('todo_due', message, todo_id=todo.id, title=todo.title, due_time=todo.due_time)

    def _deliver(self, kind, message, **payload):
        event = self._publish(kind, message=message, **payload)
        try:
            agent = self.bridge.agent()
            agent.get_history_manager().save_message(agent._session_id, 'assistant', message)
            self.bridge.append_agent_context(message)
        except Exception:
            logging.getLogger(__name__).exception("Reminder history delivery failed")
        try:
            self.bridge.speak(message)
        except Exception:
            logging.getLogger(__name__).exception("Reminder speech delivery failed")
        return event

    def _tick(self):
        while not self.stop_event.is_set():
            try:
                self._tick_once()
            except Exception as exc:
                self._publish('runtime_error', message=str(exc))
                logging.getLogger(__name__).exception("Alarm runtime tick failed")
            self.stop_event.wait(1)

    def _tick_once(self):
        pending = []
        with self.lock:
            for alarm in self.alarms.get_due_alarms():
                self.alarms.mark_fired(alarm.id)
                pending.append(('alarm', f'⏰ {alarm.time_str}「{alarm.name}」时间到啦！', {'alarm_id': alarm.id, 'name': alarm.name}))
            for countdown in self.alarms.update_countdowns():
                pending.append(('countdown', f'⏰「{countdown.name}」倒计时结束啦！', {'countdown_id': countdown.id, 'name': countdown.name}))
            self.reminder_workers = {worker for worker in self.reminder_workers if worker.isRunning()}
        for kind, message, payload in pending:
            self._deliver(kind, message, **payload)
        if not self.bridge._busy:
            self._check_reminders()

    def _check_reminders(self):
        with self.lock:
            due = self.reminders.get_due_reminders()
            if not due:
                return
            for reminder in due:
                self.reminders.mark_triggered(reminder['id'])
        names = [item.get('name', '') for item in due]
        from utils.settings import get_settings
        if get_settings().global_smart_reminder:
            from PyQt5.QtCore import Qt
            from workers.smart_reminder_worker import SmartReminderWorker
            worker = SmartReminderWorker('、'.join(names), is_combined=len(names) > 1)
            self.reminder_workers.add(worker)
            worker.finished.connect(lambda text, current=worker: self._on_reminder_ready(current, text), Qt.DirectConnection)
            worker.start()
        else:
            message = f'⏰ 莲心提醒：{names[0]}（记得抽空完成哦）' if len(names) == 1 else f'⏰ 莲心提醒：有{len(names)}个事情需要你注意哦～'
            self._deliver('reminder', message, names=names)

    def _on_reminder_ready(self, worker, text):
        self._deliver('reminder', str(text), names=[])

    def state(self):
        from config import get_todo_auto_confirm, get_todo_auto_suggest
        from utils.settings import get_settings
        from brain.auto_task_executor import _running_tasks, _lock
        with _lock:
            running_ids = list(_running_tasks)
        settings = get_settings()
        with self.lock:
            return {
                'scheduler': {'status': 'running' if self.running and self.owner else 'unknown', 'message': '' if self.owner else '调度器由其他进程持有或尚未启动'},
                'alarms': [item.to_dict() for item in self.alarms.get_alarms()],
                'countdowns': [{'id': item.id, 'name': item.name, 'total_seconds': item.total_seconds, 'remaining_seconds': item.remaining_seconds, 'end_time': item.end_time.isoformat() if item.end_time else None} for item in self.alarms.get_countdowns()],
                'reminders': self.reminders.get_all(),
                'todos': [item.to_dict() for item in self.todos.get_todos(completed=True)],
                'autoTasks': [{**item.to_dict(), 'status': item.status, 'execution_count': item.execution_count} for item in self.auto_tasks.get_all_tasks()],
                'runningAutoTaskIds': running_ids,
                'todoAutoSuggest': get_todo_auto_suggest(),
                'todoAutoConfirm': get_todo_auto_confirm(),
                'smartReminder': bool(getattr(settings, 'global_smart_reminder', False)),
                'autoTaskLogs': self.auto_tasks.get_logs(limit=100),
                'autoTaskWorkflows': {item.task_id: self.auto_tasks.get_workflow_runs(item.task_id) for item in self.auto_tasks.get_all_tasks()},
            }

    def action(self, action, payload):
        self._require(isinstance(payload, dict), '请求格式无效')
        if action == 'runtime.start':
            self._require(self.start(), '无法接管调度：请完全退出旧版 Qt 莲心，或等待上一运行时结束后重试')
            return self.state()
        self._require(self.owner and self.running, '闹钟运行时不在当前 API 进程，暂不能安全修改')
        with self.lock:
            return self._perform_action(action, payload)

    def _perform_action(self, action, payload):
        item_id = str(payload.get('id', ''))
        if action == 'alarm.add':
            name = str(payload.get('name', '')).strip()
            time_str = str(payload.get('time_str', ''))
            repeat = str(payload.get('repeat', 'once'))
            self._require(name and _valid_time(time_str) and repeat in {'once', 'daily', 'weekdays', 'weekends'}, '闹钟信息无效')
            self.alarms.add_alarm(name, time_str, repeat)
        elif action == 'alarm.delete':
            self.alarms.delete_alarm(item_id)
        elif action == 'alarm.toggle':
            self.alarms.toggle_enabled(item_id)
        elif action == 'alarm.snooze':
            from datetime import timedelta
            alarm = next((item for item in self.alarms.get_alarms() if item.id == item_id), None)
            self._require(alarm is not None, '找不到该闹钟')
            snooze_at = datetime.now() + timedelta(minutes=5)
            self.alarms.add_alarm(f'{alarm.name}(贪睡)', snooze_at.strftime('%H:%M'), 'once')
        elif action == 'countdown.start':
            seconds = int(payload.get('total_seconds', 0))
            self._require(str(payload.get('name', '')).strip() and 0 < seconds <= 99 * 3600 + 59 * 60 + 59, '倒计时长度无效')
            self.alarms.add_countdown(str(payload['name']).strip(), seconds)
        elif action == 'countdown.cancel':
            self.alarms.remove_countdown(item_id)
        elif action == 'reminder.add':
            name = str(payload.get('name', '')).strip()
            rule = str(payload.get('rule', 'once'))
            time_str = str(payload.get('time', ''))
            self._require(name and _valid_time(time_str) and rule in {'once', 'daily', 'weekly', 'monthly'}, '提醒信息无效')
            _valid_recurrence(rule, payload)
            self._require(0 <= int(payload.get('advance_minutes', 0)) <= 1440, '提前时间无效')
            self._require(1 <= int(payload.get('interval', 1)) <= 365, '提醒间隔无效')
            self.reminders.add(name, rule, time_str, interval=int(payload.get('interval', 1)), weekdays=payload.get('weekdays', []), day_of_month=payload.get('day_of_month'), advance_minutes=int(payload.get('advance_minutes', 0)), smart_reply=bool(payload.get('smart_reply', False)))
        elif action == 'reminder.delete':
            self.reminders.delete(item_id)
        elif action == 'reminder.toggle':
            row = next((item for item in self.reminders.get_all() if item['id'] == item_id), None)
            if row: self.reminders.enable(item_id, not row.get('enabled', True))
        elif action == 'todo.add':
            title = str(payload.get('title', '')).strip()
            priority = str(payload.get('priority', 'medium'))
            self._require(title and priority in {'low', 'medium', 'high'}, '待办内容无效')
            self.todos.add_todo(title, _local_due_time(payload.get('due_time')), priority, str(payload.get('description', '')))
        elif action == 'todo.delete':
            self.todos.delete_todo(item_id)
        elif action == 'todo.toggle':
            self.todos.toggle_complete(item_id)
        elif action == 'todo.complete':
            self._require(self.todos.complete_todo(item_id), '找不到该待办')
        elif action == 'todo.update':
            fields = {key: payload[key] for key in ('title', 'due_time', 'priority', 'description') if key in payload}
            self._require(not ('priority' in fields and fields['priority'] not in {'low', 'medium', 'high'}), '待办优先级无效')
            self._require('title' not in fields or bool(str(fields['title']).strip()), '待办标题不能为空')
            if 'due_time' in fields:
                fields['due_time'] = _local_due_time(fields['due_time'])
            if fields:
                self._require(self.todos.update_todo(item_id, **fields), '找不到该待办')
        elif action == 'todo.snooze':
            minutes = int(payload.get('minutes', 10))
            self._require(1 <= minutes <= 1440, '稍后提醒时间无效')
            self._require(self.todos.snooze_todo(item_id, minutes), '找不到该待办')
        elif action == 'settings.update':
            from config import save_todo_auto_confirm, save_todo_auto_suggest
            from utils.settings import get_settings
            self._require(all(type(value) is bool for value in payload.values()), '开关值必须是布尔值')
            if 'todoAutoConfirm' in payload: save_todo_auto_confirm(bool(payload['todoAutoConfirm']))
            if 'todoAutoSuggest' in payload: save_todo_auto_suggest(bool(payload['todoAutoSuggest']))
            if 'smartReminder' in payload: get_settings().global_smart_reminder = bool(payload['smartReminder'])
        elif action in {'auto_task.add', 'auto_task.update'}:
            from utils.auto_task_data import AutoTask, SCHEDULE_TYPES, MISSED_ACTIONS
            fields = {
                'name': str(payload.get('name', '')).strip(),
                'description': str(payload.get('description', '')),
                'schedule_type': str(payload.get('schedule_type', 'daily')),
                'schedule_time': str(payload.get('schedule_time', '08:00')),
                'interval_minutes': int(payload.get('interval_minutes', 0)),
                'weekdays': payload.get('weekdays', []),
                'day_of_month': payload.get('day_of_month'),
                'missed_action': str(payload.get('missed_action', 'ask')),
            }
            self._require(fields['name'] and fields['schedule_type'] in SCHEDULE_TYPES, '自动化计划无效')
            self._require(fields['missed_action'] in MISSED_ACTIONS, '错过执行策略无效')
            self._require(_valid_time(fields['schedule_time']) and (fields['schedule_type'] != 'interval' or 1 <= fields['interval_minutes'] <= 1440), '计划时间或间隔无效')
            self._require(fields['schedule_type'] != 'weekly' or bool(fields['weekdays']), '每周任务至少选择一天')
            self._require(fields['schedule_type'] != 'monthly' or 1 <= int(fields['day_of_month'] or 0) <= 31, '每月日期应为 1–31')
            _valid_recurrence(fields['schedule_type'], fields)
            if action.endswith('.add'):
                self.auto_tasks.add_task(AutoTask(**fields))
            elif not self.auto_tasks.update_task(item_id, **fields):
                raise ValueError('找不到该自动化任务')
        elif action == 'auto_task.delete':
            self.auto_tasks.delete_task(item_id)
        elif action == 'auto_task.toggle':
            self.auto_tasks.toggle_enabled(item_id)
        elif action == 'auto_task.pause':
            self.auto_tasks.pause_task(item_id)
        elif action == 'auto_task.resume':
            task = self.auto_tasks.get_task(item_id)
            self._require(task is not None, '找不到该自动化任务')
            if not task.enabled:
                self.auto_tasks.toggle_enabled(item_id)
            self.auto_tasks.resume_task(item_id)
        elif action == 'auto_task.complete':
            self.auto_tasks.complete_task(item_id)
        elif action == 'auto_task.run':
            task = self.auto_tasks.get_task(item_id)
            self._require(task is not None, '找不到该自动化任务')
            self._execute_auto_task(task)
        elif action == 'auto_task.skip':
            task = self.auto_tasks.get_task(item_id)
            self._require(task is not None, '找不到该自动化任务')
            if task.schedule_type == 'once':
                self.auto_tasks.complete_task(item_id)
            else:
                self.auto_tasks.update_task(item_id, next_run=task.compute_next_run())
        elif action == 'auto_task.cancel':
            from brain.auto_task_executor import cancel_task
            cancel_task(item_id)
        elif action == 'auto_task.cleanup':
            self.auto_tasks.cleanup_old_completed_tasks(hours=24)
        else:
            raise ValueError('不支持的闹钟面板操作')
        return self.state()

    @staticmethod
    def _require(condition, message):
        if not condition:
            raise ValueError(message)


def _valid_time(value):
    try:
        if not isinstance(value, str) or len(value) != 5:
            return False
        datetime.strptime(value, '%H:%M')
        return True
    except ValueError:
        return False


def _local_due_time(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.isoformat()


def _valid_recurrence(rule, payload):
    if rule == 'weekly':
        days = payload.get('weekdays', [])
        if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
            raise ValueError('每周至少选择一天，星期范围为 0–6')
    if rule == 'monthly' and not 1 <= int(payload.get('day_of_month') or 0) <= 31:
        raise ValueError('每月日期应为 1–31')
