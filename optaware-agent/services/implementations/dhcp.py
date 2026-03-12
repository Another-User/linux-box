"""DHCP service plugin (Kea DHCP4)."""

from __future__ import annotations

import json
from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class DHCPPlugin(BaseServicePlugin):
    name = "dhcp"
    display_name = "DHCP Server (Kea)"
    systemd_unit = "kea-dhcp4-server"
    config_paths = ["/etc/kea/kea-dhcp4.conf"]
    log_paths = ["/var/log/kea/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})
        if not running:
            recommendations.append("DHCP service is not running. Start with: systemctl start kea-dhcp4-server")

        # Validate config JSON
        config_valid = False
        config_path = Path("/etc/kea/kea-dhcp4.conf")
        if config_path.exists():
            try:
                with open(config_path) as f:
                    json.load(f)
                config_valid = True
            except json.JSONDecodeError as e:
                recommendations.append(f"Config JSON parse error: {e}")
        else:
            recommendations.append("Config file not found: /etc/kea/kea-dhcp4.conf")
        checks.append({"name": "config_valid_json", "passed": config_valid})

        # Check port 67
        rc, stdout, _ = self.run_cmd(["ss", "-ulnp"])
        port_67 = ":67 " in stdout
        checks.append({"name": "port_67_listening", "passed": port_67 or not running})

        # Check leases file
        lease_paths = ["/var/lib/kea/kea-leases4.csv", "/var/lib/kea/dhcp4.leases"]
        leases_exist = any(Path(p).exists() for p in lease_paths)
        checks.append({"name": "leases_file_exists", "passed": leases_exist or not running})

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        lease_count = 0
        for lease_path in ["/var/lib/kea/kea-leases4.csv"]:
            p = Path(lease_path)
            if p.exists():
                lines = p.read_text().strip().splitlines()
                lease_count = max(0, len(lines) - 1)  # subtract header
                break

        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "active_leases": lease_count,
            "port": 67,
        }

    def validate_config(self) -> list[str]:
        issues = []
        config_path = Path("/etc/kea/kea-dhcp4.conf")
        if not config_path.exists():
            issues.append("Config file missing: /etc/kea/kea-dhcp4.conf")
            return issues
        try:
            with open(config_path) as f:
                config = json.load(f)
            if "Dhcp4" not in config:
                issues.append("Missing 'Dhcp4' top-level key in config")
        except json.JSONDecodeError as e:
            issues.append(f"Invalid JSON: {e}")
        return issues
