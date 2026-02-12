# AI Agent Sandbox

A multi-agent sandbox platform where a **master orchestrator** controls and monitors
multiple **worker agents**, each running in isolated sandbox environments.

Inspired by [e2b.dev](https://e2b.dev) — secure, isolated execution for AI agents.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 Master Orchestrator              │
│  ┌───────────┐ ┌──────────┐ ┌────────────────┐  │
│  │ Task Queue│ │ Scheduler│ │ Agent Registry │  │
│  └─────┬─────┘ └────┬─────┘ └───────┬────────┘  │
│        └─────────────┼───────────────┘           │
│                      │                           │
│              ┌───────▼────────┐                  │
│              │  Message Bus   │                  │
│              └───────┬────────┘                  │
└──────────────────────┼──────────────────────────-┘
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │ Sandbox  │  │ Sandbox  │  │ Sandbox  │
   │ Agent 1  │  │ Agent 2  │  │ Agent N  │
   │ (isolated│  │ (isolated│  │ (isolated│
   │  env)    │  │  env)    │  │  env)    │
   └──────────┘  └──────────┘  └──────────┘
```

## Features

- **Master Orchestrator** — dispatches tasks, monitors agents, handles failures
- **Isolated Sandboxes** — each agent runs in its own subprocess/container
- **Real-time Monitoring** — WebSocket-based dashboard (like e2b.dev's monitoring tab)
- **Task Queue** — priority-based task distribution across agents
- **Agent Lifecycle** — spawn, pause, resume, kill agents on demand
- **REST + WebSocket API** — full control over the system programmatically
- **CLI** — manage everything from the command line

## Quick Start

```bash
# Install
pip install -e .

# Start the platform
sandbox start

# Or run directly
python -m agent_sandbox.cli start

# Open monitoring dashboard
# http://localhost:8000/dashboard
```

## CLI Commands

```bash
sandbox start                    # Start the orchestrator + monitoring server
sandbox agent spawn <name>       # Spawn a new worker agent
sandbox agent list               # List all running agents
sandbox agent kill <agent-id>    # Kill a specific agent
sandbox task submit <task.json>  # Submit a task to the queue
sandbox task list                # List all tasks
sandbox status                   # Show system status
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Key settings:
- `MAX_AGENTS` — maximum concurrent agents (default: 10)
- `SANDBOX_TIMEOUT` — per-task timeout in seconds (default: 300)
- `API_PORT` — monitoring API port (default: 8000)
- `SANDBOX_MODE` — `subprocess` or `docker` (default: subprocess)

## Project Structure

```
agent_sandbox/
├── core/
│   ├── orchestrator.py      # Master agent / orchestrator
│   ├── sandbox.py           # Sandbox runtime (isolation layer)
│   ├── agent.py             # Worker agent base class
│   └── registry.py          # Agent registry & lifecycle
├── comms/
│   ├── message_bus.py       # Inter-agent message passing
│   └── protocol.py          # Message protocol definitions
├── tasks/
│   ├── queue.py             # Priority task queue
│   ├── scheduler.py         # Task scheduler / dispatcher
│   └── models.py            # Task data models
├── monitoring/
│   ├── api.py               # FastAPI monitoring endpoints
│   ├── websocket.py         # Real-time WebSocket feeds
│   └── templates/
│       └── dashboard.html   # Monitoring dashboard UI
├── cli.py                   # CLI entry point
└── config.py                # Configuration management
```
