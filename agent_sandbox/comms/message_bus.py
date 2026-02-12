"""Message bus — async message passing between orchestrator and agents.

Supports:
- Direct messages (sender -> recipient)
- Broadcast messages (sender -> all)
- Topic subscriptions
- Message history for monitoring
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable, Awaitable

from agent_sandbox.comms.protocol import Message, MessageType

logger = logging.getLogger(__name__)

MessageHandler = Callable[[Message], Awaitable[None]]


class MessageBus:
    """Async in-process message bus for agent communication.

    Each agent subscribes to receive messages addressed to it.
    The orchestrator uses this to dispatch tasks and receive results.
    """

    def __init__(self, history_size: int = 1000):
        self._subscribers: dict[str, MessageHandler] = {}
        self._topic_subscribers: dict[str, list[MessageHandler]] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._message_count = 0
        self._listeners: list[Callable[[Message], Any]] = []

    def subscribe(self, agent_id: str, handler: MessageHandler) -> None:
        """Subscribe an agent to receive direct messages."""
        self._subscribers[agent_id] = handler
        logger.debug("Agent %s subscribed to message bus", agent_id)

    def unsubscribe(self, agent_id: str) -> None:
        """Unsubscribe an agent from the message bus."""
        self._subscribers.pop(agent_id, None)
        logger.debug("Agent %s unsubscribed from message bus", agent_id)

    def subscribe_topic(self, topic: str, handler: MessageHandler) -> None:
        """Subscribe to a topic for broadcast/filtered messages."""
        self._topic_subscribers.setdefault(topic, []).append(handler)

    def add_listener(self, listener: Callable[[Message], Any]) -> None:
        """Add a global listener that sees ALL messages (for monitoring)."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[Message], Any]) -> None:
        """Remove a global listener."""
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    async def publish(self, message: Message) -> None:
        """Publish a message to the bus.

        Routes to the recipient agent, or broadcasts if recipient is "*".
        """
        self._message_count += 1
        self._history.append(message.to_dict())

        # Notify global listeners (monitoring)
        for listener in self._listeners:
            try:
                result = listener(message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("Listener error: %s", exc)

        # Route message
        if message.recipient == "*":
            # Broadcast to all subscribers
            tasks = []
            for agent_id, handler in self._subscribers.items():
                if agent_id != message.sender:
                    tasks.append(self._safe_deliver(handler, message))
            if tasks:
                await asyncio.gather(*tasks)
        else:
            # Direct delivery
            handler = self._subscribers.get(message.recipient)
            if handler:
                await self._safe_deliver(handler, message)
            else:
                logger.warning(
                    "No subscriber for recipient %s (msg %s)",
                    message.recipient, message.message_id,
                )

    async def _safe_deliver(self, handler: MessageHandler, message: Message) -> None:
        """Deliver a message with error handling."""
        try:
            await handler(message)
        except Exception as exc:
            logger.error("Message delivery failed: %s", exc)

    async def request(
        self, message: Message, timeout: float = 30.0
    ) -> Message | None:
        """Send a message and wait for a correlated reply.

        Args:
            message: The request message to send.
            timeout: Seconds to wait for a response.

        Returns:
            The reply message, or None if timed out.
        """
        reply_event = asyncio.Event()
        reply_holder: list[Message] = []

        async def catch_reply(msg: Message) -> None:
            if msg.correlation_id == message.message_id:
                reply_holder.append(msg)
                reply_event.set()

        # Temporarily subscribe for the reply
        reply_key = f"_reply_{message.message_id}"
        original_handler = self._subscribers.get(message.sender)
        self._subscribers[reply_key] = catch_reply

        # Also intercept on sender's channel
        async def intercept(msg: Message) -> None:
            await catch_reply(msg)
            if original_handler:
                await original_handler(msg)

        self._subscribers[message.sender] = intercept

        try:
            await self.publish(message)
            try:
                await asyncio.wait_for(reply_event.wait(), timeout=timeout)
                return reply_holder[0] if reply_holder else None
            except asyncio.TimeoutError:
                logger.warning("Request %s timed out after %.1fs", message.message_id, timeout)
                return None
        finally:
            self._subscribers.pop(reply_key, None)
            if original_handler:
                self._subscribers[message.sender] = original_handler

    def get_history(self, limit: int = 100, since: float | None = None) -> list[dict[str, Any]]:
        """Get recent message history for monitoring."""
        history = list(self._history)
        if since:
            history = [m for m in history if m.get("timestamp", 0) >= since]
        return history[-limit:]

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_messages": self._message_count,
            "subscribers": len(self._subscribers),
            "history_size": len(self._history),
        }
