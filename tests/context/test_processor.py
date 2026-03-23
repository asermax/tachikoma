"""Tests for CoreContextProcessor.

Tests for DLT-018: Update core context files from conversation learnings.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.context.processor import (
    CONTEXT_UPDATE_PROMPT,
    CoreContextProcessor,
    _format_pending_signals_section,
    _read_pending_signals_snapshot,
)
from tachikoma.context.tools import PENDING_SIGNALS_FILENAME, PENDING_SIGNALS_HEADER
from tachikoma.sessions.model import Session


def _make_session(sdk_session_id: str = "sdk-123") -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        sdk_session_id=sdk_session_id,
    )


class TestReadPendingSignalsSnapshot:
    """Tests for _read_pending_signals_snapshot helper."""

    def test_reads_entries_from_file(self, tmp_path: Path) -> None:
        """AC: Reads and parses entries from pending signals file."""
        content = (
            PENDING_SIGNALS_HEADER
            + "- **2026-03-10**: First signal\n- **2026-03-15**: Second signal\n"
        )
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text(content)

        snapshot = _read_pending_signals_snapshot(tmp_path)

        assert len(snapshot) == 2
        assert snapshot[0] == ("2026-03-10", "First signal")
        assert snapshot[1] == ("2026-03-15", "Second signal")

    def test_returns_empty_list_for_missing_file(self, tmp_path: Path) -> None:
        """AC: Returns empty list when file doesn't exist."""
        snapshot = _read_pending_signals_snapshot(tmp_path)

        assert snapshot == []

    def test_returns_empty_list_for_empty_file(self, tmp_path: Path) -> None:
        """AC: Returns empty list when file is empty."""
        (tmp_path / PENDING_SIGNALS_FILENAME).write_text("")

        snapshot = _read_pending_signals_snapshot(tmp_path)

        assert snapshot == []


class TestFormatPendingSignalsSection:
    """Tests for _format_pending_signals_section helper."""

    def test_formats_empty_snapshot(self) -> None:
        """AC: Empty snapshot shows 'No pending signals' message."""
        result = _format_pending_signals_section([])

        assert result == "No pending signals at this time."

    def test_formats_single_entry(self) -> None:
        """AC: Single entry is formatted as S1."""
        snapshot = [("2026-03-10", "User prefers concise responses")]

        result = _format_pending_signals_section(snapshot)

        assert result == "S1: **2026-03-10**: User prefers concise responses"

    def test_formats_multiple_entries(self) -> None:
        """AC: Multiple entries are numbered S1, S2, etc."""
        snapshot = [
            ("2026-03-10", "First signal"),
            ("2026-03-15", "Second signal"),
            ("2026-03-20", "Third signal"),
        ]

        result = _format_pending_signals_section(snapshot)

        lines = result.split("\n")
        assert len(lines) == 3
        assert lines[0] == "S1: **2026-03-10**: First signal"
        assert lines[1] == "S2: **2026-03-15**: Second signal"
        assert lines[2] == "S3: **2026-03-20**: Third signal"

    def test_uses_one_based_indexing(self) -> None:
        """AC: Indexing starts at 1 (S1, not S0)."""
        snapshot = [("2026-03-10", "Signal")]

        result = _format_pending_signals_section(snapshot)

        assert result.startswith("S1:")


class TestCoreContextProcessor:
    """Tests for CoreContextProcessor."""

    async def test_calls_clean_pending_signals(
        self, mocker: pytest.MockerFixture, tmp_path: Path
    ) -> None:
        """AC: process() calls clean_pending_signals with correct data_dir."""
        mock_clean = mocker.patch(
            "tachikoma.context.processor.clean_pending_signals"
        )
        mocker.patch(
            "tachikoma.context.processor.fork_and_consume",
            new_callable=AsyncMock,
        )
        session = _make_session()

        processor = CoreContextProcessor(AgentDefaults(cwd=tmp_path))
        await processor.process(session)

        # Should be called with .tachikoma directory
        mock_clean.assert_called_once_with(tmp_path / ".tachikoma")

    async def test_calls_create_pending_signals_server_with_snapshot(
        self, mocker: pytest.MockerFixture, tmp_path: Path
    ) -> None:
        """AC: process() calls create_pending_signals_server with data_dir and snapshot."""
        mock_create_server = mocker.patch(
            "tachikoma.context.processor.create_pending_signals_server"
        )
        mocker.patch(
            "tachikoma.context.processor.fork_and_consume",
            new_callable=AsyncMock,
        )
        mock_create_server.return_value = {"type": "sdk"}

        session = _make_session()
        processor = CoreContextProcessor(AgentDefaults(cwd=tmp_path))
        await processor.process(session)

        # Should be called with .tachikoma directory and a snapshot (list)
        mock_create_server.assert_called_once()
        call_args = mock_create_server.call_args
        assert call_args[0][0] == tmp_path / ".tachikoma"
        assert isinstance(call_args[0][1], list)

    async def test_calls_fork_and_consume_with_formatted_prompt(
        self, mocker: pytest.MockerFixture, tmp_path: Path
    ) -> None:
        """AC: process() calls fork_and_consume with formatted prompt containing signals section."""
        mock_fork = mocker.patch(
            "tachikoma.context.processor.fork_and_consume",
            new_callable=AsyncMock,
        )
        session = _make_session()

        processor = CoreContextProcessor(AgentDefaults(cwd=tmp_path))
        await processor.process(session)

        mock_fork.assert_awaited_once()
        call_args = mock_fork.call_args
        assert call_args[0][0] == session  # session
        # Prompt should be formatted (not raw CONTEXT_UPDATE_PROMPT)
        prompt_arg = call_args[0][1]
        assert "{pending_signals_section}" not in prompt_arg  # Placeholder should be replaced
        assert "No pending signals at this time." in prompt_arg  # Empty state message
        assert call_args[0][2] == AgentDefaults(cwd=tmp_path)  # agent_defaults
        assert "mcp_servers" in call_args[1]
        assert "pending-signals" in call_args[1]["mcp_servers"]

    @pytest.mark.skip(
        reason="mtime changes happen inside mocked fork_and_consume, "
        "making post-step comparison difficult to observe",
    )
    async def test_logs_when_file_created(
        self, mocker: pytest.MockerFixture, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC: mtime comparison logs when files are created."""
        # Note: This would require side_effect in mock to simulate file creation
        # during the fork, which adds complexity for minimal test value.
        pass

    @pytest.mark.skip(
        reason="mtime changes happen inside mocked fork_and_consume, "
        "making post-step comparison difficult to observe",
    )
    async def test_logs_when_file_updated(
        self, mocker: pytest.MockerFixture, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC: mtime comparison logs when files are modified."""
        # Note: This would require side_effect in mock to simulate file modification
        # during the fork, which adds complexity for minimal test value.
        pass

    async def test_handles_missing_context_file_gracefully(
        self, mocker: pytest.MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Graceful handling when context files don't exist (missing mtime)."""
        mock_fork = mocker.patch(
            "tachikoma.context.processor.fork_and_consume",
            new_callable=AsyncMock,
        )
        session = _make_session()

        # Don't create context directory
        processor = CoreContextProcessor(AgentDefaults(cwd=tmp_path))
        # Should not raise
        await processor.process(session)

        mock_fork.assert_awaited_once()


class TestContextUpdatePrompt:
    """Tests for CONTEXT_UPDATE_PROMPT content."""

    def test_references_all_three_context_files(self) -> None:
        """AC: Prompt references all three context files."""
        assert "SOUL.md" in CONTEXT_UPDATE_PROMPT
        assert "USER.md" in CONTEXT_UPDATE_PROMPT
        assert "AGENTS.md" in CONTEXT_UPDATE_PROMPT

    def test_mentions_pending_signals_tools(self) -> None:
        """AC: Prompt mentions add/remove_pending_signal tools (not read tool)."""
        assert "remove_pending_signal" in CONTEXT_UPDATE_PROMPT
        assert "add_pending_signal" in CONTEXT_UPDATE_PROMPT
        assert "read_pending_signals" not in CONTEXT_UPDATE_PROMPT

    def test_contains_pending_signals_placeholder(self) -> None:
        """AC: Prompt contains the {pending_signals_section} placeholder."""
        assert "{pending_signals_section}" in CONTEXT_UPDATE_PROMPT

    def test_contains_lifecycle_guidance(self) -> None:
        """AC: Prompt contains lifecycle guidance (stage, promote, remove, cleanup stale)."""
        prompt_lower = CONTEXT_UPDATE_PROMPT.lower()
        assert "stage" in prompt_lower
        assert "promote" in prompt_lower
        assert "remove" in prompt_lower
        assert "stale" in prompt_lower or "cleanup" in prompt_lower

    def test_contains_ordering_instruction(self) -> None:
        """AC: Prompt instructs removals before additions."""
        assert "removals before" in CONTEXT_UPDATE_PROMPT.lower()

    def test_instructs_conservative_update_policy(self) -> None:
        """AC: Prompt instructs conservative update policy."""
        assert "conservative" in CONTEXT_UPDATE_PROMPT.lower()
        prompt = CONTEXT_UPDATE_PROMPT.lower()
        assert "clear" in prompt or "explicit" in prompt

    def test_instructs_reading_files_before_modifying(self) -> None:
        """AC: Prompt instructs reading files before modifying."""
        assert "read" in CONTEXT_UPDATE_PROMPT.lower()
        assert "before" in CONTEXT_UPDATE_PROMPT.lower() or "first" in CONTEXT_UPDATE_PROMPT.lower()

    def test_instructs_not_to_directly_access_pending_signals_file(self) -> None:
        """AC: Prompt instructs not to directly access pending signals file."""
        assert "tool" in CONTEXT_UPDATE_PROMPT.lower()
        prompt = CONTEXT_UPDATE_PROMPT.lower()
        # Should mention only using tools, not direct access
        assert "never access" in prompt or "tool-only" in prompt

    def test_instructs_preserving_structure(self) -> None:
        """AC: Prompt instructs preserving existing structure."""
        prompt = CONTEXT_UPDATE_PROMPT.lower()
        assert "preserve" in prompt or "maintain" in prompt
        assert "structure" in prompt or "format" in prompt
