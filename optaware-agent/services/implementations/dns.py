"""DNS service plugin (BIND9 / named)."""

from __future__ import annotations

import re
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class DNSPlugin(BaseServicePlugin):
    name = "dns"
    display_name = "DNS Server (BIND9)"
    systemd_unit = "named"
    config_paths = ["/etc/bind/named.conf", "/etc/bind/named.conf.local", "/etc/bind/named.conf.options"]
    log_paths = ["/var/log/named/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        # Check if running
        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})
        if not running:
            recommendations.append("DNS service is not running. Start with: systemctl start named")

        # Check config syntax
        rc, stdout, stderr = self.run_cmd(["named-checkconf"])
        config_ok = rc == 0
        checks.append({"name": "config_syntax", "passed": config_ok, "output": stderr or stdout})
        if not config_ok:
            recommendations.append(f"Configuration syntax error: {stderr.strip()}")

        # Check if port 53 is listening
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_53 = ":53 " in stdout or ":53\t" in stdout
        checks.append({"name": "port_53_listening", "passed": port_53})
        if not port_53 and running:
            recommendations.append("DNS is running but not listening on port 53")

        # Test resolution
        rc, stdout, _ = self.run_cmd(["dig", "@localhost", "localhost", "+short", "+time=2"])
        resolves = rc == 0
        checks.append({"name": "local_resolution", "passed": resolves})

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        running = self.is_running()
        rc, stdout, _ = self.run_cmd(["rndc", "status"])
        zones = 0
        if rc == 0:
            match = re.search(r"number of zones:\s+(\d+)", stdout)
            if match:
                zones = int(match.group(1))
        return {
            "running": running,
            "enabled": self.is_enabled(),
            "zones": zones,
            "port": 53,
        }

    def validate_config(self) -> list[str]:
        issues = []
        rc, _, stderr = self.run_cmd(["named-checkconf"])
        if rc != 0:
            issues.append(f"named-checkconf failed: {stderr.strip()}")
        return issues

    def get_metrics(self) -> dict:
        rc, stdout, _ = self.run_cmd(["rndc", "stats"])
        return {"stats_dumped": rc == 0}
