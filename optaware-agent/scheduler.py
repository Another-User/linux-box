"""OptAware cron-like task scheduler."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("optaware.scheduler")


@dataclass
class ScheduledTask:
    """Metadata and state for a single recurring scheduled task."""

    name: str
    callback: Callable[[], Any]
    interval_seconds: float
    last_run: datetime | None = field(default=None)
    enabled: bool = field(default=True)


class Scheduler:
    """
    Lightweight, asyncio-compatible task scheduler.

    Tasks are registered with a name, a callable, and an interval expressed in
    seconds.  On every call to :meth:`tick` the scheduler checks whether each
    enabled task is due (i.e. ``now - last_run >= interval``) and, if so,
    executes it without blocking the event loop.

    Both regular functions and coroutine functions are supported as callbacks.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def add_task(
        self,
        name: str,
        callback: Callable[[], Any],
        interval_seconds: float,
        enabled: bool = True,
    ) -> None:
        """Register a new scheduled task.

        Parameters
        ----------
        name:
            Unique identifier for the task.  Raises :class:`ValueError` if a
            task with the same name already exists.
        callback:
            Callable invoked when the task is due.  May be a plain function or
            a coroutine function.
        interval_seconds:
            How often the task should run, in seconds.
        enabled:
            Whether the task is active from the moment it is registered.
        """
        if name in self._tasks:
            raise ValueError(f"Task '{name}' is already registered.")
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}.")
        self._tasks[name] = ScheduledTask(
            name=name,
            callback=callback,
            interval_seconds=interval_seconds,
            enabled=enabled,
        )
        logger.debug("Registered scheduled task '%s' (interval=%ss)", name, interval_seconds)

    def remove_task(self, name: str) -> None:
        """Remove a task by name.  Raises :class:`KeyError` if not found."""
        if name not in self._tasks:
            raise KeyError(f"Task '{name}' not found.")
        del self._tasks[name]
        logger.debug("Removed scheduled task '%s'", name)

    def enable_task(self, name: str) -> None:
        """Enable a previously disabled task."""
        self._get(name).enabled = True
        logger.debug("Enabled task '%s'", name)

    def disable_task(self, name: str) -> None:
        """Disable a task so it is skipped during :meth:`tick`."""
        self._get(name).enabled = False
        logger.debug("Disabled task '%s'", name)

    # ------------------------------------------------------------------
    # Runtime API
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        """
        Check all registered tasks and execute any that are due.

        A task is *due* when it is enabled and either has never run before, or
        ``now - last_run >= interval_seconds``.

        Callbacks are awaited if they are coroutine functions; otherwise they
        are run in the default executor so they do not block the event loop.
        Exceptions raised by a callback are caught and logged so that one
        failing task does not prevent others from running.
        """
        now = datetime.now(tz=timezone.utc)
        for task in list(self._tasks.values()):
            if not task.enabled:
                continue
            if task.last_run is not None:
                elapsed = (now - task.last_run).total_seconds()
                if elapsed < task.interval_seconds:
                    continue
            await self._run_task(task, now)

    async def _run_task(self, task: ScheduledTask, run_time: datetime) -> None:
        """Execute *task* and update its ``last_run`` timestamp."""
        logger.debug("Running scheduled task '%s'", task.name)
        task.last_run = run_time
        try:
            if asyncio.iscoroutinefunction(task.callback):
                await task.callback()
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, task.callback)
        except Exception:
            logger.exception("Scheduled task '%s' raised an exception", task.name)

    # ------------------------------------------------------------------
    # Introspection API
    # ------------------------------------------------------------------

    def get_task_status(self) -> list[dict[str, Any]]:
        """Return a JSON-serialisable status snapshot for all tasks."""
        result: list[dict[str, Any]] = []
        for task in self._tasks.values():
            result.append(
                {
                    "name": task.name,
                    "enabled": task.enabled,
                    "interval_seconds": task.interval_seconds,
                    "last_run": task.last_run.isoformat() if task.last_run else None,
                }
            )
        return result

    def list_tasks(self) -> list[ScheduledTask]:
        """Return all registered :class:`ScheduledTask` objects."""
        return list(self._tasks.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, name: str) -> ScheduledTask:
        """Retrieve a task by name, raising :class:`KeyError` if absent."""
        if name not in self._tasks:
            raise KeyError(f"Task '{name}' not found.")
        return self._tasks[name]
