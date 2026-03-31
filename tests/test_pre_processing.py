"""Tests for pre-processing pipeline.

Tests for DLT-006: Pre-process messages with memory context injection.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import AgentDefinition

from tachikoma.pre_processing import (
    ContextProvider,
    ContextResult,
    PreProcessingPipeline,
    assemble_context,
)


class _FakeProvider(ContextProvider):
    """Concrete provider for testing - methods overridden per-test."""

    async def provide(self, message: str) -> ContextResult | None:
        return None


def _make_mock_provider() -> _FakeProvider:
    """Create a provider with mockable provide method."""
    provider = _FakeProvider()
    # Override the provide method with an AsyncMock
    provider.provide = AsyncMock()
    return provider


class TestContextResult:
    """Tests for ContextResult dataclass."""

    def test_valid_tag_creates_result(self) -> None:
        """AC: Valid tag creates ContextResult successfully."""
        result = ContextResult(tag="memories", content="Some content")

        assert result.tag == "memories"
        assert result.content == "Some content"

    def test_empty_tag_raises_value_error(self) -> None:
        """AC: Empty tag raises ValueError in __post_init__."""
        with pytest.raises(ValueError, match="must be non-empty"):
            ContextResult(tag="", content="Some content")

    def test_whitespace_tag_raises_value_error(self) -> None:
        """AC: Whitespace-only tag raises ValueError."""
        with pytest.raises(ValueError, match="must be non-empty"):
            ContextResult(tag="   ", content="Some content")

    def test_tag_with_space_raises_value_error(self) -> None:
        """AC: Tag with spaces raises ValueError (invalid XML tag)."""
        with pytest.raises(ValueError, match="valid XML tag name"):
            ContextResult(tag="my tag", content="Some content")

    def test_tag_starting_with_number_raises_value_error(self) -> None:
        """AC: Tag starting with number raises ValueError (invalid XML tag)."""
        with pytest.raises(ValueError, match="valid XML tag name"):
            ContextResult(tag="123tag", content="Some content")

    def test_tag_with_valid_underscore_prefix(self) -> None:
        """AC: Tag starting with underscore is valid."""
        result = ContextResult(tag="_private", content="content")
        assert result.tag == "_private"

    def test_tag_with_hyphen_is_valid(self) -> None:
        """AC: Tag containing hyphens is valid."""
        result = ContextResult(tag="my-context", content="content")
        assert result.tag == "my-context"

    def test_mcp_servers_defaults_to_none(self) -> None:
        """AC: mcp_servers field defaults to None for backward compatibility."""
        result = ContextResult(tag="test", content="content")
        assert result.mcp_servers is None

    def test_mcp_servers_can_be_set(self) -> None:
        """AC: mcp_servers can be set to a dict of server configs."""

        @tool("test_tool", "A test tool", {})
        async def test_tool(args: dict) -> dict:
            return {"content": [{"type": "text", "text": "ok"}]}

        server = create_sdk_mcp_server(name="test", version="1.0.0", tools=[test_tool])
        mcp_servers = {"test": server}

        result = ContextResult(tag="test", content="content", mcp_servers=mcp_servers)
        assert result.mcp_servers == mcp_servers
        # McpSdkServerConfig is a dict with "type" and "sdkServer" keys
        assert result.mcp_servers["test"]["type"] == "sdk"

    def test_agents_defaults_to_none(self) -> None:
        """AC: ContextResult without agents field defaults to None."""
        result = ContextResult(tag="memories", content="test")
        assert result.agents is None

    def test_agents_can_be_set(self) -> None:
        """AC: ContextResult with agents dict works correctly."""
        agents = {
            "test/agent": AgentDefinition(
                description="Test agent",
                prompt="A test prompt",
            ),
        }
        result = ContextResult(tag="skills", content="skill content", agents=agents)

        assert result.agents is not None
        assert "test/agent" in result.agents
        assert result.agents["test/agent"].description == "Test agent"

    async def test_existing_providers_still_work_without_agents(self) -> None:
        """AC: Providers returning ContextResult without agents continue working."""
        # This simulates how existing providers (like MemoryContextProvider)
        # create ContextResult without the agents field
        provider = _make_mock_provider()
        provider.provide.return_value = ContextResult(tag="memories", content="test")

        pipeline = PreProcessingPipeline()
        pipeline.register(provider)

        results = await pipeline.run("test")

        assert len(results) == 1
        assert results[0].agents is None


class TestPreProcessingPipeline:
    """Tests for PreProcessingPipeline."""

    async def test_runs_all_registered_providers(self) -> None:
        """AC: All registered providers are called with the message."""
        provider1 = _make_mock_provider()
        provider1.provide.return_value = ContextResult(tag="memories", content="test")
        provider2 = _make_mock_provider()
        provider2.provide.return_value = ContextResult(tag="skills", content="test2")

        pipeline = PreProcessingPipeline()
        pipeline.register(provider1)
        pipeline.register(provider2)

        results = await pipeline.run("hello world")

        provider1.provide.assert_awaited_once_with("hello world")
        provider2.provide.assert_awaited_once_with("hello world")
        assert len(results) == 2

    async def test_providers_run_in_parallel(self) -> None:
        """AC: Providers execute concurrently (not sequentially)."""
        call_order: list[str] = []

        async def slow_provide(message: str) -> ContextResult:
            call_order.append("slow_start")
            await asyncio.sleep(0.05)
            call_order.append("slow_end")
            return ContextResult(tag="slow", content="done")

        async def fast_provide(message: str) -> ContextResult:
            call_order.append("fast_start")
            await asyncio.sleep(0.01)
            call_order.append("fast_end")
            return ContextResult(tag="fast", content="done")

        slow_provider = _make_mock_provider()
        slow_provider.provide.side_effect = slow_provide
        fast_provider = _make_mock_provider()
        fast_provider.provide.side_effect = fast_provide

        pipeline = PreProcessingPipeline()
        pipeline.register(slow_provider)
        pipeline.register(fast_provider)

        await pipeline.run("test")

        # Both should have started before either finished (parallel execution)
        assert call_order.index("slow_start") < call_order.index("slow_end")
        assert call_order.index("fast_start") < call_order.index("fast_end")

    async def test_error_isolation_continues_other_providers(self) -> None:
        """AC: One provider failure doesn't prevent others from completing."""
        provider1 = _make_mock_provider()
        provider1.provide.side_effect = RuntimeError("failed")
        provider2 = _make_mock_provider()
        provider2.provide.return_value = ContextResult(tag="ok", content="success")

        pipeline = PreProcessingPipeline()
        pipeline.register(provider1)
        pipeline.register(provider2)

        results = await pipeline.run("test")

        # Both providers should have been called
        provider1.provide.assert_awaited_once()
        provider2.provide.assert_awaited_once()
        # Only successful result should be returned
        assert len(results) == 1
        assert results[0].tag == "ok"

    async def test_collects_successful_non_none_results(self) -> None:
        """AC: None results are filtered out."""
        provider1 = _make_mock_provider()
        provider1.provide.return_value = ContextResult(tag="memories", content="test")
        provider2 = _make_mock_provider()
        provider2.provide.return_value = None  # Provider returns None

        pipeline = PreProcessingPipeline()
        pipeline.register(provider1)
        pipeline.register(provider2)

        results = await pipeline.run("test")

        assert len(results) == 1
        assert results[0].tag == "memories"

    async def test_returns_empty_list_when_no_providers_registered(self) -> None:
        """AC: Empty pipeline returns empty list immediately."""
        pipeline = PreProcessingPipeline()

        results = await pipeline.run("test")

        assert results == []

    async def test_returns_empty_list_when_all_providers_fail(self) -> None:
        """AC: All providers failing results in empty list."""
        provider1 = _make_mock_provider()
        provider1.provide.side_effect = RuntimeError("error 1")
        provider2 = _make_mock_provider()
        provider2.provide.side_effect = RuntimeError("error 2")

        pipeline = PreProcessingPipeline()
        pipeline.register(provider1)
        pipeline.register(provider2)

        results = await pipeline.run("test")

        assert results == []

    async def test_returns_empty_list_when_all_providers_return_none(self) -> None:
        """AC: All providers returning None results in empty list."""
        provider1 = _make_mock_provider()
        provider1.provide.return_value = None
        provider2 = _make_mock_provider()
        provider2.provide.return_value = None

        pipeline = PreProcessingPipeline()
        pipeline.register(provider1)
        pipeline.register(provider2)

        results = await pipeline.run("test")

        assert results == []


class TestAssembleContext:
    """Tests for assemble_context function."""

    def test_wraps_results_in_xml_tags(self) -> None:
        """AC: Single result is wrapped in correct XML format."""
        results = [ContextResult(tag="memories", content="Some memories here")]
        message = "What was that restaurant?"

        enriched = assemble_context(results, message)

        assert "<memories>\nSome memories here\n</memories>" in enriched

    def test_multiple_results_joined_with_blank_lines(self) -> None:
        """AC: Multiple results are joined with blank lines."""
        results = [
            ContextResult(tag="memories", content="Memory content"),
            ContextResult(tag="skills", content="Skill content"),
        ]
        message = "Hello"

        enriched = assemble_context(results, message)

        # Check both blocks are present
        assert "<memories>" in enriched
        assert "</memories>" in enriched
        assert "<skills>" in enriched
        assert "</skills>" in enriched
        # Check message is at the end
        assert enriched.endswith("Hello")

    def test_returns_original_message_when_no_results(self) -> None:
        """AC: Empty results list returns original message unchanged."""
        results: list[ContextResult] = []
        message = "Just a message"

        enriched = assemble_context(results, message)

        assert enriched == message

    def test_prepends_context_before_message(self) -> None:
        """AC: Context blocks appear before the original message."""
        results = [ContextResult(tag="memories", content="Some context")]
        message = "User message"

        enriched = assemble_context(results, message)

        # Context should appear before the message
        memories_pos = enriched.find("<memories>")
        message_pos = enriched.find("User message")
        assert memories_pos < message_pos
