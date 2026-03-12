"""Tests for the agents module — identity, protocol, and IPC."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from typing import Optional
from unittest.mock import patch

import pytest

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.identity import (
    AgentIdentity,
    AgentRole,
    Permission,
    ROLE_PERMISSIONS,
    get_identity,
)
from agents.protocol import (
    Message,
    MessageType,
    sign_message,
    verify_signature,
)


# ------------------------------------------------------------------
# AgentRole tests
# ------------------------------------------------------------------


class TestAgentRole:
    def test_all_roles_defined(self):
        expected = {"coordinator", "observer", "planner", "executor", "auditor"}
        assert {r.value for r in AgentRole} == expected

    def test_role_is_string_enum(self):
        assert AgentRole.coordinator == "coordinator"
        assert isinstance(AgentRole.executor, str)


# ------------------------------------------------------------------
# ROLE_PERMISSIONS tests
# ------------------------------------------------------------------


class TestRolePermissions:
    def test_all_roles_have_permissions(self):
        for role in AgentRole:
            assert role in ROLE_PERMISSIONS, f"Missing permissions for {role}"

    def test_observer_is_read_only(self):
        perms = ROLE_PERMISSIONS[AgentRole.observer]
        assert Permission.execute_commands not in perms
        assert Permission.manage_services not in perms
        assert Permission.write_audit not in perms
        assert Permission.read_metrics in perms
        assert Permission.read_logs in perms

    def test_planner_cannot_execute(self):
        perms = ROLE_PERMISSIONS[AgentRole.planner]
        assert Permission.execute_commands not in perms
        assert Permission.manage_services not in perms
        assert Permission.call_llm in perms

    def test_executor_can_execute(self):
        perms = ROLE_PERMISSIONS[AgentRole.executor]
        assert Permission.execute_commands in perms
        assert Permission.manage_services in perms

    def test_executor_cannot_call_llm(self):
        perms = ROLE_PERMISSIONS[AgentRole.executor]
        assert Permission.call_llm not in perms

    def test_auditor_can_write_audit(self):
        perms = ROLE_PERMISSIONS[AgentRole.auditor]
        assert Permission.write_audit in perms
        assert Permission.execute_commands not in perms

    def test_coordinator_has_management_permissions(self):
        perms = ROLE_PERMISSIONS[AgentRole.coordinator]
        assert Permission.manage_agents in perms
        assert Permission.approve_actions in perms


# ------------------------------------------------------------------
# AgentIdentity tests
# ------------------------------------------------------------------


class TestAgentIdentity:
    def test_get_identity(self):
        identity = get_identity(AgentRole.observer)
        assert identity.role == AgentRole.observer
        assert identity.pid == os.getpid()
        assert isinstance(identity.hostname, str)
        assert isinstance(identity.user, str)

    def test_identity_is_frozen(self):
        identity = get_identity(AgentRole.coordinator)
        with pytest.raises(AttributeError):
            identity.role = AgentRole.executor  # type: ignore

    def test_has_permission(self):
        identity = get_identity(AgentRole.executor)
        assert identity.has_permission(Permission.execute_commands)
        assert not identity.has_permission(Permission.call_llm)

    def test_permissions_property(self):
        identity = get_identity(AgentRole.planner)
        perms = identity.permissions
        assert Permission.call_llm in perms

    def test_to_dict(self):
        identity = get_identity(AgentRole.auditor)
        d = identity.to_dict()
        assert d["role"] == "auditor"
        assert d["pid"] == os.getpid()
        assert "hostname" in d
        assert "user" in d
        assert "started_at" in d


# ------------------------------------------------------------------
# Message tests
# ------------------------------------------------------------------


class TestMessage:
    def test_create_message(self):
        msg = Message(
            msg_type=MessageType.event,
            sender=AgentRole.observer,
            payload={"cpu_percent": 85.5},
        )
        assert msg.msg_type == MessageType.event
        assert msg.sender == AgentRole.observer
        assert msg.payload["cpu_percent"] == 85.5

    def test_serialize_deserialize(self):
        msg = Message(
            msg_type=MessageType.heartbeat,
            sender=AgentRole.coordinator,
            payload={"status": "ok"},
            msg_id="test-123",
        )
        data = msg.to_bytes()
        restored = Message.from_bytes(data)

        assert restored.msg_type == msg.msg_type
        assert restored.sender == msg.sender
        assert restored.payload == msg.payload
        assert restored.msg_id == msg.msg_id

    def test_all_message_types(self):
        expected_types = {
            "event", "metrics", "heartbeat",
            "diagnose_request", "diagnose_response", "action_plan",
            "command_request", "command_response", "action_result",
            "audit_record", "shutdown", "config_reload",
            "status_request", "status_response",
        }
        assert {t.value for t in MessageType} == expected_types


# ------------------------------------------------------------------
# Signing / verification tests
# ------------------------------------------------------------------


class TestSigning:
    def test_sign_and_verify(self):
        msg = Message(
            msg_type=MessageType.action_plan,
            sender=AgentRole.coordinator,
            payload={"steps": [{"command": "systemctl restart nginx"}]},
        )
        secret = "test-secret-key-12345"
        signed = sign_message(msg, secret)

        assert signed.signature != ""
        assert verify_signature(signed, secret)

    def test_wrong_secret_fails(self):
        msg = Message(
            msg_type=MessageType.action_plan,
            sender=AgentRole.coordinator,
            payload={"steps": []},
        )
        signed = sign_message(msg, "correct-secret")
        assert not verify_signature(signed, "wrong-secret")

    def test_tampered_payload_fails(self):
        msg = Message(
            msg_type=MessageType.action_plan,
            sender=AgentRole.coordinator,
            payload={"steps": [{"command": "systemctl restart nginx"}]},
        )
        secret = "my-secret"
        signed = sign_message(msg, secret)

        # Tamper with payload
        signed.payload["steps"][0]["command"] = "rm -rf /"
        assert not verify_signature(signed, secret)

    def test_unsigned_message_fails(self):
        msg = Message(
            msg_type=MessageType.action_plan,
            sender=AgentRole.coordinator,
            payload={},
        )
        assert not verify_signature(msg, "any-secret")

    def test_sign_preserves_fields(self):
        msg = Message(
            msg_type=MessageType.command_request,
            sender=AgentRole.planner,
            payload={"query": "test"},
            msg_id="req-456",
        )
        signed = sign_message(msg, "secret")
        assert signed.msg_type == msg.msg_type
        assert signed.sender == msg.sender
        assert signed.payload == msg.payload
        assert signed.msg_id == msg.msg_id
        assert signed.timestamp == msg.timestamp


# ------------------------------------------------------------------
# IPC tests (requires asyncio)
# ------------------------------------------------------------------


class TestIPC:
    @pytest.fixture
    def socket_path(self, tmp_path):
        return str(tmp_path / "test.sock")

    @pytest.mark.asyncio
    async def test_server_client_roundtrip(self, socket_path):
        """Test that a client can send a message and receive a response."""
        from agents.ipc import IPCClient, IPCServer

        async def handler(msg: Message) -> Optional[Message]:
            return Message(
                msg_type=MessageType.status_response,
                sender=AgentRole.coordinator,
                payload={"echo": msg.payload},
            )

        server = IPCServer(socket_path, handler)
        await server.start()

        try:
            async with IPCClient(socket_path) as client:
                request = Message(
                    msg_type=MessageType.status_request,
                    sender=AgentRole.observer,
                    payload={"hello": "world"},
                )
                await client.send(request)
                response = await asyncio.wait_for(client.receive(), timeout=5.0)

                assert response.msg_type == MessageType.status_response
                assert response.sender == AgentRole.coordinator
                assert response.payload["echo"] == {"hello": "world"}
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_no_response(self, socket_path):
        """Test fire-and-forget messages (handler returns None)."""
        from agents.ipc import IPCClient, IPCServer

        received = []

        async def handler(msg: Message) -> None:
            received.append(msg)
            return None

        server = IPCServer(socket_path, handler)
        await server.start()

        try:
            async with IPCClient(socket_path) as client:
                await client.send(Message(
                    msg_type=MessageType.event,
                    sender=AgentRole.observer,
                    payload={"event": "test"},
                ))
                # Give server time to process
                await asyncio.sleep(0.1)

            assert len(received) == 1
            assert received[0].payload["event"] == "test"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_client_reconnect_failure(self, tmp_path):
        """Test that client raises ConnectionError when server is not running."""
        from agents.ipc import IPCClient

        bad_path = str(tmp_path / "nonexistent.sock")
        client = IPCClient(bad_path, max_retries=2, base_delay=0.1)

        with pytest.raises(ConnectionError, match="Failed to connect"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_socket_permissions(self, socket_path):
        """Test that the server socket has restricted permissions."""
        from agents.ipc import IPCServer

        async def handler(msg):
            return None

        server = IPCServer(socket_path, handler)
        await server.start()

        try:
            mode = os.stat(socket_path).st_mode & 0o777
            assert mode == 0o660, f"Expected 0660, got {oct(mode)}"
        finally:
            await server.stop()


# ------------------------------------------------------------------
# Command allowlist tests
# ------------------------------------------------------------------


class TestCommandAllowlist:
    def test_exact_match(self):
        from agents.runner import _command_allowed
        allowlist = {"systemctl restart nginx", "systemctl reload nginx"}
        assert _command_allowed("systemctl restart nginx", allowlist)
        assert not _command_allowed("systemctl restart apache2", allowlist)

    def test_prefix_wildcard(self):
        from agents.runner import _command_allowed
        allowlist = {"systemctl restart *", "systemctl reload *"}
        assert _command_allowed("systemctl restart nginx", allowlist)
        assert _command_allowed("systemctl restart apache2", allowlist)
        assert not _command_allowed("rm -rf /", allowlist)

    def test_empty_allowlist(self):
        from agents.runner import _command_allowed
        assert not _command_allowed("any command", set())
