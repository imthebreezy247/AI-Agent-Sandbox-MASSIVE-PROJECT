"""Tests for the sandbox runtime."""

import asyncio
import pytest

from agent_sandbox.core.sandbox import Sandbox, SandboxState


@pytest.fixture
async def sandbox():
    sbx = Sandbox(timeout=10)
    yield sbx
    await sbx.destroy()


async def test_sandbox_creation(sandbox):
    assert sandbox.state == SandboxState.CREATED
    assert sandbox.workspace.exists()


async def test_execute_shell(sandbox):
    result = await sandbox.execute("echo hello")
    assert result.exit_code == 0
    assert "hello" in result.stdout


async def test_execute_python(sandbox):
    result = await sandbox.execute_python("print(2 + 2)")
    assert result.exit_code == 0
    assert "4" in result.stdout


async def test_timeout(sandbox):
    result = await sandbox.execute("sleep 30", timeout=1)
    assert result.timed_out


async def test_write_and_read_file(sandbox):
    await sandbox.write_file("test.txt", "hello world")
    content = await sandbox.read_file("test.txt")
    assert content == "hello world"


async def test_list_files(sandbox):
    await sandbox.write_file("a.txt", "a")
    await sandbox.write_file("subdir/b.txt", "b")
    files = sandbox.list_files()
    assert "a.txt" in files
    assert "subdir/b.txt" in files


async def test_metrics(sandbox):
    await sandbox.execute("echo test")
    assert sandbox.metrics.executions == 1
    assert sandbox.metrics.total_runtime_ms > 0


async def test_stop(sandbox):
    await sandbox.stop()
    assert sandbox.state == SandboxState.STOPPED
