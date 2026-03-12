"""OptAware multi-agent architecture — identity, IPC, and role-based isolation."""

from agents.identity import AgentIdentity, AgentRole, Permission, ROLE_PERMISSIONS, get_identity
from agents.ipc import IPCClient, IPCServer
from agents.protocol import Message, MessageType, sign_message, verify_signature

__all__ = [
    "AgentIdentity",
    "AgentRole",
    "IPCClient",
    "IPCServer",
    "Message",
    "MessageType",
    "Permission",
    "ROLE_PERMISSIONS",
    "get_identity",
    "sign_message",
    "verify_signature",
]
