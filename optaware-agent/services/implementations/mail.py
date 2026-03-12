"""Mail service plugins (Postfix SMTP + Dovecot IMAP)."""

from __future__ import annotations

from pathlib import Path
from services.implementations.base import BaseServicePlugin, DiagnosticResult


class MailPlugin(BaseServicePlugin):
    name = "mail"
    display_name = "Mail Server (Postfix + Dovecot)"
    systemd_unit = "postfix"
    config_paths = ["/etc/postfix/main.cf", "/etc/postfix/master.cf", "/etc/dovecot/dovecot.conf"]
    log_paths = ["/var/log/mail.log", "/var/log/mail.err"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        # Check Postfix
        rc_postfix, _, _ = self.run_cmd(["systemctl", "is-active", "postfix"])
        postfix_running = rc_postfix == 0
        checks.append({"name": "postfix_running", "passed": postfix_running})

        # Check Dovecot
        rc_dovecot, _, _ = self.run_cmd(["systemctl", "is-active", "dovecot"])
        dovecot_running = rc_dovecot == 0
        checks.append({"name": "dovecot_running", "passed": dovecot_running})

        # Postfix config check
        rc, _, stderr = self.run_cmd(["postfix", "check"])
        postfix_config_ok = rc == 0
        checks.append({"name": "postfix_config", "passed": postfix_config_ok})
        if not postfix_config_ok:
            recommendations.append(f"Postfix config error: {stderr.strip()}")

        # Check SMTP port 25
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_25 = ":25 " in stdout
        port_587 = ":587 " in stdout
        port_993 = ":993 " in stdout
        checks.append({"name": "smtp_port_25", "passed": port_25 or not postfix_running})
        checks.append({"name": "submission_port_587", "passed": port_587 or not postfix_running})
        checks.append({"name": "imaps_port_993", "passed": port_993 or not dovecot_running})

        # Check mail queue
        rc, stdout, _ = self.run_cmd(["mailq"])
        if rc == 0 and "Mail queue is empty" not in stdout:
            queue_lines = [l for l in stdout.splitlines() if l.strip()]
            if len(queue_lines) > 50:
                recommendations.append(f"Mail queue has {len(queue_lines)} entries — may indicate delivery issues")

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        rc_p, _, _ = self.run_cmd(["systemctl", "is-active", "postfix"])
        rc_d, _, _ = self.run_cmd(["systemctl", "is-active", "dovecot"])

        queue_size = 0
        rc, stdout, _ = self.run_cmd(["mailq"])
        if rc == 0 and "Mail queue is empty" not in stdout:
            queue_size = len([l for l in stdout.splitlines() if l.strip()])

        return {
            "postfix_running": rc_p == 0,
            "dovecot_running": rc_d == 0,
            "ports": {"smtp": 25, "submission": 587, "imaps": 993},
            "mail_queue_size": queue_size,
        }

    def validate_config(self) -> list[str]:
        issues = []
        rc, _, stderr = self.run_cmd(["postfix", "check"])
        if rc != 0:
            issues.append(f"Postfix: {stderr.strip()}")

        # Check main.cf exists
        if not Path("/etc/postfix/main.cf").exists():
            issues.append("Missing /etc/postfix/main.cf")

        return issues
