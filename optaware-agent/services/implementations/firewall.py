"""Firewall service plugin (nftables)."""

from __future__ import annotations

from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class FirewallPlugin(BaseServicePlugin):
    name = "firewall"
    display_name = "Firewall (nftables)"
    systemd_unit = "nftables"
    config_paths = ["/etc/nftables.conf"]
    log_paths = ["/var/log/nftables.log"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Check nft ruleset
        rc, stdout, stderr = self.run_cmd(["nft", "list", "ruleset"])
        has_rules = rc == 0 and len(stdout.strip()) > 10
        checks.append({"name": "rules_loaded", "passed": has_rules})
        if not has_rules and running:
            recommendations.append("nftables is running but no rules are loaded")

        # Validate config
        rc, _, stderr = self.run_cmd(["nft", "-c", "-f", "/etc/nftables.conf"])
        config_ok = rc == 0
        checks.append({"name": "config_syntax", "passed": config_ok})
        if not config_ok:
            recommendations.append(f"Config syntax error: {stderr.strip()}")

        # Check for basic input chain
        if has_rules:
            has_input = "chain input" in stdout.lower()
            checks.append({"name": "input_chain_exists", "passed": has_input})
            if not has_input:
                recommendations.append("No input chain defined — system may be unprotected")

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        rc, stdout, _ = self.run_cmd(["nft", "list", "ruleset"])
        rule_count = stdout.count("\n") if rc == 0 else 0
        tables = []
        if rc == 0:
            for line in stdout.splitlines():
                if line.strip().startswith("table "):
                    tables.append(line.strip())

        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "rule_lines": rule_count,
            "tables": tables,
        }

    def validate_config(self) -> list[str]:
        issues = []
        if not Path("/etc/nftables.conf").exists():
            issues.append("Config file missing: /etc/nftables.conf")
            return issues
        rc, _, stderr = self.run_cmd(["nft", "-c", "-f", "/etc/nftables.conf"])
        if rc != 0:
            issues.append(f"Syntax error: {stderr.strip()}")
        return issues
