"""Tests for per-message pre-processing pipeline."""

from unittest.mock import AsyncMock, MagicMock

from claude_agent_sdk.types import AgentDefinition

from tachikoma.per_message_pre_processing import (
    MessageContextProvider,
    MessagePreProcessingPipeline,
)
from tachikoma.pre_processing import ContextResult
from tachikoma.sessions.model import SessionContextEntry
from tachikoma.skills.context_provider import (
    derive_agents_from_entries,
    extract_skill_names,
)


def _make_entry(
    owner: str = "skills",
    content: str = "test",
    metadata: dict | None = None,
) -> SessionContextEntry:
    """Create a test context entry."""
    return SessionContextEntry(
        id=1,
        session_id="s1",
        owner=owner,
        content=content,
        metadata=metadata,
    )


class TestExtractSkillNames:
    """Tests for extract_skill_names helper."""

    def test_empty_entries_returns_empty_set(self) -> None:
        """AC: Empty entries returns empty set."""
        assert extract_skill_names([]) == set()

    def test_entries_without_metadata_return_empty_set(self) -> None:
        """AC: Entries with None metadata are skipped."""
        entries = [_make_entry(metadata=None)]
        assert extract_skill_names(entries) == set()

    def test_extracts_skill_names_from_metadata(self) -> None:
        """AC: Extracts skill_name from metadata where owner='skills'."""
        entries = [
            _make_entry(metadata={"skill_name": "skill-a"}),
            _make_entry(metadata={"skill_name": "skill-b"}),
        ]
        assert extract_skill_names(entries) == {"skill-a", "skill-b"}

    def test_ignores_non_skills_owner(self) -> None:
        """AC: Entries with owner != 'skills' are ignored."""
        entries = [
            _make_entry(owner="memories", metadata={"skill_name": "should-not-appear"}),
            _make_entry(owner="skills", metadata={"skill_name": "should-appear"}),
        ]
        assert extract_skill_names(entries) == {"should-appear"}

    def test_ignores_entries_without_skill_name_in_metadata(self) -> None:
        """AC: Entries with metadata but no skill_name key are skipped."""
        entries = [
            _make_entry(metadata={"other_key": "value"}),
            _make_entry(metadata={"skill_name": "visible"}),
        ]
        assert extract_skill_names(entries) == {"visible"}

    def test_mixed_entries(self) -> None:
        """AC: Correctly handles mix of all entry types."""
        entries = [
            _make_entry(owner="memories", content="some memory", metadata=None),
            _make_entry(owner="skills", metadata={"skill_name": "a"}),
            _make_entry(owner="skills", metadata=None),
            _make_entry(owner="skills", metadata={"skill_name": "b"}),
            _make_entry(owner="foundational", content="soul", metadata=None),
        ]
        assert extract_skill_names(entries) == {"a", "b"}


class TestDeriveAgentsFromEntries:
    """Tests for derive_agents_from_entries helper."""

    def _make_mock_registry(self, skill_agents: dict[str, dict] | None = None):
        """Create a mock SkillRegistry.

        Args:
            skill_agents: Map of skill_name -> {agent_ns: AgentDefinition}
        """
        registry = MagicMock()
        agents_map = skill_agents or {}

        def get_agents_for_skill(name):
            return agents_map.get(name, {})

        registry.get_agents_for_skill = get_agents_for_skill
        registry.skills = {name: MagicMock() for name in agents_map}
        return registry

    def test_empty_entries_returns_empty_dict(self) -> None:
        """AC: Empty entries returns empty dict."""
        registry = self._make_mock_registry()
        assert derive_agents_from_entries([], registry) == {}

    def test_derives_agents_from_entries(self) -> None:
        """AC: Agents derived from skill names in entries + registry lookup."""
        agent_def = AgentDefinition(description="Test", prompt="prompt")

        registry = self._make_mock_registry({
            "skill-a": {"skill-a/agent1": agent_def},
        })

        entries = [_make_entry(metadata={"skill_name": "skill-a"})]
        result = derive_agents_from_entries(entries, registry)

        assert "skill-a/agent1" in result
        assert result["skill-a/agent1"] is agent_def

    def test_skips_deleted_skills_gracefully(self) -> None:
        """AC: Skill names not in registry are silently skipped."""
        registry = self._make_mock_registry({})

        entries = [_make_entry(metadata={"skill_name": "deleted-skill"})]
        result = derive_agents_from_entries(entries, registry)

        assert result == {}

    def test_merges_agents_from_multiple_skills(self) -> None:
        """AC: Agents from multiple skills are merged."""
        agent_a = AgentDefinition(description="A", prompt="a")
        agent_b = AgentDefinition(description="B", prompt="b")

        registry = self._make_mock_registry({
            "skill-a": {"skill-a/agent1": agent_a},
            "skill-b": {"skill-b/agent2": agent_b},
        })

        entries = [
            _make_entry(metadata={"skill_name": "skill-a"}),
            _make_entry(metadata={"skill_name": "skill-b"}),
        ]
        result = derive_agents_from_entries(entries, registry)

        assert len(result) == 2
        assert "skill-a/agent1" in result
        assert "skill-b/agent2" in result


class TestMessagePreProcessingPipeline:
    """Tests for MessagePreProcessingPipeline."""

    async def test_empty_providers_returns_empty_list(self) -> None:
        """AC: No providers registered → empty result."""
        pipeline = MessagePreProcessingPipeline()
        result = await pipeline.run("hello")
        assert result == []

    async def test_single_provider_returns_results(self) -> None:
        """AC: Single provider returns flattened results."""
        provider = AsyncMock(spec=MessageContextProvider)
        provider.provide.return_value = [
            ContextResult(tag="skills", content="skill content", metadata={"skill_name": "a"}),
        ]

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(provider)

        result = await pipeline.run("hello", existing_entries=[])

        assert len(result) == 1
        assert result[0].tag == "skills"

    async def test_passes_existing_entries_to_provider(self) -> None:
        """AC: existing_entries are passed through to providers."""
        provider = AsyncMock(spec=MessageContextProvider)
        provider.provide.return_value = None

        entries = [_make_entry(metadata={"skill_name": "loaded"})]

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(provider)

        await pipeline.run("hello", existing_entries=entries)

        provider.provide.assert_called_once_with(
            "hello", existing_entries=entries, sdk_session_id=None,
        )

    async def test_parallel_execution(self) -> None:
        """AC: Multiple providers run in parallel and results are flattened."""
        provider_a = AsyncMock(spec=MessageContextProvider)
        provider_a.provide.return_value = [
            ContextResult(tag="skills", content="a", metadata={"skill_name": "a"}),
        ]

        provider_b = AsyncMock(spec=MessageContextProvider)
        provider_b.provide.return_value = [
            ContextResult(tag="skills", content="b", metadata={"skill_name": "b"}),
        ]

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(provider_a)
        pipeline.register(provider_b)

        result = await pipeline.run("hello")

        assert len(result) == 2
        tags = {r.metadata["skill_name"] for r in result}
        assert tags == {"a", "b"}

    async def test_error_isolation(self) -> None:
        """AC: Provider failures don't prevent other providers from completing."""
        provider_a = AsyncMock(spec=MessageContextProvider)
        provider_a.provide.side_effect = RuntimeError("Provider A failed")

        provider_b = AsyncMock(spec=MessageContextProvider)
        provider_b.provide.return_value = [
            ContextResult(tag="skills", content="b", metadata={"skill_name": "b"}),
        ]

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(provider_a)
        pipeline.register(provider_b)

        result = await pipeline.run("hello")

        # Provider B's result still comes through
        assert len(result) == 1
        assert result[0].metadata["skill_name"] == "b"

    async def test_none_result_skipped(self) -> None:
        """AC: Providers returning None are not included in results."""
        provider = AsyncMock(spec=MessageContextProvider)
        provider.provide.return_value = None

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(provider)

        result = await pipeline.run("hello")

        assert result == []

    async def test_default_existing_entries_is_empty(self) -> None:
        """AC: When existing_entries not passed, provider gets empty list."""
        provider = AsyncMock(spec=MessageContextProvider)
        provider.provide.return_value = None

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(provider)

        await pipeline.run("hello")

        provider.provide.assert_called_once_with(
            "hello", existing_entries=[], sdk_session_id=None,
        )

    async def test_passes_sdk_session_id_to_provider(self) -> None:
        """AC: sdk_session_id is passed through to providers."""
        provider = AsyncMock(spec=MessageContextProvider)
        provider.provide.return_value = None

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(provider)

        await pipeline.run("hello", sdk_session_id="test-session-123")

        provider.provide.assert_called_once_with(
            "hello", existing_entries=[], sdk_session_id="test-session-123",
        )

    async def test_default_sdk_session_id_is_none(self) -> None:
        """AC: When sdk_session_id not passed, provider gets None."""
        provider = AsyncMock(spec=MessageContextProvider)
        provider.provide.return_value = None

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(provider)

        await pipeline.run("hello")

        _, kwargs = provider.provide.call_args
        assert kwargs["sdk_session_id"] is None
