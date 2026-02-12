"""Message protocol — defines the message format for inter-agent communication."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """Types of messages that flow through the message bus."""

    # Orchestrator -> Agent
    TASK_ASSIGN = "task_assign"
    AGENT_PAUSE = "agent_pause"
    AGENT_RESUME = "agent_resume"
    AGENT_KILL = "agent_kill"
    PING = "ping"

    # Agent -> Orchestrator
    TASK_RESULT = "task_result"
    TASK_PROGRESS = "task_progress"
    AGENT_READY = "agent_ready"
    AGENT_ERROR = "agent_error"
    HEARTBEAT = "heartbeat"
    PONG = "pong"

    # System
    BROADCAST = "broadcast"
    LOG = "log"


@dataclass
class Message:
    """A message passed between agents via the message bus."""

    type: MessageType
    sender: str
    recipient: str  # agent_id or "*" for broadcast
    payload: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: f"msg-{uuid.uuid4().hex[:10]}")
    timestamp: float = field(default_factory=time.time)
    correlation_id: str | None = None  # links request/response pairs

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "type": self.type.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            message_id=data.get("message_id", f"msg-{uuid.uuid4().hex[:10]}"),
            type=MessageType(data["type"]),
            sender=data["sender"],
            recipient=data["recipient"],
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", time.time()),
            correlation_id=data.get("correlation_id"),
        )

    def reply(self, payload: dict[str, Any], msg_type: MessageType | None = None) -> Message:
        """Create a reply message to this message."""
        return Message(
            type=msg_type or MessageType.TASK_RESULT,
            sender=self.recipient,
            recipient=self.sender,
            payload=payload,
            correlation_id=self.message_id,
        )
