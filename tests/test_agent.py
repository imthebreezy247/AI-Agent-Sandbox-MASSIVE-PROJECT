"""Tests for the worker agent."""

import asyncio
import pytest

from agent_sandbox.core.agent import WorkerAgent, AgentState


@pytest.fixture
async def agent():
    a = WorkerAgent(name="test-agent", sandbox_timeout=10)
    yield a
    await a.destroy()


async def test_agent_creation(agent):
    assert agent.state == AgentState.IDLE
    assert agent.name == "test-agent"


async def test_execute_shell_task(agent):
    result = await agent.execute_task({
        "task_id": "t1",
        "type": "shell",
        "payload": {"command": "echo agent_test"},
    })
    assert result["status"] == "success"
    assert "agent_test" in result["stdout"]
    assert agent.tasks_completed == 1


async def test_execute_python_task(agent):
    result = await agent.execute_task({
        "task_id": "t2",
        "type": "python",
        "payload": {"code": "print(3 * 7)"},
    })
    assert result["status"] == "success"
    assert "21" in result["stdout"]


async def test_unknown_task_type(agent):
    result = await agent.execute_task({
        "task_id": "t3",
        "type": "unknown_type",
        "payload": {},
    })
    assert result["status"] == "error"
    assert agent.tasks_failed == 1


async def test_pause_resume(agent):
    await agent.pause()
    assert agent.state == AgentState.PAUSED
    await agent.resume()
    assert agent.state == AgentState.IDLE


async def test_agent_info(agent):
    info = agent.get_info()
    assert info.name == "test-agent"
    assert info.agent_id == agent.id
