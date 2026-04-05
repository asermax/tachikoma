"""Core context module tests.

Tests for DLT-005: Load foundational context for personality and user knowledge.
Tests for DLT-041: Persist session context to database.
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
    build_system_prompt,
    context_hook,
    load_foundational_context,
    render_system_preamble,
)
from tachikoma.sessions.model import SessionContextEntry


class TestLoadFoundationalContext:
    """Tests for the load_foundational_context function (DLT-041)."""

    def test_all_files_present_returns_tuples(self, tmp_path: Path) -> None:
        """AC: All files with content → list of (owner, content) tuples."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Test soul content")
        (context_dir / "USER.md").write_text("Test user content")
        (context_dir / "AGENTS.md").write_text("Test agents content")

        result = load_foundational_context(tmp_path)

        assert len(result) == 3
        assert result[0][0] == "soul"
        assert result[1][0] == "user"
        assert result[2][0] == "agents"

    def test_content_is_raw_not_xml_wrapped(self, tmp_path: Path) -> None:
        """AC: Content in tuples is raw text.

        XML wrapping happens in build_system_prompt(), not during load.
        """
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Test content")

        result = load_foundational_context(tmp_path)

        assert len(result) == 1
        owner, content = result[0]
        assert owner == "soul"
        assert content == "Test content"

    def test_ordering_is_soul_user_agents(self, tmp_path: Path) -> None:
        """AC: Ordering is SOUL first, USER second, AGENTS last."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("A")
        (context_dir / "USER.md").write_text("B")
        (context_dir / "AGENTS.md").write_text("C")

        result = load_foundational_context(tmp_path)

        assert result[0][0] == "soul"
        assert result[1][0] == "user"
        assert result[2][0] == "agents"

    def test_missing_file_warns_and_excludes(self, tmp_path: Path, caplog) -> None:
        """AC: Missing file → warns, excludes from result."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Soul content")
        (context_dir / "AGENTS.md").write_text("Agents content")
        # USER.md is missing

        with caplog.at_level("WARNING"):
            result = load_foundational_context(tmp_path)

        assert len(result) == 2
        owners = [e[0] for e in result]
        assert "soul" in owners
        assert "agents" in owners
        assert "user" not in owners

    def test_empty_file_skipped_silently(self, tmp_path: Path, caplog) -> None:
        """AC: Empty file → skipped silently (no warning)."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Soul content")
        (context_dir / "USER.md").write_text("")  # Empty
        (context_dir / "AGENTS.md").write_text("Agents content")

        with caplog.at_level("WARNING"):
            result = load_foundational_context(tmp_path)

        assert len(result) == 2
        assert not any(record.level == "WARNING" for record in caplog.records)

    def test_all_files_missing_returns_empty_list(self, tmp_path: Path) -> None:
        """AC: All files missing → returns empty list."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()
        # No files created

        result = load_foundational_context(tmp_path)

        assert result == []


class TestRenderSystemPreamble:
    """Tests for the render_system_preamble function."""

    def test_default_renders_with_system_timezone(self) -> None:
        """AC: Empty timezone falls back through system tz chain."""
        result = render_system_preamble()

        assert "# Your Identity" in result
        assert "## Date and Time" in result
        assert "date" in result

    def test_explicit_timezone_renders_in_preamble(self) -> None:
        """AC: Explicit timezone is rendered in the Date and Time section."""
        result = render_system_preamble(timezone="UTC")

        assert "**UTC**" in result
        assert "TZ='UTC'" in result

    def test_invalid_timezone_falls_back(self) -> None:
        """AC: Invalid timezone falls back through resolution chain."""
        result = render_system_preamble(timezone="Invalid/Timezone")

        # Should still render successfully (no crash)
        assert "# Your Identity" in result
        assert "## Date and Time" in result


class TestLoadContextIntegration:
    """Integration tests for load_foundational_context + build_system_prompt together.

    These replace the old load_context() tests, verifying the full pipeline
    from file loading through assembly with the rendered preamble.
    """

    @staticmethod
    def _to_entries(
        raw: list[tuple[str, str]],
        session_id: str = "test",
    ) -> list[SessionContextEntry]:
        """Convert raw (owner, content) tuples to SessionContextEntry instances."""
        return [
            SessionContextEntry(id=i, session_id=session_id, owner=owner, content=content)
            for i, (owner, content) in enumerate(raw, start=1)
        ]

    def test_all_files_present_returns_assembled_string(self, tmp_path: Path) -> None:
        """AC: All files with content → assembled string with preamble + XML sections."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Test soul content")
        (context_dir / "USER.md").write_text("Test user content")
        (context_dir / "AGENTS.md").write_text("Test agents content")

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

        preamble = render_system_preamble()
        assert preamble in result
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

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

        # Check ordering
        soul_pos = result.find("<soul>")
        user_pos = result.find("<user>")
        agents_pos = result.find("<agents>")

        assert soul_pos < user_pos < agents_pos

    def test_preamble_prepended(self, tmp_path: Path) -> None:
        """AC: Rendered preamble is prepended before any file content."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Content")

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

        assert result.startswith(render_system_preamble())

    def test_one_file_missing_includes_remaining(self, tmp_path: Path) -> None:
        """AC: One file missing → warns during load, includes remaining in assembly."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Soul content")
        (context_dir / "AGENTS.md").write_text("Agents content")
        # USER.md is missing

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

        assert "<soul>\nSoul content\n</soul>" in result
        assert "<agents>\nAgents content\n</agents>" in result
        assert "<user>\n" not in result

    def test_one_file_empty_skips_silently(self, tmp_path: Path) -> None:
        """AC: One file empty → skipped silently, includes remaining in assembly."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("Soul content")
        (context_dir / "USER.md").write_text("")  # Empty
        (context_dir / "AGENTS.md").write_text("Agents content")

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

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

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

        assert "<soul>\n" not in result
        assert "<user>\nUser content\n</user>" in result
        assert "<agents>\nAgents content\n</agents>" in result

    def test_all_files_missing_returns_preamble_only(self, tmp_path: Path) -> None:
        """AC: All files missing → returns rendered preamble only (never None)."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()
        # No files created

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

        assert result == render_system_preamble()

    def test_all_files_empty_returns_preamble_only(self, tmp_path: Path) -> None:
        """AC: All files empty → returns rendered preamble only (never None)."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        (context_dir / "SOUL.md").write_text("")
        (context_dir / "USER.md").write_text("")
        (context_dir / "AGENTS.md").write_text("")

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

        assert result == render_system_preamble()

    def test_preamble_always_present(self, tmp_path: Path) -> None:
        """AC: Rendered preamble is always present, even without context files."""
        context_dir = tmp_path / CONTEXT_DIR_NAME
        context_dir.mkdir()

        raw = load_foundational_context(tmp_path)
        result = build_system_prompt(self._to_entries(raw))

        preamble = render_system_preamble()
        assert preamble in result
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

    async def test_always_stores_foundational_context(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook always stores foundational_context in ctx.extras."""
        ws = settings_manager.settings.workspace
        context_path = ws.path / CONTEXT_DIR_NAME
        context_path.mkdir(parents=True)

        (context_path / "SOUL.md").write_text("Content")

        await context_hook(ctx)

        assert "foundational_context" in ctx.extras
        assert ctx.extras["foundational_context"] is not None

    async def test_stores_foundational_context_even_when_all_files_empty(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook stores foundational_context even when all files empty (empty list)."""
        ws = settings_manager.settings.workspace
        context_path = ws.path / CONTEXT_DIR_NAME
        context_path.mkdir(parents=True)

        (context_path / "SOUL.md").write_text("")
        (context_path / "USER.md").write_text("")
        (context_path / "AGENTS.md").write_text("")

        await context_hook(ctx)

        assert "foundational_context" in ctx.extras
        assert ctx.extras["foundational_context"] == []

    async def test_foundational_context_contains_raw_entries(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: foundational_context entries are raw (owner, content) tuples.

        XML wrapping happens in build_system_prompt(), not during load.
        """
        ws = settings_manager.settings.workspace
        context_path = ws.path / CONTEXT_DIR_NAME
        context_path.mkdir(parents=True)

        (context_path / "SOUL.md").write_text("Soul content")
        (context_path / "USER.md").write_text("User content")
        (context_path / "AGENTS.md").write_text("Agents content")

        await context_hook(ctx)

        entries = ctx.extras["foundational_context"]
        assert len(entries) == 3

        # Check first entry (soul) - raw content, not XML-wrapped
        owner, content = entries[0]
        assert owner == "soul"
        assert content == "Soul content"

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
