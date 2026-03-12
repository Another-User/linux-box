"""Interactive CLI setup wizard for first-run OptAware configuration."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
from pathlib import Path

import yaml

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
except ImportError:
    # Fallback if rich is not installed
    Console = None  # type: ignore[assignment, misc]

DEFAULT_DATA_DIR = "/data"
DEFAULT_CONFIG_PATH = "/etc/optaware/optaware.yaml"

CORE_SERVICES = [
    ("dns", "DNS Server (bind9)"),
    ("dhcp", "DHCP Server (kea-dhcp4)"),
    ("firewall", "Firewall (nftables)"),
    ("vpn", "VPN (WireGuard)"),
    ("web", "Web Server (nginx)"),
    ("mail_smtp", "Mail SMTP (postfix)"),
    ("mail_imap", "Mail IMAP (dovecot)"),
    ("database_pg", "PostgreSQL Database"),
    ("monitoring", "Monitoring (prometheus)"),
    ("backup", "Backup (restic)"),
    ("ntp", "NTP (chronyd)"),
    ("ssh", "SSH Server (sshd)"),
    ("certificates", "Certificates (certbot)"),
]

ELECTIVE_SERVICES = [
    ("samba", "Samba File Sharing"),
    ("nfs", "NFS Server"),
    ("ldap", "LDAP Directory"),
    ("redis", "Redis Cache"),
    ("docker_registry", "Docker Registry"),
    ("load_balancer", "Load Balancer (HAProxy)"),
    ("grafana", "Grafana Dashboard"),
    ("mysql", "MySQL/MariaDB"),
    ("fail2ban", "Fail2ban Intrusion Prevention"),
]


def _detect_hostname() -> str:
    """Detect the system hostname."""
    return socket.gethostname()


def _detect_installed_services() -> list[str]:
    """Scan systemd for installed service units."""
    installed = []
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", "--type=service", "--no-pager", "--plain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        unit_names = {
            line.split()[0].replace(".service", "")
            for line in result.stdout.strip().splitlines()[1:]
            if line.strip()
        }
        service_unit_map = {
            "dns": "named",
            "dhcp": "kea-dhcp4-server",
            "firewall": "nftables",
            "vpn": "wg-quick@wg0",
            "web": "nginx",
            "mail_smtp": "postfix",
            "mail_imap": "dovecot",
            "database_pg": "postgresql",
            "monitoring": "prometheus",
            "ntp": "chronyd",
            "ssh": "sshd",
            "samba": "smbd",
            "nfs": "nfs-server",
            "ldap": "slapd",
            "redis": "redis-server",
            "load_balancer": "haproxy",
            "grafana": "grafana-server",
            "mysql": "mariadb",
            "fail2ban": "fail2ban",
        }
        for svc_key, unit_name in service_unit_map.items():
            if unit_name in unit_names:
                installed.append(svc_key)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return installed


def run_wizard(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Run the interactive setup wizard."""
    if Console is None:
        print("Error: 'rich' library is required. Install with: pip install rich")
        return {}

    console = Console()

    console.print(
        Panel(
            "[bold blue]OptAware[/bold blue] — Intelligent Linux Server Management Agent\n"
            "First-run configuration wizard",
            title="Welcome",
            border_style="blue",
        )
    )

    # Step 1: Hostname & environment
    console.print("\n[bold]Step 1: System Identity[/bold]")
    detected_hostname = _detect_hostname()
    hostname = Prompt.ask("Hostname", default=detected_hostname)
    environment = Prompt.ask(
        "Environment",
        choices=["production", "staging", "dev"],
        default="production",
    )

    # Step 2: Scan installed services
    console.print("\n[bold]Step 2: Scanning installed services...[/bold]")
    installed = _detect_installed_services()
    if installed:
        console.print(f"  Found {len(installed)} installed services: {', '.join(installed)}")
    else:
        console.print("  No services auto-detected (will use defaults)")

    # Step 3: Core services
    console.print("\n[bold]Step 3: Core Services[/bold]")
    table = Table(title="Available Core Services")
    table.add_column("#", style="dim")
    table.add_column("Service")
    table.add_column("Installed", style="green")
    for i, (key, label) in enumerate(CORE_SERVICES, 1):
        status = "Yes" if key in installed else "No"
        table.add_row(str(i), label, status)
    console.print(table)

    core_input = Prompt.ask(
        "Select core services (comma-separated numbers, or 'all')",
        default="all",
    )
    if core_input.lower() == "all":
        selected_core = [k for k, _ in CORE_SERVICES]
    else:
        indices = [int(x.strip()) - 1 for x in core_input.split(",") if x.strip().isdigit()]
        selected_core = [CORE_SERVICES[i][0] for i in indices if 0 <= i < len(CORE_SERVICES)]

    # Step 4: Elective services
    console.print("\n[bold]Step 4: Elective Services[/bold]")
    table = Table(title="Available Elective Services")
    table.add_column("#", style="dim")
    table.add_column("Service")
    table.add_column("Installed", style="green")
    for i, (key, label) in enumerate(ELECTIVE_SERVICES, 1):
        status = "Yes" if key in installed else "No"
        table.add_row(str(i), label, status)
    console.print(table)

    elective_input = Prompt.ask(
        "Select elective services (comma-separated numbers, or 'none')",
        default="none",
    )
    if elective_input.lower() == "none":
        selected_elective: list[str] = []
    else:
        indices = [int(x.strip()) - 1 for x in elective_input.split(",") if x.strip().isdigit()]
        selected_elective = [
            ELECTIVE_SERVICES[i][0] for i in indices if 0 <= i < len(ELECTIVE_SERVICES)
        ]

    # Step 5: LLM provider
    console.print("\n[bold]Step 5: LLM Configuration[/bold]")
    provider = Prompt.ask("LLM provider", choices=["anthropic", "openai", "local"], default="anthropic")
    api_key = Prompt.ask("API key (or set OPTAWARE_LLM_API_KEY later)", default="", password=True)

    # Step 6: Data directory
    console.print("\n[bold]Step 6: Data Directory[/bold]")
    data_dir = Prompt.ask("Data directory", default=DEFAULT_DATA_DIR)

    # Step 7: Approval mode
    console.print("\n[bold]Step 7: Action Approval Mode[/bold]")
    console.print("  auto   — Agent executes low-risk actions automatically")
    console.print("  manual — All actions require human approval")
    console.print("  hybrid — Auto for low-risk, manual for high-risk")
    approval_mode = Prompt.ask("Approval mode", choices=["auto", "manual", "hybrid"], default="hybrid")

    # Build config
    config = {
        "optaware": {
            "general": {
                "hostname": hostname,
                "environment": environment,
                "data_dir": data_dir,
                "log_level": "INFO",
            },
            "services": {
                "core_services": selected_core,
                "elective_services": selected_elective,
                "auto_start": True,
            },
            "llm": {
                "provider": provider,
                "model": "claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4o",
                "api_key": api_key or "",
                "max_tokens": 4096,
                "temperature": 0.1,
                "cost_limit_daily": 5.00,
            },
            "perception": {
                "log_watch_enabled": True,
                "metric_interval_sec": 30,
                "anomaly_sensitivity": 0.7,
            },
            "planning": {
                "approval_mode": approval_mode,
                "dry_run_default": True,
                "max_concurrent_actions": 3,
            },
            "knowledge": {
                "vector_store_url": "http://localhost:6333",
                "embedding_model": "all-MiniLM-L6-v2",
                "index_name": "optaware-knowledge",
            },
            "docker": {
                "socket_path": "/var/run/docker.sock",
                "compose_file": "docker/docker-compose.yml",
                "persistent_volumes": {
                    "config": f"{data_dir}/config",
                    "logs": f"{data_dir}/logs",
                    "knowledge": f"{data_dir}/knowledge",
                    "backups": f"{data_dir}/backups",
                },
            },
            "portal": {
                "enabled": True,
                "host": "0.0.0.0",
                "port": 8080,
                "secret_key": secrets.token_hex(32),
            },
        }
    }

    # Step 8: Write config
    console.print("\n[bold]Step 8: Writing Configuration[/bold]")
    config_dir = Path(config_path).parent
    config_dir.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    console.print(f"  Config written to: {config_path}")

    # Create data directories
    for subdir in ["config", "logs", "knowledge", "backups"]:
        Path(data_dir, subdir).mkdir(parents=True, exist_ok=True)
    console.print(f"  Data directories created under: {data_dir}")

    # Step 9: Summary
    console.print("\n")
    summary = Table(title="Configuration Summary")
    summary.add_column("Setting", style="bold")
    summary.add_column("Value")
    summary.add_row("Hostname", hostname)
    summary.add_row("Environment", environment)
    summary.add_row("Data Directory", data_dir)
    summary.add_row("Core Services", ", ".join(selected_core) or "none")
    summary.add_row("Elective Services", ", ".join(selected_elective) or "none")
    summary.add_row("LLM Provider", provider)
    summary.add_row("Approval Mode", approval_mode)
    summary.add_row("Portal", f"http://0.0.0.0:8080")
    console.print(summary)

    console.print(
        Panel(
            "OptAware is configured. Start with: [bold]optaware start[/bold]",
            border_style="green",
        )
    )

    return config


if __name__ == "__main__":
    run_wizard()
