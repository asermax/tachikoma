"""Tests for SkillRegistry.

Tests for DLT-003: Skill system foundation and sub-agent delegation.
"""

import shutil
from pathlib import Path

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

        registry = SkillRegistry(tmp_path / "workspace")

        assert registry.get_agents() == {}
        assert registry.skills == {}

    def test_missing_skills_dir(self, tmp_path: Path) -> None:
        """AC: Missing skills/ directory → empty agents dict (valid state)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        registry = SkillRegistry(workspace)

        assert registry.get_agents() == {}
        assert registry.skills == {}

    def test_ignores_regular_files(self, tmp_path: Path) -> None:
        """AC: Only directory entries are considered as skills."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        # Create a regular file (should be ignored)
        (skills_dir / "readme.txt").write_text("Not a skill")

        registry = SkillRegistry(workspace)

        assert registry.get_agents() == {}
        assert registry.skills == {}

    def test_discovers_valid_skills(self, tmp_path: Path) -> None:
        """AC: Valid skill directories are discovered."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        create_skill(skills_dir, "test-skill", "A test skill")

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

        assert "my-skill" in registry.skills
        assert registry.skills["my-skill"].name == "my-skill"

    def test_valid_skill_md(self, tmp_path: Path) -> None:
        """AC: Valid SKILL.md is loaded correctly."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        create_skill(skills_dir, "valid-skill", "A valid skill", version="1.0.0")

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

        assert registry.get_agents() == {}

    def test_empty_agents_dir(self, tmp_path: Path) -> None:
        """AC: Empty agents/ dir → no agents loaded."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "empty-agents", "A skill")
        (skill_dir / "agents").mkdir()

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

        assert registry.get_agents() == {}

    def test_discovers_md_agents(self, tmp_path: Path) -> None:
        """AC: .md files in agents/ are discovered."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        create_agent(skill_dir, "extractor", "Extracts data")

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

        assert registry.get_agents() == {}

    def test_empty_body_valid(self, tmp_path: Path) -> None:
        """AC: Agent with empty markdown body is valid (empty prompt)."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        skill_dir = create_skill(skills_dir, "test-skill", "A skill")
        create_agent(skill_dir, "empty-body", "No prompt body", body="")

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

        agents = registry.get_agents()
        assert "skill-one/common-name" in agents
        assert "skill-two/common-name" in agents
        assert (
            agents["skill-one/common-name"].description == "Agent from skill one"
        )
        assert (
            agents["skill-two/common-name"].description == "Agent from skill two"
        )


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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

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

        registry = SkillRegistry(workspace)

        assert "skill-one" in registry.skills
        assert "skill-two" in registry.skills
        assert registry.skills["skill-one"].version == "1.0.0"
        assert registry.skills["skill-two"].version is None


class TestRegistryRefresh:
    """Tests for hot-reload refresh functionality (DLT-038)."""

    def test_mark_dirty_sets_flag(self, tmp_path: Path) -> None:
        """AC: mark_dirty() sets _dirty to True."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry(workspace)
        assert registry._dirty is False

        registry.mark_dirty()

        assert registry._dirty is True

    def test_refresh_no_op_when_not_dirty(self, tmp_path: Path, mocker) -> None:
        """AC: refresh() is no-op when _dirty is False (no filesystem access)."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry(workspace)
        # Spy on _discover to verify it's not called
        spy_discover = mocker.spy(registry, "_discover")

        # _dirty is False by default
        assert registry._dirty is False

        registry.refresh()

        # _discover should not be called
        spy_discover.assert_not_called()

    def test_refresh_rediscover_when_dirty(self, tmp_path: Path) -> None:
        """AC: refresh() re-discovers skills from disk when dirty."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        # Create initial skill
        create_skill(skills_dir, "initial-skill", "Initial")

        registry = SkillRegistry(workspace)
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
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry(workspace)
        assert registry.skills == {}

        # Add skill after construction
        create_skill(skills_dir, "added-later", "Added after construction")

        registry.mark_dirty()
        registry.refresh()

        assert "added-later" in registry.skills

    def test_refresh_removes_deleted_skills(self, tmp_path: Path) -> None:
        """AC: Deleted skills removed after mark_dirty() + refresh()."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        # Create initial skill
        skill_dir = create_skill(skills_dir, "to-delete", "Will be deleted")

        registry = SkillRegistry(workspace)
        assert "to-delete" in registry.skills

        # Delete the skill directory
        shutil.rmtree(skill_dir)

        registry.mark_dirty()
        registry.refresh()

        assert "to-delete" not in registry.skills

    def test_swap_on_success_restores_on_failure(self, tmp_path: Path, mocker) -> None:
        """AC: When _discover() raises, old dict references are restored."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        # Create initial skill
        create_skill(skills_dir, "existing-skill", "Existing")

        registry = SkillRegistry(workspace)
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
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry(workspace)

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
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        registry = SkillRegistry(workspace)
        registry.mark_dirty()
        assert registry._dirty is True

        registry.refresh()

        assert registry._dirty is False

    def test_refresh_handles_missing_skills_directory(self, tmp_path: Path) -> None:
        """AC: refresh() gracefully handles missing skills directory."""
        workspace = tmp_path / "workspace"
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True)

        # Create initial skill
        create_skill(skills_dir, "skill-one", "One")

        registry = SkillRegistry(workspace)
        assert "skill-one" in registry.skills

        # Delete the entire skills directory
        shutil.rmtree(skills_dir)

        registry.mark_dirty()
        registry.refresh()

        # Should have empty dicts (gracefully emptied)
        assert registry.skills == {}
        assert registry.get_agents() == {}
        assert registry._dirty is False  # Successful refresh (empty is success)
