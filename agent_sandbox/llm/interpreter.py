"""LLM-based task interpreter using Claude."""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from agent_sandbox.config import get_config
from agent_sandbox.llm.prompts import TASK_DECOMPOSITION_SYSTEM, TASK_DECOMPOSITION_USER
from agent_sandbox.tasks.models import Task, TaskPriority

logger = logging.getLogger(__name__)


class LLMInterpreterError(Exception):
    """Raised when LLM interpretation fails."""


class LLMInterpreter:
    """Interprets natural language instructions into executable tasks using Claude."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        config = get_config()
        self.api_key = api_key or config.llm_api_key
        self.model = model or config.llm_model

        if not self.api_key:
            raise LLMInterpreterError(
                "No API key provided. Set ANTHROPIC_API_KEY environment variable."
            )

        self.client = anthropic.Anthropic(api_key=self.api_key)

    async def interpret(
        self,
        instruction: str,
        working_dir: str = ".",
        files: list[str] | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> list[Task]:
        """Convert a natural language instruction into a list of executable tasks.

        Args:
            instruction: Natural language description of what to do
            working_dir: Current working directory context
            files: List of files in the workspace (for context)
            priority: Priority level for created tasks

        Returns:
            List of Task objects ready for submission to the orchestrator
        """
        files_str = ", ".join(files[:20]) if files else "(empty)"
        if files and len(files) > 20:
            files_str += f" ... and {len(files) - 20} more"

        user_message = TASK_DECOMPOSITION_USER.format(
            instruction=instruction,
            working_dir=working_dir,
            files=files_str,
        )

        logger.info(f"Interpreting instruction: {instruction[:100]}...")

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=TASK_DECOMPOSITION_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIError as e:
            raise LLMInterpreterError(f"Claude API error: {e}") from e

        # Extract text content
        content = response.content[0].text if response.content else ""

        # Check if response was truncated
        if response.stop_reason == "max_tokens":
            logger.warning("LLM response was truncated due to max_tokens limit")

        # Parse JSON response
        try:
            # Handle potential markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            result = json.loads(content.strip())
        except json.JSONDecodeError as e:
            # Try to recover by finding valid JSON
            logger.warning(f"Initial JSON parse failed, attempting recovery: {e}")
            try:
                # Try to find a complete JSON object
                import re
                json_match = re.search(r'\{[\s\S]*"tasks"\s*:\s*\[[\s\S]*?\]\s*\}', content)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    logger.error(f"Failed to parse LLM response: {content[:500]}...")
                    raise LLMInterpreterError(f"Invalid JSON response from LLM: {e}") from e
            except (json.JSONDecodeError, AttributeError) as e2:
                logger.error(f"Failed to recover JSON: {content[:500]}...")
                raise LLMInterpreterError(f"Invalid JSON response from LLM: {e}") from e

        # Validate response structure
        if "tasks" not in result:
            raise LLMInterpreterError("LLM response missing 'tasks' field")

        reasoning = result.get("reasoning", "")
        logger.info(f"LLM reasoning: {reasoning}")

        # Convert to Task objects
        tasks = self._create_task_chain(result["tasks"], priority, instruction)

        logger.info(f"Created {len(tasks)} tasks from instruction")
        return tasks

    def _create_task_chain(
        self,
        task_dicts: list[dict[str, Any]],
        priority: TaskPriority,
        original_instruction: str,
    ) -> list[Task]:
        """Create a chain of dependent tasks from LLM output."""
        tasks: list[Task] = []
        previous_task_id: str | None = None

        for i, task_dict in enumerate(task_dicts):
            task_type = task_dict.get("type", "shell")
            payload = task_dict.get("payload", {})
            description = task_dict.get("description", f"Step {i + 1}")

            # Build dependency list
            depends_on = []
            if previous_task_id:
                depends_on.append(previous_task_id)

            # Create task with metadata
            task = Task(
                type=task_type,
                payload=payload,
                priority=priority,
                depends_on=depends_on,
                tags=["llm-generated"],
                metadata={
                    "description": description,
                    "step": i + 1,
                    "total_steps": len(task_dicts),
                    "original_instruction": original_instruction[:500],
                    "needs_confirmation": task_dict.get("needs_confirmation", False),
                },
            )

            tasks.append(task)
            previous_task_id = task.task_id

        return tasks

    def interpret_sync(
        self,
        instruction: str,
        working_dir: str = ".",
        files: list[str] | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> list[Task]:
        """Synchronous version of interpret() for non-async contexts."""
        import asyncio

        return asyncio.get_event_loop().run_until_complete(
            self.interpret(instruction, working_dir, files, priority)
        )
