"""Tests for episodic memory processor.

Tests for DLT-008: Extract and store memories from conversations.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.episodic import EPISODIC_PROMPT, EpisodicProcessor
from tachikoma.sessions.model import Session


def _make_session(sdk_session_id: str = "sdk-123") -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        sdk_session_id=sdk_session_id,
    )


class TestEpisodicProcessor:
    """Tests for EpisodicProcessor."""

    async def test_calls_fork_and_consume_with_correct_args(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Processor calls fork_and_consume with session, prompt, and cwd."""
        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )
        session = _make_session()
        cwd = Path("/workspace")

        defaults = AgentDefaults(cwd=cwd)
        processor = EpisodicProcessor(defaults)
        await processor.process(session)

        mock_fork.assert_awaited_once_with(session, EPISODIC_PROMPT, defaults)

    def test_prompt_references_correct_subdirectory(self) -> None:
        """AC: Prompt mentions the episodic subdirectory path."""
        assert "memories/episodic" in EPISODIC_PROMPT

    def test_prompt_instructs_reading_existing_files(self) -> None:
        """AC: Prompt instructs to read existing files before making changes."""
        assert "read" in EPISODIC_PROMPT.lower()
        assert "existing" in EPISODIC_PROMPT.lower()

    def test_prompt_instructs_no_changes_is_valid(self) -> None:
        """AC: Prompt states that creating nothing is acceptable."""
        assert (
            "no files" in EPISODIC_PROMPT.lower()
            or "create no files" in EPISODIC_PROMPT.lower()
            or "creating nothing" in EPISODIC_PROMPT.lower()
        )

    def test_prompt_instructs_date_stamped_filenames(self) -> None:
        """AC: Prompt mentions YYYY-MM-DD format for episodic files."""
        assert "YYYY-MM-DD" in EPISODIC_PROMPT

    def test_prompt_instructs_same_day_consolidation(self) -> None:
        """AC: Prompt mentions consolidating same-day entries."""
        assert "consolidat" in EPISODIC_PROMPT.lower()

    async def test_propagates_fork_and_consume_error(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Exceptions from fork_and_consume propagate."""
        _mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume",
            side_effect=RuntimeError("SDK error"),
        )
        session = _make_session()

        processor = EpisodicProcessor(AgentDefaults(cwd=Path("/workspace")))

        with pytest.raises(RuntimeError, match="SDK error"):
            await processor.process(session)
