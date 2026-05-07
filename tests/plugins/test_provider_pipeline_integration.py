"""Tests for plugin provider runtime pipeline integration.

Covers acceptance criteria from Pipeline Registration (R2, R4, R7):
- Plugin providers participate in pipeline execution
- Error isolation: one plugin provider failure doesn't affect others (DES-002)
- Providers return full ContextResult with mcp_servers, agents, metadata
"""

from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import AgentDefinition

from tachikoma.pre_processing import ContextProvider, ContextResult, PreProcessingPipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _PluginProvider(ContextProvider):
    """A plugin-style provider for testing."""

    def __init__(self, *, config):
        self.config = config
        self._result: ContextResult | None = None

    def set_result(self, result: ContextResult | None) -> None:
        self._result = result

    async def provide(self, message: str) -> ContextResult | None:
        return self._result

    def status_message(self, result: ContextResult | None = None) -> str:
        return "Plugin provider"


class _BuiltinProvider(ContextProvider):
    """Simulates a built-in provider."""

    def __init__(self, tag: str = "builtin"):
        self._tag = tag

    async def provide(self, message: str) -> ContextResult | None:
        return ContextResult(tag=self._tag, content="built-in data")

    def status_message(self, result: ContextResult | None = None) -> str:
        return "Built-in provider"


class _FailingProvider(ContextProvider):
    """A provider that raises during provide()."""

    async def provide(self, message: str) -> ContextResult | None:
        raise RuntimeError("Plugin provider boom")

    def status_message(self, result: ContextResult | None = None) -> str:
        return "Failing provider"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProviderPipelineIntegration:
    """Plugin providers participate in pipeline runs."""

    async def test_plugin_provider_result_collected(self) -> None:
        """R7: Plugin provider returns ContextResult — collected by pipeline."""
        plugin_provider = _PluginProvider(config={"key": "val"})
        plugin_provider.set_result(ContextResult(tag="calendar", content="Meeting at 3pm"))

        pipeline = PreProcessingPipeline()
        pipeline.register(plugin_provider)

        results = await pipeline.run("What's on my calendar?")
        assert len(results) == 1
        assert results[0].tag == "calendar"
        assert "Meeting at 3pm" in results[0].content

    async def test_error_isolation_per_des002(self) -> None:
        """R4: Plugin provider failure isolated — other providers complete normally."""
        failing = _FailingProvider()
        builtin = _BuiltinProvider(tag="projects")
        plugin_provider = _PluginProvider(config={})
        plugin_provider.set_result(ContextResult(tag="crm", content="CRM data"))

        pipeline = PreProcessingPipeline()
        pipeline.register(failing)
        pipeline.register(builtin)
        pipeline.register(plugin_provider)

        results = await pipeline.run("hello")
        assert len(results) == 2
        tags = {r.tag for r in results}
        assert tags == {"projects", "crm"}

    async def test_plugin_and_builtin_providers_concurrent(self) -> None:
        """R4: Plugin and built-in providers run concurrently."""
        plugin_provider = _PluginProvider(config={})
        plugin_provider.set_result(ContextResult(tag="plugin", content="plugin data"))
        builtin = _BuiltinProvider(tag="builtin")

        pipeline = PreProcessingPipeline()
        pipeline.register(builtin)
        pipeline.register(plugin_provider)

        results = await pipeline.run("test")
        assert len(results) == 2
        tags = {r.tag for r in results}
        assert tags == {"builtin", "plugin"}

    async def test_mcp_servers_in_context_result(self) -> None:
        """R7: Plugin provider returns ContextResult with mcp_servers."""

        @tool("test_tool", "A test tool", {})
        async def test_tool(args: dict) -> dict:
            return {"content": [{"type": "text", "text": "ok"}]}

        server = create_sdk_mcp_server(name="test", version="1.0.0", tools=[test_tool])
        mcp_servers = {"test": server}

        plugin_provider = _PluginProvider(config={})
        plugin_provider.set_result(
            ContextResult(tag="tools", content="Available tools", mcp_servers=mcp_servers)
        )

        pipeline = PreProcessingPipeline()
        pipeline.register(plugin_provider)

        results = await pipeline.run("hello")
        assert len(results) == 1
        assert results[0].mcp_servers is not None
        assert "test" in results[0].mcp_servers

    async def test_agents_in_context_result(self) -> None:
        """R7: Plugin provider returns ContextResult with agents."""
        agents = {
            "test/agent": AgentDefinition(
                description="Test agent",
                prompt="A test prompt",
            ),
        }
        plugin_provider = _PluginProvider(config={})
        plugin_provider.set_result(
            ContextResult(tag="agents", content="Available agents", agents=agents)
        )

        pipeline = PreProcessingPipeline()
        pipeline.register(plugin_provider)

        results = await pipeline.run("hello")
        assert len(results) == 1
        assert results[0].agents is not None
        assert "test/agent" in results[0].agents

    async def test_metadata_in_context_result(self) -> None:
        """R7: Plugin provider returns ContextResult with metadata."""
        metadata = {"source": "plugin", "version": "1.0"}
        plugin_provider = _PluginProvider(config={})
        plugin_provider.set_result(
            ContextResult(tag="data", content="Some data", metadata=metadata)
        )

        pipeline = PreProcessingPipeline()
        pipeline.register(plugin_provider)

        results = await pipeline.run("hello")
        assert len(results) == 1
        assert results[0].metadata == metadata

    async def test_multiple_plugin_providers_isolated_errors(self) -> None:
        """R4: Multiple plugin providers — one fails, others succeed."""
        failing = _FailingProvider()
        good1 = _PluginProvider(config={})
        good1.set_result(ContextResult(tag="good1", content="data1"))
        good2 = _PluginProvider(config={})
        good2.set_result(ContextResult(tag="good2", content="data2"))

        pipeline = PreProcessingPipeline()
        pipeline.register(failing)
        pipeline.register(good1)
        pipeline.register(good2)

        results = await pipeline.run("test")
        assert len(results) == 2
        tags = {r.tag for r in results}
        assert tags == {"good1", "good2"}
