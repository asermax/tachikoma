"""Tests for skills bootstrap hook.

Tests for DLT-003: Skill system foundation and sub-agent delegation.
Tests for DLT-032: Multi-source registry and provider injection.
Updated for DLT-038: Hot-reload skills at runtime (registry in extras).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.skills.hooks import skills_hook
from tachikoma.skills.registry import SkillRegistry


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    config_path = tmp_path / "config.toml"
    workspace_path = tmp_path / "workspace"
    config_path.write_text(f'[workspace]\npath = "{workspace_path}"\n')
    return SettingsManager(config_path)


@pytest.fixture
async def ctx(settings_manager: SettingsManager) -> BootstrapContext:
    # Ensure workspace exists (normally created by workspace_hook)
    ws = settings_manager.settings.workspace
    ws.path.mkdir(parents=True, exist_ok=True)

    ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
    yield ctx


class TestSkillsHook:
    """Tests for skills_hook bootstrap."""

    async def test_creates_skills_directory_when_missing(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook creates skills/ directory when it doesn't exist."""
        workspace_path = settings_manager.settings.workspace.path

        await skills_hook(ctx)

        skills_path = workspace_path / "skills"
        assert skills_path.is_dir()

    async def test_idempotent_when_directory_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook is idempotent when skills/ already exists (skips, no error)."""
        workspace_path = settings_manager.settings.workspace.path

        # Run twice
        await skills_hook(ctx)
        await skills_hook(ctx)

        # Should still have a valid skills directory
        assert (workspace_path / "skills").is_dir()

    async def test_no_error_when_workspace_exists_but_skills_missing(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: No error when workspace path exists but skills/ doesn't."""
        workspace_path = settings_manager.settings.workspace.path

        # Workspace exists from fixture, but skills/ doesn't
        assert not (workspace_path / "skills").exists()

        await skills_hook(ctx)

        # Should now exist
        assert (workspace_path / "skills").is_dir()

    async def test_permission_error_propagates(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: When mkdir raises PermissionError, the exception propagates.

        The Bootstrap class wraps this in BootstrapError, but the hook itself
        should let the exception propagate.
        """
        with (
            patch.object(
                Path,
                "mkdir",
                side_effect=PermissionError("Permission denied"),
            ),
            pytest.raises(PermissionError, match="Permission denied"),
        ):
            await skills_hook(ctx)


class TestSkillsHookRegistry:
    """Tests for skills_hook registry creation and extras exposure (DLT-032, DLT-038)."""

    async def test_creates_registry_and_stores_in_extras(
        self, ctx: BootstrapContext
    ) -> None:
        """AC: Hook creates registry and stores in ctx.extras["skill_registry"]."""
        await skills_hook(ctx)

        assert "skill_registry" in ctx.extras
        assert isinstance(ctx.extras["skill_registry"], SkillRegistry)

    async def test_includes_builtin_path_when_exists(
        self, ctx: BootstrapContext, tmp_path: Path
    ) -> None:
        """AC: Hook includes builtin path in sources when it exists."""
        # Create a fake builtin skill directory (must be named "builtin" to match hooks.py)
        builtin_path = tmp_path / "builtin"
        builtin_path.mkdir()
        skill_dir = builtin_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: Test builtin skill\n---\n\nContent"
        )

        # Patch __file__ to point to our temp directory
        with patch(
            "tachikoma.skills.hooks.__file__",
            str(tmp_path / "hooks.py"),
        ):
            await skills_hook(ctx)

        registry = ctx.extras["skill_registry"]
        assert isinstance(registry, SkillRegistry)
        # Should have discovered the builtin skill
        assert "test-skill" in registry.skills

    async def test_logs_warning_when_builtin_missing(
        self, ctx: BootstrapContext, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC: Hook logs warning and continues without builtin when path doesn't exist."""
        # Patch __file__ to point to a location without builtin/
        with patch(
            "tachikoma.skills.hooks.__file__",
            str(tmp_path / "nonexistent" / "hooks.py"),
        ):
            await skills_hook(ctx)

        # Should still have registry with workspace skills only
        assert "skill_registry" in ctx.extras
        registry = ctx.extras["skill_registry"]
        assert isinstance(registry, SkillRegistry)

        # Note: Loguru logs to stderr, not standard logging.
        # The warning is visible in captured stderr but caplog won't see it.
        # This test verifies the hook continues gracefully despite missing builtin.

    async def test_registry_contains_both_builtin_and_workspace_skills(
        self, ctx: BootstrapContext, settings_manager: SettingsManager, tmp_path: Path
    ) -> None:
        """AC: Registry in extras contains both builtin and workspace skills."""
        # Create a fake builtin skill (must be named "builtin" to match hooks.py)
        builtin_path = tmp_path / "builtin"
        builtin_path.mkdir()
        builtin_skill = builtin_path / "builtin-skill"
        builtin_skill.mkdir()
        (builtin_skill / "SKILL.md").write_text(
            "---\ndescription: Builtin skill\n---\n\nContent"
        )

        # Create a workspace skill
        workspace_skills = settings_manager.settings.workspace.path / "skills"
        workspace_skills.mkdir(parents=True, exist_ok=True)
        ws_skill = workspace_skills / "workspace-skill"
        ws_skill.mkdir()
        (ws_skill / "SKILL.md").write_text(
            "---\ndescription: Workspace skill\n---\n\nContent"
        )

        with patch(
            "tachikoma.skills.hooks.__file__",
            str(tmp_path / "hooks.py"),
        ):
            await skills_hook(ctx)

        registry = ctx.extras["skill_registry"]
        assert "builtin-skill" in registry.skills
        assert "workspace-skill" in registry.skills

    async def test_registry_discovers_existing_skills(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Registry discovers skills that exist in the directory at hook time."""
        workspace_path = settings_manager.settings.workspace.path

        # Create a skill before running the hook
        skills_dir = workspace_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "description: A test skill\n"
            "---\n"
            "\n"
            "Test content"
        )

        await skills_hook(ctx)

        registry = ctx.extras["skill_registry"]
        assert isinstance(registry, SkillRegistry)
        assert "test-skill" in registry.skills

    async def test_hook_creates_registry_on_every_run(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Registry creation happens on every launch (not just first run)."""
        # First run
        await skills_hook(ctx)
        first_registry = ctx.extras["skill_registry"]

        # Clear extras to simulate a new run
        ctx.extras.clear()

        # Second run
        await skills_hook(ctx)
        second_registry = ctx.extras["skill_registry"]

        # Should have created a new registry instance
        assert isinstance(second_registry, SkillRegistry)
        assert first_registry is not second_registry  # Different instances
