"""Tests for skills context provider."""

from pathlib import Path

from claude_agent_sdk.types import ResultMessage
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.message import IncomingMessage
from tachikoma.per_message_pre_processing import render_conversation_context
from tachikoma.sessions.model import SessionContextEntry
from tachikoma.skills.context_provider import (
    SKILL_CLASSIFICATION_PROMPT,
    SkillsContextProvider,
    render_agents_context,
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

    def test_prompt_has_agents_context_placeholder(self) -> None:
        """AC: Prompt has {agents_context} placeholder for AGENTS.md injection."""
        assert "{agents_context}" in SKILL_CLASSIFICATION_PROMPT

    def test_prompt_has_conversation_context_placeholder(self) -> None:
        """AC: Prompt has {conversation_context_section} placeholder for session context."""
        assert "{conversation_context_section}" in SKILL_CLASSIFICATION_PROMPT


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
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create skills directory but no skills
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        provider = self._make_provider(tmp_path)

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is None
        mock_query.assert_not_called()

    async def test_calls_query_with_correct_options(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: query() called with effort=low, max_turns=3, cwd set."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create a skill so registry is non-empty
        skills_dir = tmp_path / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: A test skill\n---\n\nTest content")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        await provider.provide(IncomingMessage(text="hello"))

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
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create a skill
        skills_dir = tmp_path / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: A test skill\n---\n\nTest content")

        mock_query.return_value = _make_query_result("test-skill")

        provider = self._make_provider(tmp_path)

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].tag == "skills"

    async def test_xml_block_contains_skill_body_and_path(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: XML block contains skill body (no frontmatter) and directory path."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create skill with YAML frontmatter
        skills_dir = tmp_path / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Test\n---\n\n# My Skill\n\nThis is the body.")

        mock_query.return_value = _make_query_result("my-skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello"))

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
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

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
        result = await provider.provide(IncomingMessage(text="search for something"))

        assert result is not None
        assert len(result) == 1
        assert result[0].agents is None

    async def test_returns_none_for_no_relevant_skills_sentinel(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: NO_RELEVANT_SKILLS sentinel returns None."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills" / "test"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Test\n---\n\nContent")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is None

    async def test_discards_unrecognized_skill_names(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Skill names not in registry are discarded."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills" / "real-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: Real\n---\n\nContent")

        # Agent returns valid name + fake name
        mock_query.return_value = _make_query_result("real-skill\nfake-skill\nanother-fake")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert len(result) == 1
        assert "real-skill" in result[0].content
        assert "fake-skill" not in result[0].content
        assert "another-fake" not in result[0].content

    async def test_graceful_degradation_on_skill_read_failure(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: When skill body read fails, other skills still work."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create a valid skill
        skills_dir = tmp_path / "skills" / "valid-skill"
        skills_dir.mkdir(parents=True)
        skill_md = skills_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: A valid skill\n---\n\nValid content")

        # Only return the valid skill (unreadable ones are filtered by registry)
        mock_query.return_value = _make_query_result("valid-skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert len(result) == 1
        assert "valid-skill" in result[0].content
        assert "Valid content" in result[0].content

    async def test_multiple_skills_detected(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """AC: Multiple detected skills produce separate ContextResults."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

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
        result = await provider.provide(IncomingMessage(text="hello"))

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
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

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

        result = await provider.provide(IncomingMessage(text="hello"))

        # Registry agents should be unchanged
        registry_after = provider._registry.get_agents()
        assert registry_agents_before.keys() == registry_after.keys()

        # Result exists
        assert result is not None

    async def test_filters_already_loaded_skills(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Already-loaded skills (from existing_entries) are excluded from classification."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

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
                id=1,
                session_id="s1",
                owner="skills",
                content="...",
                metadata={"skill_name": "skill-a"},
            ),
        ]

        result = await provider.provide(IncomingMessage(text="hello"), existing_entries=existing)

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
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create one skill
        skills_dir = tmp_path / "skills" / "only-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\ndescription: Only\n---\n\nContent")

        provider = self._make_provider(tmp_path)

        # Pass existing entries indicating the only skill is loaded
        existing = [
            SessionContextEntry(
                id=1,
                session_id="s1",
                owner="skills",
                content="...",
                metadata={"skill_name": "only-skill"},
            ),
        ]

        result = await provider.provide(IncomingMessage(text="hello"), existing_entries=existing)

        assert result is None
        mock_query.assert_not_called()

    async def test_metadata_contains_skill_name(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Each result has metadata={'skill_name': name}."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\ndescription: My\n---\n\nContent")

        mock_query.return_value = _make_query_result("my-skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata == {"skill_name": "my-skill"}

    async def test_empty_existing_entries_classifies_full_registry(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Empty existing_entries → classifier sees full registry (same as first message)."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills" / "test"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\ndescription: Test\n---\n\nContent")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello"), existing_entries=[])

        # Classifier should see the skill
        call_args = mock_query.call_args
        prompt = call_args[1]["prompt"]
        assert "test" in prompt

        assert result is None  # NO_RELEVANT_SKILLS


class TestProviderChainExpansion:
    """Tests for chain expansion in SkillsContextProvider."""

    def _create_skill_with_deps(
        self, skills_dir: Path, name: str, description: str, deps: list[str] | None = None
    ) -> None:
        """Create a skill directory with optional depends_on."""
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        dep_yaml = ""
        if deps:
            dep_yaml = f"\ndepends_on: {deps}"
        (skill_dir / "SKILL.md").write_text(
            f'---\ndescription: "{description}"{dep_yaml}\n---\n\nBody for {name}'
        )

    async def test_emits_detected_skill_with_its_transitive_deps(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: A depends on B; classifier returns A → results contain [B, A]."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        self._create_skill_with_deps(skills_dir, "A", "Desc A", ["B"])
        self._create_skill_with_deps(skills_dir, "B", "Desc B")

        mock_query.return_value = _make_query_result("A")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([skills_dir])
        provider = SkillsContextProvider(defaults, registry)

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert len(result) == 2
        assert result[0].metadata["skill_name"] == "B"
        assert result[1].metadata["skill_name"] == "A"

    async def test_skips_deps_already_in_existing_entries(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: B already loaded; classifier returns A (depends on B) → only A emitted."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        self._create_skill_with_deps(skills_dir, "A", "Desc A", ["B"])
        self._create_skill_with_deps(skills_dir, "B", "Desc B")

        mock_query.return_value = _make_query_result("A")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([skills_dir])
        provider = SkillsContextProvider(defaults, registry)

        existing = [
            SessionContextEntry(
                id=1,
                session_id="s1",
                owner="skills",
                content="...",
                metadata={"skill_name": "B"},
            ),
        ]

        result = await provider.provide(IncomingMessage(text="hello"), existing_entries=existing)

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata["skill_name"] == "A"

    async def test_overlapping_chains_emit_shared_dep_once(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: X and Y both depend on F → results are [F, X, Y]."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        self._create_skill_with_deps(skills_dir, "X", "Desc X", ["F"])
        self._create_skill_with_deps(skills_dir, "Y", "Desc Y", ["F"])
        self._create_skill_with_deps(skills_dir, "F", "Desc F")

        mock_query.return_value = _make_query_result("X\nY")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([skills_dir])
        provider = SkillsContextProvider(defaults, registry)

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        names = [r.metadata["skill_name"] for r in result]
        assert names == ["F", "X", "Y"]

    async def test_chain_partially_loaded_still_emits_remaining(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Chain [C, B, A] but B already in entries → results are [C, A]."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        self._create_skill_with_deps(skills_dir, "A", "Desc A", ["B"])
        self._create_skill_with_deps(skills_dir, "B", "Desc B", ["C"])
        self._create_skill_with_deps(skills_dir, "C", "Desc C")

        mock_query.return_value = _make_query_result("A")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([skills_dir])
        provider = SkillsContextProvider(defaults, registry)

        existing = [
            SessionContextEntry(
                id=1,
                session_id="s1",
                owner="skills",
                content="...",
                metadata={"skill_name": "B"},
            ),
        ]

        result = await provider.provide(IncomingMessage(text="hello"), existing_entries=existing)

        assert result is not None
        names = [r.metadata["skill_name"] for r in result]
        assert names == ["C", "A"]

    async def test_classification_empty_no_resolve_calls(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Classifier returns NO_RELEVANT_SKILLS → resolver not invoked."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        self._create_skill_with_deps(skills_dir, "A", "Desc A")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([skills_dir])
        provider = SkillsContextProvider(defaults, registry)
        spy = mocker.spy(registry, "resolve_chain")

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is None
        spy.assert_not_called()

    async def test_resolver_exception_for_one_skill_does_not_block_others(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: resolve_chain raises for X but succeeds for Y → only Y emitted."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        self._create_skill_with_deps(skills_dir, "X", "Desc X")
        self._create_skill_with_deps(skills_dir, "Y", "Desc Y")

        mock_query.return_value = _make_query_result("X\nY")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([skills_dir])
        provider = SkillsContextProvider(defaults, registry)

        original_resolve = registry.resolve_chain

        def patched_resolve(name: str):
            if name == "X":
                raise RuntimeError("boom")
            return original_resolve(name)

        mocker.patch.object(registry, "resolve_chain", side_effect=patched_resolve)

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        names = [r.metadata["skill_name"] for r in result]
        assert names == ["Y"]

    async def test_candidate_list_contains_every_unloaded_skill_regardless_of_dep_relationship(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Classification prompt includes all unloaded skills, deps don't filter."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        self._create_skill_with_deps(skills_dir, "alpha", "Alpha skill", ["bravo"])
        self._create_skill_with_deps(skills_dir, "bravo", "Bravo skill")

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([skills_dir])
        provider = SkillsContextProvider(defaults, registry)

        await provider.provide(IncomingMessage(text="hello"))

        call_args = mock_query.call_args
        prompt = call_args[1]["prompt"]
        assert "- **alpha**" in prompt
        assert "- **bravo**" in prompt

    async def test_emitted_entries_shape_identical_to_classification_loaded(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC: Dep-loaded entry has same tag, content structure, and metadata keys."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        self._create_skill_with_deps(skills_dir, "A", "Desc A", ["B"])
        self._create_skill_with_deps(skills_dir, "B", "Desc B")

        mock_query.return_value = _make_query_result("A")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([skills_dir])
        provider = SkillsContextProvider(defaults, registry)

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert len(result) == 2

        for entry in result:
            assert entry.tag == "skills"
            assert "skill_name" in entry.metadata
            assert "<skill name=" in entry.content
            assert "directory=" in entry.content
            assert entry.agents is None


class TestRenderAgentsContext:
    """Tests for render_agents_context helper."""

    def test_empty_entries_returns_placeholder(self) -> None:
        """AC3: Empty list → neutral placeholder."""
        assert render_agents_context([]) == "(No agent instructions available.)"

    def test_soul_and_user_only_returns_placeholder(self) -> None:
        """AC2: soul/user entries without agents → placeholder (soul/user are not injected)."""
        entries = [
            SessionContextEntry(id=1, session_id="s1", owner="soul", content="Personality"),
            SessionContextEntry(id=2, session_id="s1", owner="user", content="About the user"),
        ]
        rendered = render_agents_context(entries)
        assert rendered == "(No agent instructions available.)"
        assert "<soul>" not in rendered
        assert "<user>" not in rendered

    def test_agents_entry_wrapped_in_xml(self) -> None:
        """AC1: agents entry → `<agents>\\n{content}\\n</agents>`."""
        entries = [
            SessionContextEntry(id=5, session_id="s1", owner="agents", content="Use X then Y"),
        ]
        assert render_agents_context(entries) == "<agents>\nUse X then Y\n</agents>"

    def test_multiple_agents_entries_emitted_in_id_order(self) -> None:
        """R2: Multiple agents entries → each in its own block, ordered by id."""
        entries = [
            SessionContextEntry(id=3, session_id="s1", owner="agents", content="Second"),
            SessionContextEntry(id=1, session_id="s1", owner="agents", content="First"),
        ]
        rendered = render_agents_context(entries)
        assert rendered == "<agents>\nFirst\n</agents>\n\n<agents>\nSecond\n</agents>"

    def test_non_agents_entries_are_ignored(self) -> None:
        """Only owner=='agents' contributes to the rendered block."""
        entries = [
            SessionContextEntry(id=1, session_id="s1", owner="soul", content="SoulContent"),
            SessionContextEntry(id=2, session_id="s1", owner="agents", content="AgentContent"),
            SessionContextEntry(id=3, session_id="s1", owner="user", content="UserContent"),
            SessionContextEntry(id=4, session_id="s1", owner="skills", content="SkillsContent"),
        ]
        rendered = render_agents_context(entries)
        assert rendered == "<agents>\nAgentContent\n</agents>"
        assert "SoulContent" not in rendered
        assert "UserContent" not in rendered
        assert "SkillsContent" not in rendered


class TestAgentsContextInjection:
    """Tests for AGENTS.md injection into the classifier prompt."""

    def _make_provider(self, tmp_path: Path) -> SkillsContextProvider:
        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([tmp_path / "skills"])
        return SkillsContextProvider(defaults, registry)

    def _seed_skill(self, tmp_path: Path, name: str = "example") -> None:
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\ndescription: Example\n---\n\nBody for {name}")

    async def test_agents_context_rendered_when_entry_present(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC1: agents entry → `<agents>…</agents>` appears in the captured prompt."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path)
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        existing = [
            SessionContextEntry(
                id=1,
                session_id="s1",
                owner="agents",
                content="Always prefer workflow W when user says Z.",
            ),
        ]

        provider = self._make_provider(tmp_path)
        await provider.provide(IncomingMessage(text="hello"), existing_entries=existing)

        prompt = mock_query.call_args[1]["prompt"]
        assert "<agents>\nAlways prefer workflow W when user says Z.\n</agents>" in prompt

    async def test_soul_and_user_entries_are_not_injected(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC2: soul/user entries present but no agents → placeholder, and no soul/user blocks."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path)
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        existing = [
            SessionContextEntry(id=1, session_id="s1", owner="soul", content="SoulBody"),
            SessionContextEntry(id=2, session_id="s1", owner="user", content="UserBody"),
        ]

        provider = self._make_provider(tmp_path)
        await provider.provide(IncomingMessage(text="hello"), existing_entries=existing)

        prompt = mock_query.call_args[1]["prompt"]
        assert "<soul>" not in prompt
        assert "<user>" not in prompt
        assert "SoulBody" not in prompt
        assert "UserBody" not in prompt
        assert "(No agent instructions available.)" in prompt

    async def test_empty_entries_uses_placeholder(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC3: No existing entries → placeholder rendered, no unsubstituted placeholder."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path)
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        await provider.provide(IncomingMessage(text="hello"))

        prompt = mock_query.call_args[1]["prompt"]
        assert "(No agent instructions available.)" in prompt
        assert "{agents_context}" not in prompt

    async def test_agents_block_precedes_available_skills(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC4: Agents block appears before '## Available Skills' in the rendered prompt."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path)
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        existing = [
            SessionContextEntry(id=1, session_id="s1", owner="agents", content="Convention body"),
        ]

        provider = self._make_provider(tmp_path)
        await provider.provide(IncomingMessage(text="hello"), existing_entries=existing)

        prompt = mock_query.call_args[1]["prompt"]
        agents_idx = prompt.index("<agents>")
        skills_idx = prompt.index("## Available Skills")
        assert agents_idx < skills_idx


class TestRenderConversationContext:
    """Tests for render_conversation_context helper."""

    def test_no_summary_returns_empty_string(self) -> None:
        """AC2: No summary (first exchange) → entire section omitted."""
        assert render_conversation_context(None, None) == ""

    def test_no_summary_ignores_last_exchange(self) -> None:
        """AC2: No summary but last_exchange provided → still empty."""
        assert render_conversation_context(None, "Some response") == ""

    def test_summary_only_omits_last_exchange(self) -> None:
        """AC3: Summary without last_exchange → section present but no 'Last assistant response'."""
        result = render_conversation_context("We discussed X", None)
        assert "## Conversation Context" in result
        assert "We discussed X" in result
        assert "Last assistant response" not in result

    def test_summary_and_last_exchange_includes_both(self) -> None:
        """AC1: Both provided → full section with summary and last exchange."""
        result = render_conversation_context("We discussed restaurants", "I like Italian food")
        assert "## Conversation Context" in result
        assert "We discussed restaurants" in result
        assert "Last assistant response" in result
        assert "I like Italian food" in result


class TestConversationContextInjection:
    """Tests for session context injection into the classifier prompt."""

    def _make_provider(self, tmp_path: Path) -> SkillsContextProvider:
        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([tmp_path / "skills"])
        return SkillsContextProvider(defaults, registry)

    def _seed_skill(self, tmp_path: Path, name: str = "example") -> None:
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\ndescription: Example\n---\n\nBody for {name}")

    async def test_conversation_context_in_prompt_when_summary_provided(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC1: Summary + last exchange → prompt contains Conversation Context section."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path)
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        await provider.provide(
            IncomingMessage(text="hello"),
            session_summary="We discussed restaurants",
            session_last_exchange="I like Italian food",
        )

        prompt = mock_query.call_args[1]["prompt"]
        assert "## Conversation Context" in prompt
        assert "We discussed restaurants" in prompt
        assert "Last assistant response" in prompt
        assert "I like Italian food" in prompt

    async def test_no_conversation_context_when_no_summary(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC2: No summary (first exchange) → no Conversation Context section."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path)
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        await provider.provide(IncomingMessage(text="hello"))

        prompt = mock_query.call_args[1]["prompt"]
        assert "## Conversation Context" not in prompt


class TestPinnedSkills:
    """Tests for pinned skills in SkillsContextProvider."""

    def _make_provider(self, tmp_path: Path) -> SkillsContextProvider:
        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([tmp_path / "skills"])
        return SkillsContextProvider(defaults, registry)

    def _seed_skill(self, tmp_path: Path, name: str, description: str = "Test") -> None:
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\ndescription: {description}\n---\n\nBody for {name}"
        )

    async def test_pinned_skill_loaded_without_classification(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Pinned skill is loaded without needing LLM classification."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path, "research", "Research skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello", pinned_skills=("research",)))

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata["skill_name"] == "research"
        assert result[0].metadata.get("_pinned") is True
        # No classification needed — only skill was pinned
        mock_query.assert_not_called()

    async def test_pinned_skill_already_loaded_skips(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Pinned skill already in existing_entries is skipped."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path, "research", "Research skill")
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        existing = [
            SessionContextEntry(
                id=1,
                session_id="s1",
                owner="skills",
                content="...",
                metadata={"skill_name": "research"},
            ),
        ]

        provider = self._make_provider(tmp_path)
        result = await provider.provide(
            IncomingMessage(text="hello", pinned_skills=("research",)),
            existing_entries=existing,
        )

        # Skill was already loaded, so nothing returned
        assert result is None

    async def test_pinned_skill_with_dependency_chain(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Pinned skill resolves its dependency chain."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create skill A that depends on B
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        for name in ("A", "B"):
            d = skills_dir / name
            d.mkdir(exist_ok=True)
        (skills_dir / "B" / "SKILL.md").write_text("---\ndescription: Base\n---\n\nB body")
        (skills_dir / "A" / "SKILL.md").write_text(
            "---\ndescription: Advanced\ndepends_on: ['B']\n---\n\nA body"
        )

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello", pinned_skills=("A",)))

        assert result is not None
        names = [r.metadata["skill_name"] for r in result]
        assert names == ["B", "A"]
        # All pinned
        assert all(r.metadata["_pinned"] for r in result)
        mock_query.assert_not_called()

    async def test_pinned_plus_classified_combined(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Pinned and classified skills are combined in results."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        self._seed_skill(tmp_path, "research", "Research skill")
        self._seed_skill(tmp_path, "planning", "Planning skill")
        mock_query.return_value = _make_query_result("planning")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello", pinned_skills=("research",)))

        assert result is not None
        assert len(result) == 2

        pinned = [r for r in result if r.metadata.get("_pinned")]
        classified = [r for r in result if not r.metadata.get("_pinned")]
        assert len(pinned) == 1
        assert pinned[0].metadata["skill_name"] == "research"
        assert len(classified) == 1
        assert classified[0].metadata["skill_name"] == "planning"

    async def test_unknown_pinned_skill_skipped_gracefully(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Unknown pinned skill name is skipped without error."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path, "research", "Research skill")
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(
            IncomingMessage(text="hello", pinned_skills=("nonexistent", "research"))
        )

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata["skill_name"] == "research"

    async def test_status_message_pinned_and_classified(self, tmp_path: Path) -> None:
        """status_message shows pinned + classified breakdown."""
        from tachikoma.pre_processing import ContextResult  # noqa: PLC0415

        provider = self._make_provider(tmp_path)
        results = [
            ContextResult(tag="skills", content="a", metadata={"skill_name": "x", "_pinned": True}),
            ContextResult(tag="skills", content="b", metadata={"skill_name": "y"}),
        ]
        msg = provider.status_message(results)
        assert "1 pinned" in msg
        assert "1 classified" in msg

    async def test_pinned_skills_empty_registry_still_loads(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """Pinned skills load even when registry is otherwise empty."""
        mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create a skill dir but don't seed the registry normally — we need one skill
        self._seed_skill(tmp_path, "research", "Research")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello", pinned_skills=("research",)))

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata["skill_name"] == "research"


class TestConversationContextInjectionRemaining:
    """Remaining tests for session context injection into the classifier prompt."""

    def _make_provider(self, tmp_path: Path) -> SkillsContextProvider:
        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([tmp_path / "skills"])
        return SkillsContextProvider(defaults, registry)

    def _seed_skill(self, tmp_path: Path, name: str = "example") -> None:
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\ndescription: Example\n---\n\nBody for {name}")

    async def test_summary_without_last_exchange(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC3: Summary but no last_exchange → section present but no 'Last assistant response'."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path)
        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        provider = self._make_provider(tmp_path)
        await provider.provide(
            IncomingMessage(text="hello"),
            session_summary="We discussed restaurants",
        )

        prompt = mock_query.call_args[1]["prompt"]
        assert "## Conversation Context" in prompt
        assert "We discussed restaurants" in prompt
        assert "Last assistant response" not in prompt


class TestQualifiedNameMetadata:
    """Tests for qualified-name metadata in context provider."""

    def _make_provider(self, tmp_path: Path) -> SkillsContextProvider:
        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([tmp_path / "skills"])
        return SkillsContextProvider(defaults, registry)

    def _seed_skill(self, tmp_path: Path, name: str, description: str = "Test") -> None:
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\ndescription: {description}\n---\n\nBody for {name}"
        )

    async def test_default_ns_metadata_uses_bare_name(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC-SSR-7: Default-namespace skill metadata uses bare name."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")
        self._seed_skill(tmp_path, "my-skill", "My skill")
        mock_query.return_value = _make_query_result("my-skill")

        provider = self._make_provider(tmp_path)
        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata["skill_name"] == "my-skill"

    async def test_plugin_skill_metadata_uses_qualified_name(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC-SSR-7: Plugin skill metadata uses 'alias:name' qualified name."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        # Create default skills dir (empty)
        default_dir = tmp_path / "skills"
        default_dir.mkdir(parents=True, exist_ok=True)

        # Create plugin skill
        plugin_dir = tmp_path / "plugins" / "review" / "skills"
        plugin_dir.mkdir(parents=True)
        skill_dir = plugin_dir / "linter"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: Plugin linter\n---\n\nPlugin linter body"
        )

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([default_dir])
        registry.add_namespaced_source("review", plugin_dir)
        provider = SkillsContextProvider(defaults, registry)

        mock_query.return_value = _make_query_result("review:linter")

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata["skill_name"] == "review:linter"

    async def test_plugin_skill_xml_block_uses_qualified_name(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC-SSR-6: Plugin skill XML block uses qualified name attribute."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        default_dir = tmp_path / "skills"
        default_dir.mkdir(parents=True, exist_ok=True)

        plugin_dir = tmp_path / "plugins" / "review" / "skills"
        plugin_dir.mkdir(parents=True)
        skill_dir = plugin_dir / "linter"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: Plugin linter\n---\n\nPlugin linter body"
        )

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([default_dir])
        registry.add_namespaced_source("review", plugin_dir)
        provider = SkillsContextProvider(defaults, registry)

        mock_query.return_value = _make_query_result("review:linter")

        result = await provider.provide(IncomingMessage(text="hello"))

        assert result is not None
        assert 'name="review:linter"' in result[0].content

    async def test_candidate_list_shows_qualified_names_for_plugin_skills(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """AC-SSR-6: Classifier candidate list shows qualified names for plugin skills."""
        mock_query = mocker.patch("tachikoma.skills.context_provider.stderr_aware_query")

        default_dir = tmp_path / "skills"
        default_dir.mkdir(parents=True, exist_ok=True)
        # Add a default-ns skill
        default_skill = default_dir / "builtin-skill"
        default_skill.mkdir()
        (default_skill / "SKILL.md").write_text("---\ndescription: Built-in skill\n---\n\nBody")

        plugin_dir = tmp_path / "plugins" / "review" / "skills"
        plugin_dir.mkdir(parents=True)
        skill_dir = plugin_dir / "linter"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: Plugin linter\n---\n\nBody")

        defaults = AgentDefaults(cwd=tmp_path)
        registry = SkillRegistry([default_dir])
        registry.add_namespaced_source("review", plugin_dir)
        provider = SkillsContextProvider(defaults, registry)

        mock_query.return_value = _make_query_result("NO_RELEVANT_SKILLS")

        await provider.provide(IncomingMessage(text="hello"))

        prompt = mock_query.call_args[1]["prompt"]
        assert "- **builtin-skill**" in prompt
        assert "- **review:linter**" in prompt
