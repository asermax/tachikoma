"""Task management tools for the agent.

Provides MCP tools for managing task definitions:
- list_tasks: List all task definitions
- create_task: Create a new task definition
- update_task: Update an existing task definition
- delete_task: Delete a task definition
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from cronsim import CronSim
from cronsim.cronsim import CronSimError
from loguru import logger
from pydantic import BaseModel, ValidationError

from tachikoma.tasks.errors import TaskRepositoryError
from tachikoma.tasks.model import ScheduleConfig, TaskDefinition
from tachikoma.tasks.repository import TaskRepository

_log = logger.bind(component="task_tools")


# ---------------------------------------------------------------------------
# Pydantic models for MCP tool args
# ---------------------------------------------------------------------------


class ListTasksArgs(BaseModel):
    archived: bool = False


class CreateTaskArgs(BaseModel):
    name: str
    schedule: str
    type: str
    prompt: str
    notify: str | None = None
    enabled: bool = True


class UpdateTaskArgs(BaseModel):
    task_id: str
    name: str | None = None
    schedule: str | None = None
    task_type: Literal["session", "background"] | None = None
    prompt: str | None = None
    notify: str | None = None
    enabled: bool | None = None


class DeleteTaskArgs(BaseModel):
    task_id: str


def create_task_tools_server(repository: TaskRepository) -> McpSdkServerConfig:
    """Create an MCP server exposing task management tools.

    Args:
        repository: The TaskRepository to use for persistence.

    Returns:
        McpSdkServerConfig for registration with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "list_tasks",
        "List task definitions.\n"
        "\n"
        "Parameters:\n"
        "- archived (bool, optional, default false): Set true to show disabled"
        " (archived) tasks instead of active ones.\n"
        "\n"
        "Each entry includes the task ID (needed for update_task and delete_task),"
        " name, type, schedule, and status.",
        ListTasksArgs.model_json_schema(),
    )
    async def list_tasks(args: dict) -> dict:
        """List task definitions, filtered by active/archived status."""
        try:
            parsed = ListTasksArgs.model_validate(args)
        except ValidationError as exc:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
            }

        try:
            if parsed.archived:
                definitions = await repository.list_disabled_definitions()
            else:
                definitions = await repository.list_enabled_definitions()

            if not definitions:
                label = "archived" if parsed.archived else "active"
                return {
                    "content": [{"type": "text", "text": f"No {label} tasks found."}],
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
                lines.append(f"- [{d.id}] **{d.name}** [{d.task_type}] {status}")
                lines.append(f"  Schedule: {schedule_desc}{last_fired}")
                lines.append(f"  Prompt: {d.prompt[:100]}{'...' if len(d.prompt) > 100 else ''}")
                if d.notify:
                    lines.append(f"  Notify: {d.notify}")
                lines.append("")

            return {
                "content": [{"type": "text", "text": "\n".join(lines)}],
            }

        except TaskRepositoryError as exc:
            cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
            return {"is_error": True, "content": [{"type": "text", "text": f"{exc}{cause}"}]}
        except Exception as exc:
            _log.exception("Unexpected error listing tasks: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Unexpected error: {exc}"}],
            }

    @tool(
        "create_task",
        "Create a new scheduled task definition.\n"
        "\n"
        "Parameters:\n"
        "- name (str, required): Human-readable task name\n"
        "- schedule (str, required): Cron expression (e.g., '0 9 * * *' for daily at 9 AM)"
        " or ISO datetime for one-shot (e.g., '2026-04-01T14:00:00Z')\n"
        "- type (str, required): 'session' (delivered during idle) or 'background'"
        " (isolated execution)\n"
        "- prompt (str, required): Instruction the agent follows when the task fires\n"
        "- notify (str, optional): Success notification instruction — when set, generates"
        " a user-facing message on completion. Omit for silent success. Failures always"
        " notify regardless of this field.\n"
        "- enabled (bool, optional, default true): Whether the task is active",
        CreateTaskArgs.model_json_schema(),
    )
    async def create_task(args: dict) -> dict:
        """Create a new task definition."""
        try:
            parsed = CreateTaskArgs.model_validate(args)
        except ValidationError as exc:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
            }

        # Validate type
        if parsed.type not in ("session", "background"):
            return {
                "is_error": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"Invalid type '{parsed.type}'. Must be 'session' or 'background'.",
                    }
                ],
            }

        # Parse and validate schedule
        schedule_config = _parse_schedule(parsed.schedule)
        if schedule_config is None:
            return {
                "is_error": True,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Invalid schedule '{parsed.schedule}'. Use a cron expression"
                            " (e.g., '0 9 * * *') or an ISO datetime"
                            " (e.g., '2026-03-22T10:00:00Z')."
                        ),
                    }
                ],
            }

        # For one-shot schedules, validate the datetime is in the future
        if (
            schedule_config.type == "once"
            and schedule_config.at is not None
            and schedule_config.at <= datetime.now(UTC)
        ):
            return {
                "is_error": True,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "One-shot schedule datetime must be in the future."
                            f" Got: {schedule_config.at.isoformat()}"
                        ),
                    }
                ],
            }

        # Create the definition
        definition = TaskDefinition(
            id=str(uuid4()),
            name=parsed.name,
            schedule=schedule_config,
            task_type=parsed.type,  # type: ignore[arg-type]  # validated above
            prompt=parsed.prompt,
            enabled=parsed.enabled,
            notify=parsed.notify,
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

        except TaskRepositoryError as exc:
            cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
            return {"is_error": True, "content": [{"type": "text", "text": f"{exc}{cause}"}]}
        except Exception as exc:
            _log.exception("Unexpected error creating task: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Unexpected error: {exc}"}],
            }

    @tool(
        "update_task",
        "Update an existing task definition.\n"
        "\n"
        "Parameters:\n"
        "- task_id (str, required): ID of the task to update (get IDs from list_tasks)\n"
        "- name (str, optional): New human-readable name\n"
        "- schedule (str, optional): New cron expression or ISO datetime\n"
        "- task_type (str, optional): Change type — 'session' or 'background'\n"
        "- prompt (str, optional): New agent instruction\n"
        "- notify (str, optional): New success notification instruction\n"
        "- enabled (bool, optional): Enable or disable the task\n"
        "\n"
        "Only provided fields are updated; omitted fields remain unchanged.",
        UpdateTaskArgs.model_json_schema(),
    )
    async def update_task(args: dict) -> dict:
        """Update an existing task definition."""
        try:
            parsed = UpdateTaskArgs.model_validate(args)
        except ValidationError as exc:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
            }

        # Check task exists
        existing = await repository.get_definition(parsed.task_id)
        if existing is None:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Task '{parsed.task_id}' not found."}],
            }

        # Build updates
        updates = {}
        if parsed.name is not None:
            updates["name"] = parsed.name
        if parsed.schedule is not None:
            schedule_config = _parse_schedule(parsed.schedule)
            if schedule_config is None:
                return {
                    "is_error": True,
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Invalid schedule '{parsed.schedule}'."
                                " Use a cron expression or ISO datetime."
                            ),
                        }
                    ],
                }
            updates["schedule"] = schedule_config
        if parsed.prompt is not None:
            updates["prompt"] = parsed.prompt
        if parsed.notify is not None:
            updates["notify"] = parsed.notify
        if parsed.enabled is not None:
            updates["enabled"] = parsed.enabled
        if parsed.task_type is not None:
            updates["task_type"] = parsed.task_type

        if not updates:
            return {
                "content": [{"type": "text", "text": "No updates provided."}],
            }

        try:
            await repository.update_definition(parsed.task_id, **updates)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Task '{parsed.task_id}' updated successfully.",
                    }
                ],
            }

        except TaskRepositoryError as exc:
            cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
            return {"is_error": True, "content": [{"type": "text", "text": f"{exc}{cause}"}]}
        except Exception as exc:
            _log.exception("Unexpected error updating task: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Unexpected error: {exc}"}],
            }

    @tool(
        "delete_task",
        "Delete a task definition permanently.\n"
        "\n"
        "Parameters:\n"
        "- task_id (str, required): ID of the task to delete (get IDs from list_tasks)\n"
        "\n"
        "This action is permanent and cannot be undone."
        " To disable without deleting, use update_task with enabled=false.",
        DeleteTaskArgs.model_json_schema(),
    )
    async def delete_task(args: dict) -> dict:
        """Delete a task definition."""
        try:
            parsed = DeleteTaskArgs.model_validate(args)
        except ValidationError as exc:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
            }

        try:
            deleted = await repository.delete_definition(parsed.task_id)

            if deleted:
                return {
                    "content": [{"type": "text", "text": f"Task '{parsed.task_id}' deleted."}],
                }
            else:
                return {
                    "is_error": True,
                    "content": [{"type": "text", "text": f"Task '{parsed.task_id}' not found."}],
                }

        except TaskRepositoryError as exc:
            cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
            return {"is_error": True, "content": [{"type": "text", "text": f"{exc}{cause}"}]}
        except Exception as exc:
            _log.exception("Unexpected error deleting task: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Unexpected error: {exc}"}],
            }

    return create_sdk_mcp_server(
        name="task-tools",
        tools=[list_tasks, create_task, update_task, delete_task],
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
