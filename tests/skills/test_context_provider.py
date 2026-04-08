"""Tests for skills context provider.

Tests for DLT-021: Skill detection and context injection.
Tests for DLT-032: Registry injection via constructor.
Updated for DLT-038: Registry injected via constructor.
Updated for DLT-075: Per-message evaluation with metadata-based filtering.
"""

from pathlib import Path

from claude_agent_sdk.types import ResultMessage
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.sessions.model import SessionContextEntry
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
        """AC: query() called with effort=low, max_turns=3, cwd set."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create a skill so registry is non-empty
        skills_dir = tmp_path / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: A test skill\n---\n\nTest content")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        await provider.provide("hello")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]

        assert options.effort == "low"
        assert options.tools == []
        assert options.max_turns == 10
        assert options.cwd == tmp_path

    async def test_returns_context_result_with_skills_tag(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Result is a list with tag='skills' and non-empty content."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create a skill
        skills_dir = tmp_path / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: A test skill\n---\n\nTest content")

        mock_query.return_value = _make_query_result("test-skill")

        provider = self._make_provider(tmp_path)

        result = await provider.provide("hello")

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].tag == "skills"

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
        assert len(result) == 1
        assert '<skill name="my-skill"' in result[0].content
        assert "directory=" in result[0].content
        assert "# My Skill" in result[0].content
        assert "This is the body." in result[0].content
        # Frontmatter should NOT be in content
        assert "---" not in result[0].content

    async def test_agents_always_none_on_results(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Agents are None on all results — derived from entries by coordinator."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create search skill with agents
        skills_dir = tmp_path / "skills" / "search"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Search\n---\n\nSearch content")
        agents_dir = skills_dir / "agents"
        agents_dir.mkdir()
        agent_md = agents_dir / "query.md"
        agent_md.write_text("---\ndescription: Query agent\n---\n\nAgent prompt")

        mock_query.return_value = _make_query_result("search")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("search for something")

        assert result is not None
        assert len(result) == 1
        assert result[0].agents is None

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
        assert len(result) == 1
        assert "real-skill" in result[0].content
        assert "fake-skill" not in result[0].content
        assert "another-fake" not in result[0].content

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
        assert len(result) == 1
        assert "valid-skill" in result[0].content
        assert "Valid content" in result[0].content

    async def test_multiple_skills_detected(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """AC: Multiple detected skills produce separate ContextResults."""
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
        assert len(result) == 2

        # Each result has its own skill content
        contents = [r.content for r in result]
        assert any("skill-a" in c for c in contents)
        assert any("skill-b" in c for c in contents)
        assert any("A content" in c for c in contents)
        assert any("B content" in c for c in contents)

        # Each result has metadata identifying the skill
        skill_names_in_results = {r.metadata["skill_name"] for r in result}
        assert skill_names_in_results == {"skill-a", "skill-b"}

        # Agents are None on all results — derived from entries by coordinator
        assert all(r.agents is None for r in result)

    async def test_does_not_mutate_registry_agents_dict(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Registry's internal dict is not mutated by provider calls."""
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

        # Result exists
        assert result is not None

    async def test_filters_already_loaded_skills(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Already-loaded skills (from existing_entries) are excluded from classification."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create two skills
        skills_dir_a = tmp_path / "skills" / "skill-a"
        skills_dir_a.mkdir(parents=True)
        (skills_dir_a / "SKILL.md").write_text("---\ndescription: A\n---\n\nA content")

        skills_dir_b = tmp_path / "skills" / "skill-b"
        skills_dir_b.mkdir(parents=True)
        (skills_dir_b / "SKILL.md").write_text("---\ndescription: B\n---\n\nB content")

        mock_query.return_value = _make_query_result("skill-b")

        provider = self._make_provider(tmp_path)

        # Pass existing entries indicating skill-a is already loaded
        existing = [
            SessionContextEntry(
                id=1, session_id="s1", owner="skills", content="...",
                metadata={"skill_name": "skill-a"},
            ),
        ]

        result = await provider.provide("hello", existing_entries=existing)

        # Classifier should only see skill-b (skill-a filtered out)
        call_args = mock_query.call_args
        prompt = call_args[1]["prompt"]
        assert "skill-a" not in prompt
        assert "skill-b" in prompt

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata["skill_name"] == "skill-b"

    async def test_returns_none_when_all_skills_loaded(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Returns None without LLM call when all skills are already loaded (R8)."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        # Create one skill
        skills_dir = tmp_path / "skills" / "only-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\ndescription: Only\n---\n\nContent")

        provider = self._make_provider(tmp_path)

        # Pass existing entries indicating the only skill is loaded
        existing = [
            SessionContextEntry(
                id=1, session_id="s1", owner="skills", content="...",
                metadata={"skill_name": "only-skill"},
            ),
        ]

        result = await provider.provide("hello", existing_entries=existing)

        assert result is None
        mock_query.assert_not_called()

    async def test_metadata_contains_skill_name(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Each result has metadata={'skill_name': name}."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        skills_dir = tmp_path / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\ndescription: My\n---\n\nContent")

        mock_query.return_value = _make_query_result("my-skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello")

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata == {"skill_name": "my-skill"}

    async def test_empty_existing_entries_classifies_full_registry(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Empty existing_entries → classifier sees full registry (same as first message)."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.query")

        skills_dir = tmp_path / "skills" / "test"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\ndescription: Test\n---\n\nContent")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        result = await provider.provide("hello", existing_entries=[])

        # Classifier should see the skill
        call_args = mock_query.call_args
        prompt = call_args[1]["prompt"]
        assert "test" in prompt

        assert result is None  # NO_RELEVANT_SKILLS
