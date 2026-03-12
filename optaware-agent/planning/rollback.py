"""Rollback manager for OptAware agent.

Captures system state before executing actions and can restore it on demand.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from models.actions import Action, ActionStatus

logger = logging.getLogger(__name__)

# Directory where all backups live
_BACKUP_ROOT = Path(tempfile.gettempdir()) / "optaware_snapshots"


class RollbackManager:
    """Manage rollback capability for executed actions."""

    def __init__(self, backup_root: Path | None = None) -> None:
        self._backup_root = backup_root or _BACKUP_ROOT
        self._backup_root.mkdir(parents=True, exist_ok=True)
        # action_id (str) -> snapshot metadata dict
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._load_existing_snapshots()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_snapshot(self, action: Action) -> str:
        """Capture system state before executing an action.

        Backs up any config-file paths referenced in step commands and records
        the current state of related systemd services.

        Returns:
            The snapshot ID (same as action.id string).
        """
        action_id = str(action.id)
        snapshot_dir = self._backup_root / action_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        backed_up_files: list[dict[str, str]] = []
        service_states: dict[str, str] = []

        for step in action.steps:
            # Back up any file paths we can find in the command
            if step.command:
                for token in step.command.split():
                    candidate = Path(token)
                    if candidate.is_absolute() and candidate.is_file():
                        try:
                            backup_path = self._backup_file(
                                str(candidate), str(snapshot_dir)
                            )
                            backed_up_files.append(
                                {
                                    "original": str(candidate),
                                    "backup": backup_path,
                                }
                            )
                        except OSError as exc:
                            logger.warning(
                                "Could not back up %s: %s", candidate, exc
                            )

            # Record service state if this looks like a service action
            service_name = self._extract_service_name(step.command or "")
            if service_name:
                state = self._get_service_state(service_name)
                if state:
                    service_states[service_name] = state

        snapshot: dict[str, Any] = {
            "snapshot_id": action_id,
            "action_id": action_id,
            "action_type": action.action_type.value,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_dir": str(snapshot_dir),
            "backed_up_files": backed_up_files,
            "service_states": service_states,
        }

        meta_path = snapshot_dir / "snapshot.json"
        meta_path.write_text(json.dumps(snapshot, indent=2))

        self._snapshots[action_id] = snapshot
        logger.info(
            "Snapshot %s created: %d file(s), %d service(s)",
            action_id,
            len(backed_up_files),
            len(service_states),
        )
        return action_id

    def rollback(self, action_id: str) -> bool:
        """Restore system state from a previously created snapshot.

        Args:
            action_id: The ID of the action whose pre-execution snapshot
                       should be restored.

        Returns:
            True if rollback succeeded, False otherwise.
        """
        snapshot = self._snapshots.get(action_id)
        if snapshot is None:
            logger.error("No snapshot found for action %s", action_id)
            return False

        success = True

        # Restore backed-up files
        for entry in snapshot.get("backed_up_files", []):
            try:
                self._restore_file(entry["backup"], entry["original"])
                logger.info("Restored %s from %s", entry["original"], entry["backup"])
            except OSError as exc:
                logger.error(
                    "Failed to restore %s: %s", entry["original"], exc
                )
                success = False

        # Restore service states
        for service, state in snapshot.get("service_states", {}).items():
            try:
                self._restore_service_state(service, state)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to restore service %s to state %s: %s",
                    service,
                    state,
                    exc,
                )
                success = False

        if success:
            logger.info("Rollback for action %s completed successfully.", action_id)
        else:
            logger.warning("Rollback for action %s completed with errors.", action_id)

        return success

    def list_snapshots(self) -> list[dict[str, Any]]:
        """Return metadata for all available rollback points."""
        return list(self._snapshots.values())

    def cleanup_old_snapshots(self, max_age_hours: int = 24) -> None:
        """Remove snapshots older than *max_age_hours* from disk and memory."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        to_delete: list[str] = []

        for action_id, snapshot in list(self._snapshots.items()):
            created_raw = snapshot.get("created_at", "")
            try:
                created_at = datetime.fromisoformat(created_raw)
                # Ensure timezone-aware for comparison
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            if created_at < cutoff:
                to_delete.append(action_id)

        for action_id in to_delete:
            snapshot = self._snapshots.pop(action_id)
            snap_dir = Path(snapshot.get("snapshot_dir", ""))
            if snap_dir.exists():
                try:
                    shutil.rmtree(snap_dir)
                    logger.info("Removed old snapshot directory %s", snap_dir)
                except OSError as exc:
                    logger.warning("Could not remove %s: %s", snap_dir, exc)

        if to_delete:
            logger.info(
                "Cleaned up %d snapshot(s) older than %d hour(s).",
                len(to_delete),
                max_age_hours,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _backup_file(self, path: str, dest_dir: str) -> str:
        """Copy *path* to *dest_dir* and return the backup file path."""
        src = Path(path)
        dest = Path(dest_dir) / src.name
        # Avoid collisions if the same filename appears multiple times
        counter = 0
        while dest.exists():
            counter += 1
            dest = Path(dest_dir) / f"{src.stem}_{counter}{src.suffix}"
        shutil.copy2(str(src), str(dest))
        return str(dest)

    def _restore_file(self, backup_path: str, original_path: str) -> None:
        """Overwrite *original_path* with the contents of *backup_path*."""
        src = Path(backup_path)
        dst = Path(original_path)
        if not src.exists():
            raise OSError(f"Backup file not found: {backup_path}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))

    def _load_existing_snapshots(self) -> None:
        """Scan the backup root and reload any persisted snapshot metadata."""
        for meta_path in self._backup_root.glob("*/snapshot.json"):
            try:
                data = json.loads(meta_path.read_text())
                action_id = data.get("action_id")
                if action_id:
                    self._snapshots[action_id] = data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not load snapshot from %s: %s", meta_path, exc)

    @staticmethod
    def _extract_service_name(command: str) -> str:
        """Try to pull a systemd service name out of a command string."""
        tokens = command.split()
        for i, token in enumerate(tokens):
            if token in ("systemctl", "service") and i + 2 < len(tokens):
                # e.g. systemctl restart nginx  ->  nginx
                return tokens[i + 2]
        return ""

    @staticmethod
    def _get_service_state(service: str) -> str:
        """Query the current active state of a systemd service."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    @staticmethod
    def _restore_service_state(service: str, desired_state: str) -> None:
        """Attempt to bring *service* back to *desired_state*."""
        if desired_state == "active":
            subprocess.run(["systemctl", "start", service], timeout=30, check=False)
        elif desired_state in ("inactive", "dead"):
            subprocess.run(["systemctl", "stop", service], timeout=30, check=False)
        else:
            logger.info(
                "Skipping service restore for %s (desired_state=%r)",
                service,
                desired_state,
            )
