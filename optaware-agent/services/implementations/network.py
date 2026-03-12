"""Network service plugins (NTP, Samba, NFS)."""

from __future__ import annotations

from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class NTPPlugin(BaseServicePlugin):
    name = "ntp"
    display_name = "NTP (chrony)"
    systemd_unit = "chronyd"
    config_paths = ["/etc/chrony/chrony.conf"]
    log_paths = ["/var/log/chrony/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Check sync status
        rc, stdout, _ = self.run_cmd(["chronyc", "tracking"])
        synced = rc == 0 and "Leap status     : Normal" in stdout
        checks.append({"name": "time_synced", "passed": synced})
        if running and not synced:
            recommendations.append("System clock may not be synchronized")

        # Check sources
        rc, stdout, _ = self.run_cmd(["chronyc", "sources"])
        has_sources = rc == 0 and ("^*" in stdout or "^+" in stdout)
        checks.append({"name": "ntp_sources", "passed": has_sources})
        if running and not has_sources:
            recommendations.append("No reachable NTP sources")

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        offset = ""
        rc, stdout, _ = self.run_cmd(["chronyc", "tracking"])
        if rc == 0:
            for line in stdout.splitlines():
                if "System time" in line:
                    offset = line.split(":")[-1].strip()
                    break

        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "time_offset": offset,
        }

    def validate_config(self) -> list[str]:
        issues = []
        if not Path("/etc/chrony/chrony.conf").exists():
            issues.append("Config missing: /etc/chrony/chrony.conf")
        return issues


class SambaPlugin(BaseServicePlugin):
    name = "samba"
    display_name = "Samba File Sharing"
    systemd_unit = "smbd"
    config_paths = ["/etc/samba/smb.conf"]
    log_paths = ["/var/log/samba/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Config check
        rc, stdout, stderr = self.run_cmd(["testparm", "-s", "--suppress-prompt"])
        config_ok = rc == 0
        checks.append({"name": "config_valid", "passed": config_ok})
        if not config_ok:
            recommendations.append(f"Samba config error: {stderr.strip()}")

        # Port 445
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_ok = ":445 " in stdout
        checks.append({"name": "port_445", "passed": port_ok or not running})

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        shares = []
        rc, stdout, _ = self.run_cmd(["smbstatus", "--shares", "--no-pager"])
        if rc == 0:
            for line in stdout.splitlines()[3:]:
                parts = line.split()
                if parts:
                    shares.append(parts[0])

        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "shares": shares,
            "port": 445,
        }

    def validate_config(self) -> list[str]:
        issues = []
        rc, _, stderr = self.run_cmd(["testparm", "-s", "--suppress-prompt"])
        if rc != 0:
            issues.append(f"Config invalid: {stderr.strip()}")
        return issues


class NFSPlugin(BaseServicePlugin):
    name = "nfs"
    display_name = "NFS Server"
    systemd_unit = "nfs-server"
    config_paths = ["/etc/exports"]
    log_paths = []

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Check exports
        exports_path = Path("/etc/exports")
        has_exports = exports_path.exists() and exports_path.read_text().strip()
        checks.append({"name": "exports_configured", "passed": has_exports})
        if not has_exports:
            recommendations.append("No NFS exports configured in /etc/exports")

        # Check active exports
        rc, stdout, _ = self.run_cmd(["exportfs", "-v"])
        active_exports = rc == 0 and stdout.strip()
        checks.append({"name": "active_exports", "passed": active_exports or not running})

        # Port 2049
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_ok = ":2049 " in stdout
        checks.append({"name": "port_2049", "passed": port_ok or not running})

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        exports = []
        rc, stdout, _ = self.run_cmd(["exportfs", "-v"])
        if rc == 0:
            exports = [l.strip().split()[0] for l in stdout.splitlines() if l.strip()]

        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "exports": exports,
            "port": 2049,
        }

    def validate_config(self) -> list[str]:
        issues = []
        if not Path("/etc/exports").exists():
            issues.append("Missing /etc/exports")
        return issues
