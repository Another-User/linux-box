"""Base class for service plugins."""

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("optaware.services.plugin")


@dataclass
class DiagnosticResult:
    """Result of running diagnostics on a service."""

    healthy: bool
    checks: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


class BaseServicePlugin(ABC):
    """Base class all service plugins must implement."""

    name: str = "unknown"
    display_name: str = "Unknown Service"
    systemd_unit: str = ""
    config_paths: list[str] = []
    log_paths: list[str] = []

    def run_cmd(self, cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
        """Run a command and return (returncode, stdout, stderr)."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except FileNotFoundError:
            return -1, "", f"Command not found: {cmd[0]}"

    def is_installed(self) -> bool:
        """Check if the service software is installed."""
        rc, _, _ = self.run_cmd(["systemctl", "cat", self.systemd_unit])
        return rc == 0

    def is_running(self) -> bool:
        """Check if the service is currently running."""
        rc, _, _ = self.run_cmd(["systemctl", "is-active", self.systemd_unit])
        return rc == 0

    def is_enabled(self) -> bool:
        """Check if the service is enabled at boot."""
        rc, _, _ = self.run_cmd(["systemctl", "is-enabled", self.systemd_unit])
        return rc == 0

    def get_config_content(self) -> dict[str, str]:
        """Read all config files and return {path: content}."""
        configs = {}
        for path in self.config_paths:
            p = Path(path)
            if p.exists() and p.is_file():
                try:
                    configs[path] = p.read_text()
                except PermissionError:
                    configs[path] = "<permission denied>"
            elif p.is_dir():
                for f in sorted(p.glob("*")):
                    if f.is_file():
                        try:
                            configs[str(f)] = f.read_text()
                        except PermissionError:
                            configs[str(f)] = "<permission denied>"
        return configs

    def get_recent_logs(self, lines: int = 100) -> str:
        """Get recent log output from journald."""
        rc, stdout, _ = self.run_cmd(
            ["journalctl", "-u", self.systemd_unit, "-n", str(lines), "--no-pager"]
        )
        if rc == 0:
            return stdout
        return ""

    @abstractmethod
    def diagnose(self) -> DiagnosticResult:
        """Run service-specific diagnostics."""
        ...

    @abstractmethod
    def get_status_summary(self) -> dict:
        """Return a human-readable status summary."""
        ...

    @abstractmethod
    def validate_config(self) -> list[str]:
        """Validate service configuration. Returns list of issues."""
        ...

    def get_metrics(self) -> dict:
        """Return service-specific metrics. Override in subclasses."""
        return {}
