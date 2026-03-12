"""OptAware central event bus — publish/subscribe with async queue dispatch."""

import asyncio
import logging
from collections import deque
from collections.abc import Callable

from models.events import Event, EventSeverity, EventSource  # noqa: F401 — re-exported

logger = logging.getLogger("optaware.event_processor")

# Sentinel used to register a handler that receives *all* severities.
_ALL_SEVERITIES: object = object()

# Maximum number of recent events retained in the in-memory buffer.
_MAX_RECENT: int = 1000


class EventProcessor:
    """
    Asynchronous event bus for the OptAware agent.

    Producers call :meth:`publish` to put an :class:`~models.events.Event` on
    an internal :class:`asyncio.Queue`.  Consumers register handlers via
    :meth:`subscribe` and receive events through :meth:`process_loop`, which
    must be running in the event loop (typically as an ``asyncio.Task``).

    Handlers may be plain callables or coroutine functions.  Each handler is
    invoked with a single positional argument — the :class:`~models.events.Event`
    — and exceptions are caught and logged without stopping the loop.
    """

    def __init__(self) -> None:
        # Map from EventSeverity (or _ALL_SEVERITIES sentinel) to list of handlers.
        self._handlers: dict[object, list[Callable[[Event], object]]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._recent: deque[Event] = deque(maxlen=_MAX_RECENT)
        self._running: bool = False

    # ------------------------------------------------------------------
    # Subscription API
    # ------------------------------------------------------------------

    def subscribe(
        self,
        severity: EventSeverity | None,
        handler: Callable[[Event], object],
    ) -> None:
        """Register *handler* to be called for events of *severity*.

        Parameters
        ----------
        severity:
            The :class:`~models.events.EventSeverity` level to listen for.
            Pass ``None`` to receive events of *all* severities.
        handler:
            A callable (or coroutine function) that accepts a single
            :class:`~models.events.Event` argument.
        """
        key: object = severity if severity is not None else _ALL_SEVERITIES
        self._handlers.setdefault(key, [])
        if handler not in self._handlers[key]:
            self._handlers[key].append(handler)
            logger.debug(
                "Subscribed handler '%s' to severity '%s'",
                getattr(handler, "__qualname__", repr(handler)),
                severity,
            )

    def unsubscribe(self, handler: Callable[[Event], object]) -> None:
        """Remove *handler* from all severity buckets it was registered under."""
        removed = False
        for bucket in self._handlers.values():
            if handler in bucket:
                bucket.remove(handler)
                removed = True
        if removed:
            logger.debug(
                "Unsubscribed handler '%s'",
                getattr(handler, "__qualname__", repr(handler)),
            )
        else:
            logger.warning(
                "unsubscribe: handler '%s' was not registered",
                getattr(handler, "__qualname__", repr(handler)),
            )

    # ------------------------------------------------------------------
    # Publishing API
    # ------------------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Put *event* onto the internal queue for asynchronous dispatch.

        This method returns immediately; the event is dispatched to handlers
        by :meth:`process_loop`.
        """
        await self._queue.put(event)
        logger.debug(
            "Published event id=%s severity=%s source=%s",
            event.id,
            event.severity,
            event.source,
        )

    # ------------------------------------------------------------------
    # Processing loop
    # ------------------------------------------------------------------

    async def process_loop(self) -> None:
        """
        Continuously dequeue events and dispatch them to registered handlers.

        This coroutine runs until :meth:`stop` is called.  It is intended to
        be launched as an ``asyncio.Task``::

            task = asyncio.create_task(processor.process_loop())

        Cancellation is handled gracefully; any remaining items in the queue
        are *not* processed after cancellation.
        """
        self._running = True
        logger.info("Event processor loop started.")
        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # No event arrived within the timeout; loop and check _running.
                    continue
                self._recent.append(event)
                await self._dispatch(event)
                self._queue.task_done()
        except asyncio.CancelledError:
            logger.info("Event processor loop cancelled.")
            raise
        finally:
            self._running = False
            logger.info("Event processor loop stopped.")

    def stop(self) -> None:
        """Signal the processing loop to exit after the current event (if any)."""
        self._running = False

    # ------------------------------------------------------------------
    # Introspection API
    # ------------------------------------------------------------------

    def get_recent_events(self, limit: int = 50) -> list[Event]:
        """Return the most recent *limit* events (oldest first)."""
        events = list(self._recent)
        return events[-limit:] if limit < len(events) else events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _dispatch(self, event: Event) -> None:
        """Call all handlers registered for *event.severity* and for all-severities."""
        handlers: list[Callable[[Event], object]] = []

        # Handlers subscribed to this specific severity.
        for h in self._handlers.get(event.severity, []):
            handlers.append(h)

        # Handlers subscribed to all severities.
        for h in self._handlers.get(_ALL_SEVERITIES, []):
            if h not in handlers:
                handlers.append(h)

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, handler, event)
            except Exception:
                logger.exception(
                    "Handler '%s' raised an exception while processing event id=%s",
                    getattr(handler, "__qualname__", repr(handler)),
                    event.id,
                )
