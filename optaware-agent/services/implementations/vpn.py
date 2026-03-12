"""VPN service plugin (WireGuard)."""

from __future__ import annotations

import re
from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class VPNPlugin(BaseServicePlugin):
    name = "vpn"
    display_name = "VPN (WireGuard)"
    systemd_unit = "wg-quick@wg0"
    config_paths = ["/etc/wireguard/wg0.conf"]
    log_paths = []

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Check interface exists
        rc, stdout, _ = self.run_cmd(["wg", "show", "wg0"])
        interface_up = rc == 0
        checks.append({"name": "wg0_interface_up", "passed": interface_up})
        if running and not interface_up:
            recommendations.append("WireGuard service is running but wg0 interface is not up")

        # Check config exists
        config_exists = Path("/etc/wireguard/wg0.conf").exists()
        checks.append({"name": "config_exists", "passed": config_exists})
        if not config_exists:
            recommendations.append("WireGuard config not found: /etc/wireguard/wg0.conf")

        # Check config permissions (should be 600)
        if config_exists:
            mode = oct(Path("/etc/wireguard/wg0.conf").stat().st_mode)[-3:]
            secure = mode in ("600", "640")
            checks.append({"name": "config_permissions", "passed": secure})
            if not secure:
                recommendations.append(f"Config has insecure permissions ({mode}), should be 600")

        # Check peer connectivity
        if interface_up and stdout:
            peers = re.findall(r"latest handshake:\s+(.+)", stdout)
            has_active_peers = len(peers) > 0
            checks.append({"name": "active_peers", "passed": has_active_peers})

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        rc, stdout, _ = self.run_cmd(["wg", "show", "wg0"])
        peer_count = 0
        transfer_rx = 0
        transfer_tx = 0
        if rc == 0:
            peer_count = stdout.count("peer:")
            for match in re.finditer(r"transfer:\s+([\d.]+\s+\S+)\s+received,\s+([\d.]+\s+\S+)\s+sent", stdout):
                transfer_rx += 1
                transfer_tx += 1

        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "interface": "wg0",
            "peers": peer_count,
        }

    def validate_config(self) -> list[str]:
        issues = []
        config = Path("/etc/wireguard/wg0.conf")
        if not config.exists():
            issues.append("Config missing: /etc/wireguard/wg0.conf")
            return issues
        content = config.read_text()
        if "[Interface]" not in content:
            issues.append("Missing [Interface] section in wg0.conf")
        if "PrivateKey" not in content:
            issues.append("Missing PrivateKey in wg0.conf")
        return issues
