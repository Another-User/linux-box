"""IPC message protocol with HMAC-SHA256 signing.

Defines the message envelope exchanged between OptAware agent processes over
Unix domain sockets.  Action plans sent to the executor **must** carry a valid
HMAC-SHA256 signature produced by the coordinator's signing secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from agents.identity import AgentRole


class MessageType(str, Enum):
    """Types of messages exchanged between agents."""

    # Events & telemetry
    event = "event"
    metrics = "metrics"
    heartbeat = "heartbeat"

    # Diagnosis / planning
    diagnose_request = "diagnose_request"
    diagnose_response = "diagnose_response"
    action_plan = "action_plan"

    # Execution
    command_request = "command_request"
    command_response = "command_response"
    action_result = "action_result"

    # Audit
    audit_record = "audit_record"

    # Control
    shutdown = "shutdown"
    config_reload = "config_reload"
    status_request = "status_request"
    status_response = "status_response"


@dataclass
class Message:
    """Envelope for all inter-agent communication.

    Attributes:
        msg_type:   What kind of payload this message carries.
        sender:     The :class:`AgentRole` of the sending process.
        payload:    Arbitrary JSON-serialisable data.
        timestamp:  Unix epoch seconds (float).
        msg_id:     Optional unique ID for request/response correlation.
        signature:  HMAC-SHA256 hex digest (set by :func:`sign_message`).
    """

    msg_type: MessageType
    sender: AgentRole
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    msg_id: str = ""
    signature: str = ""

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialise to UTF-8 JSON bytes (compact, deterministic key order)."""
        return json.dumps(self._as_dict(), sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        """Deserialise from UTF-8 JSON bytes."""
        raw = json.loads(data)
        return cls(
            msg_type=MessageType(raw["msg_type"]),
            sender=AgentRole(raw["sender"]),
            payload=raw.get("payload", {}),
            timestamp=raw.get("timestamp", 0.0),
            msg_id=raw.get("msg_id", ""),
            signature=raw.get("signature", ""),
        )

    def _as_dict(self) -> dict[str, Any]:
        return {
            "msg_type": self.msg_type.value,
            "sender": self.sender.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "msg_id": self.msg_id,
            "signature": self.signature,
        }


# ------------------------------------------------------------------
# HMAC signing / verification
# ------------------------------------------------------------------

def _signing_input(msg: Message) -> bytes:
    """Produce the canonical byte string that is signed.

    The signature covers the message type, sender, payload, and timestamp
    but **not** the signature field itself.
    """
    canonical = json.dumps(
        {
            "msg_type": msg.msg_type.value,
            "sender": msg.sender.value,
            "payload": msg.payload,
            "timestamp": msg.timestamp,
            "msg_id": msg.msg_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return canonical


def sign_message(msg: Message, secret: str) -> Message:
    """Return a *copy* of *msg* with the ``signature`` field populated.

    The signature is an HMAC-SHA256 hex digest computed over the canonical
    JSON representation of the message (excluding the signature field).
    """
    digest = hmac.new(secret.encode(), _signing_input(msg), hashlib.sha256).hexdigest()
    return Message(
        msg_type=msg.msg_type,
        sender=msg.sender,
        payload=msg.payload,
        timestamp=msg.timestamp,
        msg_id=msg.msg_id,
        signature=digest,
    )


def verify_signature(msg: Message, secret: str) -> bool:
    """Return ``True`` when *msg* carries a valid HMAC-SHA256 signature.

    Uses :func:`hmac.compare_digest` for constant-time comparison.
    """
    if not msg.signature:
        return False
    expected = hmac.new(secret.encode(), _signing_input(msg), hashlib.sha256).hexdigest()
    return hmac.compare_digest(msg.signature, expected)
