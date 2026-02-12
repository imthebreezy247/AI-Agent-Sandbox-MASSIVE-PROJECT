"""Agent registry — tracks all active agents and their lifecycle.

The registry is the single source of truth for which agents exist,
their current state, and provides lookup/filtering capabilities.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent_sandbox.core.agent import WorkerAgent, AgentState

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Registry that tracks all worker agents."""

    def __init__(self, max_agents: int = 10):
        self.max_agents = max_agents
        self._agents: dict[str, WorkerAgent] = {}

    @property
    def count(self) -> int:
        return len(self._agents)

    @property
    def active_count(self) -> int:
        return sum(
            1 for a in self._agents.values()
            if a.state not in (AgentState.STOPPED, AgentState.FAILED)
        )

    def register(self, agent: WorkerAgent) -> None:
        """Register a new agent."""
        if self.active_count >= self.max_agents:
            raise RuntimeError(
                f"Max agents ({self.max_agents}) reached. "
                "Stop an existing agent before spawning a new one."
            )
        self._agents[agent.id] = agent
        logger.info("Registered agent %s (%s). Active: %d/%d",
                     agent.id, agent.name, self.active_count, self.max_agents)

    def unregister(self, agent_id: str) -> WorkerAgent | None:
        """Remove an agent from the registry."""
        agent = self._agents.pop(agent_id, None)
        if agent:
            logger.info("Unregistered agent %s", agent_id)
        return agent

    def get(self, agent_id: str) -> WorkerAgent | None:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def get_by_name(self, name: str) -> list[WorkerAgent]:
        """Get agents matching a name."""
        return [a for a in self._agents.values() if a.name == name]

    def get_by_tag(self, tag: str) -> list[WorkerAgent]:
        """Get agents that have a specific tag."""
        return [a for a in self._agents.values() if tag in a.tags]

    def get_idle_agents(self) -> list[WorkerAgent]:
        """Get all idle agents ready for work."""
        return [a for a in self._agents.values() if a.state == AgentState.IDLE]

    def get_busy_agents(self) -> list[WorkerAgent]:
        """Get all agents currently executing tasks."""
        return [a for a in self._agents.values() if a.state == AgentState.BUSY]

    def get_all(self) -> list[WorkerAgent]:
        """Get all agents."""
        return list(self._agents.values())

    def find_stale_agents(self, stale_threshold: float = 120.0) -> list[WorkerAgent]:
        """Find agents that haven't sent a heartbeat recently."""
        now = time.time()
        return [
            a for a in self._agents.values()
            if a.state == AgentState.BUSY
            and (now - a.last_heartbeat) > stale_threshold
        ]

    def summary(self) -> dict[str, Any]:
        """Get a summary of registry state."""
        states: dict[str, int] = {}
        for agent in self._agents.values():
            states[agent.state.value] = states.get(agent.state.value, 0) + 1

        return {
            "total": len(self._agents),
            "max": self.max_agents,
            "active": self.active_count,
            "by_state": states,
        }

    def to_list(self) -> list[dict[str, Any]]:
        """Serialize all agents for API responses."""
        return [a.to_dict() for a in self._agents.values()]
