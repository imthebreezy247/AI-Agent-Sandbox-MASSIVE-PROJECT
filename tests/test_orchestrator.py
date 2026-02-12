"""Tests for the master orchestrator."""

import asyncio
import pytest

from agent_sandbox.config import SandboxConfig
from agent_sandbox.core.orchestrator import Orchestrator
from agent_sandbox.core.agent import AgentState
from agent_sandbox.tasks.models import TaskPriority


@pytest.fixture
async def orch():
    config = SandboxConfig(max_agents=5, sandbox_timeout=10)
    o = Orchestrator(config)
    await o.start()
    yield o
    await o.stop()


async def test_spawn_agent(orch):
    agent = await orch.spawn_agent("test-worker")
    assert agent.name == "test-worker"
    assert agent.state == AgentState.IDLE
    assert orch.registry.count == 1


async def test_spawn_pool(orch):
    agents = await orch.spawn_pool("pool", count=3)
    assert len(agents) == 3
    assert orch.registry.active_count == 3


async def test_kill_agent(orch):
    agent = await orch.spawn_agent("doomed")
    assert orch.registry.count == 1
    result = await orch.kill_agent(agent.id)
    assert result is True
    assert orch.registry.count == 0


async def test_submit_and_execute_task(orch):
    await orch.spawn_agent("worker")
    task = orch.submit_shell("echo orchestrator_test")

    # Wait for scheduler to pick it up
    await asyncio.sleep(2)

    t = orch.get_task(task.task_id)
    assert t is not None
    assert t.result is not None
    assert t.result.get("status") == "success"


async def test_status(orch):
    await orch.spawn_agent("w1")
    status = orch.status()
    assert status["running"] is True
    assert status["agents"]["active"] == 1


async def test_max_agents_enforced(orch):
    for i in range(5):
        await orch.spawn_agent(f"agent-{i}")

    with pytest.raises(RuntimeError, match="Max agents"):
        await orch.spawn_agent("one-too-many")
