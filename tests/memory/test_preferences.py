"""Tests for preferences memory processor.

Extract and store memories from conversations.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.preferences import PREFERENCES_PROMPT, PreferencesProcessor
from tachikoma.memory.prompts import EXTRACTION_TOOLS, extraction_allow_rules
from tachikoma.post_processing import UTILITY_BASH_HOOK
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

    async def test_calls_fork_and_consume_with_correct_args(self, mocker: MockerFixture) -> None:
        """AC: Processor calls fork_and_consume with session, prompt, and cwd."""
        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )
        session = _make_session()
        cwd = Path("/workspace")

        defaults = AgentDefaults(cwd=cwd)
        processor = PreferencesProcessor(defaults)
        await processor.process(session)

        expected_prompt = PREFERENCES_PROMPT.replace("$WORKSPACE", str(cwd))
        scope = cwd / "memories" / "preferences"
        mock_fork.assert_awaited_once_with(
            session,
            expected_prompt,
            defaults,
            tools=EXTRACTION_TOOLS,
            allow=extraction_allow_rules(scope),
            pre_tool_use_hooks=[UTILITY_BASH_HOOK],
            model="haiku",
        )

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

    def test_prompt_instructs_agents_md_dedup(self) -> None:
        """AC: Prompt instructs to check AGENTS.md before creating files."""
        assert "AGENTS.md" in PREFERENCES_PROMPT

    def test_prompt_instructs_agents_md_graceful_fallback(self) -> None:
        """AC: Prompt handles missing AGENTS.md gracefully."""
        assert "proceed normally" in PREFERENCES_PROMPT.lower()

    async def test_propagates_fork_and_consume_error(self, mocker: MockerFixture) -> None:
        """AC: Exceptions from fork_and_consume propagate."""
        _mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume",
            side_effect=RuntimeError("SDK error"),
        )
        session = _make_session()

        processor = PreferencesProcessor(AgentDefaults(cwd=Path("/workspace")))

        with pytest.raises(RuntimeError, match="SDK error"):
            await processor.process(session)

    def test_prompt_includes_classification_examples(self) -> None:
        """AC: Prompt includes shared classification examples section."""
        assert "Classification Examples" in PREFERENCES_PROMPT
        assert "IS a preference" in PREFERENCES_PROMPT
        assert "NOT a preference" in PREFERENCES_PROMPT

    def test_prompt_includes_classification_self_check(self) -> None:
        """AC: Prompt includes mandatory classification self-check step."""
        assert "Classification self-check" in PREFERENCES_PROMPT
        assert "HOW SOMETHING WORKS" in PREFERENCES_PROMPT
        assert "HOW THE USER WANTS IT DONE" in PREFERENCES_PROMPT

    def test_prompt_includes_concrete_misclassification_examples(self) -> None:
        """AC: Prompt includes concrete misclassification patterns."""
        assert "Financial reference data" in PREFERENCES_PROMPT
        assert "Technical specifications" in PREFERENCES_PROMPT
        assert "Procedural workflows" in PREFERENCES_PROMPT
        assert "System configuration records" in PREFERENCES_PROMPT

    def test_prompt_includes_positive_preference_examples(self) -> None:
        """AC: Prompt includes positive preference examples to prevent over-filtering."""
        assert "style preference" in PREFERENCES_PROMPT.lower()
        assert "workflow preference" in PREFERENCES_PROMPT.lower()
