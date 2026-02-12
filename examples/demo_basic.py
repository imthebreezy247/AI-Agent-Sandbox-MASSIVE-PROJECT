"""Basic demo — spawn agents, submit tasks, and watch them execute.

Run with:
    python examples/demo_basic.py
"""

import asyncio
import logging

from agent_sandbox.config import SandboxConfig
from agent_sandbox.core.orchestrator import Orchestrator
from agent_sandbox.tasks.models import TaskPriority

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def main():
    # 1. Create and start the orchestrator
    config = SandboxConfig(max_agents=5, sandbox_timeout=60)
    orch = Orchestrator(config)
    await orch.start()

    print("\n=== Agent Sandbox Demo ===\n")

    # 2. Spawn a pool of 3 worker agents
    agents = await orch.spawn_pool("worker", count=3, tags=["general"])
    print(f"Spawned {len(agents)} agents:")
    for a in agents:
        print(f"  - {a.name} ({a.id})")

    # 3. Submit several tasks
    tasks = [
        orch.submit_shell("echo 'Hello from Agent Sandbox!'"),
        orch.submit_shell("python3 -c \"import sys; print(f'Python {sys.version}')\""),
        orch.submit_shell("uname -a"),
        orch.submit_python("print(sum(range(100)))"),
        orch.submit_python(
            "import json\ndata = {'agents': 3, 'status': 'running'}\nprint(json.dumps(data, indent=2))"
        ),
    ]

    print(f"\nSubmitted {len(tasks)} tasks. Waiting for completion...\n")

    # 4. Wait for all tasks to complete
    await asyncio.sleep(5)

    # 5. Print results
    print("=== Results ===\n")
    for task in tasks:
        t = orch.get_task(task.task_id)
        if t and t.result:
            status = t.result.get("status", "unknown")
            stdout = t.result.get("stdout", "").strip()
            symbol = "+" if status == "success" else "x"
            print(f"  [{symbol}] Task {t.task_id}:")
            print(f"      Type: {t.type}")
            print(f"      Status: {status}")
            if stdout:
                for line in stdout.split("\n"):
                    print(f"      > {line}")
            if t.result.get("stderr"):
                print(f"      stderr: {t.result['stderr'].strip()}")
            print()

    # 6. Show system status
    status = orch.status()
    print("=== System Status ===")
    print(f"  Active agents: {status['agents']['active']}/{status['agents']['max']}")
    print(f"  Total tasks:   {status['tasks']['total']}")
    print(f"  Messages:      {status['message_bus']['total_messages']}")

    # 7. Cleanup
    await orch.stop()
    print("\nOrchestrator stopped. Demo complete.")


if __name__ == "__main__":
    asyncio.run(main())
