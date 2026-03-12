#!/usr/bin/env python3
"""
OptAware Setup Wizard
Interactive first-run configuration wizard for the OptAware agent.

Usage:
    python3 setup_wizard.py
    python3 -c "from setup_wizard import run_wizard; run_wizard()"
"""

from __future__ import annotations

import datetime
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install it with: pip install pyyaml")
    sys.exit(1)

try:
    from rich import box
    from rich.columns import Columns  # noqa: F401 (available for future use)
    from rich.console import Console
    from rich.padding import Padding  # noqa: F401
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("ERROR: 'rich' library is required. Install it with: pip install rich")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "0.1.0"
CONFIG_OUTPUT_PATH = "/etc/optaware/optaware.yaml"
CONFIG_TEMPLATE_PATH = Path(__file__).parent / "config" / "optaware.yaml"

KNOWN_CORE_SERVICES = [
    "dns",
    "dhcp",
    "firewall",
    "vpn",
    "web",
    "mail_smtp",
    "mail_imap",
    "database_pg",
    "monitoring",
    "backup",
    "ntp",
    "ssh",
    "certificates",
]

KNOWN_ELECTIVE_SERVICES = [
    "samba",
    "nfs",
    "ldap",
    "redis",
    "docker_registry",
    "load_balancer",
    "reverse_proxy",
    "ftp",
    "printing",
    "cron_manager",
    "grafana",
    "mysql",
    "elasticsearch",
    "kibana",
    "logstash",
    "rabbitmq",
    "memcached",
    "fail2ban",
]

# Map from our internal service name to the systemd unit file name
SYSTEMD_UNIT_MAP: dict[str, str] = {
    "dns": "named.service",
    "dhcp": "kea-dhcp4.service",
    "firewall": "nftables.service",
    "vpn": "wg-quick@wg0.service",
    "web": "nginx.service",
    "mail_smtp": "postfix.service",
    "mail_imap": "dovecot.service",
    "database_pg": "postgresql.service",
    "monitoring": "prometheus.service",
    "backup": "restic-backup.timer",
    "ntp": "chronyd.service",
    "ssh": "sshd.service",
    "certificates": "certbot.timer",
    "samba": "smbd.service",
    "nfs": "nfs-server.service",
    "ldap": "slapd.service",
    "redis": "redis-server.service",
    "docker_registry": "registry.service",
    "load_balancer": "haproxy.service",
    "reverse_proxy": "nginx-proxy.service",
    "ftp": "vsftpd.service",
    "printing": "cups.service",
    "cron_manager": "cron.service",
    "grafana": "grafana-server.service",
    "mysql": "mariadb.service",
    "elasticsearch": "elasticsearch.service",
    "kibana": "kibana.service",
    "logstash": "logstash.service",
    "rabbitmq": "rabbitmq-server.service",
    "memcached": "memcached.service",
    "fail2ban": "fail2ban.service",
}

DISPLAY_NAMES: dict[str, str] = {
    "dns": "DNS Server (BIND9)",
    "dhcp": "DHCP Server (Kea)",
    "firewall": "Firewall (nftables)",
    "vpn": "VPN (WireGuard)",
    "web": "Web Server (Nginx)",
    "mail_smtp": "SMTP Mail (Postfix)",
    "mail_imap": "IMAP Mail (Dovecot)",
    "database_pg": "PostgreSQL Database",
    "monitoring": "Monitoring (Prometheus)",
    "backup": "Backup (Restic)",
    "ntp": "NTP (Chrony)",
    "ssh": "SSH Server (OpenSSH)",
    "certificates": "TLS Certificates (Certbot)",
    "samba": "File Sharing (Samba)",
    "nfs": "NFS Server",
    "ldap": "LDAP Directory (OpenLDAP)",
    "redis": "Redis Cache",
    "docker_registry": "Docker Registry",
    "load_balancer": "Load Balancer (HAProxy)",
    "reverse_proxy": "Reverse Proxy (nginx-proxy)",
    "ftp": "FTP Server (vsftpd)",
    "printing": "Print Server (CUPS)",
    "cron_manager": "Cron Scheduler",
    "grafana": "Grafana Dashboards",
    "mysql": "MySQL/MariaDB Database",
    "elasticsearch": "Elasticsearch",
    "kibana": "Kibana",
    "logstash": "Logstash",
    "rabbitmq": "RabbitMQ Message Broker",
    "memcached": "Memcached",
    "fail2ban": "Fail2ban IPS",
}

DEFAULT_CORE_SERVICES = ["dns", "dhcp", "firewall", "ntp", "ssh", "certificates"]
LLM_PROVIDERS = ["anthropic", "openai", "ollama", "azure"]
APPROVAL_MODES = ["auto", "manual", "hybrid"]
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
ENVIRONMENTS = ["production", "staging", "development"]

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "ollama": "llama3.2",
    "azure": "gpt-4",
}

# ---------------------------------------------------------------------------
# Global console instance
# ---------------------------------------------------------------------------

console = Console()


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _run(cmd: str) -> tuple[int, str]:
    """Run a shell command and return (returncode, combined_output)."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except Exception as exc:
        return 1, str(exc)


def _detect_hostname() -> str:
    """Return the current system hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "localhost"


def _scan_installed_services() -> dict[str, bool]:
    """
    Check which managed services are installed by querying systemctl.
    Returns a mapping of {service_name: is_installed}.
    Falls back to all-False if systemctl is unavailable.
    """
    installed: dict[str, bool] = {}

    code, output = _run("systemctl list-unit-files --no-pager --no-legend 2>/dev/null")
    if code != 0:
        for svc in KNOWN_CORE_SERVICES + KNOWN_ELECTIVE_SERVICES:
            installed[svc] = False
        return installed

    unit_names: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if parts:
            unit_names.add(parts[0])

    for svc, unit in SYSTEMD_UNIT_MAP.items():
        installed[svc] = unit in unit_names

    return installed


def _validate_port(value: str) -> Optional[int]:
    """Return the port as an int if valid (1–65535), else None."""
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    return None


def _validate_url(value: str) -> bool:
    """Return True if value starts with a supported URL scheme."""
    return value.startswith(("http://", "https://", "qdrant://"))


def _validate_data_dir(value: str) -> bool:
    """Return True if value is a non-root absolute path."""
    return value.startswith("/") and len(value) > 1


def _current_timestamp() -> str:
    """Return an ISO-8601 timestamp for config file headers."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _load_default_config() -> dict:
    """
    Load the default configuration structure.
    Reads from config/optaware.yaml if present, otherwise returns hardcoded defaults.
    """
    if CONFIG_TEMPLATE_PATH.exists():
        with open(CONFIG_TEMPLATE_PATH) as fh:
            data = yaml.safe_load(fh)
        return data.get("optaware", {})

    # In-memory fallback if the template file is missing
    return {
        "general": {
            "hostname": "auto",
            "environment": "production",
            "data_dir": "/data",
            "log_level": "INFO",
        },
        "services": {
            "core_services": list(DEFAULT_CORE_SERVICES),
            "elective_services": [],
            "auto_start": True,
        },
        "llm": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "api_key": "",
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
            "approval_mode": "hybrid",
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
                "config": "/data/config",
                "logs": "/data/logs",
                "knowledge": "/data/knowledge",
                "backups": "/data/backups",
            },
        },
        "portal": {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 8080,
            "secret_key": "",
        },
    }


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------


def _print_welcome() -> None:
    """Print the welcome banner and ask if the user is ready to begin."""
    banner = Text()
    banner.append("  OptAware", style="bold cyan")
    banner.append(
        "  —  AI-Powered Linux Server Management Agent\n",
        style="white",
    )
    banner.append(f"  Version {VERSION}", style="dim")

    console.print()
    console.print(
        Panel(
            banner,
            title="[bold green]Welcome[/bold green]",
            subtitle="[dim]Phase 1 Setup Wizard[/dim]",
            border_style="cyan",
            padding=(1, 4),
        )
    )
    console.print()
    console.print(
        "This wizard will guide you through the initial configuration of OptAware.\n"
        "It detects your environment, scans installed services, and writes a\n"
        "configuration file to [bold cyan]/etc/optaware/optaware.yaml[/bold cyan].\n"
    )
    console.print("[dim]Press Ctrl+C at any time to abort without saving.[/dim]")
    console.print()
    Confirm.ask("[bold]Ready to begin?[/bold]", default=True)
    console.print()


def _step_environment(config: dict) -> None:
    """
    Step 1 of 9 — Detect and confirm the hostname and deployment environment.

    Detects the current system hostname automatically and asks the user to
    confirm or override it. Also collects environment type and log level.
    """
    console.print(Rule("[bold cyan]Step 1 of 9 — Environment[/bold cyan]"))
    console.print()

    detected_hostname = _detect_hostname()
    console.print(
        f"[dim]Detected hostname:[/dim] [bold]{detected_hostname}[/bold]\n"
    )

    hostname_choice = Prompt.ask(
        "Hostname to use ([dim]'auto' to detect at runtime[/dim])",
        default="auto",
        show_default=True,
    )
    hostname_choice = hostname_choice.strip()
    if hostname_choice.lower() == "auto":
        hostname_choice = "auto"
    elif not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9\-\.]{0,253}$", hostname_choice):
        console.print(
            "[yellow]Warning: hostname looks unusual, but using it anyway.[/yellow]"
        )

    env_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    for env in ENVIRONMENTS:
        env_table.add_row(f"  [cyan]{env}[/cyan]")
    console.print("Available environments:")
    console.print(env_table)

    env_choice = Prompt.ask(
        "Environment",
        choices=ENVIRONMENTS,
        default="production",
        show_choices=False,
    )

    log_level = Prompt.ask(
        "Log level",
        choices=LOG_LEVELS,
        default="INFO",
        show_choices=True,
    )

    config["general"]["hostname"] = hostname_choice
    config["general"]["environment"] = env_choice
    config["general"]["log_level"] = log_level

    console.print()
    console.print(
        f"[green]✓[/green] Hostname: [bold]{hostname_choice}[/bold]  "
        f"Environment: [bold]{env_choice}[/bold]  "
        f"Log level: [bold]{log_level}[/bold]"
    )
    console.print()


def _step_scan_services(installed: dict[str, bool]) -> None:
    """
    Step 2 of 9 — Display the results of the installed-service scan.

    Builds a table showing every known service, its type (core/elective),
    and whether systemctl reports a matching unit file installed on the host.
    """
    console.print(Rule("[bold cyan]Step 2 of 9 — Installed Services Scan[/bold cyan]"))
    console.print()

    table = Table(
        title="Service Inventory",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Service", style="bold", min_width=16)
    table.add_column("Display Name", min_width=28)
    table.add_column("Type", justify="center", min_width=8)
    table.add_column("Installed", justify="center", min_width=10)

    for svc in KNOWN_CORE_SERVICES:
        found = installed.get(svc, False)
        status = "[green]Yes[/green]" if found else "[dim]No[/dim]"
        table.add_row(svc, DISPLAY_NAMES[svc], "[blue]core[/blue]", status)

    for svc in KNOWN_ELECTIVE_SERVICES:
        found = installed.get(svc, False)
        status = "[green]Yes[/green]" if found else "[dim]No[/dim]"
        table.add_row(svc, DISPLAY_NAMES[svc], "[yellow]elective[/yellow]", status)

    console.print(table)
    console.print()

    found_count = sum(1 for v in installed.values() if v)
    console.print(
        f"[green]✓[/green] Scan complete — "
        f"[bold]{found_count}[/bold] of [bold]{len(installed)}[/bold] known services detected."
    )
    console.print()


def _step_select_core_services(config: dict, installed: dict[str, bool]) -> None:
    """
    Step 3 of 9 — Choose which core services OptAware should manage.

    The user enters a comma-separated list of service names. Defaults to the
    six most universally required core services.
    """
    console.print(Rule("[bold cyan]Step 3 of 9 — Core Services[/bold cyan]"))
    console.print()
    console.print(
        "Select the [bold]core services[/bold] for OptAware to manage.\n"
        "Enter a comma-separated list of service names, or press Enter for defaults.\n"
    )

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="cyan", min_width=3)
    table.add_column("Name", min_width=14)
    table.add_column("Display Name", min_width=28)
    table.add_column("Installed", justify="center")
    table.add_column("Default", justify="center")

    for i, svc in enumerate(KNOWN_CORE_SERVICES, 1):
        found = "[green]Yes[/green]" if installed.get(svc) else "[dim]No[/dim]"
        default_mark = "[green]✓[/green]" if svc in DEFAULT_CORE_SERVICES else ""
        table.add_row(str(i), svc, DISPLAY_NAMES[svc], found, default_mark)

    console.print(table)
    console.print()

    default_str = ",".join(DEFAULT_CORE_SERVICES)
    raw = Prompt.ask(
        "Core services to enable",
        default=default_str,
        show_default=True,
    )

    selected: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if part in KNOWN_CORE_SERVICES:
            if part not in selected:
                selected.append(part)
        elif part:
            console.print(
                f"[yellow]Warning: '[bold]{part}[/bold]' is not a known core service — skipping.[/yellow]"
            )

    if not selected:
        console.print(
            "[yellow]No valid core services selected. Reverting to defaults.[/yellow]"
        )
        selected = list(DEFAULT_CORE_SERVICES)

    config["services"]["core_services"] = selected
    console.print(
        f"[green]✓[/green] Core services: [bold]{', '.join(selected)}[/bold]"
    )
    console.print()


def _step_select_elective_services(config: dict, installed: dict[str, bool]) -> None:
    """
    Step 4 of 9 — Choose optional elective services to manage.

    If any elective services are detected as installed, the wizard offers to
    pre-select them. The user may accept, override, or clear the selection.
    """
    console.print(Rule("[bold cyan]Step 4 of 9 — Elective Services[/bold cyan]"))
    console.print()
    console.print(
        "Select [bold]elective services[/bold] OptAware should manage.\n"
        "These are optional add-ons. Leave blank to enable none.\n"
    )

    installed_electives = [
        s for s in KNOWN_ELECTIVE_SERVICES if installed.get(s)
    ]

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="cyan", min_width=3)
    table.add_column("Name", min_width=16)
    table.add_column("Display Name", min_width=30)
    table.add_column("Installed", justify="center")

    for i, svc in enumerate(KNOWN_ELECTIVE_SERVICES, 1):
        found = "[green]Yes[/green]" if installed.get(svc) else "[dim]No[/dim]"
        table.add_row(str(i), svc, DISPLAY_NAMES[svc], found)

    console.print(table)
    console.print()

    if installed_electives:
        console.print(
            f"[dim]Installed elective services: {', '.join(installed_electives)}[/dim]\n"
        )
        pre_select = Confirm.ask(
            f"Pre-select installed elective services ({', '.join(installed_electives)})?",
            default=True,
        )
        default_electives = ",".join(installed_electives) if pre_select else ""
    else:
        default_electives = ""

    raw = Prompt.ask(
        "Elective services to enable (comma-separated, or blank for none)",
        default=default_electives,
        show_default=bool(default_electives),
    )

    selected: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part in KNOWN_ELECTIVE_SERVICES:
            if part not in selected:
                selected.append(part)
        else:
            console.print(
                f"[yellow]Warning: '[bold]{part}[/bold]' is not a known elective service — skipping.[/yellow]"
            )

    auto_start = Confirm.ask(
        "Auto-start managed services when OptAware starts?",
        default=True,
    )

    config["services"]["elective_services"] = selected
    config["services"]["auto_start"] = auto_start

    if selected:
        console.print(
            f"[green]✓[/green] Elective services: [bold]{', '.join(selected)}[/bold]"
        )
    else:
        console.print("[green]✓[/green] No elective services selected.")
    console.print()


def _step_configure_llm(config: dict) -> None:
    """
    Step 5 of 9 — Configure LLM provider, model, API key, and cost limits.

    Detects whether OPTAWARE_LLM_API_KEY is already set in the environment
    and avoids storing the key in the config file if so.
    """
    console.print(Rule("[bold cyan]Step 5 of 9 — LLM Provider[/bold cyan]"))
    console.print()
    console.print(
        "OptAware uses a Large Language Model for decision-making and analysis.\n"
        "Configure your preferred provider below.\n"
    )

    provider_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    provider_table.add_row(
        "[cyan]anthropic[/cyan]",
        "Claude models  [dim](recommended)[/dim]",
    )
    provider_table.add_row(
        "[cyan]openai[/cyan]",
        "GPT-4 and compatible models",
    )
    provider_table.add_row(
        "[cyan]ollama[/cyan]",
        "Self-hosted local models  [dim](no API key needed)[/dim]",
    )
    provider_table.add_row(
        "[cyan]azure[/cyan]",
        "Azure OpenAI Service",
    )
    console.print("Available providers:")
    console.print(provider_table)
    console.print()

    provider = Prompt.ask(
        "LLM provider",
        choices=LLM_PROVIDERS,
        default="anthropic",
        show_choices=False,
    )

    model = Prompt.ask(
        "Model name",
        default=DEFAULT_MODELS.get(provider, "claude-sonnet-4-20250514"),
        show_default=True,
    )

    if provider == "ollama":
        console.print("[dim]Ollama runs locally — no API key required.[/dim]")
        api_key = ""
    else:
        env_var = "OPTAWARE_LLM_API_KEY"
        env_val = os.environ.get(env_var, "")
        if env_val:
            console.print(
                f"[green]✓[/green] Found [bold]{env_var}[/bold] in the environment. "
                "API key will be read from the environment variable at runtime."
            )
            api_key = ""
        else:
            console.print(
                f"\n[yellow]Tip:[/yellow] Set [bold]{env_var}[/bold] in your environment "
                "to avoid storing the key in the config file.\n"
            )
            api_key = Prompt.ask(
                "API key  [dim](leave blank to set via env var later)[/dim]",
                default="",
                password=True,
            )

    max_tokens_str = Prompt.ask(
        "Max tokens per LLM response",
        default="4096",
        show_default=True,
    )
    try:
        max_tokens = int(max_tokens_str)
    except ValueError:
        max_tokens = 4096

    cost_limit_str = Prompt.ask(
        "Daily cost limit in USD  [dim](e.g. 5.00)[/dim]",
        default="5.00",
        show_default=True,
    )
    try:
        cost_limit = float(cost_limit_str)
    except ValueError:
        cost_limit = 5.00

    config["llm"]["provider"] = provider
    config["llm"]["model"] = model
    config["llm"]["api_key"] = api_key
    config["llm"]["max_tokens"] = max_tokens
    config["llm"]["cost_limit_daily"] = cost_limit

    console.print(
        f"\n[green]✓[/green] LLM: [bold]{provider}[/bold] / [bold]{model}[/bold]  "
        f"max_tokens=[bold]{max_tokens}[/bold]  "
        f"daily_limit=[bold]${cost_limit:.2f}[/bold]"
    )
    console.print()


def _step_data_directory(config: dict) -> None:
    """
    Step 6 of 9 — Set the root data directory.

    All persistent subdirectories (config, logs, knowledge, backups) are
    placed under this path. Defaults to /data.
    """
    console.print(Rule("[bold cyan]Step 6 of 9 — Data Directory[/bold cyan]"))
    console.print()
    console.print(
        "OptAware stores config snapshots, logs, knowledge-base files, and backups\n"
        "under a single root data directory.\n"
    )

    while True:
        data_dir = Prompt.ask(
            "Data directory",
            default="/data",
            show_default=True,
        )
        if _validate_data_dir(data_dir):
            break
        console.print(
            "[red]Error: data directory must be an absolute path (must start with /).[/red]"
        )

    config["general"]["data_dir"] = data_dir
    config["docker"]["persistent_volumes"]["config"] = f"{data_dir}/config"
    config["docker"]["persistent_volumes"]["logs"] = f"{data_dir}/logs"
    config["docker"]["persistent_volumes"]["knowledge"] = f"{data_dir}/knowledge"
    config["docker"]["persistent_volumes"]["backups"] = f"{data_dir}/backups"

    subdirs = ["config", "logs", "knowledge", "backups"]
    table = Table(box=box.SIMPLE, show_header=False)
    for sub in subdirs:
        table.add_row(f"  {data_dir}/{sub}")
    console.print("The following subdirectories will be created on first run:")
    console.print(table)
    console.print()
    console.print(f"[green]✓[/green] Data directory: [bold]{data_dir}[/bold]")
    console.print()


def _step_approval_mode(config: dict) -> None:
    """
    Step 7 of 9 — Configure how the agent handles risky actions.

    'hybrid' is the recommended default: the agent acts automatically on
    low-risk operations (e.g., reloading a config) but pauses and asks for
    approval before high-risk operations (e.g., deleting firewall rules).
    """
    console.print(Rule("[bold cyan]Step 7 of 9 — Approval Mode[/bold cyan]"))
    console.print()
    console.print(
        "Choose how OptAware handles action approval when making changes.\n"
    )

    mode_table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
    mode_table.add_column("Mode", min_width=10)
    mode_table.add_column("Behaviour", min_width=38)
    mode_table.add_column("Best For", min_width=26)
    mode_table.add_row(
        "[bold]auto[/bold]",
        "Executes all actions without prompting",
        "Trusted, well-tested setups",
    )
    mode_table.add_row(
        "[bold]manual[/bold]",
        "Every action requires explicit approval",
        "Conservative or first-time use",
    )
    mode_table.add_row(
        "[bold]hybrid[/bold]",
        "Auto for low-risk, manual for high-risk",
        "[green]Recommended[/green]",
    )
    console.print(mode_table)
    console.print()

    approval_mode = Prompt.ask(
        "Approval mode",
        choices=APPROVAL_MODES,
        default="hybrid",
        show_choices=True,
    )

    dry_run = Confirm.ask(
        "Enable dry-run by default?  [dim](show planned changes without executing)[/dim]",
        default=True,
    )

    max_concurrent_str = Prompt.ask(
        "Maximum concurrent actions",
        default="3",
        show_default=True,
    )
    try:
        max_concurrent = max(1, min(10, int(max_concurrent_str)))
    except ValueError:
        max_concurrent = 3

    config["planning"]["approval_mode"] = approval_mode
    config["planning"]["dry_run_default"] = dry_run
    config["planning"]["max_concurrent_actions"] = max_concurrent

    console.print(
        f"\n[green]✓[/green] Approval mode: [bold]{approval_mode}[/bold]  "
        f"dry_run=[bold]{dry_run}[/bold]  "
        f"max_concurrent=[bold]{max_concurrent}[/bold]"
    )
    console.print()


def _step_advanced_options(config: dict) -> None:
    """
    Step 8 of 9 — Optional advanced configuration.

    Covers the Qdrant vector store URL, embedding model, web portal settings,
    and perception parameters. Skipped by default to keep the wizard fast.
    """
    console.print(Rule("[bold cyan]Step 8 of 9 — Advanced Options[/bold cyan]"))
    console.print()

    configure_advanced = Confirm.ask(
        "Configure advanced options? (vector store, portal, perception)",
        default=False,
    )

    if not configure_advanced:
        console.print("[dim]Skipping — keeping defaults for all advanced options.[/dim]")
        console.print()
        return

    # --- Knowledge / Vector Store ---
    console.print("\n[bold]Knowledge Base (Qdrant Vector Store)[/bold]")
    qdrant_url = Prompt.ask(
        "Qdrant URL",
        default=config["knowledge"]["vector_store_url"],
        show_default=True,
    )
    if _validate_url(qdrant_url):
        config["knowledge"]["vector_store_url"] = qdrant_url
    else:
        console.print("[yellow]Invalid URL — keeping default.[/yellow]")

    embedding_model = Prompt.ask(
        "Embedding model",
        default=config["knowledge"]["embedding_model"],
        show_default=True,
    )
    config["knowledge"]["embedding_model"] = embedding_model

    # --- Web Portal ---
    console.print("\n[bold]Web Portal[/bold]")
    portal_enabled = Confirm.ask(
        "Enable the OptAware web portal?",
        default=True,
    )
    config["portal"]["enabled"] = portal_enabled

    if portal_enabled:
        while True:
            port_str = Prompt.ask(
                "Portal port",
                default=str(config["portal"]["port"]),
                show_default=True,
            )
            port = _validate_port(port_str)
            if port:
                config["portal"]["port"] = port
                break
            console.print("[red]Invalid port. Must be an integer between 1 and 65535.[/red]")

        portal_host = Prompt.ask(
            "Portal bind address",
            default=config["portal"]["host"],
            show_default=True,
        )
        config["portal"]["host"] = portal_host

    # --- Perception ---
    console.print("\n[bold]Perception / Monitoring[/bold]")
    metric_interval_str = Prompt.ask(
        "Metric collection interval (seconds)",
        default=str(config["perception"]["metric_interval_sec"]),
        show_default=True,
    )
    try:
        config["perception"]["metric_interval_sec"] = max(5, int(metric_interval_str))
    except ValueError:
        pass

    sensitivity_str = Prompt.ask(
        "Anomaly detection sensitivity  [dim](0.0 = low, 1.0 = high)[/dim]",
        default=str(config["perception"]["anomaly_sensitivity"]),
        show_default=True,
    )
    try:
        sensitivity = float(sensitivity_str)
        if 0.0 <= sensitivity <= 1.0:
            config["perception"]["anomaly_sensitivity"] = sensitivity
        else:
            console.print("[yellow]Value out of range (0.0–1.0) — keeping default.[/yellow]")
    except ValueError:
        pass

    console.print()
    console.print("[green]✓[/green] Advanced options saved.")
    console.print()


def _step_write_config(config: dict) -> bool:
    """
    Step 9 of 9 — Serialise and write the generated config to disk.

    Writes to /etc/optaware/optaware.yaml with mode 0640.
    If a previous config exists it is backed up first.
    Falls back to writing ./optaware.yaml locally if permission is denied.
    Returns True on success, False if the user aborted or an error occurred.
    """
    console.print(Rule("[bold cyan]Step 9 of 9 — Write Configuration[/bold cyan]"))
    console.print()

    output_path = Path(CONFIG_OUTPUT_PATH)
    console.print(
        f"Configuration will be written to: [bold cyan]{output_path}[/bold cyan]\n"
    )

    # Show a YAML preview
    preview = yaml.dump(
        {"optaware": config},
        default_flow_style=False,
        sort_keys=False,
    )
    console.print(
        Panel(
            preview,
            title="[bold]Configuration Preview[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )
    console.print()

    if not Confirm.ask("[bold]Write this configuration?[/bold]", default=True):
        console.print("[yellow]Aborted. No files were written.[/yellow]")
        return False

    # Auto-generate portal secret key on first run
    if (
        config.get("portal", {}).get("enabled")
        and not config["portal"].get("secret_key")
    ):
        config["portal"]["secret_key"] = secrets.token_hex(32)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.exists():
            backup_path = output_path.with_suffix(".yaml.bak")
            shutil.copy2(output_path, backup_path)
            console.print(
                f"[dim]Backed up previous config to {backup_path}[/dim]"
            )

        with open(output_path, "w") as fh:
            fh.write(
                "# OptAware Configuration — generated by setup_wizard.py\n"
                f"# Generated: {_current_timestamp()}\n\n"
            )
            yaml.dump(
                {"optaware": config},
                fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        os.chmod(output_path, 0o640)
        console.print(
            f"\n[bold green]✓ Configuration written to {output_path}[/bold green]"
        )
        return True

    except PermissionError:
        console.print(
            f"\n[bold red]Permission denied:[/bold red] Cannot write to "
            f"[cyan]{output_path}[/cyan].\n"
            "Run the wizard as [bold]root[/bold] or with sudo:\n\n"
            "  [bold cyan]sudo python3 setup_wizard.py[/bold cyan]\n"
        )
        save_local = Confirm.ask(
            "Save config to [bold]./optaware.yaml[/bold] in the current directory instead?",
            default=True,
        )
        if save_local:
            local_path = Path("./optaware.yaml")
            with open(local_path, "w") as fh:
                fh.write(
                    "# OptAware Configuration — generated by setup_wizard.py\n"
                    f"# Generated: {_current_timestamp()}\n\n"
                )
                yaml.dump(
                    {"optaware": config},
                    fh,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )
            console.print(
                f"\n[yellow]Config saved locally to "
                f"[bold]{local_path.resolve()}[/bold].\n"
                f"Copy it to {CONFIG_OUTPUT_PATH} with root permissions "
                f"to activate it.[/yellow]"
            )
            return True
        return False

    except Exception as exc:
        console.print(f"[bold red]Unexpected error writing config:[/bold red] {exc}")
        return False


def _step_summary(config: dict, write_success: bool) -> None:
    """Print the final configuration summary and next steps."""
    console.print()
    console.print(Rule("[bold green]Setup Complete[/bold green]"))
    console.print()

    table = Table(
        title="Configuration Summary",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Setting", min_width=26, style="bold")
    table.add_column("Value", min_width=36)

    general = config.get("general", {})
    table.add_row("Hostname", str(general.get("hostname", "auto")))
    table.add_row("Environment", str(general.get("environment", "production")))
    table.add_row("Log Level", str(general.get("log_level", "INFO")))
    table.add_row("Data Directory", str(general.get("data_dir", "/data")))

    services = config.get("services", {})
    core_svcs = services.get("core_services", [])
    elective_svcs = services.get("elective_services", [])
    table.add_row(
        "Core Services",
        ", ".join(core_svcs) if core_svcs else "[dim]none[/dim]",
    )
    table.add_row(
        "Elective Services",
        ", ".join(elective_svcs) if elective_svcs else "[dim]none[/dim]",
    )
    table.add_row("Auto Start", str(services.get("auto_start", True)))

    llm = config.get("llm", {})
    table.add_row("LLM Provider", str(llm.get("provider", "anthropic")))
    table.add_row("LLM Model", str(llm.get("model", "")))
    table.add_row("Daily Cost Limit", f"${llm.get('cost_limit_daily', 5.0):.2f}")

    planning = config.get("planning", {})
    table.add_row("Approval Mode", str(planning.get("approval_mode", "hybrid")))
    table.add_row("Dry Run Default", str(planning.get("dry_run_default", True)))

    portal = config.get("portal", {})
    if portal.get("enabled"):
        table.add_row(
            "Portal URL",
            f"http://{portal.get('host', '0.0.0.0')}:{portal.get('port', 8080)}",
        )
    else:
        table.add_row("Portal", "[dim]disabled[/dim]")

    console.print(table)
    console.print()

    if write_success:
        console.print(
            Panel(
                "[bold green]OptAware is configured and ready![/bold green]\n\n"
                f"Config:   [bold cyan]{CONFIG_OUTPUT_PATH}[/bold cyan]\n\n"
                "Next steps:\n"
                "  1. Review the config:\n"
                "       [dim]cat /etc/optaware/optaware.yaml[/dim]\n\n"
                "  2. Set your LLM API key (if not already set):\n"
                "       [dim]export OPTAWARE_LLM_API_KEY=sk-…[/dim]\n\n"
                "  3. Start the agent stack:\n"
                "       [dim]docker compose -f docker/docker-compose.yml up -d[/dim]\n\n"
                "  4. Open the portal:\n"
                "       [dim]http://localhost:8080[/dim]",
                title="[bold]What's Next[/bold]",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        console.print(
            Panel(
                "[yellow]The wizard completed but the config was not saved.[/yellow]\n\n"
                "Re-run with root privileges to write to\n"
                f"[cyan]{CONFIG_OUTPUT_PATH}[/cyan]:\n\n"
                "  [bold cyan]sudo python3 setup_wizard.py[/bold cyan]",
                title="[bold yellow]Action Required[/bold yellow]",
                border_style="yellow",
                padding=(1, 2),
            )
        )
    console.print()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_wizard() -> None:
    """
    Run the full 9-step OptAware setup wizard.

    Walks the user through environment detection, service selection,
    LLM configuration, data directory selection, approval-mode selection,
    and advanced options, then writes the resulting YAML config to
    /etc/optaware/optaware.yaml.

    Call this function directly or run the module as a script:
        python3 setup_wizard.py
    """
    try:
        _print_welcome()

        # Load the base config (from template file or in-memory defaults)
        config = _load_default_config()

        # Step 1 — Environment
        _step_environment(config)

        # Step 2 — Scan installed services
        with console.status("[bold green]Scanning installed services…[/bold green]"):
            installed = _scan_installed_services()
        _step_scan_services(installed)

        # Step 3 — Core services
        _step_select_core_services(config, installed)

        # Step 4 — Elective services
        _step_select_elective_services(config, installed)

        # Step 5 — LLM provider
        _step_configure_llm(config)

        # Step 6 — Data directory
        _step_data_directory(config)

        # Step 7 — Approval mode
        _step_approval_mode(config)

        # Step 8 — Advanced options (optional)
        _step_advanced_options(config)

        # Step 9 — Write config to disk
        write_success = _step_write_config(config)

        # Final summary
        _step_summary(config, write_success)

    except KeyboardInterrupt:
        console.print(
            "\n\n[yellow]Setup wizard aborted. No files were written.[/yellow]\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    run_wizard()
