"""Sandbox runtime — isolated execution environments for agents.

Each sandbox provides:
- Isolated filesystem (temp directory)
- Subprocess-based code execution with resource limits
- Stdout/stderr capture and streaming
- Timeout enforcement
- Clean teardown
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SandboxState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class ExecutionResult:
    """Result of running code inside a sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxMetrics:
    """Resource usage metrics for a sandbox."""

    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    disk_mb: float = 0.0
    executions: int = 0
    total_runtime_ms: float = 0.0
    last_active: float = 0.0


class Sandbox:
    """An isolated execution environment for a single agent.

    Creates a temporary workspace directory and runs commands/code
    in subprocess isolation with timeout enforcement.
    """

    def __init__(
        self,
        sandbox_id: str | None = None,
        timeout: int = 300,
        working_dir: Path | None = None,
    ):
        self.id = sandbox_id or f"sbx-{uuid.uuid4().hex[:12]}"
        self.timeout = timeout
        self.state = SandboxState.CREATED
        self.created_at = time.time()
        self.metrics = SandboxMetrics()
        self._process: asyncio.subprocess.Process | None = None
        self._env: dict[str, str] = {}

        # Create isolated workspace
        if working_dir:
            self.workspace = working_dir
            self.workspace.mkdir(parents=True, exist_ok=True)
            self._owns_workspace = False
        else:
            self.workspace = Path(tempfile.mkdtemp(prefix=f"sandbox_{self.id}_"))
            self._owns_workspace = True

        logger.info("Sandbox %s created at %s", self.id, self.workspace)

    def set_env(self, key: str, value: str) -> None:
        """Set an environment variable for this sandbox's executions."""
        self._env[key] = value

    async def execute(
        self,
        command: str,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> ExecutionResult:
        """Execute a shell command inside this sandbox.

        Args:
            command: Shell command to run.
            timeout: Override default timeout (seconds).
            stdin_data: Optional data to pipe to stdin.

        Returns:
            ExecutionResult with stdout, stderr, exit code, and timing.
        """
        if self.state == SandboxState.STOPPED:
            raise RuntimeError(f"Sandbox {self.id} is stopped")

        effective_timeout = timeout or self.timeout
        self.state = SandboxState.RUNNING

        # Build restricted environment
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.workspace),
            "SANDBOX_ID": self.id,
            **self._env,
        }

        start = time.monotonic()
        timed_out = False

        try:
            self._process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                cwd=str(self.workspace),
                env=env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    self._process.communicate(
                        input=stdin_data.encode() if stdin_data else None
                    ),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                timed_out = True
                self._process.kill()
                stdout_bytes, stderr_bytes = await self._process.communicate()

            duration_ms = (time.monotonic() - start) * 1000
            exit_code = self._process.returncode or -1

            result = ExecutionResult(
                exit_code=exit_code,
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
                duration_ms=round(duration_ms, 2),
                timed_out=timed_out,
            )

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            result = ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                duration_ms=round(duration_ms, 2),
                timed_out=False,
                metadata={"error": type(exc).__name__},
            )
            self.state = SandboxState.FAILED
            logger.error("Sandbox %s execution failed: %s", self.id, exc)
            return result

        # Update metrics
        self.metrics.executions += 1
        self.metrics.total_runtime_ms += duration_ms
        self.metrics.last_active = time.time()

        if timed_out:
            logger.warning("Sandbox %s timed out after %ds", self.id, effective_timeout)

        self.state = SandboxState.RUNNING
        self._process = None
        return result

    async def execute_python(self, code: str, timeout: int | None = None) -> ExecutionResult:
        """Execute Python code inside this sandbox."""
        script_path = self.workspace / "_exec.py"
        script_path.write_text(code)
        try:
            return await self.execute(f"python3 {script_path}", timeout=timeout)
        finally:
            script_path.unlink(missing_ok=True)

    async def write_file(self, relative_path: str, content: str) -> Path:
        """Write a file into the sandbox workspace."""
        full_path = self.workspace / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        return full_path

    async def read_file(self, relative_path: str) -> str:
        """Read a file from the sandbox workspace."""
        full_path = self.workspace / relative_path
        return full_path.read_text()

    def list_files(self, relative_path: str = ".") -> list[str]:
        """List files in the sandbox workspace."""
        target = self.workspace / relative_path
        if not target.exists():
            return []
        return [
            str(p.relative_to(self.workspace))
            for p in target.rglob("*")
            if p.is_file()
        ]

    async def stop(self) -> None:
        """Stop this sandbox and clean up resources."""
        if self._process and self._process.returncode is None:
            self._process.kill()
            await self._process.wait()

        self.state = SandboxState.STOPPED
        logger.info("Sandbox %s stopped", self.id)

    async def destroy(self) -> None:
        """Stop and remove the sandbox workspace entirely."""
        await self.stop()
        if self._owns_workspace and self.workspace.exists():
            shutil.rmtree(self.workspace, ignore_errors=True)
            logger.info("Sandbox %s workspace destroyed", self.id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize sandbox state for API/monitoring."""
        return {
            "id": self.id,
            "state": self.state.value,
            "workspace": str(self.workspace),
            "created_at": self.created_at,
            "timeout": self.timeout,
            "metrics": {
                "executions": self.metrics.executions,
                "total_runtime_ms": self.metrics.total_runtime_ms,
                "last_active": self.metrics.last_active,
            },
        }

    def __repr__(self) -> str:
        return f"<Sandbox id={self.id} state={self.state.value}>"
