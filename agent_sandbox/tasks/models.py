"""Task data models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class TaskPriority(int, Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Task:
    """A unit of work to be executed by a worker agent."""

    type: str  # "shell", "python", "write_file", "read_file", or custom
    payload: dict[str, Any]
    task_id: str = field(default_factory=lambda: f"task-{uuid.uuid4().hex[:10]}")
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    assigned_at: float | None = None
    completed_at: float | None = None
    assigned_to: str | None = None  # agent_id
    result: dict[str, Any] | None = None
    error: str | None = None
    timeout: int | None = None  # override sandbox default
    tags: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # task_ids
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dispatch_dict(self) -> dict[str, Any]:
        """Format for sending to an agent."""
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "type": self.type,
            "payload": self.payload,
        }
        if self.timeout:
            d["payload"]["timeout"] = self.timeout
        return d

    def to_dict(self) -> dict[str, Any]:
        """Full serialization for API/monitoring."""
        return {
            "task_id": self.task_id,
            "type": self.type,
            "priority": self.priority.value,
            "status": self.status.value,
            "payload": self.payload,
            "created_at": self.created_at,
            "assigned_at": self.assigned_at,
            "completed_at": self.completed_at,
            "assigned_to": self.assigned_to,
            "result": self.result,
            "error": self.error,
            "tags": self.tags,
            "depends_on": self.depends_on,
            "metadata": self.metadata,
        }

    @classmethod
    def shell(cls, command: str, **kwargs: Any) -> Task:
        """Create a shell command task."""
        return cls(type="shell", payload={"command": command}, **kwargs)

    @classmethod
    def python(cls, code: str, **kwargs: Any) -> Task:
        """Create a Python execution task."""
        return cls(type="python", payload={"code": code}, **kwargs)
