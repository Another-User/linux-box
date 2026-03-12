"""Security service plugins (SSH, Fail2ban, Certificates)."""

from __future__ import annotations

import re
from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class SSHPlugin(BaseServicePlugin):
    name = "ssh"
    display_name = "SSH Server"
    systemd_unit = "sshd"
    config_paths = ["/etc/ssh/sshd_config"]
    log_paths = ["/var/log/auth.log"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Config test
        rc, _, stderr = self.run_cmd(["sshd", "-t"])
        config_ok = rc == 0
        checks.append({"name": "config_syntax", "passed": config_ok})
        if not config_ok:
            recommendations.append(f"sshd config error: {stderr.strip()}")

        # Port 22
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_ok = ":22 " in stdout
        checks.append({"name": "port_22", "passed": port_ok or not running})

        # Security checks on config
        config_path = Path("/etc/ssh/sshd_config")
        if config_path.exists():
            content = config_path.read_text()

            # Check root login
            root_login = "PermitRootLogin no" in content or "PermitRootLogin prohibit-password" in content
            checks.append({"name": "root_login_restricted", "passed": root_login})
            if not root_login:
                recommendations.append("Consider setting 'PermitRootLogin no' or 'prohibit-password'")

            # Check password auth
            no_password = "PasswordAuthentication no" in content
            checks.append({"name": "password_auth_disabled", "passed": no_password})
            if not no_password:
                recommendations.append("Consider disabling password authentication in favor of keys")

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "port": 22,
        }

    def validate_config(self) -> list[str]:
        issues = []
        rc, _, stderr = self.run_cmd(["sshd", "-t"])
        if rc != 0:
            issues.append(f"sshd config invalid: {stderr.strip()}")
        return issues


class Fail2banPlugin(BaseServicePlugin):
    name = "fail2ban"
    display_name = "Fail2ban"
    systemd_unit = "fail2ban"
    config_paths = ["/etc/fail2ban/jail.local", "/etc/fail2ban/jail.d/"]
    log_paths = ["/var/log/fail2ban.log"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        if running:
            rc, stdout, _ = self.run_cmd(["fail2ban-client", "status"])
            client_ok = rc == 0
            checks.append({"name": "client_responsive", "passed": client_ok})

            if client_ok:
                # Check if sshd jail is active
                rc, stdout, _ = self.run_cmd(["fail2ban-client", "status", "sshd"])
                sshd_jail = rc == 0
                checks.append({"name": "sshd_jail_active", "passed": sshd_jail})
                if not sshd_jail:
                    recommendations.append("sshd jail is not active in fail2ban")

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        jails = []
        banned = 0
        rc, stdout, _ = self.run_cmd(["fail2ban-client", "status"])
        if rc == 0:
            match = re.search(r"Jail list:\s+(.+)", stdout)
            if match:
                jails = [j.strip() for j in match.group(1).split(",")]

        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "jails": jails,
        }

    def validate_config(self) -> list[str]:
        issues = []
        if not Path("/etc/fail2ban/jail.local").exists() and not Path("/etc/fail2ban/jail.d").exists():
            issues.append("No jail configuration found")
        return issues


class CertificatesPlugin(BaseServicePlugin):
    name = "certificates"
    display_name = "TLS Certificates (Let's Encrypt)"
    systemd_unit = "certbot.timer"
    config_paths = ["/etc/letsencrypt/"]
    log_paths = ["/var/log/letsencrypt/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        # Timer check
        rc, _, _ = self.run_cmd(["systemctl", "is-active", "certbot.timer"])
        timer_active = rc == 0
        checks.append({"name": "renewal_timer", "passed": timer_active})
        if not timer_active:
            recommendations.append("Certbot renewal timer is not active")

        # List certificates
        rc, stdout, _ = self.run_cmd(["certbot", "certificates"])
        has_certs = "Certificate Name" in stdout if rc == 0 else False
        checks.append({"name": "certificates_exist", "passed": has_certs})

        # Check expiry
        if has_certs:
            expiry_matches = re.findall(r"Expiry Date:\s+(.+?)(?:\s+\()", stdout)
            for expiry in expiry_matches:
                if "INVALID" in stdout or "expired" in stdout.lower():
                    recommendations.append(f"Certificate expiring/expired: {expiry}")

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        cert_count = 0
        rc, stdout, _ = self.run_cmd(["certbot", "certificates"])
        if rc == 0:
            cert_count = stdout.count("Certificate Name")

        return {
            "timer_active": self.is_running(),
            "certificates": cert_count,
        }

    def validate_config(self) -> list[str]:
        issues = []
        rc, _, _ = self.run_cmd(["certbot", "--version"])
        if rc != 0:
            issues.append("certbot not installed")
        return issues
