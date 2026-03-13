"""Core context module tests.

Tests for DLT-005: Load foundational context for personality and user knowledge.
"""

from pathlib import Path

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.context import (
    CONTEXT_DIR_NAME,
    CONTEXT_FILES,
    DEFAULT_AGENTS_CONTENT,
    DEFAULT_SOUL_CONTENT,
    DEFAULT_USER_CONTENT,
    SYSTEM_PREAMBLE,
    context_hook,
    load_context,
)


class TestLoadContext:
    """Tests for the load_context function."""

    def test_all_files_present_returns_assembled_string(self, tmp_path: Path) -> None:
        """AC: All files with content → assembled string with preamble + XML sections."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Test soul content")
        (context_dir / "USER.md").write_text("Test user content")
        (context_dir / "AGENTS.md").write_text("Test agents content")

        result = load_context(tmp_path)

        assert SYSTEM_PREAMBLE in result
        assert "<soul>\nTest soul content\n</soul>" in result
        assert "<user>\nTest user content\n</user>" in result
        assert "<agents>\nTest agents content\n</agents>" in result

    def test_ordering_is_soul_user_agents(self, tmp_path: Path) -> None:
        """AC: Ordering is SOUL first, USER second, AGENTS last in output."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("A")
        (context_dir / "USER.md").write_text("B")
        (context_dir / "AGENTS.md").write_text("C")

        result = load_context(tmp_path)

        # Check ordering
        soul_pos = result.find("<soul>")
        user_pos = result.find("<user>")
        agents_pos = result.find("<agents>")

        assert soul_pos < user_pos < agents_pos

    def test_preamble_prepended(self, tmp_path: Path) -> None:
        """AC: System preamble is prepended before any file content."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Content")

        result = load_context(tmp_path)

        assert result.startswith(SYSTEM_PREAMBLE)

    def test_one_file_missing_warns_and_includes_remaining(self, tmp_path: Path, caplog) -> None:
        """AC: One file missing → warns, includes remaining files."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Soul content")
        (context_dir / "AGENTS.md").write_text("Agents content")
        # USER.md is missing

        with caplog.at_level("WARNING"):
            load_context(tmp_path)

        result = load_context(tmp_path)
        assert "<soul>\nSoul content\n</soul>" in result
        assert "<agents>\nAgents content\n</agents>" in result
        assert "<user>\n" not in result

    def test_one_file_empty_skips_silently(self, tmp_path: Path, caplog) -> None:
        """AC: One file empty → skips silently (no warning), includes remaining files."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Soul content")
        (context_dir / "USER.md").write_text("")  # Empty
        (context_dir / "AGENTS.md").write_text("Agents content")

        load_context(tmp_path)

        # No warning should be logged
        assert not any(record.level == "WARNING" for record in caplog.records)

        result = load_context(tmp_path)
        assert "<soul>\nSoul content\n</soul>" in result
        assert "<agents>\nAgents content\n</agents>" in result
        assert "<user>\n" not in result

    def test_whitespace_only_file_treated_as_empty(self, tmp_path: Path) -> None:
        """AC: Whitespace-only file treated as empty (= content.strip() == "")."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("   \n\n   ")  # Whitespace only
        (context_dir / "USER.md").write_text("User content")
        (context_dir / "AGENTS.md").write_text("Agents content")

        result = load_context(tmp_path)
        assert "<soul>\n" not in result
        assert "<user>\nUser content\n</user>" in result
        assert "<agents>\nAgents content\n</agents>" in result

    def test_one_file_unreadable_warns_and_includes_remaining(
        self, tmp_path: Path, caplog, mocker
    ) -> None:
        """AC: One file unreadable (PermissionError) → warns, includes remaining files."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Soul content")
        (context_dir / "USER.md").write_text("User content")
        (context_dir / "AGENTS.md").write_text("Agents content")

        # Make SOUL.md unreadable
        mocker.patch.object(
            Path,
            "read_text",
            side_effect=PermissionError("Permission denied"),
        )

        with caplog.at_level("WARNING"):
            load_context(tmp_path)

    def test_all_files_missing_returns_preamble_only(self, tmp_path: Path) -> None:
        """AC: All files missing → returns preamble only (never None)."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()
        # No files created

        result = load_context(tmp_path)

        assert result == SYSTEM_PREAMBLE

    def test_all_files_empty_returns_preamble_only(self, tmp_path: Path) -> None:
        """AC: All files empty → returns preamble only (never None)."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("")
        (context_dir / "USER.md").write_text("")
        (context_dir / "AGENTS.md").write_text("")

        result = load_context(tmp_path)

        assert result == SYSTEM_PREAMBLE

    def test_preamble_always_present(self, tmp_path: Path) -> None:
        """AC: System preamble is always present, even without context files."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        result = load_context(tmp_path)

        assert SYSTEM_PREAMBLE in result
        assert "Tachikoma" in result
        assert "memories/" in result


class TestContextHook:
    """Tests for the context_hook bootstrap hook."""

    @pytest.fixture
    def ctx(self, settings_manager: SettingsManager) -> BootstrapContext:
        return BootstrapContext(settings_manager=settings_manager, prompt=input)

    async def test_creates_context_dir_and_default_files(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: No context dir → creates dir + all three files with default content."""
        await context_hook(ctx)

        ws = settings_manager.settings.workspace
        context_path = ws.path / CONTEXT_DIR_NAME

        assert context_path.is_dir()
        assert (context_path / "SOUL.md").exists()
        assert (context_path / "USER.md").exists()
        assert (context_path / "AGENTS.md").exists()

        # Verify default content
        soul_content = (context_path / "SOUL.md").read_text()
        assert soul_content == DEFAULT_SOUL_CONTENT

    async def test_idempotent_when_all_files_exist(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: All files exist → nothing changed (idempotent)."""
        ws = settings_manager.settings.workspace
        context_path = ws.path / CONTEXT_DIR_NAME
        context_path.mkdir(parents=True)

        # Create files with custom content
        (context_path / "SOUL.md").write_text("Custom soul")
        (context_path / "USER.md").write_text("Custom user")
        (context_path / "AGENTS.md").write_text("Custom agents")

        await context_hook(ctx)

        # Content should NOT be overwritten
        assert (context_path / "SOUL.md").read_text() == "Custom soul"
        assert (context_path / "USER.md").read_text() == "Custom user"
        assert (context_path / "AGENTS.md").read_text() == "Custom agents"

    async def test_one_file_missing_creates_only_that_file(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: One file missing → only that file created."""
        ws = settings_manager.settings.workspace
        context_path = ws.path / CONTEXT_DIR_NAME
        context_path.mkdir(parents=True)

        (context_path / "SOUL.md").write_text("Existing soul")
        (context_path / "AGENTS.md").write_text("Existing agents")
        # USER.md is missing

        await context_hook(ctx)

        # Only USER.md should be created
        assert (context_path / "SOUL.md").read_text() == "Existing soul"
        assert (context_path / "AGENTS.md").read_text() == "Existing agents"
        assert (context_path / "USER.md").read_text() == DEFAULT_USER_CONTENT

    async def test_always_stores_system_prompt(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook always stores system_prompt in ctx.extras (preamble at minimum)."""
        ws = settings_manager.settings.workspace
        context_path = ws.path / CONTEXT_DIR_NAME
        context_path.mkdir(parents=True)

        (context_path / "SOUL.md").write_text("Content")

        await context_hook(ctx)

        assert "system_prompt" in ctx.extras
        assert ctx.extras["system_prompt"] is not None

    async def test_stores_system_prompt_even_when_all_files_empty(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook stores system_prompt even when all files empty (preamble always present)."""
        ws = settings_manager.settings.workspace
        context_path = ws.path / CONTEXT_DIR_NAME
        context_path.mkdir(parents=True)

        (context_path / "SOUL.md").write_text("")
        (context_path / "USER.md").write_text("")
        (context_path / "AGENTS.md").write_text("")

        await context_hook(ctx)

        assert "system_prompt" in ctx.extras
        assert SYSTEM_PREAMBLE in ctx.extras["system_prompt"]

    async def test_dir_creation_failure_propagates(self, ctx: BootstrapContext, mocker) -> None:
        """AC: Dir creation failure → exception propagates (not caught)."""
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("No perms"))

        with pytest.raises(PermissionError):
            await context_hook(ctx)


class TestDefaultContent:
    """Tests for the default template content constants."""

    def test_soul_content_contains_personality_keywords(self) -> None:
        """AC: DEFAULT_SOUL_CONTENT contains personality/dialogue encouragement keywords."""
        assert "proactive" in DEFAULT_SOUL_CONTENT.lower()
        assert "assistant" in DEFAULT_SOUL_CONTENT.lower()

    def test_user_content_contains_user_discovery_prompt(self) -> None:
        """AC: DEFAULT_USER_CONTENT contains user-discovery prompt."""
        content_lower = DEFAULT_USER_CONTENT.lower()
        # Should prompt the assistant to learn about the user
        assert "ask" in content_lower or "learn" in content_lower

    def test_agents_content_explains_purpose(self) -> None:
        """AC: DEFAULT_AGENTS_CONTENT explains its purpose."""
        content_lower = DEFAULT_AGENTS_CONTENT.lower()
        assert "behavior" in content_lower or "instruction" in content_lower

    def test_context_files_ordered_correctly(self) -> None:
        """AC: CONTEXT_FILES has correct order: SOUL, USER, AGENTS."""
        assert len(CONTEXT_FILES) == 3
        assert CONTEXT_FILES[0][0] == "SOUL.md"
        assert CONTEXT_FILES[1][0] == "USER.md"
        assert CONTEXT_FILES[2][0] == "AGENTS.md"

    def test_context_files_have_correct_xml_tags(self) -> None:
        """AC: CONTEXT_FILES has correct XML tags."""
        assert CONTEXT_FILES[0][1] == "soul"
        assert CONTEXT_FILES[1][1] == "user"
        assert CONTEXT_FILES[2][1] == "agents"
