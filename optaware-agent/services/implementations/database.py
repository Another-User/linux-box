"""Database service plugins (PostgreSQL, MySQL/MariaDB)."""

from __future__ import annotations

from services.implementations.base import BaseServicePlugin, DiagnosticResult


class PostgreSQLPlugin(BaseServicePlugin):
    name = "database_pg"
    display_name = "PostgreSQL"
    systemd_unit = "postgresql"
    config_paths = ["/etc/postgresql/"]
    log_paths = ["/var/log/postgresql/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Check if accepting connections
        rc, stdout, _ = self.run_cmd(["pg_isready"])
        accepting = rc == 0
        checks.append({"name": "accepting_connections", "passed": accepting})
        if running and not accepting:
            recommendations.append("PostgreSQL is running but not accepting connections")

        # Check port 5432
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_ok = ":5432 " in stdout
        checks.append({"name": "port_5432", "passed": port_ok or not running})

        # Check data directory
        rc, stdout, _ = self.run_cmd(
            ["sudo", "-u", "postgres", "psql", "-c", "SHOW data_directory;", "-t"]
        )
        if rc == 0:
            data_dir = stdout.strip()
            checks.append({"name": "data_directory", "passed": bool(data_dir)})

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        running = self.is_running()
        version = ""
        db_count = 0

        if running:
            rc, stdout, _ = self.run_cmd(
                ["sudo", "-u", "postgres", "psql", "-c", "SELECT version();", "-t"]
            )
            if rc == 0:
                version = stdout.strip().split(",")[0] if stdout.strip() else ""

            rc, stdout, _ = self.run_cmd(
                ["sudo", "-u", "postgres", "psql", "-c", "SELECT count(*) FROM pg_database;", "-t"]
            )
            if rc == 0 and stdout.strip().isdigit():
                db_count = int(stdout.strip())

        return {
            "running": running,
            "enabled": self.is_enabled(),
            "version": version,
            "databases": db_count,
            "port": 5432,
        }

    def validate_config(self) -> list[str]:
        issues = []
        rc, _, stderr = self.run_cmd(["pg_isready"])
        if rc != 0:
            issues.append(f"pg_isready failed: {stderr.strip()}")
        return issues


class MySQLPlugin(BaseServicePlugin):
    name = "mysql"
    display_name = "MySQL/MariaDB"
    systemd_unit = "mariadb"
    config_paths = ["/etc/mysql/"]
    log_paths = ["/var/log/mysql/"]

    def diagnose(self) -> DiagnosticResult:
        checks = []
        recommendations = []

        running = self.is_running()
        checks.append({"name": "service_running", "passed": running})

        # Check connection
        rc, _, _ = self.run_cmd(["mysqladmin", "ping"])
        connectable = rc == 0
        checks.append({"name": "accepting_connections", "passed": connectable})

        # Check port 3306
        rc, stdout, _ = self.run_cmd(["ss", "-tlnp"])
        port_ok = ":3306 " in stdout
        checks.append({"name": "port_3306", "passed": port_ok or not running})

        return DiagnosticResult(
            healthy=all(c["passed"] for c in checks),
            checks=checks,
            recommendations=recommendations,
        )

    def get_status_summary(self) -> dict:
        return {
            "running": self.is_running(),
            "enabled": self.is_enabled(),
            "port": 3306,
        }

    def validate_config(self) -> list[str]:
        issues = []
        rc, _, stderr = self.run_cmd(["mysqladmin", "ping"])
        if rc != 0:
            issues.append(f"MySQL not responding: {stderr.strip()}")
        return issues
