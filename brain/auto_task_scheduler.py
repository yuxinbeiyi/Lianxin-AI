# -*- coding: utf-8 -*-
"""
AutoTaskScheduler — 自动化任务调度线程
以 QThread 方式运行，每 30 秒检查一次到期任务，通过信号通知主线程。
"""

import logging
from PyQt5.QtCore import QThread, pyqtSignal

from brain.auto_task_manager import get_auto_task_manager
from utils.auto_task_data import AutoTask

logger = logging.getLogger("AutoTaskScheduler")


class AutoTaskScheduler(QThread):
    """后台调度线程，定期检查到期任务并发射信号。"""

    task_due = pyqtSignal(object)          # 单个任务到期 → AutoTask
    task_missed = pyqtSignal(object)       # 错过任务需要询问 → AutoTask
    todo_due = pyqtSignal(object)          # 待办到期 → TodoItem
    status_changed = pyqtSignal()          # 任务列表有变化

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = get_auto_task_manager()
        self._running = False
        self._check_interval_ms = 30_000   # 30 秒
        self._missed_check_count = 0

    def run(self):
        self._running = True
        logger.info("[AutoTaskScheduler] 调度线程已启动，检查间隔: 30s")
        print("[AutoTaskScheduler] 调度线程已启动，检查间隔: 30s")

        self._cleanup_check_count = 0

        while self._running:
            try:
                self._check_due_tasks()
                self._check_due_todos()
                self._check_missed_tasks()
                # P4: 每 10 分钟自动清理已完成的 once 任务
                self._cleanup_check_count += 1
                if self._cleanup_check_count % 20 == 0:  # 20 * 30s = 10 分钟
                    cleaned = self._manager.cleanup_old_completed_tasks(hours=24)
                    if cleaned > 0:
                        self.status_changed.emit()
            except Exception as e:
                logger.error(f"[AutoTaskScheduler] 检查异常: {e}")
                print(f"[AutoTaskScheduler] 检查异常: {e}")

            self.msleep(self._check_interval_ms)

    def stop(self):
        self._running = False
        logger.info("[AutoTaskScheduler] 调度线程已停止")
        print("[AutoTaskScheduler] 调度线程已停止")

    def _check_due_tasks(self):
        from brain.auto_task_executor import _running_tasks as _exec_running
        due = self._manager.get_due_tasks()
        # 过滤掉正在执行中的任务，避免日志轰炸
        due = [t for t in due if t.task_id not in _exec_running]
        if due:
            print(f"[AutoTaskScheduler] 本轮检查发现 {len(due)} 个到期任务")
        for task in due:
            logger.info(f"[AutoTaskScheduler] 任务到期: {task.name} (ID: {task.task_id})")
            print(f"[AutoTaskScheduler] 任务到期 -> {task.name} (ID:{task.task_id})")
            self.task_due.emit(task)

    def _check_missed_tasks(self):
        self._missed_check_count += 1
        # 每 2 分钟（4 个 30s 周期）检查一次错过任务
        if self._missed_check_count % 4 != 0:
            return
        missed = self._manager.get_missed_tasks()
        if missed:
            print(f"[AutoTaskScheduler] 发现 {len(missed)} 个错过任务")
        for task in missed:
            self._manager.mark_asked(task.task_id)
            logger.info(f"[AutoTaskScheduler] 错过任务: {task.name} (ID: {task.task_id})")
            print(f"[AutoTaskScheduler] 错过任务 -> {task.name} (ID:{task.task_id})，将询问用户")
            self.task_missed.emit(task)

    def _check_due_todos(self):
        """扫描到期待办（待办不走 GUI 30 分钟轮询，统一在此准点触发）。"""
        try:
            from utils.todo_manager import get_todo_manager
            due = get_todo_manager().get_due_todos()
            for todo in due:
                logger.info(f"[AutoTaskScheduler] 待办到期: {todo.title}")
                print(f"[AutoTaskScheduler] 待办到期 -> {todo.title}")
                self.todo_due.emit(todo)
        except Exception as e:
            logger.error(f"[AutoTaskScheduler] 待办检查异常: {e}")
            print(f"[AutoTaskScheduler] 待办检查异常: {e}")