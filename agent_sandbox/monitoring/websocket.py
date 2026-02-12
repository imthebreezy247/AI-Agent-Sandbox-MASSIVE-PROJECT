"""WebSocket feed — real-time streaming of agent events and messages.

Clients connect to /ws and receive a live JSON stream of:
- Agent state changes (spawn, pause, kill, etc.)
- Task lifecycle events (submitted, assigned, completed, failed)
- Message bus traffic
- System health events
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from agent_sandbox.core.orchestrator import Orchestrator
from agent_sandbox.comms.protocol import Message

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator
        self._connections: list[WebSocket] = []
        self._running = False

    def attach(self, app: Any) -> None:
        """Attach WebSocket endpoint to a FastAPI app."""
        manager = self

        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await manager.connect(ws)
            try:
                while True:
                    # Keep connection alive; clients can send commands too
                    data = await ws.receive_text()
                    await manager._handle_client_message(ws, data)
            except WebSocketDisconnect:
                manager.disconnect(ws)

    async def start(self) -> None:
        """Start listening to orchestrator events."""
        self._running = True

        # Listen to all messages on the bus
        self.orchestrator.bus.add_listener(self._on_bus_message)

        # Listen to orchestrator events
        self.orchestrator.on("agent_spawned", self._on_event)
        self.orchestrator.on("agent_killed", self._on_event)
        self.orchestrator.on("agent_stale", self._on_event)
        self.orchestrator.on("task_result", self._on_event)

        # Start periodic status broadcast
        asyncio.create_task(self._status_broadcast_loop())

        logger.info("WebSocket manager started")

    async def stop(self) -> None:
        """Stop the WebSocket manager."""
        self._running = False
        self.orchestrator.bus.remove_listener(self._on_bus_message)
        for ws in self._connections[:]:
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()

    async def connect(self, ws: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await ws.accept()
        self._connections.append(ws)
        logger.info("WebSocket client connected (%d total)", len(self._connections))

        # Send initial state snapshot
        await self._send(ws, {
            "type": "snapshot",
            "data": self.orchestrator.status(),
            "agents": self.orchestrator.list_agents(),
            "tasks": self.orchestrator.list_tasks(),
            "timestamp": time.time(),
        })

    def disconnect(self, ws: WebSocket) -> None:
        """Handle client disconnect."""
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Send data to all connected clients."""
        if not self._connections:
            return

        payload = json.dumps(data, default=str)
        disconnected = []

        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    async def _send(self, ws: WebSocket, data: dict[str, Any]) -> None:
        """Send data to a single client."""
        try:
            await ws.send_text(json.dumps(data, default=str))
        except Exception:
            self.disconnect(ws)

    async def _on_bus_message(self, message: Message) -> None:
        """Forward bus messages to WebSocket clients."""
        await self.broadcast({
            "type": "message",
            "data": message.to_dict(),
            "timestamp": time.time(),
        })

    async def _on_event(self, event: str, data: Any) -> None:
        """Forward orchestrator events to WebSocket clients."""
        await self.broadcast({
            "type": "event",
            "event": event,
            "data": data,
            "timestamp": time.time(),
        })

    async def _handle_client_message(self, ws: WebSocket, raw: str) -> None:
        """Handle incoming messages from WebSocket clients."""
        try:
            data = json.loads(raw)
            action = data.get("action")

            if action == "ping":
                await self._send(ws, {"type": "pong", "timestamp": time.time()})

            elif action == "get_status":
                await self._send(ws, {
                    "type": "status",
                    "data": self.orchestrator.status(),
                    "timestamp": time.time(),
                })

        except json.JSONDecodeError:
            pass

    async def _status_broadcast_loop(self) -> None:
        """Periodically broadcast system status to all clients."""
        while self._running:
            await asyncio.sleep(5)
            if self._connections:
                await self.broadcast({
                    "type": "status",
                    "data": self.orchestrator.status(),
                    "agents": self.orchestrator.list_agents(),
                    "timestamp": time.time(),
                })
