"""WebSocket manager for real-time event streaming."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("optaware.cli.websocket")


class WebSocketManager:
    """Manage WebSocket connections for real-time event broadcasting."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("WebSocket client connected (%d total)", len(self._connections))

        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to OptAware event stream",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self._connections:
            self._connections.remove(websocket)
            logger.info("WebSocket client disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, event: dict) -> None:
        """Send an event to all connected clients."""
        if not self._connections:
            return

        message = json.dumps(event, default=str)
        disconnected = []

        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    async def send_to(self, websocket: WebSocket, data: dict) -> None:
        """Send data to a specific client."""
        try:
            await websocket.send_json(data)
        except Exception:
            self.disconnect(websocket)

    @property
    def client_count(self) -> int:
        return len(self._connections)


# Module-level singleton
ws_manager = WebSocketManager()


async def websocket_endpoint(websocket: WebSocket) -> None:
    """FastAPI WebSocket endpoint handler."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, handle client messages
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws_manager.send_to(websocket, {
                        "type": "pong",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
