"""File integrity monitor — baseline and periodic hash checks for critical files.

Uses SHA-256 to hash each file.  Baseline hashes are stored in memory
(and optionally persisted to a JSON file).  Periodic checks compare
the current hashes against the baseline and report any changes via
Event objects passed to a caller-supplied callback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.events import Event, EventSeverity, EventSource

logger = logging.getLogger(__name__)

# Critical system files watched by default
_DEFAULT_CRITICAL_PATHS: list[str] = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/group",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/sudoers.d",
    "/etc/ssh/sshd_config",
    "/etc/ssh/ssh_config",
    "/etc/pam.d/common-auth",
    "/etc/pam.d/common-account",
    "/etc/pam.d/su",
    "/etc/crontab",
    "/etc/hosts",
    "/etc/hostname",
    "/etc/resolv.conf",
    "/etc/nsswitch.conf",
    "/etc/ld.so.conf",
    "/etc/profile",
    "/etc/environment",
    "/boot/grub/grub.cfg",
    "/etc/fstab",
    "/etc/nftables.conf",
]


def _sha256_path(path: str) -> Optional[str]:
    """Return the hex SHA-256 digest of *path*.

    For regular files the content is hashed; for directories the sorted
    directory listing is hashed so that new/removed entries are detected.
    Returns None if the path cannot be read.
    """
    p = Path(path)
    if p.is_file():
        h = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, PermissionError) as exc:
            logger.debug("file_integrity: cannot hash file %s: %s", path, exc)
            return None
    if p.is_dir():
        try:
            names = sorted(os.listdir(path))
            return hashlib.sha256("\n".join(names).encode()).hexdigest()
        except OSError as exc:
            logger.debug("file_integrity: cannot list dir %s: %s", path, exc)
            return None
    return None


class FileIntegrityMonitor:
    """Monitor critical system files for unauthorised changes.

    Parameters
    ----------
    callback:
        Optional callable invoked with :class:`~models.events.Event` objects
        whenever a violation is detected.
    extra_paths:
        Additional paths (files or directories) to monitor beyond the
        built-in default list.
    baseline_file:
        Optional path to a JSON file used to persist and restore the baseline
        hashes across restarts.
    """

    def __init__(
        self,
        callback: Optional[Callable[[Event], None]] = None,
        extra_paths: Optional[list[str]] = None,
        baseline_file: Optional[str] = None,
    ) -> None:
        self._callback = callback
        self._baseline_file = baseline_file
        self._baselines: dict[str, str] = {}  # path -> sha256 hex digest
        self._running = False
        self._last_check: Optional[datetime] = None

        self._monitored_paths: list[str] = list(_DEFAULT_CRITICAL_PATHS)
        if extra_paths:
            for ep in extra_paths:
                if ep not in self._monitored_paths:
                    self._monitored_paths.append(ep)

        # Restore persisted baseline if available
        if baseline_file and Path(baseline_file).is_file():
            self._load_baseline_from_file(baseline_file)

    # ------------------------------------------------------------------
    # Baseline management
    # ------------------------------------------------------------------

    def create_baseline(self, paths: Optional[list[str]] = None) -> dict[str, str]:
        """Hash all given *paths* (or the default monitored paths) and store
        the results as the current baseline.

        Only paths that can be successfully hashed are stored; inaccessible
        paths are silently skipped and logged at DEBUG level.

        Returns the new baseline mapping.
        """
        target_paths = paths if paths is not None else self._monitored_paths
        new_baseline: dict[str, str] = {}
        for path in target_paths:
            digest = _sha256_path(path)
            if digest is not None:
                new_baseline[path] = digest

        self._baselines = new_baseline
        logger.info(
            "FileIntegrityMonitor: baseline created for %d path(s).",
            len(self._baselines),
        )

        if self._baseline_file:
            self._persist_baseline()

        return dict(self._baselines)

    def get_baseline(self) -> dict[str, str]:
        """Return a copy of the current baseline mapping."""
        return dict(self._baselines)

    def add_path(self, path: str) -> None:
        """Add *path* to the monitored set and hash it into the baseline."""
        if path not in self._monitored_paths:
            self._monitored_paths.append(path)
        digest = _sha256_path(path)
        if digest is not None:
            self._baselines[path] = digest

    def remove_path(self, path: str) -> None:
        """Remove *path* from monitoring and the baseline."""
        self._monitored_paths = [p for p in self._monitored_paths if p != path]
        self._baselines.pop(path, None)

    # ------------------------------------------------------------------
    # Integrity check
    # ------------------------------------------------------------------

    def check_integrity(self) -> list[dict[str, Any]]:
        """Compare current hashes against the baseline.

        Returns a list of violation dicts.  Each dict contains:

        * ``path`` — the affected file path
        * ``status`` — ``"modified"`` | ``"missing"`` | ``"new"``
        * ``message`` — human-readable description
        * ``baseline_hash`` — hex digest at baseline time (or None)
        * ``current_hash`` — current hex digest (or None if file gone)
        * ``timestamp`` — ISO-8601 timestamp of the check

        Any violation also fires the registered callback with an
        appropriate :class:`~models.events.Event`.
        """
        violations: list[dict[str, Any]] = []
        self._last_check = datetime.now(timezone.utc)
        checked_at = self._last_check.isoformat()

        # Check all baselined paths for modification or disappearance
        for path, baseline_hash in self._baselines.items():
            current_hash = _sha256_path(path)
            if current_hash is None:
                violation: dict[str, Any] = {
                    "path": path,
                    "status": "missing",
                    "message": f"File no longer accessible: {path}",
                    "baseline_hash": baseline_hash,
                    "current_hash": None,
                    "timestamp": checked_at,
                }
                violations.append(violation)
                self._emit_violation(violation)
            elif current_hash != baseline_hash:
                violation = {
                    "path": path,
                    "status": "modified",
                    "message": f"File has been modified: {path}",
                    "baseline_hash": baseline_hash,
                    "current_hash": current_hash,
                    "timestamp": checked_at,
                }
                violations.append(violation)
                self._emit_violation(violation)

        # Detect files that are monitored but not yet in the baseline
        for path in self._monitored_paths:
            if path in self._baselines:
                continue
            current_hash = _sha256_path(path)
            if current_hash is not None:
                violation = {
                    "path": path,
                    "status": "new",
                    "message": f"New file detected: {path}",
                    "baseline_hash": None,
                    "current_hash": current_hash,
                    "timestamp": checked_at,
                }
                violations.append(violation)
                self._emit_violation(violation)

        if violations:
            logger.warning(
                "FileIntegrityMonitor: %d violation(s) detected.", len(violations)
            )
        else:
            logger.debug("FileIntegrityMonitor: all paths clean.")

        return violations

    # ------------------------------------------------------------------
    # Periodic monitor loop
    # ------------------------------------------------------------------

    async def monitor_loop(self, interval: int = 300) -> None:
        """Periodically run :meth:`check_integrity` every *interval* seconds.

        If no baseline exists when the loop starts, one is created
        automatically from the monitored paths.
        """
        if not self._baselines:
            logger.info(
                "FileIntegrityMonitor: no baseline found; creating one now."
            )
            self.create_baseline()

        self._running = True
        logger.info(
            "FileIntegrityMonitor loop started (interval=%ds, paths=%d).",
            interval,
            len(self._monitored_paths),
        )
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            try:
                self.check_integrity()
            except Exception as exc:
                logger.error("FileIntegrityMonitor: check error: %s", exc)

    def stop(self) -> None:
        """Signal the monitor loop to stop after the current sleep."""
        self._running = False

    def get_status(self) -> dict[str, Any]:
        """Return current monitor status."""
        return {
            "watched_files": len(self._monitored_paths),
            "baselined_files": len(self._baselines),
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "running": self._running,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_violation(self, violation: dict[str, Any]) -> None:
        if self._callback is None:
            return
        status = violation["status"]
        path = violation["path"]
        # Elevate to critical for high-value targets
        high_value = any(
            token in path.lower()
            for token in ("shadow", "sudoers", "sshd_config", "passwd")
        )
        if status == "modified" and high_value:
            severity = EventSeverity.critical
        elif status in ("modified", "missing"):
            severity = EventSeverity.error
        else:
            severity = EventSeverity.warning

        self._callback(
            Event(
                severity=severity,
                source=EventSource.system,
                message=violation["message"],
                details=violation,
            )
        )

    def _persist_baseline(self) -> None:
        assert self._baseline_file is not None
        try:
            with open(self._baseline_file, "w", encoding="utf-8") as fh:
                json.dump(self._baselines, fh, indent=2)
            logger.debug(
                "FileIntegrityMonitor: baseline persisted to %s.", self._baseline_file
            )
        except OSError as exc:
            logger.warning(
                "FileIntegrityMonitor: could not persist baseline: %s", exc
            )

    def _load_baseline_from_file(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._baselines = {str(k): str(v) for k, v in data.items()}
                logger.info(
                    "FileIntegrityMonitor: restored %d baseline entries from %s.",
                    len(self._baselines),
                    path,
                )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "FileIntegrityMonitor: could not load baseline from %s: %s", path, exc
            )
