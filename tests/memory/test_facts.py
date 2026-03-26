"""Tests for facts memory processor.

Tests for DLT-008: Extract and store memories from conversations.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.facts import FACTS_PROMPT, FactsProcessor
from tachikoma.sessions.model import Session


def _make_session(sdk_session_id: str = "sdk-123") -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        sdk_session_id=sdk_session_id,
    )


class TestFactsProcessor:
    """Tests for FactsProcessor."""

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
        processor = FactsProcessor(defaults)
        await processor.process(session)

        mock_fork.assert_awaited_once_with(session, FACTS_PROMPT, defaults)

    def test_prompt_references_correct_subdirectory(self) -> None:
        """AC: Prompt mentions the facts subdirectory path."""
        assert "memories/facts" in FACTS_PROMPT

    def test_prompt_instructs_reading_existing_files(self) -> None:
        """AC: Prompt instructs to read existing files before making changes."""
        assert "read" in FACTS_PROMPT.lower()
        assert "existing" in FACTS_PROMPT.lower()

    def test_prompt_instructs_no_changes_is_valid(self) -> None:
        """AC: Prompt states that creating nothing is acceptable."""
        assert "no new" in FACTS_PROMPT.lower() or "create no files" in FACTS_PROMPT.lower()

    def test_prompt_instructs_topic_based_filenames(self) -> None:
        """AC: Prompt mentions descriptive, topic-based naming."""
        assert "topic" in FACTS_PROMPT.lower() or "descriptive" in FACTS_PROMPT.lower()

    async def test_propagates_fork_and_consume_error(self, mocker: pytest.MockerFixture) -> None:
        """AC: Exceptions from fork_and_consume propagate."""
        _mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume",
            side_effect=RuntimeError("SDK error"),
        )
        session = _make_session()

        processor = FactsProcessor(AgentDefaults(cwd=Path("/workspace")))

        with pytest.raises(RuntimeError, match="SDK error"):
            await processor.process(session)
