"""Tests for the scheduler."""

import asyncio
from datetime import datetime, timezone

from scheduler import Scheduler, ScheduledTask


class TestScheduler:
    def test_add_task(self):
        scheduler = Scheduler()
        scheduler.add_task("test_task", lambda: None, interval_seconds=60)
        tasks = scheduler.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].name == "test_task"

    def test_remove_task(self):
        scheduler = Scheduler()
        scheduler.add_task("task1", lambda: None, interval_seconds=30)
        scheduler.add_task("task2", lambda: None, interval_seconds=60)
        scheduler.remove_task("task1")
        tasks = scheduler.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].name == "task2"

    def test_enable_disable(self):
        scheduler = Scheduler()
        scheduler.add_task("task1", lambda: None, interval_seconds=30)
        scheduler.disable_task("task1")
        task = scheduler.list_tasks()[0]
        assert not task.enabled

        scheduler.enable_task("task1")
        task = scheduler.list_tasks()[0]
        assert task.enabled

    def test_task_status(self):
        scheduler = Scheduler()
        scheduler.add_task("t1", lambda: None, interval_seconds=30)
        scheduler.add_task("t2", lambda: None, interval_seconds=60, enabled=False)
        status = scheduler.get_task_status()
        assert len(status) == 2
