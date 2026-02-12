"""CLI entry point — manage the Agent Sandbox from the command line.

Usage:
    sandbox start           Start the orchestrator + monitoring server
    sandbox status          Show system status
    sandbox agent spawn     Spawn a new agent
    sandbox agent list      List all agents
    sandbox agent kill      Kill an agent
    sandbox task submit     Submit a task
    sandbox task list       List all tasks
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import click
import httpx
from rich.console import Console
from rich.table import Table

console = Console()
DEFAULT_API = "http://localhost:8000"


def _api_url() -> str:
    return DEFAULT_API


# --------------- start command ---------------

@click.group()
def main():
    """Agent Sandbox — multi-agent orchestration platform."""
    pass


@main.command()
@click.option("--host", default="0.0.0.0", help="API host")
@click.option("--port", default=8000, type=int, help="API port")
@click.option("--max-agents", default=10, type=int, help="Max concurrent agents")
@click.option("--log-level", default="INFO", help="Log level")
def start(host: str, port: int, max_agents: int, log_level: str):
    """Start the orchestrator and monitoring server."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from agent_sandbox.config import SandboxConfig
    from agent_sandbox.core.orchestrator import Orchestrator
    from agent_sandbox.monitoring.api import create_api
    from agent_sandbox.monitoring.websocket import WebSocketManager

    config = SandboxConfig(
        max_agents=max_agents,
        api_host=host,
        api_port=port,
        log_level=log_level,
    )

    orchestrator = Orchestrator(config)
    app = create_api(orchestrator)
    ws_manager = WebSocketManager(orchestrator)
    ws_manager.attach(app)

    @app.on_event("startup")
    async def on_startup():
        await orchestrator.start()
        await ws_manager.start()
        console.print(f"\n  [bold cyan]Agent Sandbox[/bold cyan] running on "
                       f"[bold]http://{host}:{port}[/bold]")
        console.print(f"  Dashboard: [link]http://{host}:{port}/dashboard[/link]")
        console.print(f"  API docs:  [link]http://{host}:{port}/docs[/link]\n")

    @app.on_event("shutdown")
    async def on_shutdown():
        await ws_manager.stop()
        await orchestrator.stop()

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level=log_level.lower())


# --------------- status command ---------------

@main.command()
def status():
    """Show system status."""
    try:
        resp = httpx.get(f"{_api_url()}/api/status")
        data = resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/red] Cannot connect to Agent Sandbox. Is it running?")
        sys.exit(1)

    table = Table(title="System Status", border_style="dim")
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    table.add_row("Running", str(data.get("running")))
    table.add_row("Uptime", f"{data.get('uptime_seconds', 0):.0f}s")

    agents = data.get("agents", {})
    table.add_row("Agents (active/max)", f"{agents.get('active', 0)}/{agents.get('max', 0)}")

    tasks = data.get("tasks", {})
    table.add_row("Tasks total", str(tasks.get("total", 0)))
    table.add_row("Tasks pending", str(tasks.get("pending", 0)))

    bus = data.get("message_bus", {})
    table.add_row("Messages", str(bus.get("total_messages", 0)))

    console.print(table)


# --------------- agent commands ---------------

@main.group()
def agent():
    """Manage worker agents."""
    pass


@agent.command("spawn")
@click.argument("name")
@click.option("--count", default=1, type=int, help="Number of agents to spawn")
@click.option("--tags", default="", help="Comma-separated tags")
def agent_spawn(name: str, count: int, tags: str):
    """Spawn a new worker agent."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    try:
        resp = httpx.post(f"{_api_url()}/api/agents", json={
            "name": name, "count": count, "tags": tag_list,
        })
        data = resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/red] Cannot connect. Is the sandbox running?")
        sys.exit(1)

    if isinstance(data, list):
        for a in data:
            console.print(f"  Spawned [cyan]{a['name']}[/cyan] ({a['agent_id']})")
    else:
        console.print(f"  Spawned [cyan]{data['name']}[/cyan] ({data['agent_id']})")


@agent.command("list")
def agent_list():
    """List all agents."""
    try:
        resp = httpx.get(f"{_api_url()}/api/agents")
        agents = resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/red] Cannot connect. Is the sandbox running?")
        sys.exit(1)

    if not agents:
        console.print("  No agents running.")
        return

    table = Table(border_style="dim")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("State")
    table.add_column("Tasks Done")
    table.add_column("Failed")
    table.add_column("Tags")

    state_colors = {
        "idle": "green", "busy": "blue", "paused": "yellow",
        "stopped": "dim", "failed": "red",
    }

    for a in agents:
        state = a.get("state", "unknown")
        color = state_colors.get(state, "white")
        table.add_row(
            a["agent_id"],
            a["name"],
            f"[{color}]{state}[/{color}]",
            str(a.get("tasks_completed", 0)),
            str(a.get("tasks_failed", 0)),
            ", ".join(a.get("tags", [])) or "-",
        )

    console.print(table)


@agent.command("kill")
@click.argument("agent_id")
def agent_kill(agent_id: str):
    """Kill a specific agent."""
    try:
        resp = httpx.delete(f"{_api_url()}/api/agents/{agent_id}")
        if resp.status_code == 404:
            console.print(f"[red]Agent {agent_id} not found[/red]")
        else:
            console.print(f"  Killed [red]{agent_id}[/red]")
    except httpx.ConnectError:
        console.print("[red]Error:[/red] Cannot connect.")
        sys.exit(1)


# --------------- task commands ---------------

@main.group()
def task():
    """Manage tasks."""
    pass


@task.command("submit")
@click.argument("command")
@click.option("--type", "task_type", default="shell", help="Task type")
@click.option("--priority", default=1, type=int, help="Priority (0-3)")
def task_submit(command: str, task_type: str, priority: int):
    """Submit a task."""
    payload = {"command": command} if task_type == "shell" else {"code": command}
    try:
        resp = httpx.post(f"{_api_url()}/api/tasks", json={
            "type": task_type, "payload": payload, "priority": priority,
        })
        data = resp.json()
        console.print(f"  Submitted task [cyan]{data['task_id']}[/cyan] (type={task_type})")
    except httpx.ConnectError:
        console.print("[red]Error:[/red] Cannot connect.")
        sys.exit(1)


@task.command("list")
def task_list():
    """List all tasks."""
    try:
        resp = httpx.get(f"{_api_url()}/api/tasks")
        tasks = resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/red] Cannot connect.")
        sys.exit(1)

    if not tasks:
        console.print("  No tasks.")
        return

    table = Table(border_style="dim")
    table.add_column("ID", style="dim")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Priority")
    table.add_column("Assigned To", style="dim")

    status_colors = {
        "pending": "dim", "queued": "yellow", "running": "blue",
        "completed": "green", "failed": "red", "cancelled": "dim",
    }

    for t in tasks:
        status = t.get("status", "unknown")
        color = status_colors.get(status, "white")
        table.add_row(
            t["task_id"],
            t["type"],
            f"[{color}]{status}[/{color}]",
            str(t.get("priority", 1)),
            t.get("assigned_to") or "-",
        )

    console.print(table)


@task.command("interpret")
@click.argument("instruction")
@click.option("--priority", default=1, type=int, help="Priority (0-3)")
def task_interpret(instruction: str, priority: int):
    """Submit a natural language instruction (uses LLM to decompose into tasks)."""
    console.print(f"  Interpreting: [dim]{instruction[:80]}{'...' if len(instruction) > 80 else ''}[/dim]")

    try:
        resp = httpx.post(
            f"{_api_url()}/api/tasks/interpret",
            json={"instruction": instruction, "priority": priority},
            timeout=60.0,  # LLM calls can take a while
        )
        if resp.status_code == 503:
            console.print("[red]Error:[/red] LLM not configured. Set ANTHROPIC_API_KEY.")
            sys.exit(1)
        if resp.status_code != 200:
            console.print(f"[red]Error:[/red] {resp.json().get('detail', 'Unknown error')}")
            sys.exit(1)

        data = resp.json()
    except httpx.ConnectError:
        console.print("[red]Error:[/red] Cannot connect. Is the sandbox running?")
        sys.exit(1)
    except httpx.ReadTimeout:
        console.print("[red]Error:[/red] Request timed out (LLM may be slow).")
        sys.exit(1)

    console.print(f"\n  [green]Created {data['tasks_created']} task(s):[/green]")

    for t in data.get("tasks", []):
        desc = t.get("metadata", {}).get("description", t["type"])
        console.print(f"    [cyan]{t['task_id']}[/cyan] ({t['type']}) — {desc}")

    console.print("\n  Use [bold]sandbox task list[/bold] to monitor progress.")


if __name__ == "__main__":
    main()
