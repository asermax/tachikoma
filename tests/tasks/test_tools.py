"""Tests for task MCP tools."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import mcp.types as types
import pytest
from pydantic import ValidationError

from tachikoma.tasks.errors import TaskRepositoryError
from tachikoma.tasks.model import ScheduleConfig, TaskDefinition
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.tools import (
    CreateTaskArgs,
    DeleteTaskArgs,
    ListTasksArgs,
    UpdateTaskArgs,
    _format_schedule,
    _parse_schedule,
    create_task_tools_server,
    handle_respond_to_task,
)

from .conftest import _make_definition, _make_instance

TZ_UTC = ZoneInfo("UTC")
TZ_ART = ZoneInfo("America/Argentina/Buenos_Aires")


class TestParseSchedule:
    """Tests for _parse_schedule helper."""

    def test_parse_cron_expression(self) -> None:
        """AC: Valid cron expressions are parsed correctly."""
        result = _parse_schedule("0 9 * * *", TZ_UTC)

        assert result is not None
        assert result.type == "cron"
        assert result.expression == "0 9 * * *"

    def test_parse_complex_cron(self) -> None:
        """AC: Complex cron expressions work."""
        result = _parse_schedule("*/5 * * * *", TZ_UTC)

        assert result is not None
        assert result.type == "cron"
        assert result.expression == "*/5 * * * *"

    def test_parse_iso_datetime_with_z(self) -> None:
        """AC: ISO datetime with Z suffix is parsed as one-shot."""
        result = _parse_schedule("2026-03-22T10:00:00Z", TZ_UTC)

        assert result is not None
        assert result.type == "once"
        assert result.at is not None
        assert result.at.year == 2026
        assert result.at.month == 3
        assert result.at.day == 22

    def test_parse_iso_datetime_with_offset(self) -> None:
        """AC: ISO datetime with timezone offset is parsed."""
        result = _parse_schedule("2026-03-22T10:00:00+00:00", TZ_UTC)

        assert result is not None
        assert result.type == "once"
        assert result.at is not None

    def test_parse_bare_datetime_gets_configured_tz(self) -> None:
        """AC: Bare ISO datetime is stamped with the provided timezone."""
        result = _parse_schedule("2026-03-22T10:00:00", TZ_ART)

        assert result is not None
        assert result.type == "once"
        assert result.at is not None
        assert result.at.tzinfo == TZ_ART
        assert result.at.hour == 10  # Wall clock preserved

    def test_parse_invalid_returns_none(self) -> None:
        """AC: Invalid schedule returns None."""
        result = _parse_schedule("not a valid schedule", TZ_UTC)

        assert result is None

    def test_parse_invalid_cron_returns_none(self) -> None:
        """AC: Invalid cron expression returns None."""
        result = _parse_schedule("invalid cron", TZ_UTC)

        assert result is None

    def test_parse_explicit_utc_preserved(self) -> None:
        """AC (R3): ISO datetime with Z suffix preserves UTC."""
        result = _parse_schedule("2026-04-01T15:00:00Z", TZ_ART)

        assert result is not None
        assert result.at is not None
        assert result.at.utcoffset().total_seconds() == 0

    def test_parse_explicit_offset_preserved(self) -> None:
        """AC (R3): ISO datetime with explicit offset preserved as-is."""
        result = _parse_schedule("2026-04-01T15:00:00+05:30", TZ_ART)

        assert result is not None
        assert result.at is not None
        assert result.at.utcoffset().total_seconds() == 5.5 * 3600


class TestFormatSchedule:
    """Tests for _format_schedule helper."""

    def test_format_cron(self) -> None:
        """AC: Cron schedules are formatted correctly."""
        schedule = ScheduleConfig(type="cron", expression="0 9 * * *")
        result = _format_schedule(schedule, TZ_UTC)

        assert result == "cron: 0 9 * * *"

    def test_format_once(self) -> None:
        """AC: One-shot schedules are formatted with datetime."""
        schedule = ScheduleConfig(type="once", at=datetime(2026, 3, 22, 10, 0, tzinfo=UTC))
        result = _format_schedule(schedule, TZ_UTC)

        assert "once:" in result
        assert "2026-03-22" in result

    def test_format_once_null_datetime(self) -> None:
        """AC: One-shot with null datetime shows invalid."""
        schedule = ScheduleConfig(type="once", at=None)
        result = _format_schedule(schedule, TZ_UTC)

        assert "once:" in result
        assert "invalid" in result

    def test_format_once_converts_to_configured_tz(self) -> None:
        """AC (R4): UTC datetime displayed in configured timezone."""
        # 18:00 UTC = 15:00 ART (UTC-3)
        schedule = ScheduleConfig(type="once", at=datetime(2026, 4, 1, 18, 0, tzinfo=UTC))
        result = _format_schedule(schedule, TZ_ART)

        assert "once:" in result
        assert "2026-04-01" in result
        assert "15:00" in result

    def test_format_once_already_in_configured_tz(self) -> None:
        """AC (R4): Datetime already in configured tz displays correctly."""
        schedule = ScheduleConfig(type="once", at=datetime(2026, 4, 1, 15, 0, tzinfo=TZ_ART))
        result = _format_schedule(schedule, TZ_ART)

        assert "once:" in result
        assert "2026-04-01" in result
        assert "15:00" in result


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
        server = create_task_tools_server(repo, TZ_UTC)

        # McpSdkServerConfig is a TypedDict, so check structure instead
        assert isinstance(server, dict)
        assert server.get("name") == "task-tools"
        assert server.get("type") == "sdk"
        assert "instance" in server

    def test_server_has_expected_tools(self, repo: TaskRepository) -> None:
        """AC: Server is created successfully (tools are defined)."""
        # We can't easily inspect the tools, but we can verify the server is created
        server = create_task_tools_server(repo, TZ_UTC)

        # The server config exists and is valid
        assert server is not None

    @pytest.mark.asyncio
    async def test_exclude_respond_tool_for_background(self, repo: TaskRepository) -> None:
        """AC (R8): Background sessions get a task-tools server without respond_to_task."""
        server = create_task_tools_server(repo, TZ_UTC, include_respond_tool=False)
        mcp_server = server["instance"]

        list_handler = mcp_server.request_handlers[types.ListToolsRequest]
        result = await list_handler(types.ListToolsRequest(method="tools/list"))

        tool_names = {t.name for t in result.root.tools}
        assert tool_names == {"list_tasks", "get_task", "create_task", "update_task", "delete_task"}
        assert "respond_to_task" not in tool_names

    @pytest.mark.asyncio
    async def test_include_respond_tool_by_default(self, repo: TaskRepository) -> None:
        """AC: Main conversation sessions get the full task-tools surface."""
        server = create_task_tools_server(repo, TZ_UTC)
        mcp_server = server["instance"]

        list_handler = mcp_server.request_handlers[types.ListToolsRequest]
        result = await list_handler(types.ListToolsRequest(method="tools/list"))

        tool_names = {t.name for t in result.root.tools}
        assert "respond_to_task" in tool_names


class TestFutureCheckWithTzAware:
    """Tests for future-check validation with timezone-aware datetimes (R2).

    The future-check logic (schedule.at <= datetime.now(UTC)) compares tz-aware
    datetimes by absolute instant. These tests verify the parsed output is correct.
    """

    def test_future_check_accepts_tz_aware_future(self) -> None:
        """AC (R2): Tz-aware future datetime accepted."""
        # Parse 15:00 bare → 15:00 ART = 18:00 UTC
        schedule = _parse_schedule("2026-04-01T15:00:00", TZ_ART)
        assert schedule is not None
        assert schedule.at is not None

        # "Now" is 17:00 UTC — schedule at 18:00 UTC is in the future
        now_utc = datetime(2026, 4, 1, 17, 0, tzinfo=UTC)
        assert schedule.at > now_utc

    def test_future_check_rejects_tz_aware_past(self) -> None:
        """AC (R2): Tz-aware past datetime rejected."""
        # Parse 15:00 bare → 15:00 ART = 18:00 UTC
        schedule = _parse_schedule("2026-04-01T15:00:00", TZ_ART)
        assert schedule is not None
        assert schedule.at is not None

        # "Now" is 19:00 UTC — schedule at 18:00 UTC is in the past
        now_utc = datetime(2026, 4, 1, 19, 0, tzinfo=UTC)
        assert schedule.at <= now_utc

    def test_future_check_explicit_utc_accepted(self) -> None:
        """AC (R2): Explicit UTC in future accepted regardless of local tz."""
        # Parse 18:00 UTC (explicit Z)
        schedule = _parse_schedule("2026-04-01T18:00:00Z", TZ_ART)
        assert schedule is not None
        assert schedule.at is not None

        # "Now" is 17:00 UTC — schedule at 18:00 UTC is in the future
        now_utc = datetime(2026, 4, 1, 17, 0, tzinfo=UTC)
        assert schedule.at > now_utc


# ---------------------------------------------------------------------------
# Helpers for testing tool handlers directly
# ---------------------------------------------------------------------------


def _call_tool(repo: TaskRepository, tz: ZoneInfo = TZ_UTC):
    """Return an async callable that invokes a tool by name through the MCP server."""
    server = create_task_tools_server(repo, tz)
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


class TestGetTask:
    """S4: get_task returns full details for a single task."""

    @pytest.mark.asyncio
    async def test_get_task_returns_full_prompt(self, repo: TaskRepository) -> None:
        """AC: get_task returns the complete prompt without truncation."""
        long_prompt = "A" * 200
        await repo.create_definition(_make_definition(definition_id="abc-123", prompt=long_prompt))

        call_tool = _call_tool(repo)
        result = await call_tool("get_task", {"task_id": "abc-123"})

        assert result.get("is_error") is not True
        text = result["content"][0]["text"]
        assert long_prompt in text

    @pytest.mark.asyncio
    async def test_get_task_includes_all_fields(self, repo: TaskRepository) -> None:
        """AC: get_task returns ID, name, type, status, schedule, prompt, timestamps."""
        await repo.create_definition(_make_definition(definition_id="xyz-789", name="My Task"))

        call_tool = _call_tool(repo)
        result = await call_tool("get_task", {"task_id": "xyz-789"})

        text = result["content"][0]["text"]
        assert "xyz-789" in text
        assert "My Task" in text
        assert "session" in text
        assert "enabled" in text
        assert "Prompt" in text

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, repo: TaskRepository) -> None:
        """AC: get_task returns error for unknown task ID."""
        call_tool = _call_tool(repo)
        result = await call_tool("get_task", {"task_id": "nonexistent"})

        assert "not found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_list_tasks_omits_prompt(self, repo: TaskRepository) -> None:
        """AC: list_tasks no longer shows the prompt."""
        await repo.create_definition(
            _make_definition(definition_id="p-test", prompt="Secret prompt content")
        )

        call_tool = _call_tool(repo)
        result = await call_tool("list_tasks", {})

        text = result["content"][0]["text"]
        assert "[p-test]" in text
        assert "Secret prompt content" not in text


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


class TestTimezoneDisplay:
    """Tests for timestamp display in configured timezone (R1)."""

    @pytest.mark.asyncio
    async def test_list_tasks_last_fired_at_in_configured_tz(self, repo: TaskRepository) -> None:
        """AC1: list_tasks shows last_fired_at converted to configured timezone."""
        # 18:00 UTC = 15:00 ART (UTC-3)
        last_fired = datetime(2026, 4, 1, 18, 0, tzinfo=UTC)
        await repo.create_definition(
            _make_definition(definition_id="tz-1", name="TZ Test", last_fired_at=last_fired)
        )

        call_tool = _call_tool(repo, TZ_ART)
        result = await call_tool("list_tasks", {})

        text = result["content"][0]["text"]
        assert "15:00" in text
        assert "18:00" not in text

    @pytest.mark.asyncio
    async def test_get_task_timestamps_in_configured_tz(self, repo: TaskRepository) -> None:
        """AC2: get_task shows last_fired_at and created_at in configured timezone."""
        # 18:00 UTC = 15:00 ART, 12:00 UTC = 09:00 ART
        last_fired = datetime(2026, 4, 1, 18, 0, tzinfo=UTC)
        created = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

        definition = TaskDefinition(
            id="tz-2",
            name="TZ Detail Test",
            schedule=ScheduleConfig(type="cron", expression="0 9 * * *"),
            task_type="session",
            prompt="Test prompt",
            enabled=True,
            last_fired_at=last_fired,
            created_at=created,
        )
        await repo.create_definition(definition)

        call_tool = _call_tool(repo, TZ_ART)
        result = await call_tool("get_task", {"task_id": "tz-2"})

        text = result["content"][0]["text"]

        # last_fired_at: 18:00 UTC → 15:00 ART
        assert "15:00" in text

        # created_at: 12:00 UTC → 09:00 ART
        assert "09:00" in text


class TestUpdateTaskScheduleReset:
    """Tests for schedule update resetting last_fired_at and future validation."""

    @pytest.mark.asyncio
    async def test_update_schedule_resets_last_fired_at(self, repo: TaskRepository) -> None:
        """AC3a: Updating schedule resets last_fired_at to None."""
        last_fired = datetime(2026, 4, 1, 18, 0, tzinfo=UTC)
        await repo.create_definition(
            _make_definition(
                definition_id="reset-1",
                last_fired_at=last_fired,
                enabled=False,
            )
        )

        call_tool = _call_tool(repo)
        result = await call_tool(
            "update_task",
            {"task_id": "reset-1", "schedule": "0 10 * * *", "enabled": True},
        )

        assert result.get("is_error") is not True
        updated = await repo.get_definition("reset-1")
        assert updated is not None
        assert updated.last_fired_at is None
        assert updated.enabled is True

    @pytest.mark.asyncio
    async def test_update_enabled_only_preserves_last_fired_at(self, repo: TaskRepository) -> None:
        """AC3b: Updating only enabled preserves last_fired_at."""
        last_fired = datetime(2026, 4, 1, 18, 0, tzinfo=UTC)
        await repo.create_definition(
            _make_definition(
                definition_id="preserve-1",
                last_fired_at=last_fired,
                enabled=False,
            )
        )

        call_tool = _call_tool(repo)
        result = await call_tool(
            "update_task",
            {"task_id": "preserve-1", "enabled": True},
        )

        assert result.get("is_error") is not True
        updated = await repo.get_definition("preserve-1")
        assert updated is not None
        assert updated.last_fired_at is not None

    @pytest.mark.asyncio
    async def test_update_rejects_past_one_shot_schedule(self, repo: TaskRepository) -> None:
        """AC4: update_task rejects one-shot schedules in the past."""
        await repo.create_definition(_make_definition(definition_id="past-1"))

        call_tool = _call_tool(repo)
        result = await call_tool(
            "update_task",
            {"task_id": "past-1", "schedule": "2020-01-01T00:00:00Z"},
        )

        text = result["content"][0]["text"]
        assert "future" in text.lower()
        assert "2020-01-01" in text


class TestHandleRespondToTask:
    """Tests for handle_respond_to_task (DLT-120 R3)."""

    @pytest.mark.asyncio
    async def test_success_saves_response(self, repo: TaskRepository) -> None:
        """AC: Valid response is saved on a waiting instance."""
        await repo.create_instance(
            _make_instance("inst-1", status="waiting", task_type="background")
        )

        result = await handle_respond_to_task("inst-1", "Yes, proceed", repo)

        assert "is_error" not in result
        assert "Response sent." in result["content"][0]["text"]

        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.user_response == "Yes, proceed"

    @pytest.mark.asyncio
    async def test_rejects_empty_response(self, repo: TaskRepository) -> None:
        """AC: Empty/whitespace-only response returns error."""
        result = await handle_respond_to_task("inst-1", "   ", repo)

        assert result["is_error"] is True
        assert "empty" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_rejects_not_found(self, repo: TaskRepository) -> None:
        """AC: Nonexistent instance ID returns error."""
        result = await handle_respond_to_task("ghost-id", "Yes", repo)

        assert result["is_error"] is True
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_rejects_not_waiting(self, repo: TaskRepository) -> None:
        """AC: Instance not in 'waiting' status returns error."""
        await repo.create_instance(
            _make_instance("inst-1", status="completed", task_type="background")
        )

        result = await handle_respond_to_task("inst-1", "Yes", repo)

        assert result["is_error"] is True
        assert "not waiting" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_rejects_already_responded(self, repo: TaskRepository) -> None:
        """AC: Instance with existing user_response returns error."""
        await repo.create_instance(
            _make_instance(
                "inst-1",
                status="waiting",
                task_type="background",
                user_response="First response",
            )
        )

        result = await handle_respond_to_task("inst-1", "Second response", repo)

        assert result["is_error"] is True
        assert "already pending" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_trims_whitespace(self, repo: TaskRepository) -> None:
        """AC: Response is trimmed before saving."""
        await repo.create_instance(
            _make_instance("inst-1", status="waiting", task_type="background")
        )

        result = await handle_respond_to_task("inst-1", "  Yes  ", repo)

        assert "is_error" not in result
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.user_response == "Yes"


class TestRespondToTaskTool:
    """Tests for the registered respond_to_task tool closure."""

    @pytest.mark.asyncio
    async def test_tool_registered_and_callable(self, repo: TaskRepository) -> None:
        """AC: respond_to_task tool is registered and callable."""
        await repo.create_instance(
            _make_instance("inst-1", status="waiting", task_type="background")
        )

        call_tool = _call_tool(repo)
        result = await call_tool(
            "respond_to_task",
            {"task_instance_id": "inst-1", "response": "Yes, go ahead"},
        )

        assert result.get("is_error") is not True
        assert "Response sent." in result["content"][0]["text"]
