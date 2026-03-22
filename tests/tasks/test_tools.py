"""Tests for task MCP tools."""

from datetime import UTC, datetime

from tachikoma.tasks.model import ScheduleConfig
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.tools import (
    _format_schedule,
    _parse_schedule,
    create_task_tools_server,
)


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
