"""Task management tools for the agent.

Provides MCP tools for managing task definitions:
- list_tasks: List all task definitions
- create_task: Create a new task definition
- update_task: Update an existing task definition
- delete_task: Delete a task definition
"""

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from cronsim import CronSim
from cronsim.cronsim import CronSimError
from loguru import logger
from pydantic import BaseModel, ValidationError

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
    prompt: str | None = None
    notify: str | None = None
    enabled: bool | None = None


class DeleteTaskArgs(BaseModel):
    task_id: str


def create_task_tools_server(
    repository: TaskRepository,
    timezone: ZoneInfo,
) -> McpSdkServerConfig:
    """Create an MCP server exposing task management tools.

    Args:
        repository: The TaskRepository to use for persistence.
        timezone: The configured timezone for interpreting bare datetimes.

    Returns:
        McpSdkServerConfig for registration with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "list_tasks",
        "List task definitions. Shows active tasks by default;"
        " set archived=true to see disabled tasks.",
        ListTasksArgs.model_json_schema(),
    )
    async def list_tasks(args: dict) -> dict:
        """List task definitions, filtered by active/archived status."""
        try:
            parsed = ListTasksArgs.model_validate(args)

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
                schedule_desc = _format_schedule(d.schedule, timezone)
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
        (
            "Create a new scheduled task. Schedule format: "
            "cron expression (e.g. '0 9 * * *' for daily at 9 AM), "
            "bare ISO datetime interpreted in the configured timezone "
            "(e.g. '2026-04-01T15:00:00' = 3 PM local), "
            "ISO datetime with 'Z' suffix for UTC "
            "(e.g. '2026-04-01T15:00:00Z'), "
            "or ISO datetime with explicit offset "
            "(e.g. '2026-04-01T15:00:00+05:30')."
        ),
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
        schedule_config = _parse_schedule(parsed.schedule, timezone)
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

            schedule_desc = _format_schedule(created.schedule, timezone)
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
        (
            "Update an existing task definition. The schedule field accepts the same formats "
            "as create_task: cron expressions, bare ISO datetimes (interpreted in configured "
            "timezone), ISO with 'Z' (UTC), or ISO with explicit offset."
        ),
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
            schedule_config = _parse_schedule(parsed.schedule, timezone)
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

        except Exception as exc:
            _log.exception("Failed to update task: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Error updating task: {exc}"}],
            }

    @tool(
        "delete_task",
        "Delete a task definition.",
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

        except Exception as exc:
            _log.exception("Failed to delete task: {err}", err=str(exc))
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Error deleting task: {exc}"}],
            }

    return create_sdk_mcp_server(
        name="task-tools",
        tools=[list_tasks, create_task, update_task, delete_task],
    )


def _parse_schedule(schedule: str, tz: ZoneInfo) -> ScheduleConfig | None:
    """Parse a schedule string into a ScheduleConfig.

    Bare ISO datetimes (no tz info) are stamped with the configured timezone.
    Datetimes with explicit offsets (including Z) are preserved as-is.

    Returns None if the schedule is invalid.
    """
    # Try ISO datetime first (one-shot)
    try:
        at = datetime.fromisoformat(schedule)
        if at.tzinfo is None:
            at = at.replace(tzinfo=tz)
        return ScheduleConfig(type="once", at=at)
    except ValueError:
        pass

    # Try cron expression
    try:
        CronSim(schedule, datetime.now(UTC))
        return ScheduleConfig(type="cron", expression=schedule)
    except CronSimError:
        return None


def _format_schedule(schedule: ScheduleConfig, tz: ZoneInfo) -> str:
    """Format a ScheduleConfig for display in the configured timezone."""
    if schedule.type == "cron":
        return f"cron: {schedule.expression}"
    else:
        if schedule.at:
            local_dt = schedule.at.astimezone(tz)
            return f"once: {local_dt.strftime('%Y-%m-%d %H:%M %Z')}"
        return "once: (invalid datetime)"
