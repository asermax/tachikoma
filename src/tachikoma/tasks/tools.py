"""Task management tools for the agent.

Provides MCP tools for managing task definitions:
- list_tasks: List all task definitions
- get_task: Get full details for a specific task definition
- create_task: Create a new task definition
- update_task: Update an existing task definition
- delete_task: Delete a task definition
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

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
    enabled: bool = True


class UpdateTaskArgs(BaseModel):
    task_id: str
    name: str | None = None
    schedule: str | None = None
    task_type: Literal["session", "background"] | None = None
    prompt: str | None = None
    enabled: bool | None = None


class DeleteTaskArgs(BaseModel):
    task_id: str


class GetTaskArgs(BaseModel):
    task_id: str


class RespondToTaskArgs(BaseModel):
    task_instance_id: str
    response: str


async def handle_respond_to_task(
    task_instance_id: str,
    response: str,
    repository: TaskRepository,
) -> dict:
    """Handle the respond_to_task tool invocation.

    Validates the response and target task, then saves the response for
    the runner to pick up on the next tick.
    """
    trimmed = response.strip()
    if not trimmed:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": "Response cannot be empty."}],
        }

    try:
        instance = await repository.get_instance(task_instance_id)

    except TaskRepositoryError as exc:
        cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
        return {"is_error": True, "content": [{"type": "text", "text": f"{exc}{cause}"}]}

    if instance is None:
        return {
            "is_error": True,
            "content": [
                {"type": "text", "text": f"Task instance '{task_instance_id}' not found."}
            ],
        }

    if instance.status != "waiting":
        return {
            "is_error": True,
            "content": [{"type": "text", "text": "Task is not waiting for input."}],
        }

    if instance.user_response is not None:
        return {
            "is_error": True,
            "content": [
                {"type": "text", "text": "A response is already pending for this task."}
            ],
        }

    try:
        await repository.update_instance(task_instance_id, user_response=trimmed)
    except TaskRepositoryError as exc:
        cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
        return {"is_error": True, "content": [{"type": "text", "text": f"{exc}{cause}"}]}

    return {
        "content": [{"type": "text", "text": "Response sent."}],
    }


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
                schedule_desc = _format_schedule(d.schedule, timezone)
                last_fired = (
                    f" (last: {d.last_fired_at.astimezone(timezone).strftime('%Y-%m-%d %H:%M %Z')})"
                    if d.last_fired_at
                    else ""
                )
                lines.append(f"- [{d.id}] **{d.name}** [{d.task_type}] {status}")
                lines.append(f"  Schedule: {schedule_desc}{last_fired}")
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
        "get_task",
        "Get full details for a specific task definition.\n"
        "\n"
        "Parameters:\n"
        "- task_id (str, required): ID of the task to inspect (get IDs from list_tasks)\n"
        "\n"
        "Returns the task's complete details including the full prompt.",
        GetTaskArgs.model_json_schema(),
    )
    async def get_task(args: dict) -> dict:
        """Get full details for a single task definition."""
        try:
            parsed = GetTaskArgs.model_validate(args)
        except ValidationError as exc:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
            }

        try:
            d = await repository.get_definition(parsed.task_id)

            if d is None:
                return {
                    "is_error": True,
                    "content": [{"type": "text", "text": f"Task '{parsed.task_id}' not found."}],
                }

            status = "✓ enabled" if d.enabled else "✗ disabled"
            schedule_desc = _format_schedule(d.schedule, timezone)
            last_fired = (
                f"{d.last_fired_at.astimezone(timezone).strftime('%Y-%m-%d %H:%M %Z')}"
                if d.last_fired_at
                else "never"
            )

            created_str = (
                d.created_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M %Z")
                if d.created_at
                else "unknown"
            )

            lines = [
                f"# {d.name}\n",
                f"- ID: {d.id}",
                f"- Type: {d.task_type}",
                f"- Status: {status}",
                f"- Schedule: {schedule_desc}",
                f"- Last run: {last_fired}",
                f"- Created: {created_str}",
                f"\n## Prompt\n\n{d.prompt}",
            ]

            return {
                "content": [{"type": "text", "text": "\n".join(lines)}],
            }

        except TaskRepositoryError as exc:
            cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
            return {"is_error": True, "content": [{"type": "text", "text": f"{exc}{cause}"}]}
        except Exception as exc:
            _log.exception("Unexpected error getting task: {err}", err=str(exc))
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
        "- schedule (str, required): Cron expression (e.g., '0 9 * * *' for daily at 9 AM),"
        " bare ISO datetime interpreted in the configured timezone"
        " (e.g., '2026-04-01T15:00:00' = 3 PM local),"
        " ISO datetime with 'Z' suffix for UTC (e.g., '2026-04-01T15:00:00Z'),"
        " or ISO datetime with explicit offset (e.g., '2026-04-01T15:00:00+05:30')\n"
        "- type (str, required): 'session' (delivered during idle) or 'background'"
        " (isolated execution)\n"
        "- prompt (str, required): Instruction the agent follows when the task fires\n"
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
        "- schedule (str, optional): New cron expression or ISO datetime"
        " (same formats as create_task — bare datetimes use configured timezone)\n"
        "- task_type (str, optional): Change type — 'session' or 'background'\n"
        "- prompt (str, optional): New agent instruction\n"
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
            # Validate one-shot schedules are in the future (consistent with create_task)
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

            updates["schedule"] = schedule_config

            # Reset last_fired_at — the old fire time is meaningless for a new schedule.
            # For one-shot tasks, the instance generator requires last_fired_at=None to fire.
            # For cron tasks, the anchor logic handles None by falling back to start-of-hour.
            updates["last_fired_at"] = None

        if parsed.prompt is not None:
            updates["prompt"] = parsed.prompt
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

    @tool(
        "respond_to_task",
        "Send the user's response back to a background task that is waiting for input.\n"
        "\n"
        "Parameters:\n"
        "- task_instance_id (str, required): The ID of the waiting task instance,\n"
        "  as provided in the notification's prompt text.\n"
        "- response (str, required): The user's reply to relay to the task.\n"
        "\n"
        "Use this only when a notification explicitly indicates a background task is\n"
        "waiting for user input. The tool enforces that the target task is in 'waiting'\n"
        "status — calls against any other status return a clear error.",
        RespondToTaskArgs.model_json_schema(),
    )
    async def respond_to_task(args: dict) -> dict:
        try:
            parsed = RespondToTaskArgs.model_validate(args)
        except ValidationError as exc:
            return {
                "is_error": True,
                "content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
            }
        return await handle_respond_to_task(parsed.task_instance_id, parsed.response, repository)

    return create_sdk_mcp_server(
        name="task-tools",
        tools=[list_tasks, get_task, create_task, update_task, delete_task, respond_to_task],
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
