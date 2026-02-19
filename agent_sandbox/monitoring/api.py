"""Monitoring REST API — FastAPI endpoints for managing agents and tasks.

Provides:
- GET /api/status — system overview
- GET /api/agents — list all agents
- POST /api/agents — spawn a new agent
- DELETE /api/agents/{id} — kill an agent
- POST /api/agents/{id}/pause — pause agent
- POST /api/agents/{id}/resume — resume agent
- GET /api/tasks — list all tasks
- POST /api/tasks — submit a task
- POST /api/tasks/interpret — submit natural language instruction (LLM)
- DELETE /api/tasks/{id} — cancel a task
- GET /api/messages — recent message history
- GET /dashboard — monitoring dashboard UI
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_sandbox.core.orchestrator import Orchestrator
from agent_sandbox.tasks.models import TaskPriority

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


# --------------- Request/Response models ---------------

class SpawnAgentRequest(BaseModel):
    name: str
    tags: list[str] = []
    count: int = 1  # spawn a pool if count > 1


class SubmitTaskRequest(BaseModel):
    type: str = "shell"
    payload: dict[str, Any] = {}
    priority: int = 1  # 0=LOW, 1=NORMAL, 2=HIGH, 3=CRITICAL
    tags: list[str] = []
    timeout: int | None = None


class BatchTaskRequest(BaseModel):
    tasks: list[SubmitTaskRequest]


class InterpretTaskRequest(BaseModel):
    instruction: str
    priority: int = 1
    repo: str | None = None  # Optional repo context


class CloneRepoRequest(BaseModel):
    url: str
    name: str | None = None


class PushRepoRequest(BaseModel):
    commit_message: str = "Changes from AI Agent"


# --------------- API Factory ---------------

def create_api(orchestrator: Orchestrator) -> FastAPI:
    """Create the FastAPI application wired to an orchestrator."""

    app = FastAPI(
        title="Agent Sandbox",
        description="Multi-agent sandbox monitoring and control API",
        version="0.1.0",
    )

    # --- Status ---

    @app.get("/api/status")
    async def get_status():
        return orchestrator.status()

    # --- Agents ---

    @app.get("/api/agents")
    async def list_agents():
        return orchestrator.list_agents()

    @app.post("/api/agents")
    async def spawn_agent(req: SpawnAgentRequest):
        try:
            if req.count > 1:
                agents = await orchestrator.spawn_pool(req.name, req.count, tags=req.tags)
                return [a.to_dict() for a in agents]
            else:
                agent = await orchestrator.spawn_agent(req.name, tags=req.tags)
                return agent.to_dict()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/api/agents/{agent_id}")
    async def get_agent(agent_id: str):
        agent = orchestrator.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent.to_dict()

    @app.delete("/api/agents/{agent_id}")
    async def kill_agent(agent_id: str):
        success = await orchestrator.kill_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "killed", "agent_id": agent_id}

    @app.post("/api/agents/{agent_id}/pause")
    async def pause_agent(agent_id: str):
        success = await orchestrator.pause_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "paused", "agent_id": agent_id}

    @app.post("/api/agents/{agent_id}/resume")
    async def resume_agent(agent_id: str):
        success = await orchestrator.resume_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "resumed", "agent_id": agent_id}

    # --- Tasks ---

    @app.get("/api/tasks")
    async def list_tasks():
        return orchestrator.list_tasks()

    @app.post("/api/tasks")
    async def submit_task(req: SubmitTaskRequest):
        task = orchestrator.submit_task(
            task_type=req.type,
            payload=req.payload,
            priority=TaskPriority(req.priority),
            tags=req.tags,
            timeout=req.timeout,
        )
        return task.to_dict()

    @app.post("/api/tasks/batch")
    async def submit_batch(req: BatchTaskRequest):
        tasks_data = [
            {"type": t.type, "payload": t.payload, "tags": t.tags}
            for t in req.tasks
        ]
        tasks = await orchestrator.submit_batch(tasks_data)
        return [t.to_dict() for t in tasks]

    @app.post("/api/tasks/interpret")
    async def interpret_task(req: InterpretTaskRequest):
        """Submit a natural language instruction to be interpreted by LLM."""
        from agent_sandbox.config import get_config
        from agent_sandbox.llm.interpreter import LLMInterpreter, LLMInterpreterError
        from agent_sandbox.repos.manager import RepoManager

        config = get_config()
        if not config.llm_api_key:
            raise HTTPException(
                status_code=503,
                detail="LLM not configured. Set ANTHROPIC_API_KEY environment variable.",
            )

        # Get repo context if specified
        working_dir = "."
        files = None
        if req.repo:
            try:
                manager = RepoManager(config.repos_dir, config.github_token)
                repo = await manager.get_repo(req.repo)
                working_dir = str(repo.path)
                files = manager.list_files(req.repo, max_files=200)
            except Exception as e:
                logger.warning(f"Failed to get repo context: {e}")

        try:
            interpreter = LLMInterpreter()
            tasks = await interpreter.interpret(
                instruction=req.instruction,
                working_dir=working_dir,
                files=files,
                priority=TaskPriority(req.priority),
            )
        except LLMInterpreterError as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Add repo context to task metadata
        for task in tasks:
            if req.repo:
                task.metadata["repo"] = req.repo
                task.metadata["working_dir"] = working_dir

        # Submit all tasks to the orchestrator
        submitted = []
        for task in tasks:
            orchestrator.queue.submit(task)
            submitted.append(task)
            logger.info(f"Submitted interpreted task {task.task_id}: {task.type}")

        return {
            "instruction": req.instruction,
            "repo": req.repo,
            "tasks_created": len(submitted),
            "tasks": [t.to_dict() for t in submitted],
        }

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        task = orchestrator.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task.to_dict()

    @app.delete("/api/tasks/{task_id}")
    async def cancel_task(task_id: str):
        success = orchestrator.cancel_task(task_id)
        if not success:
            raise HTTPException(status_code=404, detail="Task not found or not cancellable")
        return {"status": "cancelled", "task_id": task_id}

    # --- Messages ---

    @app.get("/api/messages")
    async def get_messages(limit: int = 100, since: float | None = None):
        return orchestrator.bus.get_history(limit=limit, since=since)

    # --- Repositories ---

    @app.get("/api/repos")
    async def list_repos():
        """List all cloned repositories."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)
        repos = await manager.list_repos()
        return [r.to_dict() for r in repos]

    @app.post("/api/repos")
    async def clone_repo(req: CloneRepoRequest):
        """Clone a GitHub repository."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager, RepoManagerError

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)

        try:
            repo = await manager.clone(req.url, req.name)
            return repo.to_dict()
        except RepoManagerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/repos/{name}")
    async def get_repo(name: str):
        """Get repository details."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager, RepoManagerError

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)

        try:
            repo = await manager.get_repo(name)
            status = await manager.get_status(name)
            return {**repo.to_dict(), "status": status.to_dict()}
        except RepoManagerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.delete("/api/repos/{name}")
    async def delete_repo(name: str):
        """Delete a cloned repository."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager, RepoManagerError

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)

        try:
            await manager.delete_repo(name)
            return {"status": "deleted", "name": name}
        except RepoManagerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/repos/{name}/files")
    async def list_repo_files(name: str):
        """List files in a repository."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager, RepoManagerError

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)

        try:
            files = manager.list_files(name)
            return {"name": name, "files": files, "count": len(files)}
        except RepoManagerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/repos/{name}/diff")
    async def get_repo_diff(name: str):
        """Get git diff for a repository."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager, RepoManagerError

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)

        try:
            diff = await manager.get_diff(name)
            return {"name": name, "diff": diff}
        except RepoManagerError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/repos/{name}/push")
    async def request_push(name: str, req: PushRepoRequest):
        """Request a push (creates pending approval)."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager, RepoManagerError

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)

        try:
            request = await manager.request_push(name, req.commit_message)
            return request.to_dict()
        except RepoManagerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/repos/{name}/push/approve")
    async def approve_push(name: str):
        """Approve and execute a pending push."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager, RepoManagerError

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)

        try:
            request = await manager.approve_push(name)
            return request.to_dict()
        except RepoManagerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/repos/{name}/push/reject")
    async def reject_push(name: str):
        """Reject a pending push."""
        from agent_sandbox.config import get_config
        from agent_sandbox.repos.manager import RepoManager, RepoManagerError

        config = get_config()
        manager = RepoManager(config.repos_dir, config.github_token)

        try:
            request = manager.reject_push(name)
            return request.to_dict()
        except RepoManagerError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # --- Dashboard ---

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        template_path = TEMPLATES_DIR / "dashboard.html"
        if not template_path.exists():
            raise HTTPException(status_code=500, detail="Dashboard template not found")
        return HTMLResponse(content=template_path.read_text(encoding="utf-8"))

    return app
