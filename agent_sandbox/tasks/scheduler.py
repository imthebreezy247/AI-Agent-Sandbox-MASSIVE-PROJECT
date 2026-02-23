"""Task scheduler — pulls tasks from the queue and dispatches to idle agents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_sandbox.core.agent import WorkerAgent, AgentState
from agent_sandbox.core.registry import AgentRegistry
from agent_sandbox.tasks.queue import TaskQueue
from agent_sandbox.tasks.models import Task, TaskStatus

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Continuously pulls tasks from the queue and assigns them to idle agents.

    Scheduling strategy:
    - Round-robin across idle agents
    - Respects task tags (if a task has tags, only agents with matching tags get it)
    - Handles agent failures by re-queuing the task
    """

    def __init__(self, queue: TaskQueue, registry: AgentRegistry):
        self.queue = queue
        self.registry = registry
        self._running = False
        self._dispatch_task: asyncio.Task | None = None
        self._active_dispatches: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """Start the scheduler loop."""
        self._running = True
        self._dispatch_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        # Cancel active dispatches
        for task in self._active_dispatches.values():
            task.cancel()
        self._active_dispatches.clear()
        logger.info("Scheduler stopped")

    async def _scheduler_loop(self) -> None:
        """Main scheduling loop."""
        while self._running:
            try:
                # Wait for a task (with timeout so we can check _running)
                task = await self.queue.get(timeout=1.0)
                if task is None:
                    continue

                # Find a suitable agent
                agent = self._find_agent(task)
                if agent is None:
                    # No agent available — re-queue
                    if not self.registry.get_all():
                        logger.warning("No agents spawned — task %s stuck in queue. "
                                       "Spawn agents with 'sandbox agent spawn worker'.",
                                       task.task_id)
                    else:
                        logger.debug("No idle agent for task %s, re-queuing", task.task_id)
                    task.status = TaskStatus.QUEUED
                    task.assigned_at = None
                    self.queue.submit(task)
                    await asyncio.sleep(0.5)
                    continue

                # Dispatch the task
                task.assigned_to = agent.id
                task.status = TaskStatus.RUNNING
                dispatch = asyncio.create_task(
                    self._dispatch_and_track(agent, task)
                )
                self._active_dispatches[task.task_id] = dispatch

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Scheduler error: %s", exc)
                await asyncio.sleep(1)

    def _find_agent(self, task: Task) -> WorkerAgent | None:
        """Find the best idle agent for a task."""
        idle_agents = self.registry.get_idle_agents()
        if not idle_agents:
            return None

        # If task has tags, prefer agents with matching tags
        if task.tags:
            tagged = [a for a in idle_agents if set(task.tags) & set(a.tags)]
            if tagged:
                idle_agents = tagged

        # Pick the agent with fewest completed tasks (load balance)
        return min(idle_agents, key=lambda a: a.tasks_completed)

    async def _dispatch_and_track(self, agent: WorkerAgent, task: Task) -> None:
        """Dispatch a task to an agent and track completion."""
        try:
            logger.info("Dispatching task %s to agent %s", task.task_id, agent.id)
            result = await agent.execute_task(task.to_dispatch_dict())

            if result.get("status") == "error":
                self.queue.fail(task.task_id, result.get("error", "Unknown error"))
            else:
                self.queue.complete(task.task_id, result)

            logger.info("Task %s completed on agent %s (status=%s)",
                        task.task_id, agent.id, result.get("status"))

        except Exception as exc:
            logger.error("Dispatch to agent %s failed: %s", agent.id, exc)
            self.queue.fail(task.task_id, str(exc))

        finally:
            self._active_dispatches.pop(task.task_id, None)
