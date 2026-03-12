"""Log watcher — watches journald, syslog, and application log files.

Parses log lines, detects severity via pattern matching, and emits
Event objects through a caller-supplied callback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from models.events import Event, EventSeverity, EventSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled severity / error-signature patterns
# ---------------------------------------------------------------------------

_CRITICAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bout of memory\b",
        r"\boom\b",
        r"\bkill process\b",
        r"\bkernel panic\b",
        r"\bsegfault\b",
        r"\bsegmentation fault\b",
        r"\bstack smashing\b",
        r"\bnull pointer dereference\b",
        r"\bdisk full\b",
        r"\bno space left on device\b",
        r"\binput/output error\b",
        r"\bi/o error\b",
        r"\bcritical\b",
        r"\bemergency\b",
        r"\bpanic\b",
    ]
]

_ERROR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\berror\b",
        r"\bfailed\b",
        r"\bfailure\b",
        r"\bexception\b",
        r"\btraceback\b",
        r"\bunhandled\b",
        r"\bpermission denied\b",
        r"\baccess denied\b",
        r"\bconnection refused\b",
        r"\bconnection timed out\b",
        r"\btimeout\b",
        r"\baborted\b",
        r"\bcore dumped\b",
        r"\bkilled\b",
    ]
]

_WARNING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bwarn(ing)?\b",
        r"\bdeprecated\b",
        r"\bretrying\b",
        r"\bretry\b",
        r"\bslow\b",
        r"\bthrottl(e|ing|ed)\b",
        r"\bhigh (cpu|memory|load|usage)\b",
        r"\bunreachable\b",
        r"\bstale\b",
    ]
]


def _classify_severity(message: str) -> EventSeverity:
    """Return the highest matching severity for *message*."""
    for pattern in _CRITICAL_PATTERNS:
        if pattern.search(message):
            return EventSeverity.critical
    for pattern in _ERROR_PATTERNS:
        if pattern.search(message):
            return EventSeverity.error
    for pattern in _WARNING_PATTERNS:
        if pattern.search(message):
            return EventSeverity.warning
    return EventSeverity.info


def _parse_journald_json(raw: str) -> Optional[dict[str, Any]]:
    """Parse a single JSON line emitted by ``journalctl --output=json``."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _journald_record_to_event(record: dict[str, Any]) -> Event:
    """Convert a journald JSON record to an :class:`Event`."""
    message: str = record.get("MESSAGE", "") or ""
    if isinstance(message, list):
        # journald may encode binary messages as integer arrays
        try:
            message = bytes(message).decode("utf-8", errors="replace")
        except Exception:
            message = str(message)

    service_name: Optional[str] = (
        record.get("_SYSTEMD_UNIT")
        or record.get("SYSLOG_IDENTIFIER")
        or record.get("_COMM")
    )
    if service_name:
        # Strip ".service" suffix for readability
        service_name = service_name.removesuffix(".service")

    severity = _classify_severity(message)

    # journald PRIORITY: 0=emerg … 3=err … 5=notice … 7=debug
    priority_str = record.get("PRIORITY", "")
    try:
        prio = int(priority_str)
        if prio <= 2:
            severity = EventSeverity.critical
        elif prio == 3:
            severity = max(severity, EventSeverity.error, key=lambda s: list(EventSeverity).index(s))
        elif prio == 4:
            if severity == EventSeverity.info:
                severity = EventSeverity.warning
    except (ValueError, TypeError):
        pass

    details: dict[str, Any] = {
        k: v
        for k, v in record.items()
        if k not in ("MESSAGE",) and not k.startswith("__")
    }

    return Event(
        severity=severity,
        source=EventSource.log_watcher,
        service_name=service_name,
        message=message,
        details=details,
    )


# ---------------------------------------------------------------------------
# Tail-file handler used by watchdog
# ---------------------------------------------------------------------------

class _TailHandler(FileSystemEventHandler):
    """Watches a single log file for modifications and reads new lines."""

    def __init__(
        self,
        path: str,
        callback: Callable[[Event], None],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__()
        self._path = path
        self._callback = callback
        self._loop = loop
        self._file = open(path, "r", errors="replace")  # noqa: WPS515
        self._file.seek(0, 2)  # seek to end

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.src_path != self._path:
            return
        for line in self._file:
            line = line.rstrip("\n")
            if not line:
                continue
            ev = Event(
                severity=_classify_severity(line),
                source=EventSource.log_watcher,
                service_name=Path(self._path).stem,
                message=line,
                details={"file": self._path},
            )
            self._loop.call_soon_threadsafe(self._callback, ev)

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class LogWatcher:
    """Watch journald, syslog, and arbitrary application log files.

    Parameters
    ----------
    callback:
        Callable invoked with each :class:`~models.events.Event` that is
        produced.  The callable is **always** invoked from the event loop
        thread so it is safe to schedule coroutines from it.
    extra_paths:
        Additional log file paths to tail on startup.
    watch_journald:
        Whether to spawn a ``journalctl --follow`` subprocess (default True).
    """

    _DEFAULT_SYSLOG_PATHS: list[str] = [
        "/var/log/syslog",
        "/var/log/messages",
        "/var/log/auth.log",
        "/var/log/kern.log",
        "/var/log/daemon.log",
    ]

    def __init__(
        self,
        callback: Callable[[Event], None],
        extra_paths: Optional[list[str]] = None,
        watch_journald: bool = True,
    ) -> None:
        self._callback = callback
        self._watch_journald = watch_journald
        self._running = False

        # file-tail state
        self._observer: Optional[Observer] = None
        self._tail_handlers: dict[str, _TailHandler] = {}  # path -> handler
        self._watched_paths: set[str] = set()

        # journald subprocess state
        self._journald_proc: Optional[subprocess.Popen[str]] = None
        self._journald_thread: Optional[threading.Thread] = None

        # seed the default paths
        self._initial_paths: list[str] = list(extra_paths or [])
        for p in self._DEFAULT_SYSLOG_PATHS:
            if p not in self._initial_paths:
                self._initial_paths.append(p)

        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start watching journald and all configured log files."""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()

        # Start watchdog observer
        self._observer = Observer()
        self._observer.start()

        # Add initial paths (only those that actually exist)
        for path in self._initial_paths:
            if Path(path).is_file():
                self._register_tail(path)

        # Start journald watcher
        if self._watch_journald:
            self._start_journald_watcher()

        logger.info(
            "LogWatcher started. Tailing %d file(s), journald=%s",
            len(self._tail_handlers),
            self._watch_journald,
        )

    def stop(self) -> None:
        """Stop all watchers and clean up resources."""
        self._running = False

        if self._journald_proc is not None:
            try:
                self._journald_proc.terminate()
            except Exception:
                pass
            self._journald_proc = None

        if self._journald_thread is not None:
            self._journald_thread.join(timeout=3)
            self._journald_thread = None

        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None

        for handler in list(self._tail_handlers.values()):
            handler.close()
        self._tail_handlers.clear()
        self._watched_paths.clear()

        logger.info("LogWatcher stopped.")

    def add_watch(self, path: str) -> None:
        """Start tailing *path* (must be an existing regular file)."""
        if path in self._watched_paths:
            return
        if not Path(path).is_file():
            logger.warning("LogWatcher.add_watch: %s is not a regular file", path)
            return
        self._register_tail(path)
        logger.info("LogWatcher: added watch for %s", path)

    def remove_watch(self, path: str) -> None:
        """Stop tailing *path*."""
        if path not in self._watched_paths:
            return
        handler = self._tail_handlers.pop(path, None)
        if handler is not None:
            handler.close()
        self._watched_paths.discard(path)
        logger.info("LogWatcher: removed watch for %s", path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_tail(self, path: str) -> None:
        """Register a watchdog watch for *path*."""
        assert self._observer is not None
        assert self._loop is not None
        handler = _TailHandler(path, self._callback, self._loop)
        watch_dir = str(Path(path).parent)
        self._observer.schedule(handler, watch_dir, recursive=False)
        self._tail_handlers[path] = handler
        self._watched_paths.add(path)

    def _start_journald_watcher(self) -> None:
        """Spawn journalctl and read its output in a daemon thread."""
        try:
            self._journald_proc = subprocess.Popen(
                [
                    "journalctl",
                    "--follow",
                    "--output=json",
                    "--lines=0",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            logger.warning("journalctl not found; journald watching disabled.")
            return

        self._journald_thread = threading.Thread(
            target=self._journald_reader_loop,
            daemon=True,
            name="optaware-journald-reader",
        )
        self._journald_thread.start()

    def _journald_reader_loop(self) -> None:
        """Read lines from the journald subprocess (runs in a thread)."""
        assert self._journald_proc is not None
        assert self._journald_proc.stdout is not None
        assert self._loop is not None

        for raw_line in self._journald_proc.stdout:
            if not self._running:
                break
            record = _parse_journald_json(raw_line)
            if record is None:
                continue
            try:
                event = _journald_record_to_event(record)
            except Exception as exc:
                logger.debug("Failed to convert journald record: %s", exc)
                continue
            self._loop.call_soon_threadsafe(self._callback, event)
