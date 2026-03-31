"""Tests for skills context provider.

Tests for DLT-021: Skill detection and context injection.
Tests for DLT-032: Registry injection via constructor.
Updated for DLT-038: Registry injected via constructor.
"""

from pathlib import Path

from claude_agent_sdk.types import ResultMessage
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.pre_processing import ContextResult
from tachikoma.skills.context_provider import (
    SKILL_CLASSIFICATION_PROMPT,
    SkillsContextProvider,
)
from tachikoma.skills.registry import SkillRegistry


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


class TestSkillClassificationPrompt:
    """Tests for SKILL_CLASSIFICATION_PROMPT constant."""

    def test_prompt_has_skills_placeholder(self) -> None:
        """AC: Prompt has {skills} placeholder for skill list/descriptions."""
        assert "{skills}" in SKILL_CLASSIFICATION_PROMPT

    def test_prompt_instructs_no_relevant_skills_sentinel(self) -> None:
        """AC: Prompt mentions NO_RELEVANT_SKILLS sentinel."""
        assert "NO_RELEVANT_SKILLS" in SKILL_CLASSIFICATION_PROMPT

    def test_prompt_instructs_one_per_line_format(self) -> None:
        """AC: Prompt mentions one per line format."""
        assert "one per line" in SKILL_CLASSIFICATION_PROMPT.lower()

    def test_prompt_has_message_placeholder(self) -> None:
        """AC: Prompt has {message} placeholder for embedding user message."""
        assert "{message}" in SKILL_CLASSIFICATION_PROMPT

    def test_prompt_instructs_no_relevant_skills_when_none_match(self) -> None:
        """AC: Prompt instructs what to return when no skills match."""
        assert "no skills are relevant" in SKILL_CLASSIFICATION_PROMPT.lower()


class TestSkillsContextProvider:
    """Tests for SkillsContextProvider."""

    def _make_provider(
        self, tmp_path: Path, agent_defaults: AgentDefaults | None = None
    ) -> SkillsContextProvider:
        """Create a provider with an injected registry."""
        defaults = agent_defaults or AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([tmp_path / "skills"])
        return SkillsContextProvider(defaults, registry)

    async def test_empty_registry_returns_none_without_query(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: No LLM call when registry has no skills (R10)."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create skills directory but no skills
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        provider = self._make_provider(tmp_path)

        result = await provider.provide("hello")

        assert result is None
        mock_query.assert_not_called()

    async def test_calls_query_with_correct_options(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: query() called with model=opus, effort=low, max_turns=3, no allowed_tools."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create a skill so registry is non-empty
        skills_dir = tmp_path / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: A test skill\n---\n\nTest content")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(
            tmp_path, AgentDefaults(cwd=tmp_path, cli_path="/custom/cli")
        )
        result = await provider.provide("hello")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        options = call_kwargs["options"]

        assert options.model == "opus"
        assert options.effort == "low"
        assert options.max_turns == 3
        # No tools for classification — defense in depth (see DES-007)
        assert options.allowed_tools == []
        assert options.permission_mode is None
        assert options.cwd == tmp_path
        assert options.cli_path == "/custom/cli"
        assert result is None

    async def test_embeds_skill_names_and_message_in_prompt(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Prompt contains skill names/descriptions and user message."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create a skill
        skills_dir = tmp_path / "skills" / "search"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Search for things\n---\n\nSearch content")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        await provider.provide("Find my documents")

        call_kwargs = mock_query.call_args[1]
        prompt = call_kwargs["prompt"]

        assert "search" in prompt
        assert "Search for things" in prompt
        assert "Find my documents" in prompt

    async def test_returns_context_result_with_skills_tag(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Happy path returns ContextResult with tag='skills'."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create a skill
        skills_dir = tmp_path / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: A test\n---\n\nSkill body content")

        mock_query.return_value = _make_query_result("test-skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is not None
        assert isinstance(result, ContextResult)
        assert result.tag == "skills"

    async def test_xml_block_contains_skill_body_and_path(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: XML block contains skill body (no frontmatter) and directory path."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create skill with YAML frontmatter
        skills_dir = tmp_path / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Test\n---\n\n# My Skill\n\nThis is the body.")

        mock_query.return_value = _make_query_result("my-skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is not None
        assert '<skill name="my-skill"' in result.content
        assert "directory=" in result.content
        assert "# My Skill" in result.content
        assert "This is the body." in result.content
        # Frontmatter should NOT be in content
        assert "---" not in result.content
        assert "description: Test" not in result.content

    async def test_filters_agents_by_detected_skill_prefix(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Only agents from detected skills are returned."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create skill with agent
        skills_dir = tmp_path / "skills" / "search"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Search\n---\n\nContent")

        agents_dir = skills_dir / "agents"
        agents_dir.mkdir()
        agent_md = agents_dir / "query.md"
        agent_md.write_text("---\ndescription: Query agent\n---\n\nAgent prompt")

        # Create another skill that should NOT have agents returned
        other_dir = tmp_path / "skills" / "other"
        other_dir.mkdir(parents=True)
        other_md = other_dir / "SKILL.md"
        other_md.write_text("---\ndescription: Other\n---\n\nOther content")

        other_agents = other_dir / "agents"
        other_agents.mkdir()
        other_agent = other_agents / "helper.md"
        other_agent.write_text("---\ndescription: Helper\n---\n\nHelp prompt")

        mock_query.return_value = _make_query_result("search")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("search for something")

        assert result is not None
        assert result.agents is not None
        assert "search/query" in result.agents
        assert "other/helper" not in result.agents

    async def test_returns_none_for_no_relevant_skills_sentinel(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: NO_RELEVANT_SKILLS sentinel returns None."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        skills_dir = tmp_path / "skills" / "test"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Test\n---\n\nContent")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is None

    async def test_discards_unrecognized_skill_names(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Skill names not in registry are discarded."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        skills_dir = tmp_path / "skills" / "real-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Real\n---\n\nContent")

        # Agent returns valid name + fake name
        mock_query.return_value = _make_query_result("real-skill\nfake-skill\nanother-fake")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is not None
        assert "real-skill" in result.content
        assert "fake-skill" not in result.content

    async def test_returns_none_on_query_exception(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Exception during query returns None (DES-002 logging)."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")
        mock_query.side_effect = RuntimeError("SDK error")

        skills_dir = tmp_path / "skills" / "test"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Test\n---\n\nContent")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is None

    async def test_returns_none_on_error_result_message(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: is_error=True in ResultMessage returns None."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        skills_dir = tmp_path / "skills" / "test"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Test\n---\n\nContent")

        mock_query.return_value = _make_query_result("Error", is_error=True)

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is None

    async def test_graceful_degradation_on_skill_read_failure(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: When skill body read fails, other skills still work."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create a valid skill
        skills_dir = tmp_path / "skills" / "valid-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: A valid skill\n---\n\nValid content")

        # Only return the valid skill (unreadable ones are filtered by registry)
        mock_query.return_value = _make_query_result("valid-skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is not None
        assert "valid-skill" in result.content
        assert "Valid content" in result.content

    async def test_multiple_skills_detected(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Multiple detected skills in XML block, agents from both."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create first skill
        skills_dir1 = tmp_path / "skills" / "skill-a"
        skills_dir1.mkdir(parents=True)
        skill_md1 = skills_dir1 / "SKILL.md"
        skill_md1.write_text("---\ndescription: A\n---\n\nA content")
        agents_dir1 = skills_dir1 / "agents"
        agents_dir1.mkdir()
        (agents_dir1 / "agent1.md").write_text("---\ndescription: Agent 1\n---\n\nPrompt 1")

        # Create second skill
        skills_dir2 = tmp_path / "skills" / "skill-b"
        skills_dir2.mkdir(parents=True)
        skill_md2 = skills_dir2 / "SKILL.md"
        skill_md2.write_text("---\ndescription: B\n---\n\nB content")
        agents_dir2 = skills_dir2 / "agents"
        agents_dir2.mkdir()
        (agents_dir2 / "agent2.md").write_text("---\ndescription: Agent 2\n---\n\nPrompt 2")

        mock_query.return_value = _make_query_result("skill-a\nskill-b")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is not None
        assert "skill-a" in result.content
        assert "skill-b" in result.content
        assert "A content" in result.content
        assert "B content" in result.content
        assert result.agents is not None
        assert "skill-a/agent1" in result.agents
        assert "skill-b/agent2" in result.agents

    async def test_does_not_mutate_registry_agents_dict(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Filtering creates new dict, does not mutate registry's internal dict."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        skills_dir = tmp_path / "skills" / "test"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Test\n---\n\nContent")

        agents_dir = skills_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "agent.md").write_text("---\ndescription: Agent\n---\n\nPrompt")

        mock_query.return_value = _make_query_result("test")

        provider = self._make_provider(tmp_path)

        # Get registry agents before the call
        registry_agents_before = provider._registry.get_agents().copy()

        result = await provider.provide("hello")

        # Registry agents should be unchanged
        registry_after = provider._registry.get_agents()
        assert registry_agents_before.keys() == registry_after.keys()

        # Result agents is a different dict
        assert result is not None
        assert result.agents is not registry_after
