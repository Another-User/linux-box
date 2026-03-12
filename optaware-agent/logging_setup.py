"""OptAware structured JSON logging with correlation ID support."""

import json
import logging
import logging.handlers
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Module-level context variables
_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
_agent_role_var: ContextVar[str] = ContextVar("agent_role", default="")

logger = logging.getLogger("optaware")


def get_correlation_id() -> str:
    """Return the current correlation ID from context, or empty string if none set."""
    return _correlation_id_var.get()


def set_correlation_id(cid: str) -> None:
    """Set the correlation ID in the current context."""
    _correlation_id_var.set(cid)


def new_correlation_id() -> str:
    """Generate a new UUID-based correlation ID, set it in context, and return it."""
    cid = str(uuid.uuid4())
    set_correlation_id(cid)
    return cid


def get_agent_role() -> str:
    """Return the current agent role from context, or empty string if none set."""
    return _agent_role_var.get()


def set_agent_role(role: str) -> None:
    """Set the agent role in the current context."""
    _agent_role_var.set(role)


class CorrelationIdFilter(logging.Filter):
    """Logging filter that injects correlation_id and agent_role into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()  # type: ignore[attr-defined]
        record.agent_role = get_agent_role()  # type: ignore[attr-defined]
        return True


class StructuredFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects with a standard set of fields
    plus any extra fields attached to the record.

    Standard output shape::

        {
            "timestamp": "2025-01-01T00:00:00.000000+00:00",
            "level": "INFO",
            "logger": "optaware",
            "message": "...",
            "correlation_id": "...",
            ...extra fields...
        }
    """

    # Fields that are part of the standard LogRecord and should not be re-emitted
    # as generic "extra" keys.
    _SKIP_FIELDS: frozenset[str] = frozenset(
        {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        # Let the base class handle exception / stack info formatting into
        # record.exc_text so we can embed it in the JSON payload.
        super().format(record)

        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        payload: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", ""),
        }

        # Embed exception information when present.
        if record.exc_text:
            payload["exception"] = record.exc_text

        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # Append any extra fields the caller attached to the record.
        for key, value in record.__dict__.items():
            if key not in self._SKIP_FIELDS and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> logging.Logger:
    """
    Configure the root ``optaware`` logger with structured JSON output.

    Parameters
    ----------
    level:
        One of ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
    log_file:
        Optional path to a rotating log file.  When *None* output goes only to
        *stderr*.

    Returns
    -------
    logging.Logger
        The configured ``optaware`` logger.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger("optaware")
    root_logger.setLevel(numeric_level)

    # Remove any handlers that may have been added by a previous call or by the
    # logging framework's default configuration.
    root_logger.handlers.clear()

    formatter = StructuredFormatter()
    corr_filter = CorrelationIdFilter()

    # Always emit to stderr.
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(numeric_level)
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(corr_filter)
    root_logger.addHandler(stderr_handler)

    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MiB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(corr_filter)
        root_logger.addHandler(file_handler)

    # Prevent log records from bubbling up to the root Python logger.
    root_logger.propagate = False

    return root_logger
