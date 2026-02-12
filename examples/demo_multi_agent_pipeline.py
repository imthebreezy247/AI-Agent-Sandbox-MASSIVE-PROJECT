"""Multi-agent pipeline demo — chain tasks across specialized agents.

This demonstrates:
- Spawning specialized agent pools with different tags
- Submitting tasks routed to specific agent types
- Task dependencies (one task waits for another)
- Batch task submission

Run with:
    python examples/demo_multi_agent_pipeline.py
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
    config = SandboxConfig(max_agents=10, sandbox_timeout=30)
    orch = Orchestrator(config)
    await orch.start()

    print("\n=== Multi-Agent Pipeline Demo ===\n")

    # Spawn specialized agent pools
    data_agents = await orch.spawn_pool("data-processor", count=2, tags=["data"])
    compute_agents = await orch.spawn_pool("compute-worker", count=3, tags=["compute"])
    io_agents = await orch.spawn_pool("io-handler", count=1, tags=["io"])

    print(f"Agent pools:")
    print(f"  data-processor: {len(data_agents)} agents")
    print(f"  compute-worker: {len(compute_agents)} agents")
    print(f"  io-handler:     {len(io_agents)} agents")

    # Stage 1: Data generation (routed to data agents)
    print("\n--- Stage 1: Data Generation ---")
    gen_task = orch.submit_python(
        "import json\ndata = [i**2 for i in range(20)]\nprint(json.dumps(data))",
        tags=["data"],
        priority=TaskPriority.HIGH,
    )
    print(f"  Submitted data generation task: {gen_task.task_id}")

    # Stage 2: Compute tasks (routed to compute agents, batch)
    print("\n--- Stage 2: Compute Tasks ---")
    compute_tasks = await orch.submit_batch([
        {"type": "python", "payload": {"code": f"print(sum(range({n})))"}, "tags": ["compute"]}
        for n in [100, 1000, 10000, 50000, 100000]
    ], priority=TaskPriority.NORMAL)
    print(f"  Submitted {len(compute_tasks)} compute tasks")

    # Stage 3: I/O task (routed to io agents)
    print("\n--- Stage 3: I/O Task ---")
    io_task = orch.submit_shell(
        "ls -la /tmp && echo '---' && df -h",
        tags=["io"],
    )
    print(f"  Submitted I/O task: {io_task.task_id}")

    # Wait for execution
    print("\nWaiting for pipeline to complete...")
    await asyncio.sleep(8)

    # Results
    print("\n=== Pipeline Results ===\n")
    all_tasks = [gen_task] + compute_tasks + [io_task]

    completed = 0
    failed = 0
    for task in all_tasks:
        t = orch.get_task(task.task_id)
        if not t:
            continue
        if t.result and t.result.get("status") == "success":
            completed += 1
            stdout = t.result.get("stdout", "").strip()[:120]
            print(f"  [+] {t.task_id} ({t.type}): {stdout}")
        elif t.result:
            failed += 1
            err = t.result.get("stderr", t.result.get("error", ""))[:120]
            print(f"  [x] {t.task_id} ({t.type}): {err}")
        else:
            print(f"  [?] {t.task_id} ({t.type}): still pending")

    print(f"\nPipeline summary: {completed} completed, {failed} failed, "
          f"{len(all_tasks) - completed - failed} pending")

    # Cleanup
    await orch.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
