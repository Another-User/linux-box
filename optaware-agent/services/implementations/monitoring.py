"""Monitoring service plugins (Prometheus, Grafana)."""

from __future__ import annotations

from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class PrometheusPlugin(BaseServicePlugin):
    name = "monitoring"
    display_name = "Prometheus"
    systemd_unit = "prometheus"
    config_paths = ["/etc/prometheus/prometheus.yml"]
    log_paths = ["/var/log/prometheus/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Config check
        rc, _, stderr = self.run_cmd(
            ["promtool", "check", "config", "/etc/prometheus/prometheus.yml"]
        )
        config_ok = rc == 0
        checks.append({"name": "config_valid", "passed": config_ok})
        if not config_ok:
            recommendations.append(f"Prometheus config error: {stderr.strip()}")

        # Port 9090
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_ok = ":9090 " in stdout
        checks.append({"name": "port_9090", "passed": port_ok or not running})

        # Check targets via API
        rc, stdout, _ = self.run_cmd(
            ["curl", "-s", "http://localhost:9090/api/v1/targets"]
        )
        if rc == 0 and '"status":"success"' in stdout:
            checks.append({"name": "targets_api", "passed": True})
        elif running:
            checks.append({"name": "targets_api", "passed": False})
            recommendations.append("Prometheus API not responding")

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "port": 9090,
            "config": "/etc/prometheus/prometheus.yml",
        }

    def validate_config(self) -> list[str]:
        issues = []
        config = Path("/etc/prometheus/prometheus.yml")
        if not config.exists():
            issues.append("Config missing: /etc/prometheus/prometheus.yml")
            return issues
        rc, _, stderr = self.run_cmd(
            ["promtool", "check", "config", str(config)]
        )
        if rc != 0:
            issues.append(f"Config invalid: {stderr.strip()}")
        return issues


class GrafanaPlugin(BaseServicePlugin):
    name = "grafana"
    display_name = "Grafana"
    systemd_unit = "grafana-server"
    config_paths = ["/etc/grafana/grafana.ini"]
    log_paths = ["/var/log/grafana/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Port 3000
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_ok = ":3000 " in stdout
        checks.append({"name": "port_3000", "passed": port_ok or not running})

        # Health API
        rc, stdout, _ = self.run_cmd(["curl", "-s", "http://localhost:3000/api/health"])
        healthy = rc == 0 and "ok" in stdout.lower()
        checks.append({"name": "health_api", "passed": healthy or not running})

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "port": 3000,
        }

    def validate_config(self) -> list[str]:
        issues = []
        if not Path("/etc/grafana/grafana.ini").exists():
            issues.append("Config missing: /etc/grafana/grafana.ini")
        return issues
