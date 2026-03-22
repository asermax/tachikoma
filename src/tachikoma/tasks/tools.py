"""Task management tools for the agent.

Provides MCP tools for managing task definitions:
- list_tasks: List all task definitions
- create_task: Create a new task definition
- update_task: Update an existing task definition
- delete_task: Delete a task definition
"""

from datetime import UTC, datetime
from uuid import uuid4

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from cronsim import CronSim
from cronsim.cronsim import CronSimError
from loguru import logger

from tachikoma.tasks.model import ScheduleConfig, TaskDefinition
from tachikoma.tasks.repository import TaskRepository

_log = logger.bind(component="task_tools")


def create_task_tools_server(repository: TaskRepository) -> McpSdkServerConfig:
    """Create an MCP server exposing task management tools.

    Args:
        repository: The TaskRepository to use for persistence.

    Returns:
        McpSdkServerConfig for registration with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "list_tasks",
        "List all task definitions with their current status.",
        {},  # No input parameters
    )
    async def list_tasks(args: dict) -> dict:
        """List all task definitions."""
        try:
            definitions = await repository.list_definitions()

            if not definitions:
                return {
                    "content": [{"type": "text", "text": "No task definitions found."}],
                }

            lines = ["# Task Definitions\n"]
            for d in definitions:
                status = "✓ enabled" if d.enabled else "✗ disabled"
                schedule_desc = _format_schedule(d.schedule)
                last_fired = (
                    f" (last: {d.last_fired_at.strftime('%Y-%m-%d %H:%M')})"
                    if d.last_fired_at
                    else ""
                )
                lines.append(f"- **{d.name}** [{d.task_type}] {status}")
                lines.append(f"  Schedule: {schedule_desc}{last_fired}")
                lines.append(f"  Prompt: {d.prompt[:100]}{'...' if len(d.prompt) > 100 else ''}")
                if d.notify:
                    lines.append(f"  Notify: {d.notify}")
                lines.append("")

            return {
                "content": [{"type": "text", "text": "\n".join(lines)}],
            }

        except Exception as exc:
            _log.exception("Failed to list tasks: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Error listing tasks: {exc}"}],
            }

    @tool(
        "create_task",
        "Create a new scheduled task.",
        {
            "name": str,
            "schedule": str,
            "type": str,
            "prompt": str,
            "notify": str | None,
            "enabled": bool,
        },
    )
    async def create_task(args: dict) -> dict:
        """Create a new task definition."""
        name = args.get("name", "")
        schedule = args.get("schedule", "")
        task_type = args.get("type", "")
        prompt = args.get("prompt", "")
        notify = args.get("notify")
        enabled = args.get("enabled", True)

        # Validate required fields
        errors = []
        if not name:
            errors.append("name is required")
        if not schedule:
            errors.append("schedule is required")
        if not task_type:
            errors.append("type is required")
        if not prompt:
            errors.append("prompt is required")

        if errors:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Missing required fields: {', '.join(errors)}"}],
            }

        # Validate type
        if task_type not in ("session", "background"):
            return {
                "is_error": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"Invalid type '{task_type}'. Must be 'session' or 'background'.",
                    }
                ],
            }

        # Parse and validate schedule
        schedule_config = _parse_schedule(schedule)
        if schedule_config is None:
            return {
                "is_error": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"Invalid schedule '{schedule}'. Use a cron expression (e.g., '0 9 * * *') or an ISO datetime (e.g., '2026-03-22T10:00:00Z').",
                    }
                ],
            }

        # For one-shot schedules, validate the datetime is in the future
        if schedule_config.type == "once" and schedule_config.at is not None:
            if schedule_config.at <= datetime.now(UTC):
                return {
                    "is_error": True,
                    "content": [
                        {
                            "type": "text",
                            "text": f"One-shot schedule datetime must be in the future. Got: {schedule_config.at.isoformat()}",
                        }
                    ],
                }

        # Create the definition
        definition = TaskDefinition(
            id=str(uuid4()),
            name=name,
            schedule=schedule_config,
            task_type=task_type,  # type: ignore[arg-type]
            prompt=prompt,
            enabled=enabled,
            notify=notify,
            last_fired_at=None,
            created_at=datetime.now(UTC),
        )

        try:
            created = await repository.create_definition(definition)

            schedule_desc = _format_schedule(created.schedule)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Task '{created.name}' created successfully.\n"
                        f"- ID: {created.id}\n"
                        f"- Type: {created.task_type}\n"
                        f"- Schedule: {schedule_desc}\n"
                        f"- Enabled: {created.enabled}",
                    }
                ],
            }

        except Exception as exc:
            _log.exception("Failed to create task: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Error creating task: {exc}"}],
            }

    @tool(
        "update_task",
        "Update an existing task definition.",
        {
            "task_id": str,
            "name": str | None,
            "schedule": str | None,
            "prompt": str | None,
            "notify": str | None,
            "enabled": bool | None,
        },
    )
    async def update_task(args: dict) -> dict:
        """Update an existing task definition."""
        task_id = args.get("task_id", "")
        name = args.get("name")
        schedule = args.get("schedule")
        prompt = args.get("prompt")
        notify = args.get("notify")
        enabled = args.get("enabled")

        if not task_id:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": "task_id is required."}],
            }

        # Check task exists
        existing = await repository.get_definition(task_id)
        if existing is None:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Task '{task_id}' not found."}],
            }

        # Build updates
        updates = {}
        if name is not None:
            updates["name"] = name
        if schedule is not None:
            schedule_config = _parse_schedule(schedule)
            if schedule_config is None:
                return {
                    "is_error": True,
                    "content": [
                        {
                            "type": "text",
                            "text": f"Invalid schedule '{schedule}'. Use a cron expression or ISO datetime.",
                        }
                    ],
                }
            updates["schedule"] = schedule_config
        if prompt is not None:
            updates["prompt"] = prompt
        if notify is not None:
            updates["notify"] = notify
        if enabled is not None:
            updates["enabled"] = enabled

        if not updates:
            return {
                "content": [{"type": "text", "text": "No updates provided."}],
            }

        try:
            await repository.update_definition(task_id, **updates)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Task '{task_id}' updated successfully.",
                    }
                ],
            }

        except Exception as exc:
            _log.exception("Failed to update task: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Error updating task: {exc}"}],
            }

    @tool(
        "delete_task",
        "Delete a task definition.",
        {"task_id": str},
    )
    async def delete_task(args: dict) -> dict:
        """Delete a task definition."""
        task_id = args.get("task_id", "")

        if not task_id:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": "task_id is required."}],
            }

        try:
            deleted = await repository.delete_definition(task_id)

            if deleted:
                return {
                    "content": [{"type": "text", "text": f"Task '{task_id}' deleted."}],
                }
            else:
                return {
                    "is_error": True,
                    "content": [{"type": "text", "text": f"Task '{task_id}' not found."}],
                }

        except Exception as exc:
            _log.exception("Failed to delete task: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Error deleting task: {exc}"}],
            }

    return create_sdk_mcp_server(
        "task-tools",
        [list_tasks, create_task, update_task, delete_task],
    )


def _parse_schedule(schedule: str) -> ScheduleConfig | None:
    """Parse a schedule string into a ScheduleConfig.

    Returns None if the schedule is invalid.
    """
    # Try ISO datetime first (one-shot)
    try:
        at = datetime.fromisoformat(schedule.replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return ScheduleConfig(type="once", at=at)
    except ValueError:
        pass

    # Try cron expression
    try:
        CronSim(schedule, datetime.now(UTC))
        return ScheduleConfig(type="cron", expression=schedule)
    except CronSimError:
        return None


def _format_schedule(schedule: ScheduleConfig) -> str:
    """Format a ScheduleConfig for display."""
    if schedule.type == "cron":
        return f"cron: {schedule.expression}"
    else:
        if schedule.at:
            return f"once: {schedule.at.strftime('%Y-%m-%d %H:%M %Z')}"
        return "once: (invalid datetime)"
