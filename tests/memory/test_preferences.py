"""Tests for preferences memory processor.

Tests for DLT-008: Extract and store memories from conversations.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tachikoma.memory.preferences import PREFERENCES_PROMPT, PreferencesProcessor
from tachikoma.sessions.model import Session


def _make_session(sdk_session_id: str = "sdk-123") -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        sdk_session_id=sdk_session_id,
    )


class TestPreferencesProcessor:
    """Tests for PreferencesProcessor."""

    async def test_calls_fork_and_consume_with_correct_args(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Processor calls fork_and_consume with session, prompt, and cwd."""
        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )
        session = _make_session()
        cwd = Path("/workspace")

        processor = PreferencesProcessor(cwd=cwd)
        await processor.process(session)

        mock_fork.assert_awaited_once_with(session, PREFERENCES_PROMPT, cwd, cli_path=None)

    def test_prompt_references_correct_subdirectory(self) -> None:
        """AC: Prompt mentions the preferences subdirectory path."""
        assert "memories/preferences" in PREFERENCES_PROMPT

    def test_prompt_instructs_reading_existing_files(self) -> None:
        """AC: Prompt instructs to read existing files before making changes."""
        assert "read" in PREFERENCES_PROMPT.lower()
        assert "existing" in PREFERENCES_PROMPT.lower()

    def test_prompt_instructs_no_changes_is_valid(self) -> None:
        """AC: Prompt states that creating nothing is acceptable."""
        assert (
            "no preference" in PREFERENCES_PROMPT.lower()
            or "create no files" in PREFERENCES_PROMPT.lower()
        )

    def test_prompt_instructs_topic_based_filenames(self) -> None:
        """AC: Prompt mentions descriptive, topic-based naming."""
        assert "descriptive" in PREFERENCES_PROMPT.lower() or "topic" in PREFERENCES_PROMPT.lower()

    async def test_propagates_fork_and_consume_error(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Exceptions from fork_and_consume propagate."""
        _mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume",
            side_effect=RuntimeError("SDK error"),
        )
        session = _make_session()

        processor = PreferencesProcessor(cwd=Path("/workspace"))

        with pytest.raises(RuntimeError, match="SDK error"):
            await processor.process(session)
