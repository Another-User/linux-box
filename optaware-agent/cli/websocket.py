"""OptAware WebSocket manager — Phase 8.

Provides a centralised :class:`WebSocketManager` that tracks all active
WebSocket connections and broadcasts JSON event payloads to them.

The module-level singleton ``ws_manager`` is imported by :mod:`cli.api` and
registered as the handler for the ``/ws/events`` endpoint.

Typical usage from the daemon event loop::

    from cli.websocket import ws_manager
    from models.events import Event

    async def on_new_event(event: Event) -> None:
        await ws_manager.broadcast({
            "type": "event",
            "id": str(event.id),
            "severity": event.severity.value,
            "source": event.source.value,
            "service_name": event.service_name,
            "message": event.message,
            "timestamp": event.timestamp.isoformat(),
        })

WebSocket endpoint
------------------
The FastAPI route is defined in :mod:`cli.api` at ``/ws/events``.
Clients connect to ``ws://host:8080/ws/events``.

Message protocol
----------------
Server → client (JSON):

    {"type": "connected", "message": "…", "timestamp": "…", "client_id": "…"}
    {"type": "event", "id": "…", "severity": "…", "message": "…", …}
    {"type": "pong", "timestamp": "…"}
    {"type": "broadcast", …}  — arbitrary broadcast payload

Client → server (JSON):

    {"type": "ping"}          — server replies with pong
    {"type": "subscribe", "filters": {"severity": ["error", "critical"]}}
    {"type": "unsubscribe"}   — remove filters (receive everything)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection wrapper
# ---------------------------------------------------------------------------


class _Connection:
    """Wraps a single :class:`~fastapi.WebSocket` with per-connection metadata.

    Attributes:
        client_id:  A unique identifier for this connection (UUID4 string).
        websocket:  The underlying FastAPI WebSocket object.
        filters:    Optional dict of filter criteria (e.g. severity list).
                    When ``None`` the connection receives all broadcasts.
        connected_at: ISO-8601 timestamp of connection acceptance.
    """

    __slots__ = ("client_id", "websocket", "filters", "connected_at")

    def __init__(self, websocket: WebSocket) -> None:
        self.client_id: str = str(uuid.uuid4())
        self.websocket: WebSocket = websocket
        self.filters: Optional[dict[str, Any]] = None
        self.connected_at: str = datetime.now(timezone.utc).isoformat()

    def matches(self, event: dict[str, Any]) -> bool:
        """Return ``True`` if *event* passes this connection's filters.

        When no filters are set every event matches.  Supported filter keys:

        * ``severity`` — list of severity strings; event must match one.
        * ``service``  — list of service-name strings; event must match one.
        """
        if self.filters is None:
            return True

        severity_filter: Optional[list[str]] = self.filters.get("severity")
        if severity_filter:
            ev_severity = event.get("severity", "")
            if ev_severity not in severity_filter:
                return False

        service_filter: Optional[list[str]] = self.filters.get("service")
        if service_filter:
            ev_service = event.get("service_name", "")
            if ev_service not in service_filter:
                return False

        return True


# ---------------------------------------------------------------------------
# WebSocketManager
# ---------------------------------------------------------------------------


class WebSocketManager:
    """Manage all active WebSocket connections and route event broadcasts.

    The manager is designed to be used as a module-level singleton.  It is
    intentionally *not* thread-locked because FastAPI/asyncio runs on a single
    event-loop thread by default.

    Usage::

        manager = WebSocketManager()

        # In a FastAPI WebSocket endpoint:
        await manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()   # or process ping/subscribe
        except WebSocketDisconnect:
            manager.disconnect(websocket)

        # From the daemon event loop:
        await manager.broadcast({"type": "event", "message": "…"})
    """

    def __init__(self) -> None:
        self._connections: dict[str, _Connection] = {}  # client_id → _Connection

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, websocket: WebSocket) -> _Connection:
        """Accept *websocket* and register it as an active connection.

        Sends a ``"connected"`` welcome message immediately after acceptance.

        Args:
            websocket: The incoming :class:`~fastapi.WebSocket` instance.

        Returns:
            The :class:`_Connection` wrapper created for this connection.
        """
        await websocket.accept()
        conn = _Connection(websocket)
        self._connections[conn.client_id] = conn
        logger.info(
            "WebSocket client %s connected (%d total).",
            conn.client_id,
            len(self._connections),
        )

        await self._send_safe(
            conn,
            {
                "type": "connected",
                "client_id": conn.client_id,
                "message": "Connected to OptAware real-time event stream.",
                "timestamp": conn.connected_at,
            },
        )
        return conn

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove the connection associated with *websocket*.

        This method is safe to call even if the connection is not currently
        tracked (e.g. after a double-disconnect).

        Args:
            websocket: The :class:`~fastapi.WebSocket` to deregister.
        """
        to_remove = [
            cid for cid, conn in self._connections.items() if conn.websocket is websocket
        ]
        for cid in to_remove:
            del self._connections[cid]
            logger.info(
                "WebSocket client %s disconnected (%d remaining).",
                cid,
                len(self._connections),
            )

    def disconnect_by_id(self, client_id: str) -> None:
        """Remove the connection with the given *client_id*.

        Args:
            client_id: The UUID string assigned at connection time.
        """
        if client_id in self._connections:
            del self._connections[client_id]
            logger.info(
                "WebSocket client %s removed by ID (%d remaining).",
                client_id,
                len(self._connections),
            )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def _send_safe(self, conn: _Connection, data: dict[str, Any]) -> bool:
        """Send *data* as JSON to *conn*, returning ``True`` on success.

        On any send error the connection is removed from the active set so
        subsequent broadcasts skip it.

        Args:
            conn: The :class:`_Connection` to send to.
            data: JSON-serialisable dict payload.

        Returns:
            ``True`` if the send succeeded, ``False`` otherwise.
        """
        try:
            await conn.websocket.send_text(json.dumps(data, default=str))
            return True
        except Exception as exc:
            logger.debug(
                "WebSocket send failed for client %s (%s); removing.",
                conn.client_id,
                exc,
            )
            self._connections.pop(conn.client_id, None)
            return False

    async def send_to(self, websocket: WebSocket, data: dict[str, Any]) -> bool:
        """Send *data* to a specific connection identified by its websocket.

        Args:
            websocket: Target :class:`~fastapi.WebSocket`.
            data:      JSON-serialisable payload.

        Returns:
            ``True`` if the connection was found and the send succeeded.
        """
        for conn in list(self._connections.values()):
            if conn.websocket is websocket:
                return await self._send_safe(conn, data)
        return False

    async def send_to_client(self, client_id: str, data: dict[str, Any]) -> bool:
        """Send *data* to the connection with the given *client_id*.

        Args:
            client_id: UUID string assigned at connection time.
            data:      JSON-serialisable payload.

        Returns:
            ``True`` if the client was found and the send succeeded.
        """
        conn = self._connections.get(client_id)
        if conn is None:
            return False
        return await self._send_safe(conn, data)

    async def broadcast(self, event: dict[str, Any]) -> int:
        """Broadcast *event* to all connections whose filters match.

        Connections that fail to receive the message are removed from the
        active set so future broadcasts are not delayed by dead sockets.

        Args:
            event: A JSON-serialisable dict representing the event payload.
                   Conventionally includes ``"type"``, ``"timestamp"``, and
                   any event-specific fields.

        Returns:
            The number of clients the event was successfully delivered to.
        """
        if not self._connections:
            return 0

        delivered = 0
        for conn in list(self._connections.values()):
            if conn.matches(event):
                ok = await self._send_safe(conn, event)
                if ok:
                    delivered += 1

        if delivered:
            logger.debug(
                "Broadcast event type=%r to %d/%d client(s).",
                event.get("type"),
                delivered,
                len(self._connections),
            )
        return delivered

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def set_filters(self, websocket: WebSocket, filters: Optional[dict[str, Any]]) -> None:
        """Set (or clear) event filters for the given connection.

        Args:
            websocket: The connection to update.
            filters:   A dict with optional keys ``severity`` (list[str]) and
                       ``service`` (list[str]).  Pass ``None`` to clear.
        """
        for conn in self._connections.values():
            if conn.websocket is websocket:
                conn.filters = filters
                logger.debug(
                    "Filters updated for client %s: %s",
                    conn.client_id,
                    filters,
                )
                return

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def client_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._connections)

    def client_ids(self) -> list[str]:
        """Return the list of active client ID strings."""
        return list(self._connections.keys())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WebSocketManager clients={self.client_count}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

ws_manager = WebSocketManager()


# ---------------------------------------------------------------------------
# Standalone FastAPI endpoint handler
# ---------------------------------------------------------------------------


async def websocket_endpoint(websocket: WebSocket) -> None:
    """FastAPI WebSocket handler for the ``/ws/events`` endpoint.

    This function can be registered directly with the FastAPI app::

        from fastapi import WebSocket
        from cli.websocket import websocket_endpoint

        @app.websocket("/ws/events")
        async def ws_route(ws: WebSocket):
            await websocket_endpoint(ws)

    In practice :mod:`cli.api` registers its own inline handler that delegates
    here, so you should not need to register this manually.

    Protocol summary
    ----------------
    * After connection the client receives ``{"type": "connected", …}``.
    * The client may send ``{"type": "ping"}`` and will receive
      ``{"type": "pong", "timestamp": "…"}``.
    * The client may send ``{"type": "subscribe", "filters": {…}}`` to
      restrict which events are delivered.
    * The client may send ``{"type": "unsubscribe"}`` to clear filters.
    * All other incoming frames are silently ignored.
    * On disconnect the connection is removed from the active set.
    """
    conn = await ws_manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await ws_manager.send_to(
                    websocket,
                    {"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()},
                )

            elif msg_type == "subscribe":
                filters = msg.get("filters")
                ws_manager.set_filters(websocket, filters)
                await ws_manager.send_to(
                    websocket,
                    {
                        "type": "subscribed",
                        "filters": filters,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )

            elif msg_type == "unsubscribe":
                ws_manager.set_filters(websocket, None)
                await ws_manager.send_to(
                    websocket,
                    {
                        "type": "unsubscribed",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.warning(
            "WebSocket handler error for client %s: %s", conn.client_id, exc
        )
        ws_manager.disconnect(websocket)
