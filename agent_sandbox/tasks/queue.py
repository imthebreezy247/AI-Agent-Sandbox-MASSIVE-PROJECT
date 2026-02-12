"""Priority task queue for distributing work to agents."""

from __future__ import annotations

import asyncio
import heapq
import logging
import time
from typing import Any

from agent_sandbox.tasks.models import Task, TaskStatus, TaskPriority

logger = logging.getLogger(__name__)


class _PrioritizedTask:
    """Wrapper for heap ordering: higher priority first, then FIFO."""

    _counter = 0

    def __init__(self, task: Task):
        _PrioritizedTask._counter += 1
        self.priority = -task.priority.value  # negate for max-heap via min-heap
        self.seq = _PrioritizedTask._counter
        self.task = task

    def __lt__(self, other: _PrioritizedTask) -> bool:
        if self.priority == other.priority:
            return self.seq < other.seq
        return self.priority < other.priority


class TaskQueue:
    """Priority-based task queue with dependency tracking.

    Tasks are ordered by priority (CRITICAL > HIGH > NORMAL > LOW),
    with FIFO ordering within the same priority level.
    """

    def __init__(self):
        self._heap: list[_PrioritizedTask] = []
        self._all_tasks: dict[str, Task] = {}
        self._event = asyncio.Event()

    @property
    def pending_count(self) -> int:
        return len(self._heap)

    @property
    def total_count(self) -> int:
        return len(self._all_tasks)

    def submit(self, task: Task) -> Task:
        """Submit a task to the queue."""
        task.status = TaskStatus.QUEUED
        self._all_tasks[task.task_id] = task
        heapq.heappush(self._heap, _PrioritizedTask(task))
        self._event.set()
        logger.info("Task %s queued (priority=%s, type=%s)",
                     task.task_id, task.priority.name, task.type)
        return task

    async def get(self, timeout: float | None = None) -> Task | None:
        """Get the next task from the queue (blocks until available).

        Args:
            timeout: Max seconds to wait. None = wait forever.

        Returns:
            The highest-priority task, or None if timed out.
        """
        while True:
            # Try to pop from heap
            while self._heap:
                entry = heapq.heappop(self._heap)
                task = entry.task

                # Skip cancelled tasks still in heap
                if task.status == TaskStatus.CANCELLED:
                    continue

                # Check dependencies
                if task.depends_on:
                    deps_met = all(
                        self._all_tasks.get(dep_id, None) is not None
                        and self._all_tasks[dep_id].status == TaskStatus.COMPLETED
                        for dep_id in task.depends_on
                    )
                    if not deps_met:
                        # Re-queue — dependencies not yet met
                        heapq.heappush(self._heap, entry)
                        break

                task.status = TaskStatus.ASSIGNED
                task.assigned_at = time.time()
                return task

            # Nothing available, wait for new submissions
            self._event.clear()
            try:
                if timeout is not None:
                    await asyncio.wait_for(self._event.wait(), timeout=timeout)
                else:
                    await self._event.wait()
            except asyncio.TimeoutError:
                return None

    def cancel(self, task_id: str) -> bool:
        """Cancel a queued task."""
        task = self._all_tasks.get(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.QUEUED):
            task.status = TaskStatus.CANCELLED
            logger.info("Task %s cancelled", task_id)
            return True
        return False

    def complete(self, task_id: str, result: dict[str, Any]) -> None:
        """Mark a task as completed with its result."""
        task = self._all_tasks.get(task_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.result = result
            # Wake up queue in case dependent tasks are waiting
            self._event.set()

    def fail(self, task_id: str, error: str) -> None:
        """Mark a task as failed."""
        task = self._all_tasks.get(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            task.error = error

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        return self._all_tasks.get(task_id)

    def get_all(self, status: TaskStatus | None = None) -> list[Task]:
        """Get all tasks, optionally filtered by status."""
        tasks = list(self._all_tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def summary(self) -> dict[str, Any]:
        """Get queue statistics."""
        statuses: dict[str, int] = {}
        for task in self._all_tasks.values():
            statuses[task.status.value] = statuses.get(task.status.value, 0) + 1

        return {
            "total": self.total_count,
            "pending": self.pending_count,
            "by_status": statuses,
        }
