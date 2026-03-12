"""Agent process runner — launches per-role main loops.

Each agent role has its own async main loop.  In multi-agent mode, each
role runs as a separate OS process under a dedicated service account.
In standalone (monolithic) mode, all roles run in-process as async tasks.

Entry point::

    python -m agents.runner --role observer --config /etc/optaware/optaware.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import grp
import logging
import os
import pwd
import signal
import sys
from typing import Optional

from agents.identity import AgentIdentity, AgentRole, Permission, get_identity
from agents.ipc import IPCClient, IPCServer
from agents.protocol import Message, MessageType, sign_message, verify_signature
from config import OptAwareConfig, load_config
from logging_setup import new_correlation_id, set_agent_role, setup_logging

logger = logging.getLogger("optaware.agents.runner")


# ------------------------------------------------------------------
# Privilege management
# ------------------------------------------------------------------

def drop_privileges(user: str, group: str) -> None:
    """Drop root privileges to *user*/*group*.

    Must be called **after** binding privileged sockets but **before**
    entering the main event loop.  No-op if already running as the
    target user or if not root.
    """
    if os.getuid() != 0:
        logger.debug("Not root (uid=%d); skipping privilege drop.", os.getuid())
        return

    try:
        pw = pwd.getpwnam(user)
        gr = grp.getgrnam(group)
    except KeyError as exc:
        logger.error("Cannot drop privileges — user/group not found: %s", exc)
        return

    # Set supplementary groups, then GID, then UID (order matters).
    os.setgroups([gr.gr_gid])
    os.setgid(gr.gr_gid)
    os.setuid(pw.pw_uid)

    logger.info("Dropped privileges to %s:%s (uid=%d, gid=%d)", user, group, pw.pw_uid, gr.gr_gid)


def _socket_path_for(role: AgentRole, config: OptAwareConfig) -> str:
    """Derive the IPC socket path for *role* from config."""
    agents_cfg = config.agents
    role_cfg = getattr(agents_cfg, role.value, None)
    if role_cfg and role_cfg.socket_path:
        return role_cfg.socket_path
    return os.path.join(agents_cfg.socket_dir, f"{role.value}.sock")


# ------------------------------------------------------------------
# Per-role main loops
# ------------------------------------------------------------------

async def run_coordinator(config: OptAwareConfig, identity: AgentIdentity) -> None:
    """Coordinator: orchestrates all agents, runs IPC server, manages API."""
    logger.info("Coordinator starting (pid=%d)", identity.pid)

    async def handle_message(msg: Message) -> Optional[Message]:
        logger.debug("Coordinator received %s from %s", msg.msg_type.value, msg.sender.value)

        if msg.msg_type == MessageType.event:
            # Forward events to auditor
            logger.debug("Event from %s: %s", msg.sender.value, msg.payload.get("message", ""))
            return None

        if msg.msg_type == MessageType.heartbeat:
            return Message(
                msg_type=MessageType.status_response,
                sender=AgentRole.coordinator,
                payload={"status": "ok", "agents_connected": True},
            )

        if msg.msg_type == MessageType.diagnose_response:
            # Planner returned an action plan — sign and forward to executor
            secret = config.agents.signing_secret
            if secret and msg.payload.get("action_plan"):
                plan_msg = Message(
                    msg_type=MessageType.action_plan,
                    sender=AgentRole.coordinator,
                    payload=msg.payload["action_plan"],
                )
                plan_msg = sign_message(plan_msg, secret)
                logger.info("Signed action plan for executor")
                # In a full implementation, forward to executor via its IPC socket
            return None

        if msg.msg_type == MessageType.action_result:
            logger.info("Action result: %s", msg.payload.get("status", "unknown"))
            return None

        if msg.msg_type == MessageType.status_request:
            return Message(
                msg_type=MessageType.status_response,
                sender=AgentRole.coordinator,
                payload=identity.to_dict(),
            )

        return None

    socket_path = _socket_path_for(AgentRole.coordinator, config)
    server = IPCServer(socket_path, handle_message)
    await server.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("Coordinator running — waiting for shutdown signal")
    await stop_event.wait()

    await server.stop()
    logger.info("Coordinator stopped")


async def run_observer(config: OptAwareConfig, identity: AgentIdentity) -> None:
    """Observer: collects metrics and logs, forwards events to coordinator."""
    logger.info("Observer starting (pid=%d)", identity.pid)

    coordinator_path = _socket_path_for(AgentRole.coordinator, config)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    interval = config.perception.metric_interval_sec

    while not stop_event.is_set():
        try:
            # Collect metrics via psutil
            metrics: dict = {}
            try:
                import psutil
                metrics = {
                    "cpu_percent": psutil.cpu_percent(interval=0.5),
                    "memory_percent": psutil.virtual_memory().percent,
                    "disk_percent": psutil.disk_usage("/").percent,
                }
            except ImportError:
                metrics = {"error": "psutil not available"}

            # Send to coordinator
            try:
                async with IPCClient(coordinator_path, max_retries=1) as client:
                    await client.send(Message(
                        msg_type=MessageType.metrics,
                        sender=AgentRole.observer,
                        payload=metrics,
                    ))
            except ConnectionError:
                logger.debug("Could not reach coordinator; will retry next interval")

        except Exception:
            logger.exception("Observer collection error")

        # Wait for interval or stop signal
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Observer stopped")


async def run_planner(config: OptAwareConfig, identity: AgentIdentity) -> None:
    """Planner: listens for diagnosis requests, calls LLM, returns plans."""
    logger.info("Planner starting (pid=%d)", identity.pid)

    async def handle_message(msg: Message) -> Optional[Message]:
        if msg.msg_type == MessageType.diagnose_request:
            logger.info("Received diagnosis request: %s", msg.payload.get("issue", ""))

            # In production, this would call the LLM via cognition layer
            diagnosis = {
                "issue": msg.payload.get("issue", ""),
                "analysis": "Automated analysis pending LLM integration",
                "action_plan": {
                    "description": f"Investigate: {msg.payload.get('issue', '')}",
                    "steps": [],
                    "risk_level": "low",
                },
            }

            return Message(
                msg_type=MessageType.diagnose_response,
                sender=AgentRole.planner,
                payload=diagnosis,
            )

        return None

    socket_path = _socket_path_for(AgentRole.planner, config)
    server = IPCServer(socket_path, handle_message)
    await server.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("Planner running — awaiting requests")
    await stop_event.wait()

    await server.stop()
    logger.info("Planner stopped")


async def run_executor(config: OptAwareConfig, identity: AgentIdentity) -> None:
    """Executor: validates signed action plans and executes approved commands."""
    logger.info("Executor starting (pid=%d)", identity.pid)

    signing_secret = config.agents.signing_secret
    executor_cfg = config.agents.executor
    allowed_commands = set(executor_cfg.allowed_commands) if executor_cfg else set()

    async def handle_message(msg: Message) -> Optional[Message]:
        if msg.msg_type == MessageType.action_plan:
            # Verify signature before executing anything
            if signing_secret and not verify_signature(msg, signing_secret):
                logger.warning("REJECTED: action plan with invalid signature from %s", msg.sender.value)
                return Message(
                    msg_type=MessageType.action_result,
                    sender=AgentRole.executor,
                    payload={"status": "rejected", "reason": "invalid_signature"},
                )

            # Check commands against allowlist
            steps = msg.payload.get("steps", [])
            for step in steps:
                cmd = step.get("command", "")
                if allowed_commands and not _command_allowed(cmd, allowed_commands):
                    logger.warning("REJECTED: command not in allowlist: %s", cmd)
                    return Message(
                        msg_type=MessageType.action_result,
                        sender=AgentRole.executor,
                        payload={"status": "rejected", "reason": f"command_not_allowed: {cmd}"},
                    )

            logger.info("Executing action plan: %s", msg.payload.get("description", ""))

            # Execute via the existing Executor class
            from planning.executor import Executor
            executor = Executor()

            # Build a minimal Action for the executor
            from models.actions import Action, ActionStep, ActionType
            action = Action(
                action_type=ActionType.remediate,
                description=msg.payload.get("description", ""),
                steps=[
                    ActionStep(
                        step_number=i + 1,
                        description=s.get("description", ""),
                        command=s.get("command"),
                    )
                    for i, s in enumerate(steps)
                ],
            )

            dry_run = config.planning.dry_run_default
            result = await executor.execute(action, dry_run=dry_run)

            return Message(
                msg_type=MessageType.action_result,
                sender=AgentRole.executor,
                payload={
                    "status": result.status.value,
                    "action_id": str(result.id),
                    "dry_run": dry_run,
                    "result": result.result,
                },
            )

        return None

    socket_path = _socket_path_for(AgentRole.executor, config)
    server = IPCServer(socket_path, handle_message)
    await server.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("Executor running — awaiting signed action plans")
    await stop_event.wait()

    await server.stop()
    logger.info("Executor stopped")


async def run_auditor(config: OptAwareConfig, identity: AgentIdentity) -> None:
    """Auditor: receives all events/actions and writes an immutable audit trail."""
    logger.info("Auditor starting (pid=%d)", identity.pid)

    async def handle_message(msg: Message) -> Optional[Message]:
        # Record everything to the audit trail
        try:
            from planning.audit import AuditTrail
            trail = AuditTrail()
            trail.record({
                "msg_type": msg.msg_type.value,
                "sender": msg.sender.value,
                "payload": msg.payload,
                "timestamp": msg.timestamp,
                "agent_role": identity.role.value,
            })
        except Exception:
            logger.exception("Failed to write audit record")

        return None

    socket_path = _socket_path_for(AgentRole.auditor, config)
    server = IPCServer(socket_path, handle_message)
    await server.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("Auditor running — recording all messages")
    await stop_event.wait()

    await server.stop()
    logger.info("Auditor stopped")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _command_allowed(cmd: str, allowlist: set[str]) -> bool:
    """Check whether *cmd* matches any pattern in the allowlist.

    Allowlist entries can be:
    - Exact commands: ``systemctl restart nginx``
    - Prefix patterns ending with ``*``: ``systemctl restart *``
    """
    cmd_stripped = cmd.strip()
    for pattern in allowlist:
        if pattern.endswith("*"):
            if cmd_stripped.startswith(pattern[:-1]):
                return True
        elif cmd_stripped == pattern:
            return True
    return False


# ------------------------------------------------------------------
# Role dispatch
# ------------------------------------------------------------------

_ROLE_RUNNERS = {
    AgentRole.coordinator: run_coordinator,
    AgentRole.observer: run_observer,
    AgentRole.planner: run_planner,
    AgentRole.executor: run_executor,
    AgentRole.auditor: run_auditor,
}


async def _run_agent(role: AgentRole, config: OptAwareConfig) -> None:
    """Set up identity, logging, optionally drop privileges, then run."""
    identity = get_identity(role)
    set_agent_role(role.value)
    new_correlation_id()

    setup_logging(
        level=config.general.log_level,
        log_file=os.path.join(config.general.data_dir, "logs", f"optaware-{role.value}.log")
        if os.path.isdir(os.path.join(config.general.data_dir, "logs"))
        else None,
    )

    # Drop privileges if configured and running as root.
    role_cfg = getattr(config.agents, role.value, None)
    if role_cfg and role_cfg.user:
        drop_privileges(role_cfg.user, role_cfg.group)

    runner = _ROLE_RUNNERS[role]
    await runner(config, identity)


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main() -> None:
    """CLI entry point: ``python -m agents.runner --role <role> --config <path>``."""
    parser = argparse.ArgumentParser(
        prog="optaware-agent",
        description="Run an OptAware agent process for a specific role.",
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=[r.value for r in AgentRole],
        help="Agent role to run.",
    )
    parser.add_argument(
        "--config",
        default="/etc/optaware/optaware.yaml",
        help="Path to the OptAware configuration file.",
    )
    args = parser.parse_args()

    role = AgentRole(args.role)
    config = load_config(args.config)

    try:
        asyncio.run(_run_agent(role, config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
