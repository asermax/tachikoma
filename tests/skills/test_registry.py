"""Tests for SkillRegistry.

Tests for DLT-003: Skill system foundation and sub-agent delegation.
"""

import shutil
from pathlib import Path

import pytest
from loguru import logger

from tachikoma.skills.registry import SkillRegistry


def create_skill(
    skills_dir: Path,
    name: str,
    description: str,
    version: str | None = None,
) -> Path:
    """Create a skill directory with SKILL.md."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = f"""---
description: "{description}"
"""
    if version:
        frontmatter += f'version: "{version}"\n'
    frontmatter += "---\n"

    (skill_dir / "SKILL.md").write_text(frontmatter)
    return skill_dir


def create_agent(
    skill_dir: Path,
    name: str,
    description: str,
    model: str | None = None,
    tools: list[str] | None = None,
    body: str = "",
) -> Path:
    """Create an agent definition file."""
    agents_dir = skill_dir / "agents"
    agents_dir.mkdir(exist_ok=True)

    frontmatter = f"""---
description: "{description}"
"""
    if model:
        frontmatter += f'model: "{model}"\n'
    if tools:
        frontmatter += f"tools: {tools}\n"
    frontmatter += "---\n"

    content = frontmatter + body
    agent_path = agents_dir / f"{name}.md"
    agent_path.write_text(content)
    return agent_path


class TestSkillDiscovery:
    """Tests for skill directory discovery."""

    def test_empty_skills_dir(self, tmp_path: Path) -> None:
        """AC: Empty skills/ directory → empty agents dict (valid state)."""
        skills_dir = tmp_path / "workspace" / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry([skills_dir])

        assert registry.get_agents() == {}
        assert registry.skills == {}

    def test_missing_skills_dir(self, tmp_path: Path) -> None:
        """AC: Missing skills/ directory → empty agents dict (valid state)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Pass non-existent path — registry should handle gracefully
        registry = SkillRegistry([workspace / "skills"])

        assert registry.get_agents() == {}
        assert registry.skills == {}

    def test_ignores_regular_files(self, tmp_path: Path) -> None:
        """AC: Only directory entries are considered as skills."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        # Create a regular file (should be ignored)
        (skills_dir / "readme.txt").write_text("Not a skill")

        registry = SkillRegistry([skills_dir])

        assert registry.get_agents() == {}
        assert registry.skills == {}

    def test_discovers_valid_skills(self, tmp_path: Path) -> None:
        """AC: Valid skill directories are discovered."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        create_skill(skills_dir, "test-skill", "A test skill")

        registry = SkillRegistry([skills_dir])

        assert "test-skill" in registry.skills
        assert registry.skills["test-skill"].description == "A test skill"


class TestSkillValidation:
    """Tests for SKILL.md validation."""

    def test_missing_skill_md(self, tmp_path: Path) -> None:
        """AC: Skill directory without SKILL.md is skipped with warning."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        # Create directory without SKILL.md
        (skills_dir / "incomplete-skill").mkdir()

        registry = SkillRegistry([skills_dir])

        assert registry.skills == {}

    def test_no_name_in_frontmatter(self, tmp_path: Path) -> None:
        """AC1: SKILL.md without name field loads using folder name."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
description: "A description"
---
""")

        registry = SkillRegistry([skills_dir])

        assert "test-skill" in registry.skills
        assert registry.skills["test-skill"].name == "test-skill"

    def test_empty_description(self, tmp_path: Path) -> None:
        """AC: SKILL.md with empty description is skipped."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
description: ""
---
""")

        registry = SkillRegistry([skills_dir])

        assert registry.skills == {}

    def test_frontmatter_name_ignored_when_mismatched(self, tmp_path: Path) -> None:
        """AC3: SKILL.md name ≠ folder name — folder name wins, no error."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "folder-name"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: "different-name"
description: "A description"
---
""")

        registry = SkillRegistry([skills_dir])

        assert "folder-name" in registry.skills
        assert registry.skills["folder-name"].name == "folder-name"

    def test_frontmatter_name_ignored_when_matching(self, tmp_path: Path) -> None:
        """AC2: SKILL.md name matches folder — loads normally."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: "my-skill"
description: "A description"
---
""")

        registry = SkillRegistry([skills_dir])

        assert "my-skill" in registry.skills
        assert registry.skills["my-skill"].name == "my-skill"

    def test_valid_skill_md(self, tmp_path: Path) -> None:
        """AC: Valid SKILL.md is loaded correctly."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        create_skill(skills_dir, "valid-skill", "A valid skill", version="1.0.0")

        registry = SkillRegistry([skills_dir])

        assert "valid-skill" in registry.skills
        skill = registry.skills["valid-skill"]
        assert skill.name == "valid-skill"
        assert skill.description == "A valid skill"
        assert skill.version == "1.0.0"


class TestAgentDiscovery:
    """Tests for agent file discovery."""

    def test_missing_agents_dir(self, tmp_path: Path) -> None:
        """AC: Skill without agents/ dir is valid (no agents loaded)."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        create_skill(skills_dir, "no-agents-skill", "A skill without agents")

        registry = SkillRegistry([skills_dir])

        assert registry.get_agents() == {}

    def test_empty_agents_dir(self, tmp_path: Path) -> None:
        """AC: Empty agents/ dir → no agents loaded."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "empty-agents", "A skill")
        (skill_dir / "agents").mkdir()

        registry = SkillRegistry([skills_dir])

        assert registry.get_agents() == {}

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        """AC: Non-.md files in agents/ are ignored."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()

        # Create non-.md files
        (agents_dir / "readme.txt").write_text("Not an agent")
        (agents_dir / "config.json").write_text("{}")

        registry = SkillRegistry([skills_dir])

        assert registry.get_agents() == {}

    def test_discovers_md_agents(self, tmp_path: Path) -> None:
        """AC: .md files in agents/ are discovered."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        create_agent(skill_dir, "extractor", "Extracts data")

        registry = SkillRegistry([skills_dir])

        assert "test-skill/extractor" in registry.get_agents()


class TestAgentValidation:
    """Tests for agent definition validation."""

    def test_empty_description_skipped(self, tmp_path: Path) -> None:
        """AC: Agent with empty description is skipped."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()

        (agents_dir / "bad-agent.md").write_text("""---
description: ""
---
""")

        registry = SkillRegistry([skills_dir])

        assert registry.get_agents() == {}

    def test_empty_body_valid(self, tmp_path: Path) -> None:
        """AC: Agent with empty markdown body is valid (empty prompt)."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        create_agent(skill_dir, "empty-body", "No prompt body", body="")

        registry = SkillRegistry([skills_dir])

        agents = registry.get_agents()
        assert "test-skill/empty-body" in agents
        assert agents["test-skill/empty-body"].prompt == ""

    def test_with_tools(self, tmp_path: Path) -> None:
        """AC: Agent with tools list includes tools in AgentDefinition."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        create_agent(
            skill_dir,
            "tooled-agent",
            "Has tools",
            tools=["Read", "Glob", "Grep"],
        )

        registry = SkillRegistry([skills_dir])

        agents = registry.get_agents()
        assert "test-skill/tooled-agent" in agents
        assert agents["test-skill/tooled-agent"].tools == ["Read", "Glob", "Grep"]

    def test_with_model(self, tmp_path: Path) -> None:
        """AC: Agent with model specified includes model in AgentDefinition."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        create_agent(skill_dir, "sonnet-agent", "Uses sonnet", model="sonnet")

        registry = SkillRegistry([skills_dir])

        agents = registry.get_agents()
        assert "test-skill/sonnet-agent" in agents
        assert agents["test-skill/sonnet-agent"].model == "sonnet"

    def test_without_tools_model(self, tmp_path: Path) -> None:
        """AC: Agent without tools/model has None for both."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        create_agent(skill_dir, "minimal-agent", "Minimal agent")

        registry = SkillRegistry([skills_dir])

        agents = registry.get_agents()
        assert "test-skill/minimal-agent" in agents
        assert agents["test-skill/minimal-agent"].tools is None
        assert agents["test-skill/minimal-agent"].model is None

    def test_model_passthrough(self, tmp_path: Path) -> None:
        """AC: Invalid model strings are converted to None (SDK validates at delegation time).

        Per the design decision, we registry does NOT validate model values - it passes
        valid values through and converts invalid values to None. The SDK will raise an
        error at invocation time if an invalid model is used.
        """
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        create_agent(skill_dir, "custom-model", "Custom model", model="custom-model-name")

        registry = SkillRegistry([skills_dir])

        agents = registry.get_agents()
        assert "test-skill/custom-model" in agents
        # Invalid model is converted to None (registry doesn't validate, SDK does)
        assert agents["test-skill/custom-model"].model is None


class TestAgentNamespacing:
    """Tests for agent namespacing."""

    def test_correct_namespace_format(self, tmp_path: Path) -> None:
        """AC: Agents are namespaced as "skill-name/agent-name"."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "my-skill", "A skill")
        create_agent(skill_dir, "my-agent", "An agent")

        registry = SkillRegistry([skills_dir])

        assert "my-skill/my-agent" in registry.get_agents()

    def test_multiple_skills_no_collisions(self, tmp_path: Path) -> None:
        """AC: Multiple skills with same agent name don't collide."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir1 = create_skill(skills_dir, "skill-one", "First skill")
        create_agent(skill_dir1, "common-name", "Agent from skill one")

        skill_dir2 = create_skill(skills_dir, "skill-two", "Second skill")
        create_agent(skill_dir2, "common-name", "Agent from skill two")

        registry = SkillRegistry([skills_dir])

        agents = registry.get_agents()
        assert "skill-one/common-name" in agents
        assert "skill-two/common-name" in agents
        assert agents["skill-one/common-name"].description == "Agent from skill one"
        assert agents["skill-two/common-name"].description == "Agent from skill two"


class TestErrorHandling:
    """Tests for graceful error handling."""

    def test_bad_yaml_in_skill_md(self, tmp_path: Path) -> None:
        """AC: Bad YAML in SKILL.md is skipped with warning."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "bad-yaml-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: [invalid yaml
description: "Missing quote
---
""")

        registry = SkillRegistry([skills_dir])

        assert "bad-yaml-skill" not in registry.skills

    def test_bad_yaml_in_agent(self, tmp_path: Path) -> None:
        """AC: Bad YAML in agent file is skipped with warning."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()

        (agents_dir / "bad-agent.md").write_text("""---
description: {bad yaml
---
""")

        registry = SkillRegistry([skills_dir])

        assert "test-skill/bad-agent" not in registry.get_agents()

    def test_mixed_valid_invalid_continues_loading(self, tmp_path: Path) -> None:
        """AC: Mixed valid/invalid skills continue loading valid ones."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        # Valid skill
        create_skill(skills_dir, "valid-skill", "A valid skill")

        # Invalid skill (no SKILL.md)
        (skills_dir / "invalid-skill").mkdir()

        # Another valid skill
        create_skill(skills_dir, "another-valid", "Another valid skill")

        registry = SkillRegistry([skills_dir])

        assert "valid-skill" in registry.skills
        assert "another-valid" in registry.skills
        assert "invalid-skill" not in registry.skills


class TestSkillMetadata:
    """Tests for skill metadata retention."""

    def test_skills_property_returns_metadata(self, tmp_path: Path) -> None:
        """AC: Skills property returns retained skill metadata."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        create_skill(skills_dir, "skill-one", "First skill", version="1.0.0")
        create_skill(skills_dir, "skill-two", "Second skill")

        registry = SkillRegistry([skills_dir])

        assert "skill-one" in registry.skills
        assert "skill-two" in registry.skills
        assert registry.skills["skill-one"].version == "1.0.0"
        assert registry.skills["skill-two"].version is None


class TestMultiSourceDiscovery:
    """Tests for multi-source registry discovery and collision handling."""

    def test_discovers_skills_from_multiple_sources(self, tmp_path: Path) -> None:
        """AC: Skills from both sources are discovered."""
        source1 = tmp_path / "builtin"
        source2 = tmp_path / "workspace"
        source1.mkdir()
        source2.mkdir()

        create_skill(source1, "builtin-skill", "A built-in skill")
        create_skill(source2, "workspace-skill", "A workspace skill")

        registry = SkillRegistry([source1, source2])

        assert "builtin-skill" in registry.skills
        assert "workspace-skill" in registry.skills
        assert registry.skills["builtin-skill"].description == "A built-in skill"
        assert registry.skills["workspace-skill"].description == "A workspace skill"

    def test_last_wins_on_name_collision(self, tmp_path: Path) -> None:
        """AC: Same skill name in two sources — second source wins completely."""
        source1 = tmp_path / "builtin"
        source2 = tmp_path / "workspace"
        source1.mkdir()
        source2.mkdir()

        create_skill(source1, "shared-skill", "Built-in version", version="1.0.0")
        create_skill(source2, "shared-skill", "Workspace version", version="2.0.0")

        registry = SkillRegistry([source1, source2])

        assert "shared-skill" in registry.skills
        skill = registry.skills["shared-skill"]
        assert skill.description == "Workspace version"
        assert skill.version == "2.0.0"
        assert skill.path == source2 / "shared-skill"

    def test_collision_clears_earlier_agents(self, tmp_path: Path) -> None:
        """AC: On name collision, first source's agents are removed (no orphans)."""
        source1 = tmp_path / "builtin"
        source2 = tmp_path / "workspace"
        source1.mkdir()
        source2.mkdir()

        # Source 1: skill with agent
        skill1_dir = create_skill(source1, "shared", "Built-in version")
        create_agent(skill1_dir, "builtin-agent", "Built-in agent")

        # Source 2: same skill with different agent
        skill2_dir = create_skill(source2, "shared", "Workspace version")
        create_agent(skill2_dir, "workspace-agent", "Workspace agent")

        registry = SkillRegistry([source1, source2])

        agents = registry.get_agents()
        # Source 1's agent should be gone
        assert "shared/builtin-agent" not in agents
        # Source 2's agent should be present
        assert "shared/workspace-agent" in agents

    def test_empty_source_in_list_gracefully_skipped(self, tmp_path: Path) -> None:
        """AC: Empty/missing source in list is skipped, other sources work."""
        source1 = tmp_path / "builtin"
        source2 = tmp_path / "nonexistent"  # doesn't exist
        source3 = tmp_path / "workspace"

        source1.mkdir()
        source3.mkdir()

        create_skill(source1, "builtin-skill", "Built-in")
        create_skill(source3, "workspace-skill", "Workspace")

        registry = SkillRegistry([source1, source2, source3])

        assert "builtin-skill" in registry.skills
        assert "workspace-skill" in registry.skills

    def test_single_source_list_works(self, tmp_path: Path) -> None:
        """AC: Single-source list works identically to before (regression)."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        create_skill(skills_dir, "test-skill", "A test skill")

        registry = SkillRegistry([skills_dir])

        assert "test-skill" in registry.skills
        assert registry.skills["test-skill"].description == "A test skill"

    def test_collision_with_agents_in_second_source_only(self, tmp_path: Path) -> None:
        """AC: Collision where only second source has agents works correctly."""
        source1 = tmp_path / "builtin"
        source2 = tmp_path / "workspace"
        source1.mkdir()
        source2.mkdir()

        # Source 1: skill without agents
        create_skill(source1, "shared", "Built-in version")

        # Source 2: same skill with agent
        skill2_dir = create_skill(source2, "shared", "Workspace version")
        create_agent(skill2_dir, "my-agent", "Workspace agent")

        registry = SkillRegistry([source1, source2])

        agents = registry.get_agents()
        assert "shared/my-agent" in agents


class TestRegistryRefresh:
    """Tests for hot-reload refresh functionality (DLT-038)."""

    def test_mark_dirty_sets_flag(self, tmp_path: Path) -> None:
        """AC: mark_dirty() sets _dirty to True."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry([skills_dir])
        assert registry._dirty is False

        registry.mark_dirty()

        assert registry._dirty is True

    def test_refresh_no_op_when_not_dirty(self, tmp_path: Path, mocker) -> None:
        """AC: refresh() is no-op when _dirty is False (no filesystem access)."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry([skills_dir])
        # Spy on _discover to verify it's not called
        spy_discover = mocker.spy(registry, "_discover")

        # _dirty is False by default
        assert registry._dirty is False

        registry.refresh()

        # _discover should not be called
        spy_discover.assert_not_called()

    def test_refresh_rediscover_when_dirty(self, tmp_path: Path) -> None:
        """AC: refresh() re-discovers skills from disk when dirty."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        # Create initial skill
        create_skill(skills_dir, "initial-skill", "Initial")

        registry = SkillRegistry([skills_dir])
        assert "initial-skill" in registry.skills

        # Add new skill after construction
        create_skill(skills_dir, "new-skill", "New")

        # Mark dirty and refresh
        registry.mark_dirty()
        registry.refresh()

        # New skill should now be present
        assert "new-skill" in registry.skills
        assert "initial-skill" in registry.skills

    def test_refresh_picks_up_new_skills(self, tmp_path: Path) -> None:
        """AC: New skills added to disk appear after mark_dirty() + refresh()."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry([skills_dir])
        assert registry.skills == {}

        # Add skill after construction
        create_skill(skills_dir, "added-later", "Added after construction")

        registry.mark_dirty()
        registry.refresh()

        assert "added-later" in registry.skills

    def test_refresh_removes_deleted_skills(self, tmp_path: Path) -> None:
        """AC: Deleted skills removed after mark_dirty() + refresh()."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        # Create initial skill
        skill_dir = create_skill(skills_dir, "to-delete", "Will be deleted")

        registry = SkillRegistry([skills_dir])
        assert "to-delete" in registry.skills

        # Delete the skill directory
        shutil.rmtree(skill_dir)

        registry.mark_dirty()
        registry.refresh()

        assert "to-delete" not in registry.skills

    def test_swap_on_success_restores_on_failure(self, tmp_path: Path, mocker) -> None:
        """AC: When _discover() raises, old dict references are restored."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        # Create initial skill
        create_skill(skills_dir, "existing-skill", "Existing")

        registry = SkillRegistry([skills_dir])
        old_agents = registry._agents
        old_skills = registry._skills

        # Make _discover raise an exception
        mocker.patch.object(
            registry,
            "_discover",
            side_effect=PermissionError("Permission denied"),
        )

        registry.mark_dirty()
        registry.refresh()

        # Old references should be restored
        assert registry._agents is old_agents
        assert registry._skills is old_skills
        assert "existing-skill" in registry.skills

    def test_dirty_remains_true_after_failed_refresh(self, tmp_path: Path, mocker) -> None:
        """AC: After failed refresh, _dirty remains True for retry."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry([skills_dir])

        # Make _discover raise an exception
        mocker.patch.object(
            registry,
            "_discover",
            side_effect=PermissionError("Permission denied"),
        )

        registry.mark_dirty()
        registry.refresh()

        # _dirty should still be True
        assert registry._dirty is True

    def test_refresh_clears_dirty_flag_on_success(self, tmp_path: Path) -> None:
        """AC: Successful refresh clears _dirty flag."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry([skills_dir])
        registry.mark_dirty()
        assert registry._dirty is True

        registry.refresh()

        assert registry._dirty is False

    def test_refresh_handles_missing_skills_directory(self, tmp_path: Path) -> None:
        """AC: refresh() gracefully handles missing skills directory."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        # Create initial skill
        create_skill(skills_dir, "skill-one", "One")

        registry = SkillRegistry([skills_dir])
        assert "skill-one" in registry.skills

        # Delete the entire skills directory
        shutil.rmtree(skills_dir)

        registry.mark_dirty()
        registry.refresh()

        # Should have empty dicts (gracefully emptied)
        assert registry.skills == {}
        assert registry.get_agents() == {}
        assert registry._dirty is False  # Successful refresh (empty is success)


class TestAddSource:
    """Tests for SkillRegistry.add_source() (DLT-063)."""

    def test_add_source_discovers_skills(self, tmp_path: Path) -> None:
        """add_source() discovers skills from the new path immediately."""
        initial_dir = tmp_path / "initial"
        initial_dir.mkdir()
        new_dir = tmp_path / "added"
        new_dir.mkdir()

        registry = SkillRegistry([initial_dir])
        assert registry.skills == {}

        create_skill(new_dir, "added-skill", "Added via add_source")

        registry.add_source(new_dir)

        assert "added-skill" in registry.skills
        assert registry.skills["added-skill"].description == "Added via add_source"

    def test_add_source_included_in_refresh(self, tmp_path: Path) -> None:
        """Added source is included in subsequent refresh() calls."""
        initial_dir = tmp_path / "initial"
        initial_dir.mkdir()
        added_dir = tmp_path / "added"
        added_dir.mkdir()

        create_skill(added_dir, "refreshed-skill", "Before refresh")

        registry = SkillRegistry([initial_dir])
        registry.add_source(added_dir)
        assert "refreshed-skill" in registry.skills

        registry.mark_dirty()
        registry.refresh()

        assert "refreshed-skill" in registry.skills

    def test_add_source_nonexistent_path_no_error(self, tmp_path: Path) -> None:
        """add_source() with non-existent path does not raise."""
        initial_dir = tmp_path / "initial"
        initial_dir.mkdir()

        registry = SkillRegistry([initial_dir])

        # Should not raise
        registry.add_source(tmp_path / "nonexistent")

        assert registry.skills == {}


class TestSkillDependsOnParsing:
    """Tests for depends_on frontmatter parsing (DLT-118)."""

    def test_parses_depends_on_list(self, tmp_path: Path) -> None:
        """AC: depends_on: [skill-a, skill-b] → tuple preserving order."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            '---\ndescription: "Test"\ndepends_on:\n  - skill-a\n  - skill-b\n---\n\nBody'
        )

        registry = SkillRegistry([skills_dir])

        assert registry.skills["my-skill"].depends_on == ("skill-a", "skill-b")

    def test_missing_depends_on_defaults_empty(self, tmp_path: Path) -> None:
        """AC: No depends_on field → empty tuple."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text('---\ndescription: "Test"\n---\n\nBody')

        registry = SkillRegistry([skills_dir])

        assert registry.skills["my-skill"].depends_on == ()

    def test_empty_depends_on_list_behaves_same_as_missing(self, tmp_path: Path) -> None:
        """AC: depends_on: [] → empty tuple, no warning."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text('---\ndescription: "Test"\ndepends_on: []\n---\n\nBody')

        registry = SkillRegistry([skills_dir])

        assert registry.skills["my-skill"].depends_on == ()

    def test_invalid_depends_on_warns_and_falls_back(self, tmp_path: Path) -> None:
        """AC: Non-list depends_on → skill loads with empty tuple, warning logged."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            '---\ndescription: "Test"\ndepends_on: "not-a-list"\n---\n\nBody'
        )

        registry = SkillRegistry([skills_dir])

        assert "my-skill" in registry.skills
        assert registry.skills["my-skill"].depends_on == ()

    def test_depends_on_non_string_elements_warns_and_falls_back(self, tmp_path: Path) -> None:
        """AC: depends_on with non-string elements → skill loads with empty tuple."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            '---\ndescription: "Test"\ndepends_on:\n  - foo\n  - 123\n---\n\nBody'
        )

        registry = SkillRegistry([skills_dir])

        assert "my-skill" in registry.skills
        assert registry.skills["my-skill"].depends_on == ()

    def test_depends_on_case_sensitive(self, tmp_path: Path) -> None:
        """AC: Case-sensitive names — Foo and foo are distinct, preserved verbatim."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        (skills_dir / "Foo").mkdir()
        (skills_dir / "Foo" / "SKILL.md").write_text(
            '---\ndescription: "Upper"\ndepends_on:\n  - foo\n---\n\nBody'
        )

        (skills_dir / "foo").mkdir()
        (skills_dir / "foo" / "SKILL.md").write_text('---\ndescription: "Lower"\n---\n\nBody')

        registry = SkillRegistry([skills_dir])

        assert registry.skills["Foo"].depends_on == ("foo",)
        assert registry.skills["foo"].depends_on == ()


class TestResolveChain:
    """Tests for SkillRegistry.resolve_chain() (DLT-118)."""

    def _build_skills(self, skills_dir: Path, skills: dict[str, tuple[str, list[str]]]) -> None:
        """Create multiple skills with dependencies.

        Args:
            skills: mapping of name → (description, [depends_on names])
        """
        for name, (desc, deps) in skills.items():
            skill_dir = skills_dir / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            dep_yaml = ""
            if deps:
                dep_yaml = f"\ndepends_on: {deps}"
            (skill_dir / "SKILL.md").write_text(
                f'---\ndescription: "{desc}"{dep_yaml}\n---\n\nBody for {name}'
            )

    def test_linear_chain_deps_first_anchor_last(self, tmp_path: Path) -> None:
        """AC: A→B→C returns [C, B, A]."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "A": ("Desc A", ["B"]),
            "B": ("Desc B", ["C"]),
            "C": ("Desc C", []),
        })

        registry = SkillRegistry([skills_dir])
        chain = registry.resolve_chain("A")

        names = [s.name for s in chain]
        assert names == ["C", "B", "A"]

    def test_cycle_terminates_each_skill_once(self, tmp_path: Path) -> None:
        """AC: A↔B → both appear once, terminates."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "A": ("Desc A", ["B"]),
            "B": ("Desc B", ["A"]),
        })

        registry = SkillRegistry([skills_dir])
        chain = registry.resolve_chain("A")

        names = [s.name for s in chain]
        assert len(names) == len(set(names))
        assert set(names) == {"A", "B"}
        assert names[-1] == "A"

    def test_self_reference_returns_single_element(self, tmp_path: Path) -> None:
        """AC: A depends on [a] → returns [A]."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "A": ("Desc A", ["A"]),
        })

        registry = SkillRegistry([skills_dir])
        chain = registry.resolve_chain("A")

        assert [s.name for s in chain] == ["A"]

    def test_diamond_shared_dep_appears_once(self, tmp_path: Path) -> None:
        """AC: A→B, A→C, B→D, C→D → D once before B and C, A last."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "A": ("Desc A", ["B", "C"]),
            "B": ("Desc B", ["D"]),
            "C": ("Desc C", ["D"]),
            "D": ("Desc D", []),
        })

        registry = SkillRegistry([skills_dir])
        chain = registry.resolve_chain("A")

        names = [s.name for s in chain]
        assert names[-1] == "A"
        assert names.count("D") == 1
        assert names.index("D") < names.index("B")
        assert names.index("D") < names.index("C")

    def test_no_dependencies_returns_single_element_chain(self, tmp_path: Path) -> None:
        """AC: Skill with no deps → [self]."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "A": ("Desc A", []),
        })

        registry = SkillRegistry([skills_dir])
        chain = registry.resolve_chain("A")

        assert [s.name for s in chain] == ["A"]

    def test_unknown_dep_silently_skipped_during_resolution(self, tmp_path: Path) -> None:
        """AC: A depends on [missing-x] → returns [A], no warning at resolution."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "A": ("Desc A", ["missing-x"]),
        })

        registry = SkillRegistry([skills_dir])
        chain = registry.resolve_chain("A")

        assert [s.name for s in chain] == ["A"]

    def test_partial_unknown_real_dep_still_resolved(self, tmp_path: Path) -> None:
        """AC: A depends on [missing-x, real-b] → returns [real-b, A]."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "A": ("Desc A", ["missing-x", "real-b"]),
            "real-b": ("Desc B", []),
        })

        registry = SkillRegistry([skills_dir])
        chain = registry.resolve_chain("A")

        assert [s.name for s in chain] == ["real-b", "A"]

    def test_cache_hit_returns_same_list_object(self, tmp_path: Path) -> None:
        """AC: Second call returns same list object (memoized)."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "A": ("Desc A", ["B"]),
            "B": ("Desc B", []),
        })

        registry = SkillRegistry([skills_dir])
        first = registry.resolve_chain("A")
        second = registry.resolve_chain("A")

        assert first is second

    def test_keyerror_when_anchor_not_registered(self, tmp_path: Path) -> None:
        """AC: resolve_chain("nonexistent") raises KeyError."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry([skills_dir])

        with pytest.raises(KeyError):
            registry.resolve_chain("nonexistent")

    def test_workspace_override_uses_workspace_depends_on(self, tmp_path: Path) -> None:
        """AC: Two sources with same skill name, different deps → last-wins deps used."""
        source1 = tmp_path / "builtin"
        source2 = tmp_path / "workspace"
        source1.mkdir()
        source2.mkdir()

        self._build_skills(source1, {
            "foo": ("Built-in", ["a"]),
        })
        self._build_skills(source2, {
            "foo": ("Workspace", ["b"]),
        })
        self._build_skills(source1, {
            "a": ("A", []),
        })
        self._build_skills(source2, {
            "b": ("B", []),
        })

        registry = SkillRegistry([source1, source2])
        chain = registry.resolve_chain("foo")

        names = [s.name for s in chain]
        assert names == ["b", "foo"]

    def test_cross_source_dependency_resolves_normally(self, tmp_path: Path) -> None:
        """AC: Built-in skill depends on workspace-only skill → resolves across sources."""
        source1 = tmp_path / "builtin"
        source2 = tmp_path / "workspace"
        source1.mkdir()
        source2.mkdir()

        self._build_skills(source1, {
            "foo": ("Built-in", ["bar"]),
        })
        self._build_skills(source2, {
            "bar": ("Workspace-only", []),
        })

        registry = SkillRegistry([source1, source2])
        chain = registry.resolve_chain("foo")

        names = [s.name for s in chain]
        assert names == ["bar", "foo"]

    def test_case_sensitive_distinct_skills_do_not_conflate(self, tmp_path: Path) -> None:
        """AC: Foo depends on [foo] → chain is [foo, Foo] (distinct entries)."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        self._build_skills(skills_dir, {
            "Foo": ("Upper", ["foo"]),
            "foo": ("Lower", []),
        })

        registry = SkillRegistry([skills_dir])
        chain = registry.resolve_chain("Foo")

        names = [s.name for s in chain]
        assert names == ["foo", "Foo"]


class TestValidateDeps:
    """Tests for SkillRegistry._validate_deps() (DLT-118)."""

    def test_unknown_deps_warn_once_per_skill(self, tmp_path: Path) -> None:
        """AC: Skill with unknown deps gets one warning listing all missing names."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            '---\ndescription: "Test"\ndepends_on:\n  - missing-x\n  - missing-y\n---\n\nBody'
        )

        warnings: list[str] = []
        sink_id = logger.add(
            lambda m: warnings.append(str(m)),
            filter=lambda r: r["level"].no >= 30,
        )

        try:
            registry = SkillRegistry([skills_dir])
        finally:
            logger.remove(sink_id)

        assert "my-skill" in registry.skills
        assert any("my-skill" in w and "missing-x" in w and "missing-y" in w for w in warnings)

    def test_skill_with_all_valid_deps_no_warning(self, tmp_path: Path) -> None:
        """AC: Skill with only valid deps → no warning."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        (skills_dir / "dep-skill").mkdir(parents=True)
        (skills_dir / "dep-skill" / "SKILL.md").write_text(
            '---\ndescription: "Dep"\n---\n\nBody'
        )

        skill_dir2 = skills_dir / "my-skill"
        skill_dir2.mkdir()
        (skill_dir2 / "SKILL.md").write_text(
            '---\ndescription: "Test"\ndepends_on:\n  - dep-skill\n---\n\nBody'
        )

        warnings: list[str] = []
        sink_id = logger.add(
            lambda m: warnings.append(str(m)),
            filter=lambda r: r["level"].no >= 30,
        )

        try:
            SkillRegistry([skills_dir])
        finally:
            logger.remove(sink_id)

        assert not any("unknown dependencies" in w for w in warnings)

    def test_skill_with_empty_depends_on_no_warning(self, tmp_path: Path) -> None:
        """AC: Skill with empty depends_on → no warning."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text('---\ndescription: "Test"\n---\n\nBody')

        warnings: list[str] = []
        sink_id = logger.add(
            lambda m: warnings.append(str(m)),
            filter=lambda r: r["level"].no >= 30,
        )

        try:
            SkillRegistry([skills_dir])
        finally:
            logger.remove(sink_id)

        assert not any("unknown dependencies" in w for w in warnings)


class TestCacheInvalidation:
    """Tests for chain cache invalidation on refresh/add_source (DLT-118)."""

    def test_refresh_clears_chain_cache(self, tmp_path: Path) -> None:
        """AC: After refresh, cached chains are recomputed from fresh state."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        (skills_dir / "A").mkdir()
        (skills_dir / "A" / "SKILL.md").write_text(
            '---\ndescription: "A"\ndepends_on:\n  - B\n---\n\nBody'
        )
        (skills_dir / "B").mkdir()
        (skills_dir / "B" / "SKILL.md").write_text('---\ndescription: "B"\n---\n\nBody')

        registry = SkillRegistry([skills_dir])
        chain1 = registry.resolve_chain("A")
        assert [s.name for s in chain1] == ["B", "A"]

        # Modify B's dependencies on disk
        (skills_dir / "B" / "SKILL.md").write_text(
            '---\ndescription: "B"\ndepends_on:\n  - C\n---\n\nBody'
        )
        (skills_dir / "C").mkdir()
        (skills_dir / "C" / "SKILL.md").write_text('---\ndescription: "C"\n---\n\nBody')

        registry.mark_dirty()
        registry.refresh()

        chain2 = registry.resolve_chain("A")
        assert [s.name for s in chain2] == ["C", "B", "A"]

    def test_add_source_clears_chain_cache(self, tmp_path: Path) -> None:
        """AC: After add_source, chains reflect the new skills."""
        source1 = tmp_path / "initial"
        source2 = tmp_path / "added"
        source1.mkdir()
        source2.mkdir()

        (source1 / "A").mkdir()
        (source1 / "A" / "SKILL.md").write_text(
            '---\ndescription: "A"\ndepends_on:\n  - B\n---\n\nBody'
        )

        registry = SkillRegistry([source1])
        # A depends on B which doesn't exist → chain is just [A]
        chain1 = registry.resolve_chain("A")
        assert [s.name for s in chain1] == ["A"]

        # Add source that contains B
        (source2 / "B").mkdir()
        (source2 / "B" / "SKILL.md").write_text('---\ndescription: "B"\n---\n\nBody')

        registry.add_source(source2)

        chain2 = registry.resolve_chain("A")
        assert [s.name for s in chain2] == ["B", "A"]

    def test_refresh_failure_leaves_cache_empty(self, tmp_path: Path, mocker) -> None:
        """AC: Failed refresh leaves cache empty, _skills restored."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)

        (skills_dir / "A").mkdir()
        (skills_dir / "A" / "SKILL.md").write_text('---\ndescription: "A"\n---\n\nBody')

        registry = SkillRegistry([skills_dir])
        registry.resolve_chain("A")

        # Force discover failure
        mocker.patch.object(
            registry,
            "_discover",
            side_effect=PermissionError("nope"),
        )

        registry.mark_dirty()
        registry.refresh()

        assert registry._chain_cache == {}
        assert "A" in registry.skills

    def test_validate_deps_called_after_add_source(self, tmp_path: Path) -> None:
        """AC: add_source triggers _validate_deps for the new skills."""
        source1 = tmp_path / "initial"
        source2 = tmp_path / "added"
        source1.mkdir()
        source2.mkdir()

        (source2 / "A").mkdir()
        (source2 / "A" / "SKILL.md").write_text(
            '---\ndescription: "A"\ndepends_on:\n  - nonexistent\n---\n\nBody'
        )

        registry = SkillRegistry([source1])
        registry.add_source(source2)

        # Skill loaded successfully despite missing dep
        assert "A" in registry.skills
