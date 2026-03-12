"""OptAware CLI — Rich terminal interface using Click."""

from __future__ import annotations

import json
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="optaware")
def cli():
    """OptAware — Intelligent Linux Server Management Agent."""
    pass


@cli.command()
def status():
    """Show system overview."""
    console.print(Panel("[bold blue]OptAware System Status[/bold blue]", border_style="blue"))

    # System metrics
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        table = Table(title="System Metrics")
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        table.add_column("Status")

        cpu_status = "[green]OK[/green]" if cpu < 80 else "[red]HIGH[/red]"
        mem_status = "[green]OK[/green]" if mem.percent < 85 else "[red]HIGH[/red]"
        disk_status = "[green]OK[/green]" if disk.percent < 85 else "[red]HIGH[/red]"

        table.add_row("CPU", f"{cpu:.1f}%", cpu_status)
        table.add_row("Memory", f"{mem.percent:.1f}% ({mem.used // (1024**3)}GB / {mem.total // (1024**3)}GB)", mem_status)
        table.add_row("Disk /", f"{disk.percent:.1f}% ({disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB)", disk_status)

        console.print(table)
    except ImportError:
        console.print("[yellow]psutil not available — install for metrics[/yellow]")


@cli.group()
def services():
    """Manage services."""
    pass


@services.command("list")
@click.option("--type", "svc_type", type=click.Choice(["all", "core", "elective"]), default="all")
def services_list(svc_type: str):
    """List all managed services."""
    try:
        from config.loader import load_config
        from services.manifest_loader import load_manifest

        svc_list = load_manifest("config/service_manifest.yaml")
        table = Table(title="Managed Services")
        table.add_column("Name", style="bold")
        table.add_column("Display Name")
        table.add_column("Type")
        table.add_column("Unit")
        table.add_column("Port")

        for svc in svc_list:
            if svc_type != "all" and svc.service_type.value != svc_type:
                continue
            type_style = "cyan" if svc.service_type.value == "core" else "dim"
            table.add_row(
                svc.name,
                svc.display_name,
                f"[{type_style}]{svc.service_type.value}[/{type_style}]",
                svc.systemd_unit or "-",
                str(svc.port) if svc.port else "-",
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error loading services: {e}[/red]")


@services.command()
@click.argument("name")
@click.option("--dry-run/--no-dry-run", default=True)
def start(name: str, dry_run: bool):
    """Start a service."""
    console.print(f"{'[DRY RUN] ' if dry_run else ''}Starting service: [bold]{name}[/bold]")
    if not dry_run:
        import subprocess
        result = subprocess.run(["systemctl", "start", name], capture_output=True, text=True)
        if result.returncode == 0:
            console.print(f"[green]Service {name} started successfully[/green]")
        else:
            console.print(f"[red]Failed to start {name}: {result.stderr}[/red]")
    else:
        console.print(f"[yellow]Would start service {name}. Use --no-dry-run to execute.[/yellow]")


@services.command()
@click.argument("name")
@click.option("--dry-run/--no-dry-run", default=True)
def stop(name: str, dry_run: bool):
    """Stop a service."""
    console.print(f"{'[DRY RUN] ' if dry_run else ''}Stopping service: [bold]{name}[/bold]")
    if not dry_run:
        import subprocess
        result = subprocess.run(["systemctl", "stop", name], capture_output=True, text=True)
        if result.returncode == 0:
            console.print(f"[green]Service {name} stopped[/green]")
        else:
            console.print(f"[red]Failed to stop {name}: {result.stderr}[/red]")
    else:
        console.print(f"[yellow]Would stop service {name}. Use --no-dry-run to execute.[/yellow]")


@services.command()
@click.argument("name")
@click.option("--dry-run/--no-dry-run", default=True)
def restart(name: str, dry_run: bool):
    """Restart a service."""
    console.print(f"{'[DRY RUN] ' if dry_run else ''}Restarting service: [bold]{name}[/bold]")
    if not dry_run:
        import subprocess
        result = subprocess.run(["systemctl", "restart", name], capture_output=True, text=True)
        if result.returncode == 0:
            console.print(f"[green]Service {name} restarted[/green]")
        else:
            console.print(f"[red]Failed to restart {name}: {result.stderr}[/red]")
    else:
        console.print(f"[yellow]Would restart service {name}. Use --no-dry-run to execute.[/yellow]")


@services.command()
@click.argument("name")
def diagnose(name: str):
    """Run diagnostics on a service."""
    try:
        from services.implementations import get_plugin

        plugin = get_plugin(name)
        if plugin is None:
            console.print(f"[red]No plugin found for service '{name}'[/red]")
            return

        console.print(f"Running diagnostics for [bold]{name}[/bold]...")
        result = plugin.diagnose()

        table = Table(title=f"Diagnostics: {name}")
        table.add_column("Check", style="bold")
        table.add_column("Result")

        for check in result.checks:
            status = "[green]PASS[/green]" if check["passed"] else "[red]FAIL[/red]"
            table.add_row(check["name"], status)

        console.print(table)

        if result.recommendations:
            console.print("\n[bold yellow]Recommendations:[/bold yellow]")
            for rec in result.recommendations:
                console.print(f"  - {rec}")

        overall = "[green]HEALTHY[/green]" if result.healthy else "[red]UNHEALTHY[/red]"
        console.print(f"\nOverall: {overall}")
    except Exception as e:
        console.print(f"[red]Error running diagnostics: {e}[/red]")


@cli.command()
@click.option("--severity", type=click.Choice(["all", "info", "warning", "error", "critical"]), default="all")
@click.option("--limit", default=20, help="Number of events to show")
def events(severity: str, limit: int):
    """Show recent events."""
    console.print(Panel("[bold]Recent Events[/bold]", border_style="blue"))
    console.print("[dim]Event display requires running daemon. Connect via API for live events.[/dim]")


@cli.group()
def actions():
    """Manage actions."""
    pass


@actions.command("list")
def actions_list():
    """List pending actions."""
    console.print(Panel("[bold]Pending Actions[/bold]", border_style="yellow"))
    console.print("[dim]No pending actions. Actions appear when the agent detects issues.[/dim]")


@actions.command()
def history():
    """Show action history."""
    console.print(Panel("[bold]Action History[/bold]", border_style="blue"))
    console.print("[dim]Action history requires running daemon.[/dim]")


@cli.command()
@click.argument("question", nargs=-1, required=True)
def ask(question: tuple[str, ...]):
    """Ask OptAware a question about the system."""
    q = " ".join(question)
    console.print(f"\n[bold blue]Question:[/bold blue] {q}")
    console.print("[dim]Connecting to LLM provider...[/dim]")
    console.print("[yellow]LLM integration requires API key configuration. Run 'optaware setup' first.[/yellow]")


@cli.group()
def config():
    """Configuration management."""
    pass


@config.command("show")
def config_show():
    """Show current configuration."""
    try:
        from config.loader import load_config
        cfg = load_config("config/optaware.yaml")
        console.print(Panel("[bold]Current Configuration[/bold]", border_style="blue"))
        # Redact sensitive fields
        data = cfg.model_dump() if hasattr(cfg, "model_dump") else {}
        if "llm" in data and "api_key" in data["llm"]:
            data["llm"]["api_key"] = "***" if data["llm"]["api_key"] else "(not set)"
        if "portal" in data and "secret_key" in data["portal"]:
            data["portal"]["secret_key"] = "***" if data["portal"]["secret_key"] else "(not set)"
        console.print_json(json.dumps(data, indent=2, default=str))
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")


@config.command()
def validate():
    """Validate configuration."""
    try:
        from config.loader import load_config
        from config.validator import validate_config
        cfg = load_config("config/optaware.yaml")
        issues = validate_config(cfg)
        if issues:
            console.print("[yellow]Configuration issues found:[/yellow]")
            for issue in issues:
                console.print(f"  - {issue}")
        else:
            console.print("[green]Configuration is valid.[/green]")
    except Exception as e:
        console.print(f"[red]Validation error: {e}[/red]")


@cli.command()
def setup():
    """Run the setup wizard."""
    from setup_wizard import run_wizard
    run_wizard()


def main():
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
