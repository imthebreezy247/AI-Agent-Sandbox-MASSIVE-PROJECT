"""Master Orchestrator — the central controller that manages all agents.

The orchestrator:
- Spawns and destroys worker agents
- Dispatches tasks via the scheduler
- Monitors agent health (heartbeat checks)
- Handles agent failures (re-assignment, respawning)
- Provides the top-level API for the entire system
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable

from agent_sandbox.config import SandboxConfig, get_config
from agent_sandbox.core.agent import WorkerAgent, AgentState
from agent_sandbox.core.registry import AgentRegistry
from agent_sandbox.comms.message_bus import MessageBus
from agent_sandbox.comms.protocol import Message, MessageType
from agent_sandbox.tasks.queue import TaskQueue
from agent_sandbox.tasks.models import Task, TaskPriority
from agent_sandbox.tasks.scheduler import TaskScheduler

logger = logging.getLogger(__name__)


class Orchestrator:
    """Master agent that controls the entire sandbox platform.

    This is the single entry point for managing agents, submitting tasks,
    and querying system state. Inspired by e2b.dev's orchestration layer.
    """

    ORCHESTRATOR_ID = "orchestrator"

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or get_config()
        self.registry = AgentRegistry(max_agents=self.config.max_agents)
        self.bus = MessageBus()
        self.queue = TaskQueue()
        self.scheduler = TaskScheduler(self.queue, self.registry)

        self._running = False
        self._health_task: asyncio.Task | None = None
        self._event_handlers: dict[str, list[Callable]] = {}
        self.started_at: float | None = None

        # Subscribe orchestrator to the bus
        self.bus.subscribe(self.ORCHESTRATOR_ID, self._handle_message)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the orchestrator and all subsystems."""
        if self._running:
            return

        self._running = True
        self.started_at = time.time()

        # Start the task scheduler
        await self.scheduler.start()

        # Start health monitor
        self._health_task = asyncio.create_task(self._health_monitor())

        logger.info("Orchestrator started (max_agents=%d, mode=%s)",
                     self.config.max_agents, self.config.sandbox_mode)

    async def stop(self) -> None:
        """Stop the orchestrator and all agents."""
        self._running = False

        # Stop scheduler
        await self.scheduler.stop()

        # Stop health monitor
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        # Destroy all agents
        for agent in self.registry.get_all():
            await agent.destroy()
            self.bus.unsubscribe(agent.id)

        logger.info("Orchestrator stopped")

    # ------------------------------------------------------------------
    # Agent management
    # ------------------------------------------------------------------

    async def spawn_agent(
        self,
        name: str,
        tags: list[str] | None = None,
        agent_id: str | None = None,
    ) -> WorkerAgent:
        """Spawn a new worker agent with its own sandbox.

        Args:
            name: Human-readable agent name.
            tags: Optional tags for task routing.
            agent_id: Optional custom agent ID.

        Returns:
            The newly created WorkerAgent.
        """
        agent = WorkerAgent(
            name=name,
            agent_id=agent_id,
            sandbox_timeout=self.config.sandbox_timeout,
            tags=tags,
        )

        # Wire up callbacks
        agent.on_state_change(self._on_agent_state_change)

        # Register
        self.registry.register(agent)

        # Subscribe agent to message bus
        self.bus.subscribe(agent.id, self._make_agent_handler(agent))

        # Emit event
        await self._emit("agent_spawned", agent.to_dict())

        logger.info("Spawned agent %s (%s) tags=%s", agent.id, name, tags)
        return agent

    async def kill_agent(self, agent_id: str) -> bool:
        """Kill and remove an agent."""
        agent = self.registry.get(agent_id)
        if not agent:
            return False

        await agent.destroy()
        self.bus.unsubscribe(agent_id)
        self.registry.unregister(agent_id)

        await self._emit("agent_killed", {"agent_id": agent_id})
        return True

    async def pause_agent(self, agent_id: str) -> bool:
        """Pause an agent (stops accepting tasks)."""
        agent = self.registry.get(agent_id)
        if not agent:
            return False
        await agent.pause()
        return True

    async def resume_agent(self, agent_id: str) -> bool:
        """Resume a paused agent."""
        agent = self.registry.get(agent_id)
        if not agent:
            return False
        await agent.resume()
        return True

    def get_agent(self, agent_id: str) -> WorkerAgent | None:
        """Get an agent by ID."""
        return self.registry.get(agent_id)

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents."""
        return self.registry.to_list()

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def submit_task(
        self,
        task_type: str,
        payload: dict[str, Any],
        priority: TaskPriority = TaskPriority.NORMAL,
        tags: list[str] | None = None,
        timeout: int | None = None,
        depends_on: list[str] | None = None,
    ) -> Task:
        """Submit a task to the queue for execution.

        Args:
            task_type: "shell", "python", "write_file", "read_file", or custom.
            payload: Task-specific data.
            priority: Task priority level.
            tags: Tags for agent routing.
            timeout: Override default timeout.
            depends_on: Task IDs this task depends on.

        Returns:
            The created Task.
        """
        task = Task(
            type=task_type,
            payload=payload,
            priority=priority,
            tags=tags or [],
            timeout=timeout,
            depends_on=depends_on or [],
        )
        self.queue.submit(task)
        return task

    def submit_shell(self, command: str, **kwargs: Any) -> Task:
        """Shortcut: submit a shell command task."""
        return self.submit_task("shell", {"command": command}, **kwargs)

    def submit_python(self, code: str, **kwargs: Any) -> Task:
        """Shortcut: submit a Python code task."""
        return self.submit_task("python", {"code": code}, **kwargs)

    def get_task(self, task_id: str) -> Task | None:
        return self.queue.get_task(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self.queue.get_all()]

    def cancel_task(self, task_id: str) -> bool:
        return self.queue.cancel(task_id)

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    async def spawn_pool(
        self, name_prefix: str, count: int, tags: list[str] | None = None
    ) -> list[WorkerAgent]:
        """Spawn a pool of identical agents.

        Args:
            name_prefix: Base name (agents get numbered suffixes).
            count: Number of agents to spawn.
            tags: Tags applied to all agents.

        Returns:
            List of spawned agents.
        """
        agents = []
        for i in range(count):
            agent = await self.spawn_agent(
                name=f"{name_prefix}-{i}",
                tags=tags,
            )
            agents.append(agent)
        logger.info("Spawned pool of %d agents: %s-*", count, name_prefix)
        return agents

    async def submit_batch(
        self,
        tasks: list[dict[str, Any]],
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> list[Task]:
        """Submit a batch of tasks at once.

        Args:
            tasks: List of dicts with "type" and "payload" keys.
            priority: Priority for all tasks in the batch.

        Returns:
            List of created Tasks.
        """
        created = []
        for t in tasks:
            task = self.submit_task(
                task_type=t["type"],
                payload=t["payload"],
                priority=priority,
                tags=t.get("tags", []),
            )
            created.append(task)
        return created

    # ------------------------------------------------------------------
    # Monitoring / Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Get full system status."""
        return {
            "running": self._running,
            "started_at": self.started_at,
            "uptime_seconds": (time.time() - self.started_at) if self.started_at else 0,
            "config": {
                "max_agents": self.config.max_agents,
                "sandbox_mode": self.config.sandbox_mode,
                "sandbox_timeout": self.config.sandbox_timeout,
            },
            "agents": self.registry.summary(),
            "tasks": self.queue.summary(),
            "message_bus": self.bus.stats,
        }

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on(self, event: str, handler: Callable) -> None:
        """Register an event handler."""
        self._event_handlers.setdefault(event, []).append(handler)

    async def _emit(self, event: str, data: Any = None) -> None:
        """Emit an event to all registered handlers."""
        for handler in self._event_handlers.get(event, []):
            try:
                result = handler(event, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("Event handler error (%s): %s", event, exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _handle_message(self, message: Message) -> None:
        """Handle messages sent to the orchestrator."""
        if message.type == MessageType.HEARTBEAT:
            agent = self.registry.get(message.sender)
            if agent:
                agent.heartbeat()

        elif message.type == MessageType.AGENT_ERROR:
            logger.error("Agent %s reported error: %s",
                         message.sender, message.payload.get("error"))

        elif message.type == MessageType.TASK_RESULT:
            await self._emit("task_result", message.payload)

    def _make_agent_handler(self, agent: WorkerAgent) -> Callable:
        """Create a message handler for an agent."""
        async def handler(message: Message) -> None:
            if message.type == MessageType.TASK_ASSIGN:
                result = await agent.execute_task(message.payload)
                reply = message.reply(result, MessageType.TASK_RESULT)
                await self.bus.publish(reply)

            elif message.type == MessageType.AGENT_PAUSE:
                await agent.pause()

            elif message.type == MessageType.AGENT_RESUME:
                await agent.resume()

            elif message.type == MessageType.AGENT_KILL:
                await self.kill_agent(agent.id)

            elif message.type == MessageType.PING:
                pong = message.reply({"agent_id": agent.id}, MessageType.PONG)
                await self.bus.publish(pong)

        return handler

    def _on_agent_state_change(self, agent: WorkerAgent, old: AgentState, new: AgentState) -> None:
        """Callback when an agent's state changes."""
        logger.debug("Agent %s: %s -> %s", agent.id, old.value, new.value)

    async def _health_monitor(self) -> None:
        """Periodically check agent health via heartbeats."""
        while self._running:
            try:
                stale = self.registry.find_stale_agents(stale_threshold=120)
                for agent in stale:
                    logger.warning("Agent %s appears stale (no heartbeat for >120s)", agent.id)
                    await self._emit("agent_stale", {"agent_id": agent.id})
            except Exception as exc:
                logger.error("Health monitor error: %s", exc)

            await asyncio.sleep(30)
