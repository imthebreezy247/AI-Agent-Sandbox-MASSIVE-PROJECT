"""Worker agent — runs inside a sandbox and executes tasks.

Each agent:
- Owns a Sandbox instance for isolated execution
- Receives tasks from the orchestrator via the message bus
- Reports results and status back to the orchestrator
- Can be spawned, paused, resumed, or killed
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from agent_sandbox.core.sandbox import Sandbox, ExecutionResult, SandboxState

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class AgentInfo:
    """Public agent metadata for monitoring/registry."""

    agent_id: str
    name: str
    state: str
    sandbox_id: str
    created_at: float
    tasks_completed: int = 0
    tasks_failed: int = 0
    current_task_id: str | None = None
    last_heartbeat: float = 0.0
    tags: list[str] = field(default_factory=list)


# Type alias for task handler functions
TaskHandler = Callable[["WorkerAgent", dict[str, Any]], Awaitable[dict[str, Any]]]


class WorkerAgent:
    """A worker agent that runs tasks inside its own sandbox.

    The master orchestrator spawns these. Each agent owns a sandbox
    and processes tasks dispatched to it.
    """

    def __init__(
        self,
        name: str,
        agent_id: str | None = None,
        sandbox_timeout: int = 300,
        tags: list[str] | None = None,
    ):
        self.id = agent_id or f"agent-{uuid.uuid4().hex[:8]}"
        self.name = name
        self.state = AgentState.IDLE
        self.created_at = time.time()
        self.tags = tags or []

        # Each agent gets its own sandbox
        self.sandbox = Sandbox(
            sandbox_id=f"sbx-{self.id}",
            timeout=sandbox_timeout,
        )

        # Stats
        self.tasks_completed = 0
        self.tasks_failed = 0
        self.current_task_id: str | None = None
        self.last_heartbeat = time.time()

        # Custom task handlers keyed by task type
        self._handlers: dict[str, TaskHandler] = {}

        # Current working directory for repo context
        self._current_working_dir: str | None = None

        # Event callbacks
        self._on_task_complete: Callable | None = None
        self._on_task_failed: Callable | None = None
        self._on_state_change: Callable | None = None

        logger.info("Agent %s (%s) created", self.id, self.name)

    def register_handler(self, task_type: str, handler: TaskHandler) -> None:
        """Register a handler for a specific task type."""
        self._handlers[task_type] = handler

    def on_task_complete(self, callback: Callable) -> None:
        self._on_task_complete = callback

    def on_task_failed(self, callback: Callable) -> None:
        self._on_task_failed = callback

    def on_state_change(self, callback: Callable) -> None:
        self._on_state_change = callback

    def _set_state(self, new_state: AgentState) -> None:
        old = self.state
        self.state = new_state
        if self._on_state_change and old != new_state:
            try:
                self._on_state_change(self, old, new_state)
            except Exception:
                pass

    async def execute_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute a task dispatched by the orchestrator.

        Args:
            task: Dict with at least "task_id", "type", and "payload".

        Returns:
            Result dict with "status", "output", and metadata.
        """
        task_id = task.get("task_id", "unknown")
        task_type = task.get("type", "shell")
        payload = task.get("payload", {})

        # Get working directory from task metadata (for repo context)
        metadata = task.get("metadata", {})
        self._current_working_dir = metadata.get("working_dir")

        self.current_task_id = task_id
        self._set_state(AgentState.BUSY)
        self.last_heartbeat = time.time()

        logger.info("Agent %s executing task %s (type=%s)", self.id, task_id, task_type)

        try:
            # Check for custom handler first
            if task_type in self._handlers:
                result = await self._handlers[task_type](self, payload)
            elif task_type == "shell":
                result = await self._handle_shell(payload)
            elif task_type == "python":
                result = await self._handle_python(payload)
            elif task_type == "write_file":
                result = await self._handle_write_file(payload)
            elif task_type == "read_file":
                result = await self._handle_read_file(payload)
            else:
                result = {
                    "status": "error",
                    "error": f"Unknown task type: {task_type}",
                }

            # Track success/failure
            if result.get("status") == "error":
                self.tasks_failed += 1
                if self._on_task_failed:
                    await self._on_task_failed(self, task, result)
            else:
                self.tasks_completed += 1
                if self._on_task_complete:
                    await self._on_task_complete(self, task, result)

        except Exception as exc:
            self.tasks_failed += 1
            result = {
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
            logger.error("Agent %s task %s failed: %s", self.id, task_id, exc)
            if self._on_task_failed:
                try:
                    await self._on_task_failed(self, task, result)
                except Exception:
                    pass

        self.current_task_id = None
        self._set_state(AgentState.IDLE)
        self.last_heartbeat = time.time()

        return {
            "task_id": task_id,
            "agent_id": self.id,
            **result,
        }

    async def _handle_shell(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a shell command."""
        command = payload.get("command", "")
        if not command:
            return {"status": "error", "error": "No command provided"}

        result = await self.sandbox.execute(
            command,
            timeout=payload.get("timeout"),
            cwd=self._current_working_dir,
        )
        return self._execution_to_dict(result)

    async def _handle_python(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute Python code."""
        code = payload.get("code", "")
        if not code:
            return {"status": "error", "error": "No code provided"}

        result = await self.sandbox.execute_python(
            code,
            timeout=payload.get("timeout"),
        )
        return self._execution_to_dict(result)

    async def _handle_write_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write a file into the sandbox or repo."""
        from pathlib import Path

        path = payload.get("path", "")
        content = payload.get("content", "")
        if not path:
            return {"status": "error", "error": "No path provided"}

        # If we have a repo working dir, write relative to that
        if self._current_working_dir:
            full_path = Path(self._current_working_dir) / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            return {"status": "success", "path": str(full_path)}
        else:
            written = await self.sandbox.write_file(path, content)
            return {"status": "success", "path": str(written)}

    async def _handle_read_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Read a file from the sandbox or repo."""
        from pathlib import Path

        path = payload.get("path", "")
        if not path:
            return {"status": "error", "error": "No path provided"}

        try:
            # If we have a repo working dir, read relative to that
            if self._current_working_dir:
                full_path = Path(self._current_working_dir) / path
                content = full_path.read_text()
            else:
                content = await self.sandbox.read_file(path)
            return {"status": "success", "content": content}
        except FileNotFoundError:
            return {"status": "error", "error": f"File not found: {path}"}

    @staticmethod
    def _execution_to_dict(result: ExecutionResult) -> dict[str, Any]:
        status = "success" if result.exit_code == 0 and not result.timed_out else "error"
        d: dict[str, Any] = {
            "status": status,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }
        if result.timed_out:
            d["timed_out"] = True
        return d

    async def pause(self) -> None:
        """Pause this agent (stops accepting new tasks)."""
        self._set_state(AgentState.PAUSED)
        logger.info("Agent %s paused", self.id)

    async def resume(self) -> None:
        """Resume a paused agent."""
        if self.state == AgentState.PAUSED:
            self._set_state(AgentState.IDLE)
            logger.info("Agent %s resumed", self.id)

    async def stop(self) -> None:
        """Stop this agent and its sandbox."""
        self._set_state(AgentState.STOPPED)
        await self.sandbox.stop()
        logger.info("Agent %s stopped", self.id)

    async def destroy(self) -> None:
        """Destroy this agent and clean up sandbox workspace."""
        await self.stop()
        await self.sandbox.destroy()
        logger.info("Agent %s destroyed", self.id)

    def heartbeat(self) -> None:
        """Update heartbeat timestamp."""
        self.last_heartbeat = time.time()

    def get_info(self) -> AgentInfo:
        """Get agent metadata for monitoring."""
        return AgentInfo(
            agent_id=self.id,
            name=self.name,
            state=self.state.value,
            sandbox_id=self.sandbox.id,
            created_at=self.created_at,
            tasks_completed=self.tasks_completed,
            tasks_failed=self.tasks_failed,
            current_task_id=self.current_task_id,
            last_heartbeat=self.last_heartbeat,
            tags=self.tags,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        info = self.get_info()
        return {
            "agent_id": info.agent_id,
            "name": info.name,
            "state": info.state,
            "sandbox_id": info.sandbox_id,
            "created_at": info.created_at,
            "tasks_completed": info.tasks_completed,
            "tasks_failed": info.tasks_failed,
            "current_task_id": info.current_task_id,
            "last_heartbeat": info.last_heartbeat,
            "tags": info.tags,
            "sandbox": self.sandbox.to_dict(),
        }

    def __repr__(self) -> str:
        return f"<WorkerAgent id={self.id} name={self.name!r} state={self.state.value}>"
