"""Tests for the task queue."""

import asyncio
import pytest

from agent_sandbox.tasks.queue import TaskQueue
from agent_sandbox.tasks.models import Task, TaskPriority, TaskStatus


@pytest.fixture
def queue():
    return TaskQueue()


async def test_submit_and_get(queue):
    task = Task.shell("echo test")
    queue.submit(task)
    assert queue.pending_count == 1

    got = await queue.get(timeout=1)
    assert got is not None
    assert got.task_id == task.task_id
    assert got.status == TaskStatus.ASSIGNED


async def test_priority_ordering(queue):
    low = Task.shell("low", priority=TaskPriority.LOW)
    high = Task.shell("high", priority=TaskPriority.HIGH)
    normal = Task.shell("normal", priority=TaskPriority.NORMAL)

    queue.submit(low)
    queue.submit(normal)
    queue.submit(high)

    first = await queue.get(timeout=1)
    assert first.task_id == high.task_id

    second = await queue.get(timeout=1)
    assert second.task_id == normal.task_id

    third = await queue.get(timeout=1)
    assert third.task_id == low.task_id


async def test_cancel(queue):
    task = Task.shell("cancel me")
    queue.submit(task)
    assert queue.cancel(task.task_id) is True

    # Cancelled task should be skipped
    result = await queue.get(timeout=0.5)
    assert result is None


async def test_complete(queue):
    task = Task.shell("complete me")
    queue.submit(task)
    await queue.get(timeout=1)
    queue.complete(task.task_id, {"status": "success"})

    t = queue.get_task(task.task_id)
    assert t.status == TaskStatus.COMPLETED
    assert t.result["status"] == "success"


async def test_summary(queue):
    queue.submit(Task.shell("a"))
    queue.submit(Task.shell("b"))
    s = queue.summary()
    assert s["total"] == 2
    assert s["pending"] == 2
