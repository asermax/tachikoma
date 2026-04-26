"""Tests for workflow condition evaluator."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.workflows.conditions import (
    ConditionResult,
    _build_evaluation_prompt,
    _parse_evaluation_response,
    evaluate_condition,
)

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestBuildEvaluationPrompt:
    def test_includes_condition_and_context(self):
        prompt = _build_evaluation_prompt(
            condition_prompt="Only run if output.md exists",
            step_states={"01-plan": "completed", "02-execute": "pending"},
            scratchpad_path="/tmp/scratch.md",
        )

        assert "Only run if output.md exists" in prompt
        assert "/tmp/scratch.md" in prompt
        assert "01-plan" in prompt
        assert "completed" in prompt
        assert '"passes"' in prompt

    def test_json_in_step_states_is_valid(self):
        step_states = {"01-plan": "completed"}
        prompt = _build_evaluation_prompt(
            "test condition", step_states, "/tmp/scratch.md"
        )

        # Extract the JSON from the prompt
        for line in prompt.splitlines():
            if line.startswith("- Step states:"):
                json_str = line.split(": ", 1)[1]
                parsed = json.loads(json_str)
                assert parsed == step_states


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseEvaluationResponse:
    def test_valid_passes_true(self):
        result = _parse_evaluation_response('{"passes": true, "reason": "file exists"}')
        assert result == ConditionResult(passes=True, is_error=False, reason="file exists")

    def test_valid_passes_false(self):
        result = _parse_evaluation_response('{"passes": false, "reason": "file missing"}')
        assert result == ConditionResult(passes=False, is_error=False, reason="file missing")

    def test_missing_reason_defaults_to_empty(self):
        result = _parse_evaluation_response('{"passes": true}')
        assert result == ConditionResult(passes=True, is_error=False, reason="")

    def test_non_json_returns_error(self):
        result = _parse_evaluation_response("this is not json")
        assert result.is_error is True
        assert result.passes is False
        assert "Non-JSON" in result.reason

    def test_json_array_returns_error(self):
        result = _parse_evaluation_response("[1, 2, 3]")
        assert result.is_error is True
        assert "Expected JSON object" in result.reason

    def test_missing_passes_field_returns_error(self):
        result = _parse_evaluation_response('{"reason": "no passes field"}')
        assert result.is_error is True
        assert "Missing or non-boolean" in result.reason

    def test_non_boolean_passes_returns_error(self):
        result = _parse_evaluation_response('{"passes": "yes", "reason": "wrong type"}')
        assert result.is_error is True
        assert "Missing or non-boolean" in result.reason

    def test_markdown_wrapped_json(self):
        raw = '```json\n{"passes": true, "reason": "ok"}\n```'
        result = _parse_evaluation_response(raw)
        assert result == ConditionResult(passes=True, is_error=False, reason="ok")

    def test_empty_string_returns_error(self):
        result = _parse_evaluation_response("")
        assert result.is_error is True
        assert result.passes is False

    def test_whitespace_only_returns_error(self):
        result = _parse_evaluation_response("   \n  ")
        assert result.is_error is True


# ---------------------------------------------------------------------------
# evaluate_condition (with mocked fork_and_capture)
# ---------------------------------------------------------------------------


def _make_defaults() -> AgentDefaults:
    return AgentDefaults(
        cwd=Path("/tmp/test-workspace"),
        cli_path=Path("/usr/local/bin/claude"),
        env={},
    )


class TestEvaluateCondition:
    @pytest.mark.asyncio
    async def test_no_session_id_assumes_passes(self):
        result = await evaluate_condition(
            condition_prompt="test",
            step_states={},
            scratchpad_path="/tmp/scratch.md",
            workspace_path=Path("/tmp"),
            agent_defaults=_make_defaults(),
            sdk_session_id=None,
        )

        assert result.passes is True
        assert result.is_error is False
        assert "No session" in result.reason

    @pytest.mark.asyncio
    @patch("tachikoma.workflows.conditions.fork_and_capture", new_callable=AsyncMock)
    async def test_valid_passes_true_response(self, mock_capture):
        mock_capture.return_value = '{"passes": true, "reason": "found it"}'

        result = await evaluate_condition(
            condition_prompt="Check if output.md exists",
            step_states={"01-plan": "completed"},
            scratchpad_path="/tmp/scratch.md",
            workspace_path=Path("/tmp"),
            agent_defaults=_make_defaults(),
            sdk_session_id="sess-123",
        )

        assert result == ConditionResult(passes=True, is_error=False, reason="found it")
        mock_capture.assert_called_once()

    @pytest.mark.asyncio
    @patch("tachikoma.workflows.conditions.fork_and_capture", new_callable=AsyncMock)
    async def test_valid_passes_false_response(self, mock_capture):
        mock_capture.return_value = '{"passes": false, "reason": "no file"}'

        result = await evaluate_condition(
            condition_prompt="Check if output.md exists",
            step_states={},
            scratchpad_path="/tmp/scratch.md",
            workspace_path=Path("/tmp"),
            agent_defaults=_make_defaults(),
            sdk_session_id="sess-123",
        )

        assert result == ConditionResult(passes=False, is_error=False, reason="no file")

    @pytest.mark.asyncio
    @patch("tachikoma.workflows.conditions.fork_and_capture", new_callable=AsyncMock)
    async def test_empty_response_returns_error(self, mock_capture):
        mock_capture.return_value = ""

        result = await evaluate_condition(
            condition_prompt="test",
            step_states={},
            scratchpad_path="/tmp/scratch.md",
            workspace_path=Path("/tmp"),
            agent_defaults=_make_defaults(),
            sdk_session_id="sess-123",
        )

        assert result.is_error is True
        assert "empty response" in result.reason

    @pytest.mark.asyncio
    @patch("tachikoma.workflows.conditions.fork_and_capture", new_callable=AsyncMock)
    async def test_sdk_error_returns_error(self, mock_capture):
        mock_capture.side_effect = RuntimeError("SDK timeout")

        result = await evaluate_condition(
            condition_prompt="test",
            step_states={},
            scratchpad_path="/tmp/scratch.md",
            workspace_path=Path("/tmp"),
            agent_defaults=_make_defaults(),
            sdk_session_id="sess-123",
        )

        assert result.is_error is True
        assert "SDK timeout" in result.reason

    @pytest.mark.asyncio
    @patch("tachikoma.workflows.conditions.fork_and_capture", new_callable=AsyncMock)
    async def test_non_json_response_returns_error(self, mock_capture):
        mock_capture.return_value = "I think the condition is met."

        result = await evaluate_condition(
            condition_prompt="test",
            step_states={},
            scratchpad_path="/tmp/scratch.md",
            workspace_path=Path("/tmp"),
            agent_defaults=_make_defaults(),
            sdk_session_id="sess-123",
        )

        assert result.is_error is True
        assert "Non-JSON" in result.reason

    @pytest.mark.asyncio
    @patch("tachikoma.workflows.conditions.fork_and_capture", new_callable=AsyncMock)
    async def test_uses_processor_model(self, mock_capture):
        mock_capture.return_value = '{"passes": true, "reason": "ok"}'

        defaults = AgentDefaults(
            cwd=Path("/tmp"),
            cli_path=Path("/usr/bin/claude"),
            env={},
            processor_model="haiku-test",
        )

        await evaluate_condition(
            condition_prompt="test",
            step_states={},
            scratchpad_path="/tmp/scratch.md",
            workspace_path=Path("/tmp"),
            agent_defaults=defaults,
            sdk_session_id="sess-123",
        )

        call_kwargs = mock_capture.call_args
        assert call_kwargs.kwargs.get("model") == "haiku-test" or (
            call_kwargs[1] if len(call_kwargs.args) > 1 else None
        )
