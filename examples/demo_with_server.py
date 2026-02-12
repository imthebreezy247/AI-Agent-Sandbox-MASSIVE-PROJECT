"""Demo with monitoring server — starts the full stack with pre-spawned agents.

This starts the API server with agents already running so you can
open the dashboard immediately and see activity.

Run with:
    python examples/demo_with_server.py

Then open: http://localhost:8000/dashboard
"""

import asyncio
import logging

from agent_sandbox.config import SandboxConfig
from agent_sandbox.core.orchestrator import Orchestrator
from agent_sandbox.monitoring.api import create_api
from agent_sandbox.monitoring.websocket import WebSocketManager
from agent_sandbox.tasks.models import TaskPriority

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def seed_agents_and_tasks(orch: Orchestrator):
    """Seed the system with agents and tasks for the demo."""
    await asyncio.sleep(1)  # Let server start

    # Spawn agents
    await orch.spawn_pool("worker", count=4, tags=["general"])
    await orch.spawn_agent("specialist", tags=["python", "compute"])

    # Submit a wave of tasks
    for i in range(10):
        orch.submit_python(
            f"import time; time.sleep(0.5); print('Task {i} result:', {i} ** 2)",
            priority=TaskPriority.NORMAL,
        )

    # Submit some shell tasks
    orch.submit_shell("echo 'System info:' && uname -a")
    orch.submit_shell("echo 'Disk usage:' && df -h /tmp")
    orch.submit_shell("echo 'Process count:' && ps aux | wc -l")


def main():
    config = SandboxConfig(max_agents=10, api_port=8000)
    orch = Orchestrator(config)
    app = create_api(orch)
    ws_manager = WebSocketManager(orch)
    ws_manager.attach(app)

    @app.on_event("startup")
    async def on_startup():
        await orch.start()
        await ws_manager.start()
        # Seed demo data in background
        asyncio.create_task(seed_agents_and_tasks(orch))
        print("\n  Agent Sandbox running on http://localhost:8000")
        print("  Dashboard: http://localhost:8000/dashboard")
        print("  API docs:  http://localhost:8000/docs\n")

    @app.on_event("shutdown")
    async def on_shutdown():
        await ws_manager.stop()
        await orch.stop()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
