"""Tests for LastExchangeProcessor.

Tests for DLT-096: Include last exchange in session resumption candidates.
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

        mock_registry.update_last_exchange.assert_awaited_once_with(
            session.id, "Hi there!"
        )

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
