"""Tests for boundary detection.

Tests for DLT-026: Detect conversation boundaries via topic analysis.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk.types import ResultMessage

from tachikoma.boundary.detector import detect_boundary


class TestDetectBoundary:
    """Tests for detect_boundary() function."""

    async def test_returns_true_for_continuation(self, mocker: pytest.MockerFixture) -> None:
        """AC: Continuation returns True."""
        # Mock the query to return a continuation result
        async def fake_query(*args, **kwargs):
            yield ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=80,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.01,
                usage={"input_tokens": 10},
                structured_output={"continues_conversation": True},
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="Tell me more about that",
            summary="User is asking about Python testing.",
            cwd=Path("/workspace"),
        )

        assert result is True

    async def test_returns_false_for_topic_shift(self, mocker: pytest.MockerFixture) -> None:
        """AC: Topic shift returns False."""
        async def fake_query(*args, **kwargs):
            yield ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=80,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.01,
                usage={"input_tokens": 10},
                structured_output={"continues_conversation": False},
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="What should I cook for dinner?",
            summary="User is discussing Python testing frameworks.",
            cwd=Path("/workspace"),
        )

        assert result is False

    async def test_passes_opus_low_effort_model_to_options(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Uses Opus model with low effort for fast, reliable classification."""
        mock_query = mocker.patch("tachikoma.boundary.detector.query")

        async def fake_query_gen(*args, **kwargs):
            yield ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=80,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.01,
                usage={"input_tokens": 10},
                structured_output={"continues_conversation": True},
            )

        mock_query.return_value = fake_query_gen()

        await detect_boundary(
            message="Hello",
            summary="Test summary",
            cwd=Path("/workspace"),
        )

        # Verify the options include opus model with low effort
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.model == "opus"
        assert options.effort == "low"

    async def test_passes_json_schema_output_format(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Uses JSON schema for reliable parsing."""
        mock_query = mocker.patch("tachikoma.boundary.detector.query")

        async def fake_query_gen(*args, **kwargs):
            yield ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=80,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.01,
                usage={"input_tokens": 10},
                structured_output={"continues_conversation": True},
            )

        mock_query.return_value = fake_query_gen()

        await detect_boundary(
            message="Hello",
            summary="Test summary",
            cwd=Path("/workspace"),
        )

        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.output_format is not None
        assert options.output_format["type"] == "json_schema"
        assert "continues_conversation" in options.output_format["schema"]["properties"]

    async def test_propagates_query_errors(self, mocker: pytest.MockerFixture) -> None:
        """AC: SDK errors propagate (coordinator handles fail-open)."""
        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.boundary.detector.query", side_effect=failing_query)

        with pytest.raises(RuntimeError, match="SDK error"):
            await detect_boundary(
                message="Hello",
                summary="Test summary",
                cwd=Path("/workspace"),
            )

    async def test_defaults_to_continuation_when_no_structured_output(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Returns True (continuation) when structured_output is None."""
        async def fake_query(*args, **kwargs):
            yield ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=80,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.01,
                usage={"input_tokens": 10},
                structured_output=None,
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="Hello",
            summary="Test summary",
            cwd=Path("/workspace"),
        )

        assert result is True

    async def test_defaults_to_continuation_when_no_result_message(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Returns True (continuation) when no ResultMessage received."""
        async def fake_query(*args, **kwargs):
            # Yield nothing - no messages
            return
            yield  # make it a generator

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="Hello",
            summary="Test summary",
            cwd=Path("/workspace"),
        )

        assert result is True

    async def test_passes_cwd_to_options(self, mocker: pytest.MockerFixture) -> None:
        """AC: Working directory is passed to SDK options."""
        mock_query = mocker.patch("tachikoma.boundary.detector.query")

        async def fake_query_gen(*args, **kwargs):
            yield ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=80,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.01,
                usage={"input_tokens": 10},
                structured_output={"continues_conversation": True},
            )

        mock_query.return_value = fake_query_gen()

        cwd = Path("/custom/workspace")
        await detect_boundary(
            message="Hello",
            summary="Test summary",
            cwd=cwd,
        )

        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.cwd == cwd

    async def test_uses_no_tools(self, mocker: pytest.MockerFixture) -> None:
        """AC: Detection uses no tools for fast inference."""
        mock_query = mocker.patch("tachikoma.boundary.detector.query")

        async def fake_query_gen(*args, **kwargs):
            yield ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=80,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.01,
                usage={"input_tokens": 10},
                structured_output={"continues_conversation": True},
            )

        mock_query.return_value = fake_query_gen()

        await detect_boundary(
            message="Hello",
            summary="Test summary",
            cwd=Path("/workspace"),
        )

        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.allowed_tools == []
