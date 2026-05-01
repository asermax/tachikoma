"""Tests for LastExchangeProcessor.

Include last exchange in session resumption candidates.
Filter last exchange to final text response only.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tachikoma.boundary.last_exchange import LastExchangeProcessor
from tachikoma.sessions.model import Session


def _make_session(last_exchange: str | None = None) -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        last_exchange=last_exchange,
    )


class TestLastExchangeProcessor:
    """Tests for LastExchangeProcessor."""

    async def test_updates_last_exchange_on_valid_response(self) -> None:
        """AC: agent_response is persisted to registry.update_last_exchange."""
        mock_registry = MagicMock()
        mock_registry.update_last_exchange = AsyncMock()

        processor = LastExchangeProcessor(mock_registry)
        session = _make_session()

        await processor.process(session, "Hello", "Hi there!")

        mock_registry.update_last_exchange.assert_awaited_once_with(session.id, "Hi there!")

    async def test_skips_update_on_empty_response(self) -> None:
        """AC: empty response is skipped, previous value preserved."""
        mock_registry = MagicMock()
        mock_registry.update_last_exchange = AsyncMock()

        processor = LastExchangeProcessor(mock_registry)
        session = _make_session(last_exchange="Previous response")

        await processor.process(session, "Hello", "")

        mock_registry.update_last_exchange.assert_not_awaited()

    async def test_skips_update_on_whitespace_only_response(self) -> None:
        """AC: whitespace-only response is skipped, previous value preserved."""
        mock_registry = MagicMock()
        mock_registry.update_last_exchange = AsyncMock()

        processor = LastExchangeProcessor(mock_registry)
        session = _make_session(last_exchange="Previous response")

        await processor.process(session, "Hello", "   \n\t  ")

        mock_registry.update_last_exchange.assert_not_awaited()

    async def test_propagates_registry_errors(self) -> None:
        """AC: registry errors propagate to pipeline for return_exceptions=True isolation."""
        mock_registry = MagicMock()
        mock_registry.update_last_exchange = AsyncMock(side_effect=RuntimeError("DB error"))

        processor = LastExchangeProcessor(mock_registry)
        session = _make_session()

        with pytest.raises(RuntimeError, match="DB error"):
            await processor.process(session, "Hello", "Response")

    async def test_uses_final_text_when_present(self) -> None:
        """AC1: final_text is used instead of agent_response when provided."""
        mock_registry = MagicMock()
        mock_registry.update_last_exchange = AsyncMock()

        processor = LastExchangeProcessor(mock_registry)
        session = _make_session()

        await processor.process(
            session,
            "Hello",
            "Let me check...Found it!",
            final_text="Found it!",
        )

        mock_registry.update_last_exchange.assert_awaited_once_with(session.id, "Found it!")

    async def test_falls_back_to_full_response_when_final_text_is_none(self) -> None:
        """AC2: agent_response is used when final_text is None (no tool calls)."""
        mock_registry = MagicMock()
        mock_registry.update_last_exchange = AsyncMock()

        processor = LastExchangeProcessor(mock_registry)
        session = _make_session()

        await processor.process(session, "Hello", "Full response here")

        mock_registry.update_last_exchange.assert_awaited_once_with(
            session.id,
            "Full response here",
        )

    async def test_falls_back_to_full_response_when_final_text_is_empty(self) -> None:
        """AC3: agent_response is used when final_text is empty (tool call, no trailing text)."""
        mock_registry = MagicMock()
        mock_registry.update_last_exchange = AsyncMock()

        processor = LastExchangeProcessor(mock_registry)
        session = _make_session()

        await processor.process(
            session,
            "Hello",
            "Let me check...Done!",
            final_text="",
        )

        mock_registry.update_last_exchange.assert_awaited_once_with(
            session.id,
            "Let me check...Done!",
        )
