"""Tests for boundary detection.

Tests for DLT-026: Detect conversation boundaries via topic analysis.
Tests for DLT-028: Resume conversation on topic revisit.
"""

from pathlib import Path

import pytest
from claude_agent_sdk.types import ResultMessage

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.boundary.detector import (
    BoundaryResult,
    SessionCandidate,
    detect_boundary,
)


class TestSessionCandidate:
    """Tests for SessionCandidate dataclass."""

    def test_instantiates_with_required_fields(self) -> None:
        """AC: SessionCandidate has id and summary fields."""
        candidate = SessionCandidate(id="session-123", summary="Discussion about Python")

        assert candidate.id == "session-123"
        assert candidate.summary == "Discussion about Python"

    def test_is_frozen(self) -> None:
        """AC: SessionCandidate is immutable."""
        candidate = SessionCandidate(id="session-123", summary="Summary")

        with pytest.raises(AttributeError):
            candidate.id = "new-id"


class TestBoundaryResult:
    """Tests for BoundaryResult dataclass."""

    def test_instantiates_with_continues_only(self) -> None:
        """AC: BoundaryResult can be created with just continues field."""
        result = BoundaryResult(continues=True)

        assert result.continues is True
        assert result.resume_session_id is None

    def test_instantiates_with_all_fields(self) -> None:
        """AC: BoundaryResult can include resume_session_id."""
        result = BoundaryResult(continues=False, resume_session_id="session-456")

        assert result.continues is False
        assert result.resume_session_id == "session-456"

    def test_is_frozen(self) -> None:
        """AC: BoundaryResult is immutable."""
        result = BoundaryResult(continues=True)

        with pytest.raises(AttributeError):
            result.continues = False


class TestDetectBoundary:
    """Tests for detect_boundary() function."""

    async def test_returns_boundary_result_for_continuation(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Continuation returns BoundaryResult with continues=True."""

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
                structured_output={"continues_conversation": True, "resume_session_id": None},
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="Tell me more about that",
            summary="User is asking about Python testing.",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        assert isinstance(result, BoundaryResult)
        assert result.continues is True
        assert result.resume_session_id is None

    async def test_returns_boundary_result_for_topic_shift(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Topic shift returns BoundaryResult with continues=False."""

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
                structured_output={"continues_conversation": False, "resume_session_id": None},
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="What should I cook for dinner?",
            summary="User is discussing Python testing frameworks.",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        assert isinstance(result, BoundaryResult)
        assert result.continues is False
        assert result.resume_session_id is None

    async def test_returns_resume_session_id_when_match_found(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Topic shift with matching candidate returns resume_session_id."""

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
                structured_output={
                    "continues_conversation": False,
                    "resume_session_id": "session-123",
                },
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        candidates = [
            SessionCandidate(id="session-123", summary="Discussion about Python debugging"),
        ]

        result = await detect_boundary(
            message="Remember that Python debugging we did?",
            summary="User is discussing meal planning.",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            candidates=candidates,
        )

        assert isinstance(result, BoundaryResult)
        assert result.continues is False
        assert result.resume_session_id == "session-123"

    async def test_returns_none_resume_id_when_no_match(self, mocker: pytest.MockerFixture) -> None:
        """AC: Topic shift without match returns resume_session_id=None."""

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
                structured_output={
                    "continues_conversation": False,
                    "resume_session_id": None,
                },
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        candidates = [
            SessionCandidate(id="session-123", summary="Discussion about cooking"),
        ]

        result = await detect_boundary(
            message="Let's talk about space exploration",
            summary="User is discussing Python testing.",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            candidates=candidates,
        )

        assert isinstance(result, BoundaryResult)
        assert result.continues is False
        assert result.resume_session_id is None

    async def test_with_empty_candidates_list(self, mocker: pytest.MockerFixture) -> None:
        """AC: Empty candidates list works correctly."""

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
                structured_output={"continues_conversation": True, "resume_session_id": None},
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="Hello",
            summary="Test summary",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            candidates=[],
        )

        assert result.continues is True
        assert result.resume_session_id is None

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
                structured_output={"continues_conversation": True, "resume_session_id": None},
            )

        mock_query.return_value = fake_query_gen()

        await detect_boundary(
            message="Hello",
            summary="Test summary",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        # Verify the options include opus model with low effort
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.model == "opus"
        assert options.effort == "low"

    async def test_passes_json_schema_output_format_with_resume_field(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Uses JSON schema with continues_conversation and resume_session_id."""
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
                structured_output={"continues_conversation": True, "resume_session_id": None},
            )

        mock_query.return_value = fake_query_gen()

        await detect_boundary(
            message="Hello",
            summary="Test summary",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.output_format is not None
        assert options.output_format["type"] == "json_schema"
        assert "continues_conversation" in options.output_format["schema"]["properties"]
        assert "resume_session_id" in options.output_format["schema"]["properties"]

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
                agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            )

    async def test_defaults_to_continuation_when_no_structured_output(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Returns BoundaryResult(continues=True) when structured_output is None."""

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
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        assert result.continues is True
        assert result.resume_session_id is None

    async def test_defaults_to_continuation_when_no_result_message(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Returns BoundaryResult(continues=True) when no ResultMessage received."""

        async def fake_query(*args, **kwargs):
            # Yield nothing - no messages
            return
            yield  # make it a generator

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="Hello",
            summary="Test summary",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        assert result.continues is True
        assert result.resume_session_id is None

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
                structured_output={"continues_conversation": True, "resume_session_id": None},
            )

        mock_query.return_value = fake_query_gen()

        cwd = Path("/custom/workspace")
        await detect_boundary(
            message="Hello",
            summary="Test summary",
            agent_defaults=AgentDefaults(cwd=cwd),
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
                structured_output={"continues_conversation": True, "resume_session_id": None},
            )

        mock_query.return_value = fake_query_gen()

        await detect_boundary(
            message="Hello",
            summary="Test summary",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.allowed_tools == []
        assert options.max_turns == 3
        assert options.permission_mode is None

    async def test_includes_candidates_in_prompt_when_provided(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Candidates are formatted into the user prompt."""
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
                structured_output={"continues_conversation": True, "resume_session_id": None},
            )

        mock_query.return_value = fake_query_gen()

        candidates = [
            SessionCandidate(id="session-123", summary="Discussion about Python"),
        ]

        await detect_boundary(
            message="Hello",
            summary="Test summary",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            candidates=candidates,
        )

        call_kwargs = mock_query.call_args
        prompt = call_kwargs[1]["prompt"]
        assert "session-123" in prompt
        assert "Discussion about Python" in prompt

    async def test_does_not_include_candidates_when_not_provided(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Candidates section is not added when no candidates provided."""
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
                structured_output={"continues_conversation": True, "resume_session_id": None},
            )

        mock_query.return_value = fake_query_gen()

        await detect_boundary(
            message="Hello",
            summary="Test summary",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        call_kwargs = mock_query.call_args
        prompt = call_kwargs[1]["prompt"]
        assert "Previous Session Candidates" not in prompt

    async def test_empty_string_resume_id_treated_as_none(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Empty string resume_session_id is converted to None."""

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
                structured_output={
                    "continues_conversation": False,
                    "resume_session_id": "",
                },
            )

        mocker.patch("tachikoma.boundary.detector.query", side_effect=fake_query)

        result = await detect_boundary(
            message="New topic",
            summary="Old topic",
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        )

        assert result.resume_session_id is None
