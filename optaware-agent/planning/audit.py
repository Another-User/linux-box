"""Audit trail for OptAware agent.

Records every action taken by the agent for accountability, compliance, and
post-incident review.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.actions import Action

logger = logging.getLogger(__name__)

_DEFAULT_LOG_FILE = Path("/var/log/optaware/audit.jsonl")


class AuditTrail:
    """Record all actions for accountability.

    Each entry is written as a single JSON line to *log_file* and kept in
    memory in ``_entries`` for fast in-process querying.
    """

    def __init__(self, log_file: Path | None = None) -> None:
        self._log_file: Path = log_file or _DEFAULT_LOG_FILE
        self._entries: list[dict[str, Any]] = []
        self._ensure_log_dir()
        self._load_existing_entries()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        action: Action,
        actor: str,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append a new audit entry for an action.

        Args:
            action: The Action that was (or attempted to be) executed.
            actor: Identifier for who/what triggered the action
                   (e.g. "daemon", "user:alice", "api").
            outcome: Short description of the result
                     (e.g. "completed", "failed", "denied", "rolled_back").
            details: Optional extra context (error messages, diff, etc.).
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action_id": str(action.id),
            "action_type": action.action_type.value,
            "actor": actor,
            "outcome": outcome,
            "status": action.status.value,
            "details": details or {},
        }

        self._entries.append(entry)
        self._write_entry(entry)

        logger.debug(
            "Audit: action=%s type=%s actor=%s outcome=%s",
            entry["action_id"],
            entry["action_type"],
            actor,
            outcome,
        )

    def get_entries(
        self,
        since: datetime | None = None,
        action_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return audit entries matching the given filters.

        Args:
            since: Only return entries with a timestamp >= this value.
                   If timezone-naive, UTC is assumed.
            action_type: Filter by ``action_type`` field value.

        Returns:
            A list of matching entry dicts, in chronological order.
        """
        results = list(self._entries)

        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            results = [
                e for e in results
                if self._parse_ts(e["timestamp"]) >= since
            ]

        if action_type is not None:
            results = [e for e in results if e["action_type"] == action_type]

        return results

    def search(self, query: str) -> list[dict[str, Any]]:
        """Return entries whose JSON representation contains *query* (case-insensitive).

        Args:
            query: Substring to search for across all fields.

        Returns:
            Matching entries, in the order they were recorded.
        """
        lower_query = query.lower()
        results: list[dict[str, Any]] = []
        for entry in self._entries:
            serialised = json.dumps(entry, default=str).lower()
            if lower_query in serialised:
                results.append(entry)
        return results

    def export(self, format: str = "json") -> str:  # noqa: A002
        """Serialise the entire audit trail.

        Args:
            format: Either ``"json"`` (pretty-printed JSON array) or
                    ``"jsonl"`` (one JSON object per line).

        Returns:
            The serialised audit trail as a string.

        Raises:
            ValueError: For unsupported format values.
        """
        if format == "json":
            return json.dumps(self._entries, indent=2, default=str)
        if format == "jsonl":
            return "\n".join(
                json.dumps(entry, default=str) for entry in self._entries
            )
        raise ValueError(
            f"Unsupported export format {format!r}. Use 'json' or 'jsonl'."
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_log_dir(self) -> None:
        """Create the parent directory of the log file if it does not exist."""
        try:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Cannot create audit log directory %s: %s. "
                "Audit entries will be kept in memory only.",
                self._log_file.parent,
                exc,
            )

    def _load_existing_entries(self) -> None:
        """Read any pre-existing JSON-lines from the log file into memory."""
        if not self._log_file.exists():
            return
        try:
            for line in self._log_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._entries.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed audit line: %s", exc)
        except OSError as exc:
            logger.warning("Could not read audit log %s: %s", self._log_file, exc)

    def _write_entry(self, entry: dict[str, Any]) -> None:
        """Append *entry* as a JSON line to the persistent log file."""
        try:
            with self._log_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            logger.warning(
                "Could not write to audit log %s: %s. "
                "Entry is stored in memory only.",
                self._log_file,
                exc,
            )

    @staticmethod
    def _parse_ts(ts_str: str) -> datetime:
        """Parse an ISO-8601 timestamp string into an aware datetime."""
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
