"""Tests for summary processor.

Tests for DLT-026: Detect conversation boundaries via topic analysis.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk.types import AssistantMessage, TextBlock

from tachikoma.boundary.summary import SummaryProcessor
from tachikoma.sessions.model import Session


def _make_session(summary: str | None = None) -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        summary=summary,
    )


class TestSummaryProcessor:
    """Tests for SummaryProcessor."""

    async def test_calls_query_with_opus_low_effort(self, mocker: pytest.MockerFixture) -> None:
        """AC: Summary processor uses Opus model with low effort."""
        mock_query = mocker.patch("tachikoma.boundary.summary.query")
        mock_registry = MagicMock()
        mock_registry.update_summary = AsyncMock()

        async def fake_query(*args, **kwargs):
            yield AssistantMessage(
                content=[TextBlock(text="Test summary")],
                model="claude-opus",
            )

        mock_query.return_value = fake_query()

        processor = SummaryProcessor(mock_registry, Path("/workspace"))
        session = _make_session()

        await processor.process(session, "Hello", "Hi there!")

        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.model == "opus"
        assert options.effort == "low"

    async def test_updates_summary_on_registry(self, mocker: pytest.MockerFixture) -> None:
        """AC: Summary is persisted to registry."""
        mock_query = mocker.patch("tachikoma.boundary.summary.query")
        mock_registry = MagicMock()
        mock_registry.update_summary = AsyncMock()

        expected_summary = "User discussed Python testing frameworks."
        async def fake_query(*args, **kwargs):
            yield AssistantMessage(
                content=[TextBlock(text=expected_summary)],
                model="claude-opus",
            )

        mock_query.return_value = fake_query()

        processor = SummaryProcessor(mock_registry, Path("/workspace"))
        session = _make_session()

        await processor.process(session, "Tell me about pytest", "Pytest is...")

        mock_registry.update_summary.assert_awaited_once_with(
            session.id, expected_summary
        )

    async def test_handles_none_previous_summary(self, mocker: pytest.MockerFixture) -> None:
        """AC: First exchange (None summary) is handled correctly."""
        mock_query = mocker.patch("tachikoma.boundary.summary.query")
        mock_registry = MagicMock()
        mock_registry.update_summary = AsyncMock()

        async def fake_query(*args, **kwargs):
            yield AssistantMessage(
                content=[TextBlock(text="First exchange summary")],
                model="claude-opus",
            )

        mock_query.return_value = fake_query()

        processor = SummaryProcessor(mock_registry, Path("/workspace"))
        session = _make_session(summary=None)

        await processor.process(session, "Hello", "Hi there!")

        # Verify the prompt was built with "No previous summary" text
        call_kwargs = mock_query.call_args
        prompt = call_kwargs[1]["prompt"]
        assert "No previous summary" in prompt

    async def test_uses_existing_summary_in_prompt(self, mocker: pytest.MockerFixture) -> None:
        """AC: Previous summary is included in the prompt for updates."""
        mock_query = mocker.patch("tachikoma.boundary.summary.query")
        mock_registry = MagicMock()
        mock_registry.update_summary = AsyncMock()

        async def fake_query(*args, **kwargs):
            yield AssistantMessage(
                content=[TextBlock(text="Updated summary")],
                model="claude-opus",
            )

        mock_query.return_value = fake_query()

        processor = SummaryProcessor(mock_registry, Path("/workspace"))
        existing_summary = "User discussed Python testing."
        session = _make_session(summary=existing_summary)

        await processor.process(session, "What about mocking?", "Mocking is...")

        # Verify the existing summary is in the prompt
        call_kwargs = mock_query.call_args
        prompt = call_kwargs[1]["prompt"]
        assert existing_summary in prompt

    async def test_uses_no_tools(self, mocker: pytest.MockerFixture) -> None:
        """AC: Summary processor uses no tools."""
        mock_query = mocker.patch("tachikoma.boundary.summary.query")
        mock_registry = MagicMock()
        mock_registry.update_summary = AsyncMock()

        async def fake_query(*args, **kwargs):
            yield AssistantMessage(
                content=[TextBlock(text="Summary")],
                model="claude-opus",
            )

        mock_query.return_value = fake_query()

        processor = SummaryProcessor(mock_registry, Path("/workspace"))
        session = _make_session()

        await processor.process(session, "Hello", "Hi there!")

        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.allowed_tools == []

    async def test_propagates_query_errors(self, mocker: pytest.MockerFixture) -> None:
        """AC: SDK errors propagate to pipeline for error isolation."""
        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.boundary.summary.query", side_effect=failing_query)

        mock_registry = MagicMock()
        mock_registry.update_summary = AsyncMock()

        processor = SummaryProcessor(mock_registry, Path("/workspace"))
        session = _make_session()

        with pytest.raises(RuntimeError, match="SDK error"):
            await processor.process(session, "Hello", "Hi there!")
