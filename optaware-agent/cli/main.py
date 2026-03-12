"""OptAware Rich CLI — Phase 8.

Entry point: the ``optaware`` Click group.  Each sub-command uses Rich for
formatted terminal output (tables, panels, status spinners).

All commands that need live data communicate with the OptAware REST API
(default http://localhost:8080) via httpx.  The API base URL and API key can
be set through ``--api-url`` / ``--api-key`` options or the environment
variables ``OPTAWARE_API_URL`` / ``OPTAWARE_API_KEY``.
"""

from __future__ import annotations

import json as _json
import sys
from typing import Optional

import click
import httpx
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

_DEFAULT_API_URL = "http://localhost:8080"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _api(ctx: click.Context) -> str:
    return ctx.obj.get("api_url", _DEFAULT_API_URL)


def _headers(ctx: click.Context) -> dict[str, str]:
    key = ctx.obj.get("api_key", "")
    return {"X-API-Key": key} if key else {}


def _get(ctx: click.Context, path: str, params: Optional[dict] = None) -> dict:
    """GET from the OptAware API; exits on connection or HTTP error."""
    url = f"{_api(ctx)}{path}"
    try:
        resp = httpx.get(url, params=params, headers=_headers(ctx), timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        console.print(
            f"[bold red]Connection refused:[/bold red] cannot reach OptAware API at [cyan]{url}[/cyan].\n"
            "Make sure the daemon is running."
        )
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        console.print(
            f"[bold red]API error {exc.response.status_code}:[/bold red] {exc.response.text}"
        )
        sys.exit(1)


def _post(ctx: click.Context, path: str, body: Optional[dict] = None) -> dict:
    """POST to the OptAware API; exits on connection or HTTP error."""
    url = f"{_api(ctx)}{path}"
    try:
        resp = httpx.post(url, json=body or {}, headers=_headers(ctx), timeout=30)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        console.print(
            f"[bold red]Connection refused:[/bold red] cannot reach OptAware API at [cyan]{url}[/cyan]."
        )
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        console.print(
            f"[bold red]API error {exc.response.status_code}:[/bold red] {exc.response.text}"
        )
        sys.exit(1)


def _status_badge(status: str) -> str:
    """Wrap a service status string in the appropriate Rich colour markup."""
    colors = {
        "running": "green",
        "stopped": "red",
        "degraded": "yellow",
        "starting": "cyan",
        "stopping": "dim",
        "unknown": "dim",
    }
    c = colors.get(status.lower(), "white")
    return f"[{c}]{status}[/{c}]"


def _severity_badge(severity: str) -> str:
    """Wrap an event severity string in colour markup."""
    colors = {
        "info": "cyan",
        "warning": "yellow",
        "error": "red",
        "critical": "bold red",
    }
    c = colors.get(severity.lower(), "white")
    return f"[{c}]{severity}[/{c}]"


def _print_action_result(result: dict, label: str) -> None:
    """Render an API action-result dict as a Rich panel."""
    success: bool = result.get("success", False)
    message: str = result.get("message", "")
    dry_run: bool = result.get("details", {}).get("dry_run", False)
    prefix = "[dim][DRY-RUN][/dim] " if dry_run else ""
    if success:
        console.print(
            Panel(
                f"{prefix}[bold green]OK[/bold green]  {message}",
                title=f"[bold]{label}[/bold]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                f"[bold red]FAILED[/bold red]  {message}",
                title=f"[bold]{label}[/bold]",
                border_style="red",
            )
        )


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version="0.1.0", prog_name="optaware")
@click.option(
    "--api-url",
    envvar="OPTAWARE_API_URL",
    default=_DEFAULT_API_URL,
    show_default=True,
    help="Base URL of the OptAware REST API.",
)
@click.option(
    "--api-key",
    envvar="OPTAWARE_API_KEY",
    default="",
    help="API key for authentication.",
)
@click.pass_context
def cli(ctx: click.Context, api_url: str, api_key: str) -> None:
    """OptAware — AI-powered Linux server management agent."""
    ctx.ensure_object(dict)
    ctx.obj["api_url"] = api_url.rstrip("/")
    ctx.obj["api_key"] = api_key


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show system overview: service statuses table and metrics summary."""
    with console.status("[bold green]Fetching system status…[/bold green]"):
        data = _get(ctx, "/api/status")

    services_list: list[dict] = data.get("services", [])
    metrics: dict = data.get("metrics", {})

    # ---- Service status table ----
    svc_table = Table(
        title="Service Status",
        box=box.ROUNDED,
        header_style="bold magenta",
        show_header=True,
    )
    svc_table.add_column("Name", style="bold", min_width=18)
    svc_table.add_column("Display Name", min_width=26)
    svc_table.add_column("Type", justify="center", min_width=10)
    svc_table.add_column("Status", justify="center", min_width=10)
    svc_table.add_column("Port", justify="right", min_width=6)

    for svc in services_list:
        svc_table.add_row(
            svc.get("name", ""),
            svc.get("display_name", ""),
            svc.get("service_type", ""),
            _status_badge(svc.get("status", "unknown")),
            str(svc.get("port")) if svc.get("port") else "—",
        )

    console.print(svc_table)

    # ---- Metrics summary ----
    cpu = metrics.get("cpu", {})
    mem = metrics.get("memory", {})
    disk_parts = metrics.get("disk", {}).get("partitions", [])

    m_table = Table(
        title="System Metrics",
        box=box.SIMPLE,
        header_style="bold cyan",
        show_header=True,
    )
    m_table.add_column("Metric", style="bold", min_width=28)
    m_table.add_column("Value", min_width=22)

    m_table.add_row("CPU Usage", f"{cpu.get('usage_percent', 0.0):.1f}%")
    m_table.add_row(
        "Load Average (1m / 5m / 15m)",
        (
            f"{cpu.get('load_avg_1m', 0.0)} / "
            f"{cpu.get('load_avg_5m', 0.0)} / "
            f"{cpu.get('load_avg_15m', 0.0)}"
        ),
    )
    m_table.add_row("Memory Usage", f"{mem.get('percent', 0.0):.1f}%")
    used_gib = mem.get("used_bytes", 0) / (1024 ** 3)
    total_gib = mem.get("total_bytes", 0) / (1024 ** 3)
    m_table.add_row("Memory Used / Total", f"{used_gib:.2f} GiB / {total_gib:.2f} GiB")

    for part in disk_parts[:4]:
        m_table.add_row(
            f"Disk  {part.get('mountpoint', '?')}",
            f"{part.get('percent', 0.0):.1f}%",
        )

    console.print(m_table)


# ---------------------------------------------------------------------------
# services group
# ---------------------------------------------------------------------------


@cli.group()
def services() -> None:
    """Manage and inspect system services."""


@services.command(name="list")
@click.option(
    "--type",
    "svc_type",
    type=click.Choice(["all", "core", "elective"]),
    default="all",
    show_default=True,
    help="Filter by service type.",
)
@click.pass_context
def services_list(ctx: click.Context, svc_type: str) -> None:
    """List all registered services with their current status."""
    with console.status("[bold green]Loading services…[/bold green]"):
        data = _get(ctx, "/api/services")

    items: list[dict] = data if isinstance(data, list) else data.get("services", [])

    if svc_type != "all":
        items = [s for s in items if s.get("service_type", "") == svc_type]

    table = Table(
        title="All Services",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("Name", style="bold", min_width=18)
    table.add_column("Display Name", min_width=26)
    table.add_column("Type", justify="center", min_width=10)
    table.add_column("Status", justify="center", min_width=10)
    table.add_column("Unit / Container", min_width=26)
    table.add_column("Port", justify="right", min_width=6)

    for svc in items:
        unit = svc.get("systemd_unit") or svc.get("docker_container") or "—"
        table.add_row(
            svc.get("name", ""),
            svc.get("display_name", ""),
            svc.get("service_type", ""),
            _status_badge(svc.get("status", "unknown")),
            unit,
            str(svc.get("port")) if svc.get("port") else "—",
        )

    console.print(table)
    console.print(f"[dim]{len(items)} service(s) shown[/dim]")


@services.command(name="start")
@click.argument("name")
@click.option("--dry-run/--no-dry-run", default=True, show_default=True)
@click.pass_context
def services_start(ctx: click.Context, name: str, dry_run: bool) -> None:
    """Start service NAME."""
    with console.status(f"[bold green]Starting {name}…[/bold green]"):
        result = _post(ctx, f"/api/services/{name}/start", {"dry_run": dry_run})
    _print_action_result(result, f"Start  {name}")


@services.command(name="stop")
@click.argument("name")
@click.option("--dry-run/--no-dry-run", default=True, show_default=True)
@click.pass_context
def services_stop(ctx: click.Context, name: str, dry_run: bool) -> None:
    """Stop service NAME."""
    with console.status(f"[bold yellow]Stopping {name}…[/bold yellow]"):
        result = _post(ctx, f"/api/services/{name}/stop", {"dry_run": dry_run})
    _print_action_result(result, f"Stop  {name}")


@services.command(name="restart")
@click.argument("name")
@click.option("--dry-run/--no-dry-run", default=True, show_default=True)
@click.pass_context
def services_restart(ctx: click.Context, name: str, dry_run: bool) -> None:
    """Restart service NAME."""
    with console.status(f"[bold cyan]Restarting {name}…[/bold cyan]"):
        result = _post(ctx, f"/api/services/{name}/restart", {"dry_run": dry_run})
    _print_action_result(result, f"Restart  {name}")


@services.command(name="diagnose")
@click.argument("name")
@click.pass_context
def services_diagnose(ctx: click.Context, name: str) -> None:
    """Show detailed diagnostics for service NAME."""
    with console.status(f"[bold green]Fetching details for {name}…[/bold green]"):
        svc = _get(ctx, f"/api/services/{name}")

    title = Text()
    title.append(svc.get("display_name") or name, style="bold")
    title.append("  ", style="")
    title.append(svc.get("status", "unknown"), style="dim")

    rows: list[tuple[str, str]] = [
        ("Name", svc.get("name", "")),
        ("Display Name", svc.get("display_name", "") or "—"),
        ("Type", svc.get("service_type", "")),
        ("Status", _status_badge(svc.get("status", "unknown"))),
        ("Systemd Unit", svc.get("systemd_unit") or "—"),
        ("Docker Container", svc.get("docker_container") or "—"),
        ("Port", str(svc.get("port")) if svc.get("port") else "—"),
        ("Health Check", svc.get("health_check_cmd") or "—"),
        ("Config Paths", ", ".join(svc.get("config_paths", [])) or "—"),
        ("Log Paths", ", ".join(svc.get("log_paths", [])) or "—"),
        (
            "Dependencies",
            ", ".join(d.get("service_name", "") for d in svc.get("dependencies", [])) or "—",
        ),
    ]

    detail_table = Table(box=box.SIMPLE, show_header=False)
    detail_table.add_column("Field", style="bold cyan", min_width=18)
    detail_table.add_column("Value")
    for field_name, value in rows:
        detail_table.add_row(field_name, value)

    console.print(Panel(detail_table, title=str(title), border_style="cyan"))


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--severity", "-s",
    type=click.Choice(["info", "warning", "error", "critical"]),
    default=None,
    help="Filter by severity level.",
)
@click.option("--service", "-S", default=None, help="Filter by service name.")
@click.option("--limit", "-n", default=20, show_default=True, help="Maximum events to show.")
@click.pass_context
def events(
    ctx: click.Context,
    severity: Optional[str],
    service: Optional[str],
    limit: int,
) -> None:
    """Show recent events, optionally filtered by severity or service."""
    params: dict = {"limit": limit}
    if severity:
        params["severity"] = severity
    if service:
        params["service"] = service

    with console.status("[bold green]Fetching events…[/bold green]"):
        data = _get(ctx, "/api/events", params=params)

    items: list[dict] = data if isinstance(data, list) else data.get("events", [])

    table = Table(
        title="Recent Events",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("Timestamp", min_width=20)
    table.add_column("Severity", justify="center", min_width=10)
    table.add_column("Source", min_width=18)
    table.add_column("Service", min_width=14)
    table.add_column("Message")

    for ev in items:
        table.add_row(
            str(ev.get("timestamp", ""))[:19],
            _severity_badge(ev.get("severity", "info")),
            ev.get("source", ""),
            ev.get("service_name") or "—",
            ev.get("message", ""),
        )

    console.print(table)
    console.print(f"[dim]{len(items)} event(s) shown[/dim]")


# ---------------------------------------------------------------------------
# actions group
# ---------------------------------------------------------------------------


@cli.group()
def actions() -> None:
    """Manage pending and historical actions."""


@actions.command(name="list")
@click.pass_context
def actions_list(ctx: click.Context) -> None:
    """List actions currently pending approval."""
    with console.status("[bold green]Fetching pending actions…[/bold green]"):
        data = _get(ctx, "/api/actions", params={"status": "pending"})

    items: list[dict] = data if isinstance(data, list) else data.get("actions", [])

    if not items:
        console.print("[dim]No pending actions.[/dim]")
        return

    table = Table(
        title="Pending Actions",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("ID", min_width=36)
    table.add_column("Type", min_width=18)
    table.add_column("Status", justify="center", min_width=12)
    table.add_column("Created", min_width=20)
    table.add_column("Steps", justify="right", min_width=6)

    for action in items:
        table.add_row(
            str(action.get("id", "")),
            action.get("action_type", ""),
            action.get("status", ""),
            str(action.get("created_at", ""))[:19],
            str(len(action.get("steps", []))),
        )

    console.print(table)


@actions.command(name="approve")
@click.argument("action_id")
@click.pass_context
def actions_approve(ctx: click.Context, action_id: str) -> None:
    """Approve the pending action with ACTION_ID."""
    with console.status(f"[bold green]Approving {action_id}…[/bold green]"):
        _post(ctx, f"/api/actions/{action_id}/approve")

    console.print(
        Panel(
            f"[bold green]Approved[/bold green]  Action [cyan]{action_id}[/cyan] is now approved for execution.",
            border_style="green",
        )
    )


@actions.command(name="deny")
@click.argument("action_id")
@click.option("--reason", "-r", default="", help="Reason for the denial.")
@click.pass_context
def actions_deny(ctx: click.Context, action_id: str, reason: str) -> None:
    """Deny the pending action with ACTION_ID."""
    with console.status(f"[bold red]Denying {action_id}…[/bold red]"):
        _post(ctx, f"/api/actions/{action_id}/deny", {"reason": reason})

    body = f"[bold red]Denied[/bold red]  Action [cyan]{action_id}[/cyan] has been denied."
    if reason:
        body += f"\n[dim]Reason: {reason}[/dim]"
    console.print(Panel(body, border_style="red"))


@actions.command(name="history")
@click.option("--limit", "-n", default=20, show_default=True, help="Number of actions to show.")
@click.pass_context
def actions_history(ctx: click.Context, limit: int) -> None:
    """Show the full action execution history."""
    with console.status("[bold green]Fetching action history…[/bold green]"):
        data = _get(ctx, "/api/actions", params={"limit": limit})

    items: list[dict] = data if isinstance(data, list) else data.get("actions", [])

    _st_colors: dict[str, str] = {
        "pending": "yellow",
        "approved": "cyan",
        "executing": "blue",
        "completed": "green",
        "failed": "red",
        "rolled_back": "dim",
    }

    table = Table(
        title="Action History",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("ID", min_width=36)
    table.add_column("Type", min_width=18)
    table.add_column("Status", justify="center", min_width=14)
    table.add_column("Created", min_width=20)
    table.add_column("Completed", min_width=20)

    for action in items:
        st = action.get("status", "")
        color = _st_colors.get(st, "white")
        table.add_row(
            str(action.get("id", "")),
            action.get("action_type", ""),
            f"[{color}]{st}[/{color}]",
            str(action.get("created_at", ""))[:19],
            str(action.get("completed_at") or "")[:19] or "—",
        )

    if not items:
        console.print("[dim]No actions found.[/dim]")
    else:
        console.print(table)


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("question", nargs=-1, required=True)
@click.pass_context
def ask(ctx: click.Context, question: tuple[str, ...]) -> None:
    """Ask OptAware a question about the system (sends to LLM).

    QUESTION is a natural-language query.

    Example:  optaware ask why is my web service responding slowly
    """
    q = " ".join(question)
    console.print(
        Panel(f"[bold]{q}[/bold]", title="[dim]Your question[/dim]", border_style="dim")
    )

    with console.status("[bold cyan]Thinking…[/bold cyan]"):
        result = _post(ctx, "/api/ask", {"question": q})

    answer: str = result.get("answer") or result.get("response") or str(result)
    sources: list[dict] = result.get("sources", [])

    console.print(
        Panel(answer, title="[bold cyan]OptAware[/bold cyan]", border_style="cyan")
    )

    if sources:
        console.print("\n[dim]Sources:[/dim]")
        for idx, src in enumerate(sources, 1):
            title = src.get("title", "Untitled")
            path = src.get("source_path", "")
            score = src.get("score", 0.0)
            console.print(f"  [dim]{idx}. {title}  {path}  [score={score:.3f}][/dim]")


# ---------------------------------------------------------------------------
# config group
# ---------------------------------------------------------------------------


@cli.group(name="config")
def config_group() -> None:
    """View and manage the OptAware configuration."""


@config_group.command(name="show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Display the current active configuration (secrets redacted)."""
    with console.status("[bold green]Loading configuration…[/bold green]"):
        data = _get(ctx, "/api/config")

    console.print(
        Panel(
            _json.dumps(data, indent=2, default=str),
            title="[bold]Current Configuration[/bold]",
            border_style="cyan",
        )
    )


@config_group.command(name="validate")
@click.pass_context
def config_validate(ctx: click.Context) -> None:
    """Validate the active configuration against the schema."""
    with console.status("[bold green]Validating…[/bold green]"):
        data = _post(ctx, "/api/config/validate")

    valid: bool = data.get("valid", False)
    errors: list[str] = data.get("errors", [])

    if valid:
        console.print(
            Panel("[bold green]Configuration is valid.[/bold green]", border_style="green")
        )
    else:
        error_lines = "\n".join(f"  • {e}" for e in errors) if errors else "Unknown error."
        console.print(
            Panel(
                f"[bold red]Configuration is INVALID:[/bold red]\n\n{error_lines}",
                border_style="red",
            )
        )


@config_group.command(name="reload")
@click.pass_context
def config_reload(ctx: click.Context) -> None:
    """Signal the daemon to reload its configuration from disk."""
    with console.status("[bold cyan]Reloading configuration…[/bold cyan]"):
        result = _post(ctx, "/api/config/reload")

    success: bool = result.get("success", result.get("reloaded", False))
    message: str = result.get("message", "Reload requested.")

    if success:
        console.print(Panel(f"[bold green]Reloaded.[/bold green]  {message}", border_style="green"))
    else:
        console.print(Panel(f"[bold red]Reload failed.[/bold red]  {message}", border_style="red"))


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


@cli.command()
def setup() -> None:
    """Run the interactive first-run setup wizard."""
    try:
        from setup_wizard import run_wizard  # noqa: PLC0415

        run_wizard()
    except ImportError:
        console.print(
            "[bold red]Error:[/bold red] setup_wizard module not found.\n"
            "Run this command from the optaware-agent project root."
        )
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup wizard cancelled.[/yellow]")
        sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
