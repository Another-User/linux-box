"""Agent identity and role-based permission definitions.

Each OptAware agent process runs under a distinct Linux service account with
a well-defined role.  This module provides the canonical role enumeration,
an identity dataclass resolved from the running process, and a permission
matrix that maps roles to allowed operations.
"""

from __future__ import annotations

import os
import pwd
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import FrozenSet


class AgentRole(str, Enum):
    """Roles that an OptAware agent process can assume."""

    coordinator = "coordinator"
    observer = "observer"
    planner = "planner"
    executor = "executor"
    auditor = "auditor"


class Permission(str, Enum):
    """Granular permissions that can be granted to agent roles."""

    read_metrics = "read_metrics"
    read_logs = "read_logs"
    read_config = "read_config"
    execute_commands = "execute_commands"
    write_audit = "write_audit"
    call_llm = "call_llm"
    manage_services = "manage_services"
    manage_agents = "manage_agents"
    broadcast_events = "broadcast_events"
    approve_actions = "approve_actions"


# Maps each role to the set of permissions it holds.
ROLE_PERMISSIONS: dict[AgentRole, FrozenSet[Permission]] = {
    AgentRole.coordinator: frozenset({
        Permission.read_metrics,
        Permission.read_logs,
        Permission.read_config,
        Permission.manage_agents,
        Permission.broadcast_events,
        Permission.approve_actions,
        Permission.manage_services,
    }),
    AgentRole.observer: frozenset({
        Permission.read_metrics,
        Permission.read_logs,
        Permission.broadcast_events,
    }),
    AgentRole.planner: frozenset({
        Permission.read_metrics,
        Permission.read_logs,
        Permission.read_config,
        Permission.call_llm,
    }),
    AgentRole.executor: frozenset({
        Permission.execute_commands,
        Permission.manage_services,
    }),
    AgentRole.auditor: frozenset({
        Permission.read_metrics,
        Permission.read_logs,
        Permission.read_config,
        Permission.write_audit,
    }),
}


@dataclass(frozen=True)
class AgentIdentity:
    """Immutable snapshot identifying a running agent process."""

    role: AgentRole
    pid: int
    hostname: str
    user: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def permissions(self) -> FrozenSet[Permission]:
        """Return the set of permissions granted to this agent's role."""
        return ROLE_PERMISSIONS.get(self.role, frozenset())

    def has_permission(self, perm: Permission) -> bool:
        """Check whether this agent is allowed to perform *perm*."""
        return perm in self.permissions

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict."""
        return {
            "role": self.role.value,
            "pid": self.pid,
            "hostname": self.hostname,
            "user": self.user,
            "started_at": self.started_at.isoformat(),
        }


def get_identity(role: AgentRole) -> AgentIdentity:
    """Create an :class:`AgentIdentity` from the current process state."""
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        user = str(os.getuid())

    return AgentIdentity(
        role=role,
        pid=os.getpid(),
        hostname=socket.gethostname(),
        user=user,
    )
