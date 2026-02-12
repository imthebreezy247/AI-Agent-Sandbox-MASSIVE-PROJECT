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

    # --- Dashboard ---

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        template_path = TEMPLATES_DIR / "dashboard.html"
        if not template_path.exists():
            raise HTTPException(status_code=500, detail="Dashboard template not found")
        return HTMLResponse(content=template_path.read_text())

    return app
