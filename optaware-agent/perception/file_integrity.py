"""File integrity monitoring — detect unauthorized changes to critical files."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("optaware.perception.file_integrity")

DEFAULT_WATCH_PATHS = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/group",
    "/etc/sudoers",
    "/etc/ssh/sshd_config",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/fstab",
    "/etc/crontab",
    "/etc/nftables.conf",
]


class FileIntegrityMonitor:
    """Monitor critical system files for unauthorized changes."""

    def __init__(self, watch_paths: list[str] | None = None) -> None:
        self._paths = watch_paths or DEFAULT_WATCH_PATHS
        self._baselines: dict[str, str] = {}
        self._last_check: datetime | None = None
        self._running = False

    def _hash_file(self, path: str) -> str | None:
        """Compute SHA-256 hash of a file."""
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except (FileNotFoundError, PermissionError) as e:
            logger.debug("Cannot hash %s: %s", path, e)
            return None

    def create_baseline(self, paths: list[str] | None = None) -> dict[str, str]:
        """Hash all watched files and store as baseline."""
        target_paths = paths or self._paths
        self._baselines.clear()
        for path in target_paths:
            file_hash = self._hash_file(path)
            if file_hash is not None:
                self._baselines[path] = file_hash
                logger.debug("Baseline for %s: %s", path, file_hash[:16])
        logger.info("Created baseline for %d files", len(self._baselines))
        return dict(self._baselines)

    def check_integrity(self) -> list[dict]:
        """Compare current file hashes against baseline."""
        changes: list[dict] = []
        self._last_check = datetime.now(timezone.utc)

        for path, baseline_hash in self._baselines.items():
            current_hash = self._hash_file(path)

            if current_hash is None:
                changes.append({
                    "path": path,
                    "status": "missing",
                    "message": f"File no longer accessible: {path}",
                    "baseline_hash": baseline_hash,
                    "current_hash": None,
                    "timestamp": self._last_check.isoformat(),
                })
            elif current_hash != baseline_hash:
                changes.append({
                    "path": path,
                    "status": "modified",
                    "message": f"File has been modified: {path}",
                    "baseline_hash": baseline_hash,
                    "current_hash": current_hash,
                    "timestamp": self._last_check.isoformat(),
                })

        # Check for new files in watched directories
        for path in self._paths:
            if path not in self._baselines:
                current_hash = self._hash_file(path)
                if current_hash is not None:
                    changes.append({
                        "path": path,
                        "status": "new",
                        "message": f"New file detected: {path}",
                        "baseline_hash": None,
                        "current_hash": current_hash,
                        "timestamp": self._last_check.isoformat(),
                    })

        if changes:
            logger.warning("Integrity check found %d changes", len(changes))
        else:
            logger.debug("Integrity check passed — no changes detected")

        return changes

    def add_path(self, path: str) -> None:
        """Add a path to monitor."""
        if path not in self._paths:
            self._paths.append(path)
            file_hash = self._hash_file(path)
            if file_hash is not None:
                self._baselines[path] = file_hash

    def remove_path(self, path: str) -> None:
        """Remove a path from monitoring."""
        self._paths = [p for p in self._paths if p != path]
        self._baselines.pop(path, None)

    async def monitor_loop(self, interval: int = 300, callback=None) -> None:
        """Periodically check file integrity."""
        self._running = True
        if not self._baselines:
            self.create_baseline()

        logger.info("File integrity monitor started (interval=%ds)", interval)
        while self._running:
            changes = self.check_integrity()
            if changes and callback:
                for change in changes:
                    await callback(change)
            await asyncio.sleep(interval)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False

    def get_status(self) -> dict:
        """Return current monitor status."""
        return {
            "watched_files": len(self._paths),
            "baselined_files": len(self._baselines),
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "running": self._running,
        }
