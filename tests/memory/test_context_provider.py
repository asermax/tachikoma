"""Tests for memory context provider.

Tests for DLT-006: Pre-process messages with memory context injection.
"""

from pathlib import Path

import pytest
from claude_agent_sdk.types import ResultMessage

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.context_provider import MEMORY_SEARCH_PROMPT, MemoryContextProvider
from tachikoma.pre_processing import ContextResult


def _make_query_result(result: str | None, is_error: bool = False):
    """Create an async generator that yields a ResultMessage."""

    async def gen():
        yield ResultMessage(
            subtype="error" if is_error else "success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=is_error,
            num_turns=1,
            session_id="test-session",
            total_cost_usd=0.01,
            usage={"input_tokens": 10},
            result=result,
        )

    return gen()


class TestMemoryContextProvider:
    """Tests for MemoryContextProvider."""

    async def test_calls_query_with_correct_options(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: query() called with correct model, effort, max_turns, allowed_tools, cwd."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        content = "## Relevant Memories\n1. **memories/test.md** — Test"
        mock_query.return_value = _make_query_result(content)

        cwd = Path("/workspace")
        provider = MemoryContextProvider(AgentDefaults(cwd=cwd))

        result = await provider.provide("What restaurant did I like?")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        options = call_kwargs["options"]

        assert options.model == "opus"
        assert options.effort == "low"
        assert options.max_turns == 8
        assert options.allowed_tools == ["Read", "Glob", "Grep"]
        assert options.permission_mode == "bypassPermissions"
        assert options.cwd == cwd
        assert result is not None

    async def test_returns_context_result_with_memories_tag(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Returns ContextResult with tag='memories' when memories found."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        content = "## Relevant Memories\n1. **memories/episodic/2026-03-13.md** — Summary"
        mock_query.return_value = _make_query_result(content)

        provider = MemoryContextProvider(AgentDefaults(cwd=Path("/workspace")))
        result = await provider.provide("Tell me about yesterday")

        assert result is not None
        assert isinstance(result, ContextResult)
        assert result.tag == "memories"
        assert "## Relevant Memories" in result.content

    async def test_returns_none_when_no_relevant_memories(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Returns None when result contains NO_RELEVANT_MEMORIES."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        mock_query.return_value = _make_query_result("NO_RELEVANT_MEMORIES")

        provider = MemoryContextProvider(AgentDefaults(cwd=Path("/workspace")))
        result = await provider.provide("What's the weather?")

        assert result is None

    async def test_returns_none_when_result_is_error(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Returns None when ResultMessage has is_error=True."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        mock_query.return_value = _make_query_result("Error occurred", is_error=True)

        provider = MemoryContextProvider(AgentDefaults(cwd=Path("/workspace")))
        result = await provider.provide("Hello")

        assert result is None

    async def test_returns_none_when_result_field_is_none(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Returns None when ResultMessage.result is None."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        mock_query.return_value = _make_query_result(None)

        provider = MemoryContextProvider(AgentDefaults(cwd=Path("/workspace")))
        result = await provider.provide("Hello")

        assert result is None

    async def test_embeds_user_message_in_prompt(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: The prompt passed to query() contains the user's message text."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        mock_query.return_value = _make_query_result("NO_RELEVANT_MEMORIES")

        provider = MemoryContextProvider(AgentDefaults(cwd=Path("/workspace")))
        await provider.provide("What was that restaurant I liked?")

        call_kwargs = mock_query.call_args[1]
        prompt = call_kwargs["prompt"]

        assert "What was that restaurant I liked?" in prompt

    async def test_returns_none_on_exception(self, mocker: pytest.MockerFixture) -> None:
        """AC: Returns None and logs exception when query raises."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.side_effect = RuntimeError("SDK error")

        provider = MemoryContextProvider(AgentDefaults(cwd=Path("/workspace")))
        result = await provider.provide("Hello")

        assert result is None


class TestMemorySearchPrompt:
    """Tests for MEMORY_SEARCH_PROMPT constant."""

    def test_prompt_references_memory_directories(self) -> None:
        """AC: Prompt mentions episodic, facts, and preferences directories."""
        assert "memories/episodic" in MEMORY_SEARCH_PROMPT
        assert "memories/facts" in MEMORY_SEARCH_PROMPT
        assert "memories/preferences" in MEMORY_SEARCH_PROMPT

    def test_prompt_instructs_ranked_list_format(self) -> None:
        """AC: Prompt mentions ranking and file paths."""
        assert "ranked" in MEMORY_SEARCH_PROMPT.lower()
        assert "file path" in MEMORY_SEARCH_PROMPT.lower() or "path" in MEMORY_SEARCH_PROMPT.lower()

    def test_prompt_instructs_no_relevant_memories_sentinel(self) -> None:
        """AC: Prompt mentions NO_RELEVANT_MEMORIES sentinel."""
        assert "NO_RELEVANT_MEMORIES" in MEMORY_SEARCH_PROMPT

    def test_prompt_handles_empty_directories(self) -> None:
        """AC: Prompt instructs that empty directories should return NO_RELEVANT_MEMORIES."""
        assert "empty" in MEMORY_SEARCH_PROMPT.lower()

    def test_prompt_includes_message_placeholder(self) -> None:
        """AC: Prompt has {message} placeholder for embedding user message."""
        assert "{message}" in MEMORY_SEARCH_PROMPT
