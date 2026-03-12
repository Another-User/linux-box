"""Backup service plugin (Restic)."""

from __future__ import annotations

from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class BackupPlugin(BaseServicePlugin):
    name = "backup"
    display_name = "Backup (Restic)"
    systemd_unit = "restic-backup.timer"
    config_paths = ["/etc/restic/"]
    log_paths = []

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        # Check timer
        rc, stdout, _ = self.run_cmd(["systemctl", "is-active", "restic-backup.timer"])
        timer_active = rc == 0
        checks.append({"name": "timer_active", "passed": timer_active})
        if not timer_active:
            recommendations.append("Backup timer is not active")

        # Check restic binary
        rc, _, _ = self.run_cmd(["restic", "version"])
        installed = rc == 0
        checks.append({"name": "restic_installed", "passed": installed})

        # Check repository
        repo_path = "/data/backups/restic"
        if Path(repo_path).exists():
            rc, stdout, stderr = self.run_cmd(["restic", "-r", repo_path, "check", "--no-lock"])
            repo_ok = rc == 0
            checks.append({"name": "repository_integrity", "passed": repo_ok})
            if not repo_ok:
                recommendations.append(f"Repository check failed: {stderr.strip()}")
        else:
            checks.append({"name": "repository_exists", "passed": False})
            recommendations.append(f"Backup repository not found at {repo_path}")

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        # Get last backup time
        last_backup = ""
        rc, stdout, _ = self.run_cmd(["restic", "-r", "/data/backups/restic", "snapshots", "--latest", "1", "--json"])
        if rc == 0 and stdout.strip():
            import json
            try:
                snapshots = json.loads(stdout)
                if snapshots:
                    last_backup = snapshots[0].get("time", "")
            except (json.JSONDecodeError, IndexError):
                pass

        return {
            "timer_active": self.is_running(),
            "enabled": self.is_enabled(),
            "last_backup": last_backup,
            "repository": "/data/backups/restic",
        }

    def validate_config(self) -> list[str]:
        issues = []
        if not Path("/data/backups/restic").exists():
            issues.append("Backup repository not initialized at /data/backups/restic")
        rc, _, _ = self.run_cmd(["restic", "version"])
        if rc != 0:
            issues.append("restic binary not found")
        return issues
