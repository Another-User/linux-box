"""Web server plugin (nginx)."""

from __future__ import annotations

import re
from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class WebPlugin(BaseServicePlugin):
    name = "web"
    display_name = "Web Server (nginx)"
    systemd_unit = "nginx"
    config_paths = ["/etc/nginx/nginx.conf", "/etc/nginx/sites-enabled/"]
    log_paths = ["/var/log/nginx/access.log", "/var/log/nginx/error.log"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Config syntax check
        rc, stdout, stderr = self.run_cmd(["nginx", "-t"])
        # nginx -t outputs to stderr even on success
        config_ok = rc == 0
        checks.append({"name": "config_syntax", "passed": config_ok, "output": stderr})
        if not config_ok:
            recommendations.append(f"nginx config error: {stderr.strip()}")

        # Check ports 80/443
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_80 = ":80 " in stdout
        port_443 = ":443 " in stdout
        checks.append({"name": "port_80_listening", "passed": port_80 or not running})
        checks.append({"name": "port_443_listening", "passed": port_443 or not running})

        # Check enabled sites
        sites_path = Path("/etc/nginx/sites-enabled")
        if sites_path.exists():
            sites = list(sites_path.iterdir())
            checks.append({"name": "sites_configured", "passed": len(sites) > 0})
            if not sites:
                recommendations.append("No sites enabled in /etc/nginx/sites-enabled/")

        # Check error log for recent issues
        error_log = Path("/var/log/nginx/error.log")
        if error_log.exists():
            try:
                content = error_log.read_text()
                recent_errors = [l for l in content.splitlines()[-20:] if "error" in l.lower() or "crit" in l.lower()]
                checks.append({"name": "no_recent_errors", "passed": len(recent_errors) == 0})
                if recent_errors:
                    recommendations.append(f"Found {len(recent_errors)} recent errors in nginx error log")
            except PermissionError:
                pass

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        sites = []
        sites_path = Path("/etc/nginx/sites-enabled")
        if sites_path.exists():
            sites = [f.name for f in sites_path.iterdir()]

        rc, stdout, _ = self.run_cmd(["nginx", "-v"])
        version = ""
        if rc == 0 or stdout or True:
            # nginx -v outputs to stderr
            rc2, _, stderr = self.run_cmd(["nginx", "-v"])
            match = re.search(r"nginx/([\d.]+)", stderr)
            if match:
                version = match.group(1)

        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "version": version,
            "sites": sites,
            "ports": [80, 443],
        }

    def validate_config(self) -> list[str]:
        issues = []
        rc, _, stderr = self.run_cmd(["nginx", "-t"])
        if rc != 0:
            issues.append(f"Config validation failed: {stderr.strip()}")
        return issues
