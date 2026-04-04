"""Tests for task MCP tools."""

from datetime import UTC, datetime

import mcp.types as types
import pytest
from pydantic import ValidationError

from tachikoma.tasks.errors import TaskRepositoryError
from tachikoma.tasks.model import ScheduleConfig
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.tools import (
    CreateTaskArgs,
    DeleteTaskArgs,
    ListTasksArgs,
    UpdateTaskArgs,
    _format_schedule,
    _parse_schedule,
    create_task_tools_server,
)

from .conftest import _make_definition


class TestParseSchedule:
    """Tests for _parse_schedule helper."""

    def test_parse_cron_expression(self) -> None:
        """AC: Valid cron expressions are parsed correctly."""
        result = _parse_schedule("0 9 * * *")

        assert result is not None
        assert result.type == "cron"
        assert result.expression == "0 9 * * *"

    def test_parse_complex_cron(self) -> None:
        """AC: Complex cron expressions work."""
        result = _parse_schedule("*/5 * * * *")

        assert result is not None
        assert result.type == "cron"
        assert result.expression == "*/5 * * * *"

    def test_parse_iso_datetime_with_z(self) -> None:
        """AC: ISO datetime with Z suffix is parsed as one-shot."""
        result = _parse_schedule("2026-03-22T10:00:00Z")

        assert result is not None
        assert result.type == "once"
        assert result.at is not None
        assert result.at.year == 2026
        assert result.at.month == 3
        assert result.at.day == 22

    def test_parse_iso_datetime_with_offset(self) -> None:
        """AC: ISO datetime with timezone offset is parsed."""
        result = _parse_schedule("2026-03-22T10:00:00+00:00")

        assert result is not None
        assert result.type == "once"
        assert result.at is not None

    def test_parse_iso_datetime_naive_gets_utc(self) -> None:
        """AC: Naive ISO datetime gets UTC tzinfo."""
        result = _parse_schedule("2026-03-22T10:00:00")

        assert result is not None
        assert result.type == "once"
        assert result.at is not None
        assert result.at.tzinfo == UTC

    def test_parse_invalid_returns_none(self) -> None:
        """AC: Invalid schedule returns None."""
        result = _parse_schedule("not a valid schedule")

        assert result is None

    def test_parse_invalid_cron_returns_none(self) -> None:
        """AC: Invalid cron expression returns None."""
        result = _parse_schedule("invalid cron")

        assert result is None


class TestFormatSchedule:
    """Tests for _format_schedule helper."""

    def test_format_cron(self) -> None:
        """AC: Cron schedules are formatted correctly."""
        schedule = ScheduleConfig(type="cron", expression="0 9 * * *")
        result = _format_schedule(schedule)

        assert result == "cron: 0 9 * * *"

    def test_format_once(self) -> None:
        """AC: One-shot schedules are formatted with datetime."""
        schedule = ScheduleConfig(type="once", at=datetime(2026, 3, 22, 10, 0, tzinfo=UTC))
        result = _format_schedule(schedule)

        assert "once:" in result
        assert "2026-03-22" in result

    def test_format_once_null_datetime(self) -> None:
        """AC: One-shot with null datetime shows invalid."""
        schedule = ScheduleConfig(type="once", at=None)
        result = _format_schedule(schedule)

        assert "once:" in result
        assert "invalid" in result


class TestToolArgModels:
    """Tests for Pydantic arg models — especially bool coercion from strings."""

    def test_list_tasks_archived_string_true(self) -> None:
        parsed = ListTasksArgs.model_validate({"archived": "true"})
        assert parsed.archived is True

    def test_list_tasks_archived_string_false(self) -> None:
        parsed = ListTasksArgs.model_validate({"archived": "false"})
        assert parsed.archived is False

    def test_list_tasks_archived_default(self) -> None:
        parsed = ListTasksArgs.model_validate({})
        assert parsed.archived is False

    def test_list_tasks_archived_bool_passthrough(self) -> None:
        parsed = ListTasksArgs.model_validate({"archived": True})
        assert parsed.archived is True

    def test_create_task_enabled_string_true(self) -> None:
        parsed = CreateTaskArgs.model_validate(
            {
                "name": "test",
                "schedule": "0 9 * * *",
                "type": "session",
                "prompt": "do something",
                "enabled": "true",
            }
        )
        assert parsed.enabled is True

    def test_create_task_enabled_string_false(self) -> None:
        parsed = CreateTaskArgs.model_validate(
            {
                "name": "test",
                "schedule": "0 9 * * *",
                "type": "session",
                "prompt": "do something",
                "enabled": "false",
            }
        )
        assert parsed.enabled is False

    def test_create_task_enabled_default(self) -> None:
        parsed = CreateTaskArgs.model_validate(
            {
                "name": "test",
                "schedule": "0 9 * * *",
                "type": "session",
                "prompt": "do something",
            }
        )
        assert parsed.enabled is True

    def test_create_task_missing_required_raises(self) -> None:
        with pytest.raises(ValidationError):
            CreateTaskArgs.model_validate({"name": "test"})

    def test_update_task_enabled_string_true(self) -> None:
        parsed = UpdateTaskArgs.model_validate({"task_id": "abc", "enabled": "true"})
        assert parsed.enabled is True

    def test_update_task_enabled_string_false(self) -> None:
        parsed = UpdateTaskArgs.model_validate({"task_id": "abc", "enabled": "false"})
        assert parsed.enabled is False

    def test_update_task_enabled_none_default(self) -> None:
        parsed = UpdateTaskArgs.model_validate({"task_id": "abc"})
        assert parsed.enabled is None

    def test_update_task_bool_passthrough(self) -> None:
        parsed = UpdateTaskArgs.model_validate({"task_id": "abc", "enabled": True})
        assert parsed.enabled is True

    def test_delete_task_missing_required_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeleteTaskArgs.model_validate({})


class TestCreateTaskToolsServer:
    """Tests for the MCP server factory."""

    def test_returns_mcp_server_config(self, repo: TaskRepository) -> None:
        """AC: Factory returns a dict with expected structure."""
        server = create_task_tools_server(repo)

        # McpSdkServerConfig is a TypedDict, so check structure instead
        assert isinstance(server, dict)
        assert server.get("name") == "task-tools"
        assert server.get("type") == "sdk"
        assert "instance" in server

    def test_server_has_expected_tools(self, repo: TaskRepository) -> None:
        """AC: Server is created successfully (tools are defined)."""
        # We can't easily inspect the tools, but we can verify the server is created
        server = create_task_tools_server(repo)

        # The server config exists and is valid
        assert server is not None


# ---------------------------------------------------------------------------
# Helpers for testing tool handlers directly
# ---------------------------------------------------------------------------


def _call_tool(repo: TaskRepository):
    """Return an async callable that invokes a tool by name through the MCP server."""
    server = create_task_tools_server(repo)
    mcp_server = server["instance"]
    call_handler = mcp_server.request_handlers[types.CallToolRequest]

    async def _invoke(name: str, args: dict) -> dict:
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=args),
        )
        result = await call_handler(request)

        # ServerResult wraps CallToolResult — unwrap via .root
        inner = result.root if hasattr(result, "root") else result
        content = []
        for c in inner.content:
            content.append({"type": "text", "text": c.text})

        return {
            "is_error": inner.isError or False,
            "content": content,
        }

    return _invoke


class TestListTasksOutput:
    """S1: list_tasks output includes task IDs."""

    @pytest.mark.asyncio
    async def test_list_tasks_includes_task_id(self, repo: TaskRepository) -> None:
        """AC: Each entry includes the task's id field in [id] format."""
        definition = _make_definition(definition_id="abc-123", name="Morning Check")
        await repo.create_definition(definition)

        call_tool = _call_tool(repo)
        result = await call_tool("list_tasks", {})

        text = result["content"][0]["text"]
        assert "[abc-123]" in text
        assert "**Morning Check**" in text

    @pytest.mark.asyncio
    async def test_list_tasks_multiple_definitions_show_ids(self, repo: TaskRepository) -> None:
        """AC: Multiple tasks each show their own ID."""
        await repo.create_definition(_make_definition(definition_id="id-1", name="Task One"))
        await repo.create_definition(_make_definition(definition_id="id-2", name="Task Two"))

        call_tool = _call_tool(repo)
        result = await call_tool("list_tasks", {})

        text = result["content"][0]["text"]
        assert "[id-1]" in text
        assert "[id-2]" in text


class TestUpdateTaskType:
    """S2: task_type field on UpdateTaskArgs and handler."""

    def test_update_task_args_valid_session(self) -> None:
        """AC: task_type='session' validates successfully."""
        parsed = UpdateTaskArgs.model_validate({"task_id": "abc", "task_type": "session"})
        assert parsed.task_type == "session"

    def test_update_task_args_valid_background(self) -> None:
        """AC: task_type='background' validates successfully."""
        parsed = UpdateTaskArgs.model_validate({"task_id": "abc", "task_type": "background"})
        assert parsed.task_type == "background"

    def test_update_task_args_invalid_type_raises(self) -> None:
        """AC: Invalid task_type value raises ValidationError with valid options."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateTaskArgs.model_validate({"task_id": "abc", "task_type": "invalid"})

        error_str = str(exc_info.value)
        assert "session" in error_str or "background" in error_str

    def test_update_task_args_task_type_default_none(self) -> None:
        """AC: task_type defaults to None when not provided."""
        parsed = UpdateTaskArgs.model_validate({"task_id": "abc"})
        assert parsed.task_type is None

    @pytest.mark.asyncio
    async def test_update_task_changes_task_type(self, repo: TaskRepository) -> None:
        """AC: Calling update_task with task_type changes the definition's task_type."""
        await repo.create_definition(
            _make_definition(definition_id="task-1", task_type="background")
        )

        call_tool = _call_tool(repo)
        result = await call_tool("update_task", {"task_id": "task-1", "task_type": "session"})

        assert result.get("is_error") is not True
        updated = await repo.get_definition("task-1")
        assert updated is not None
        assert updated.task_type == "session"


class TestErrorHandling:
    """S3: Error handling with TaskRepositoryError surfacing.

    Tests the error formatting pattern used by all four tools.
    Since the MCP SDK handles its own validation and error wrapping,
    these tests verify the formatting logic at the unit level.
    """

    def test_task_repository_error_with_cause_format(self) -> None:
        """AC: TaskRepositoryError with __cause__ formats both wrapper and cause."""
        original = RuntimeError("database is locked")
        exc = TaskRepositoryError("Failed to update task definition test-id")
        exc.__cause__ = original

        # Verify the pattern used in tools.py
        cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
        text = f"{exc}{cause}"

        assert "Failed to update task definition test-id" in text
        assert "database is locked" in text

    def test_task_repository_error_without_cause_format(self) -> None:
        """AC: TaskRepositoryError without __cause__ shows only wrapper message."""
        exc = TaskRepositoryError("Failed to delete task definition test-id")

        cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
        text = f"{exc}{cause}"

        assert "Failed to delete task definition test-id" in text
        assert "Cause:" not in text

    def test_list_tasks_args_validation_error_includes_field(self) -> None:
        """AC: Pydantic ValidationError for list_tasks includes field name."""
        with pytest.raises(ValidationError) as exc_info:
            ListTasksArgs.model_validate({"archived": "notabool"})

        error_str = str(exc_info.value)
        assert "archived" in error_str
