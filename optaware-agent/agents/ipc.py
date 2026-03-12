"""Unix domain socket IPC for inter-agent communication.

Provides :class:`IPCServer` and :class:`IPCClient` that exchange
length-prefixed JSON messages over ``asyncio`` Unix stream sockets.

Wire format (per message)::

    ┌──────────────┬──────────────────────┐
    │ 4 bytes BE   │ N bytes UTF-8 JSON   │
    │ (length N)   │ (Message payload)    │
    └──────────────┴──────────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Optional

from agents.protocol import Message

logger = logging.getLogger("optaware.agents.ipc")

# 4-byte big-endian unsigned int for length prefix.
_HEADER_FMT = "!I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# Maximum single message size (16 MiB — generous for JSON payloads).
_MAX_MSG_SIZE = 16 * 1024 * 1024

# Type alias for the async handler callback.
MessageHandler = Callable[[Message], Coroutine[Any, Any, Optional[Message]]]


# ------------------------------------------------------------------
# Low-level helpers
# ------------------------------------------------------------------

async def _send_message(writer: asyncio.StreamWriter, msg: Message) -> None:
    """Write a length-prefixed message to *writer*."""
    data = msg.to_bytes()
    header = struct.pack(_HEADER_FMT, len(data))
    writer.write(header + data)
    await writer.drain()


async def _recv_message(reader: asyncio.StreamReader) -> Optional[Message]:
    """Read a length-prefixed message from *reader*, or ``None`` on EOF."""
    header = await reader.readexactly(_HEADER_SIZE)
    (length,) = struct.unpack(_HEADER_FMT, header)
    if length > _MAX_MSG_SIZE:
        raise ValueError(f"Message too large: {length} bytes (max {_MAX_MSG_SIZE})")
    data = await reader.readexactly(length)
    return Message.from_bytes(data)


# ------------------------------------------------------------------
# Server
# ------------------------------------------------------------------

class IPCServer:
    """Async Unix domain socket server for receiving agent messages.

    Usage::

        async def handler(msg: Message) -> Optional[Message]:
            print(msg)
            return None  # or return a response Message

        server = IPCServer("/run/optaware/coordinator.sock", handler)
        await server.start()
        # ... run until shutdown ...
        await server.stop()
    """

    def __init__(self, socket_path: str, handler: MessageHandler) -> None:
        self._socket_path = socket_path
        self._handler = handler
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        """Bind and start listening on the Unix socket."""
        path = Path(self._socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Remove stale socket file from a previous run.
        if path.exists():
            path.unlink()

        self._server = await asyncio.start_unix_server(
            self._client_connected, path=str(path)
        )

        # Restrict socket permissions: owner + group only.
        os.chmod(str(path), 0o660)
        logger.info("IPC server listening on %s", self._socket_path)

    async def stop(self) -> None:
        """Close the server and clean up the socket file."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        path = Path(self._socket_path)
        if path.exists():
            path.unlink()
            logger.debug("Removed socket file %s", self._socket_path)

        logger.info("IPC server stopped (%s)", self._socket_path)

    async def _client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection (may exchange many messages)."""
        peer = writer.get_extra_info("peername") or "unknown"
        logger.debug("IPC client connected: %s", peer)
        try:
            while True:
                try:
                    msg = await _recv_message(reader)
                except asyncio.IncompleteReadError:
                    break  # Client disconnected.
                except ValueError as exc:
                    logger.warning("Bad message from %s: %s", peer, exc)
                    break

                if msg is None:
                    break

                # Dispatch to the handler.
                try:
                    response = await self._handler(msg)
                    if response is not None:
                        await _send_message(writer, response)
                except Exception:
                    logger.exception("Handler error for message from %s", peer)
        finally:
            writer.close()
            await writer.wait_closed()
            logger.debug("IPC client disconnected: %s", peer)


# ------------------------------------------------------------------
# Client
# ------------------------------------------------------------------

class IPCClient:
    """Async Unix domain socket client for sending messages to an agent.

    Supports use as an async context manager::

        async with IPCClient("/run/optaware/coordinator.sock") as client:
            await client.send(msg)
            response = await client.receive()

    Also supports manual ``connect()`` / ``disconnect()`` with auto-reconnect.
    """

    def __init__(
        self,
        socket_path: str,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 4.0,
    ) -> None:
        self._socket_path = socket_path
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    # -- Context manager --

    async def __aenter__(self) -> "IPCClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # -- Connection management --

    async def connect(self) -> None:
        """Open a connection to the server, retrying with exponential backoff."""
        delay = self._base_delay
        last_err: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    self._socket_path
                )
                logger.debug("Connected to %s", self._socket_path)
                return
            except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
                last_err = exc
                if attempt < self._max_retries:
                    logger.debug(
                        "Connect attempt %d/%d to %s failed (%s), retrying in %.1fs",
                        attempt, self._max_retries, self._socket_path, exc, delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._max_delay)

        raise ConnectionError(
            f"Failed to connect to {self._socket_path} after {self._max_retries} attempts: {last_err}"
        )

    async def disconnect(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
            logger.debug("Disconnected from %s", self._socket_path)

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    # -- Messaging --

    async def send(self, msg: Message) -> None:
        """Send a message to the server."""
        if self._writer is None:
            raise ConnectionError("Not connected")
        await _send_message(self._writer, msg)

    async def receive(self) -> Message:
        """Wait for and return the next message from the server."""
        if self._reader is None:
            raise ConnectionError("Not connected")
        msg = await _recv_message(self._reader)
        if msg is None:
            raise ConnectionError("Server closed connection")
        return msg

    async def request(self, msg: Message) -> Message:
        """Send *msg* and return the server's response (request-response pattern)."""
        await self.send(msg)
        return await self.receive()
