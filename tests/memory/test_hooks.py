"""Tests for memory bootstrap hook.

Extract and store memories from conversations.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.memory.hooks import memory_hook


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    config_path = tmp_path / "config.toml"
    workspace_path = tmp_path / "workspace"
    config_path.write_text(f'[workspace]\npath = "{workspace_path}"\n')
    return SettingsManager(config_path)


@pytest.fixture
async def ctx(settings_manager: SettingsManager) -> BootstrapContext:
    # Ensure workspace and data dirs exist (normally created by workspace_hook)
    ws = settings_manager.settings.workspace
    ws.path.mkdir(parents=True, exist_ok=True)
    ws.data_path.mkdir(exist_ok=True)

    ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
    yield ctx


class TestMemoryHook:
    """Tests for memory_hook."""

    async def test_creates_memories_directory_structure(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook creates all five directories (memories/, episodic/, facts/,
        preferences/, transcripts/)."""
        workspace_path = settings_manager.settings.workspace.path

        await memory_hook(ctx)

        memories_root = workspace_path / "memories"
        assert memories_root.is_dir()
        assert (memories_root / "episodic").is_dir()
        assert (memories_root / "facts").is_dir()
        assert (memories_root / "preferences").is_dir()
        assert (memories_root / "transcripts").is_dir()

    async def test_idempotent_when_directories_exist(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Running twice produces no error and no change."""
        workspace_path = settings_manager.settings.workspace.path

        # Run twice
        await memory_hook(ctx)
        await memory_hook(ctx)

        # Verify directories still exist and are correct
        memories_root = workspace_path / "memories"
        assert memories_root.is_dir()
        assert (memories_root / "episodic").is_dir()
        assert (memories_root / "facts").is_dir()
        assert (memories_root / "preferences").is_dir()
        assert (memories_root / "transcripts").is_dir()

    async def test_creates_subdirectories_inside_workspace_path(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Directories are created under the configured workspace path."""
        workspace_path = settings_manager.settings.workspace.path
        memories_root = workspace_path / "memories"

        await memory_hook(ctx)

        # Verify all paths are under workspace
        assert memories_root.is_relative_to(workspace_path)
        assert (memories_root / "episodic").is_relative_to(workspace_path)
        assert (memories_root / "facts").is_relative_to(workspace_path)
        assert (memories_root / "preferences").is_relative_to(workspace_path)
        assert (memories_root / "transcripts").is_relative_to(workspace_path)


class TestMemoryHookIndexCreation:
    """Tests for bootstrap hook index creation (R8)."""

    async def test_empty_facts_dir_gets_header_only_index(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Empty directory gets MEMORY.md with header only."""
        workspace_path = settings_manager.settings.workspace.path

        await memory_hook(ctx)

        index_path = workspace_path / "memories" / "facts" / "MEMORY.md"
        assert index_path.exists()
        assert index_path.read_text() == "# Memory Index\n"

    async def test_empty_preferences_dir_gets_header_only_index(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Empty directory gets MEMORY.md with header only."""
        workspace_path = settings_manager.settings.workspace.path

        await memory_hook(ctx)

        index_path = workspace_path / "memories" / "preferences" / "MEMORY.md"
        assert index_path.exists()
        assert index_path.read_text() == "# Memory Index\n"

    async def test_existing_md_files_triggers_rebuild(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
        mocker: MockerFixture,
    ) -> None:
        """AC: Directories with existing .md files trigger run_index_rebuild."""
        workspace_path = settings_manager.settings.workspace.path
        facts_dir = workspace_path / "memories" / "facts"
        facts_dir.mkdir(parents=True, exist_ok=True)
        # Create a file so the directory is not empty
        (facts_dir / "existing.md").write_text("# Some fact")

        mock_rebuild = mocker.patch(
            "tachikoma.memory.hooks.run_index_rebuild",
            new_callable=AsyncMock,
        )

        await memory_hook(ctx)

        mock_rebuild.assert_awaited_once()
        call_args = mock_rebuild.call_args
        assert call_args[0][1] == "facts"  # memory_type argument

    async def test_existing_memory_index_is_not_rebuilt(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
        mocker: MockerFixture,
    ) -> None:
        """AC: Existing MEMORY.md is left alone (idempotent)."""
        workspace_path = settings_manager.settings.workspace.path
        facts_dir = workspace_path / "memories" / "facts"
        facts_dir.mkdir(parents=True, exist_ok=True)
        # Pre-create MEMORY.md
        (facts_dir / "MEMORY.md").write_text("# Memory Index\n\n[Existing](./existing.md): Test")
        (facts_dir / "existing.md").write_text("# Some fact")

        mock_rebuild = mocker.patch(
            "tachikoma.memory.hooks.run_index_rebuild",
            new_callable=AsyncMock,
        )

        await memory_hook(ctx)

        mock_rebuild.assert_not_called()

    async def test_episodic_dir_does_not_get_index(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Episodic directory does NOT get MEMORY.md (R5 exclusion)."""
        workspace_path = settings_manager.settings.workspace.path

        await memory_hook(ctx)

        episodic_index = workspace_path / "memories" / "episodic" / "MEMORY.md"
        assert not episodic_index.exists()

    async def test_transcripts_dir_does_not_get_index(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Transcripts directory is unaffected."""
        workspace_path = settings_manager.settings.workspace.path

        await memory_hook(ctx)

        transcripts_index = workspace_path / "memories" / "transcripts" / "MEMORY.md"
        assert not transcripts_index.exists()

    async def test_idempotent_index_creation(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
        mocker: MockerFixture,
    ) -> None:
        """AC: Second run does not trigger rebuild."""
        mock_rebuild = mocker.patch(
            "tachikoma.memory.hooks.run_index_rebuild",
            new_callable=AsyncMock,
        )

        await memory_hook(ctx)
        await memory_hook(ctx)

        mock_rebuild.assert_not_called()

    async def test_rebuild_called_for_both_types_with_files(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
        mocker: MockerFixture,
    ) -> None:
        """AC: Both facts and preferences trigger rebuild when they have files."""
        workspace_path = settings_manager.settings.workspace.path
        facts_dir = workspace_path / "memories" / "facts"
        prefs_dir = workspace_path / "memories" / "preferences"
        facts_dir.mkdir(parents=True, exist_ok=True)
        prefs_dir.mkdir(parents=True, exist_ok=True)
        (facts_dir / "fact.md").write_text("# Fact")
        (prefs_dir / "pref.md").write_text("# Pref")

        mock_rebuild = mocker.patch(
            "tachikoma.memory.hooks.run_index_rebuild",
            new_callable=AsyncMock,
        )

        await memory_hook(ctx)

        assert mock_rebuild.await_count == 2
        types_called = [call[0][1] for call in mock_rebuild.call_args_list]
        assert "facts" in types_called
        assert "preferences" in types_called


class TestMemoryHookStaticIndexInjection:
    """Tests for memory_hook stashing formatted indexes in ctx.extras."""

    async def test_stashes_memory_indexes_with_populated_files(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Populated MEMORY.md files → entries in ctx.extras["memory_indexes"]."""
        workspace_path = settings_manager.settings.workspace.path
        facts_dir = workspace_path / "memories" / "facts"
        prefs_dir = workspace_path / "memories" / "preferences"
        facts_dir.mkdir(parents=True)
        prefs_dir.mkdir(parents=True)

        # Pre-create MEMORY.md files (simulating existing index)
        (facts_dir / "MEMORY.md").write_text(
            "# Memory Index\n\n[Fact](./fact.md): A fact\n"
        )
        (prefs_dir / "MEMORY.md").write_text(
            "# Memory Index\n\n[Pref](./pref.md): A preference\n"
        )
        # Create the referenced files so the directory is non-empty
        (facts_dir / "fact.md").write_text("# A fact\n")
        (prefs_dir / "pref.md").write_text("# A pref\n")

        await memory_hook(ctx)

        assert "memory_indexes" in ctx.extras
        indexes = ctx.extras["memory_indexes"]
        assert len(indexes) == 2
        tags = [tag for tag, _ in indexes]
        assert all(t == "memory_index" for t in tags)

    async def test_no_memory_files_empty_list_in_extras(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: No MEMORY.md files (header-only indexes) → empty list in extras."""
        await memory_hook(ctx)

        assert "memory_indexes" in ctx.extras
        # Newly created MEMORY.md files are header-only → format_memory_index returns None
        assert ctx.extras["memory_indexes"] == []

    async def test_empty_memory_md_files_empty_extras(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Empty MEMORY.md files → empty list in extras."""
        workspace_path = settings_manager.settings.workspace.path
        facts_dir = workspace_path / "memories" / "facts"
        prefs_dir = workspace_path / "memories" / "preferences"
        facts_dir.mkdir(parents=True)
        prefs_dir.mkdir(parents=True)

        # Pre-create header-only MEMORY.md files
        (facts_dir / "MEMORY.md").write_text("# Memory Index\n")
        (prefs_dir / "MEMORY.md").write_text("# Memory Index\n")

        await memory_hook(ctx)

        assert "memory_indexes" in ctx.extras
        assert ctx.extras["memory_indexes"] == []
