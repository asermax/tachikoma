"""Tests for the memory index module and index-related prompt integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

import tachikoma.memory
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.facts import FACTS_PROMPT
from tachikoma.memory.index import (
    DESCRIPTION_WORKER_PROMPT,
    HEAVY_INDEX_REBUILD_PROMPT,
    format_memory_index,
    load_memory_indexes,
    run_index_rebuild,
)
from tachikoma.memory.preferences import PREFERENCES_PROMPT
from tachikoma.memory.prompts import INDEX_UPDATE_SECTION
from tachikoma.post_processing import UTILITY_BASH_HOOK

# ---------------------------------------------------------------------------
# DESCRIPTION_WORKER_PROMPT
# ---------------------------------------------------------------------------


class TestDescriptionWorkerPrompt:
    """Verify DESCRIPTION_WORKER_PROMPT contains required content."""

    def test_contains_xml_output_format(self) -> None:
        """AC: Worker prompt specifies structured XML output format."""
        assert "<file" in DESCRIPTION_WORKER_PROMPT
        assert "</file>" in DESCRIPTION_WORKER_PROMPT
        assert "path=" in DESCRIPTION_WORKER_PROMPT

    def test_contains_file_reading_instruction(self) -> None:
        """AC: Worker prompt instructs to read files with the Read tool."""
        assert "Read" in DESCRIPTION_WORKER_PROMPT
        assert "file" in DESCRIPTION_WORKER_PROMPT.lower()

    def test_contains_description_length_constraint(self) -> None:
        """AC: Worker prompt specifies 80-character limit for descriptions."""
        assert "80" in DESCRIPTION_WORKER_PROMPT

    def test_contains_one_line_description_instruction(self) -> None:
        """AC: Worker prompt asks for one-line descriptions."""
        assert "one-line" in DESCRIPTION_WORKER_PROMPT.lower()


# ---------------------------------------------------------------------------
# HEAVY_INDEX_REBUILD_PROMPT
# ---------------------------------------------------------------------------


class TestHeavyIndexRebuildPrompt:
    """Verify HEAVY_INDEX_REBUILD_PROMPT contains required content."""

    def test_contains_batch_grouping_instructions(self) -> None:
        """AC: Prompt instructs to group files into batches of 5–8."""
        assert "batch" in HEAVY_INDEX_REBUILD_PROMPT.lower()
        assert "5" in HEAVY_INDEX_REBUILD_PROMPT
        assert "8" in HEAVY_INDEX_REBUILD_PROMPT

    def test_contains_agent_tool_spawning(self) -> None:
        """AC: Prompt instructs to use Agent tool for description workers."""
        assert "Agent" in HEAVY_INDEX_REBUILD_PROMPT

    def test_contains_haiku_model_instruction(self) -> None:
        """AC: Prompt specifies haiku model for description workers."""
        assert "haiku" in HEAVY_INDEX_REBUILD_PROMPT.lower()

    def test_contains_structural_analysis(self) -> None:
        """AC: Prompt instructs orchestrator to analyze for merges/renames."""
        assert "merge" in HEAVY_INDEX_REBUILD_PROMPT.lower()
        assert "rename" in HEAVY_INDEX_REBUILD_PROMPT.lower()

    def test_contains_memory_md_format(self) -> None:
        """AC: Prompt specifies MEMORY.md header and entry format."""
        assert "# Memory Index" in HEAVY_INDEX_REBUILD_PROMPT
        assert "[Human-readable Name](./filename.md)" in HEAVY_INDEX_REBUILD_PROMPT

    def test_contains_permissions_section(self) -> None:
        """AC: Prompt includes permissions section explaining tool access."""
        assert "## Permissions" in HEAVY_INDEX_REBUILD_PROMPT

    def test_contains_empty_directory_handling(self) -> None:
        """AC: Prompt handles empty directories (header-only MEMORY.md)."""
        assert "empty" in HEAVY_INDEX_REBUILD_PROMPT.lower()

    def test_contains_workspace_placeholder(self) -> None:
        """AC: Prompt uses $WORKSPACE placeholder for path injection."""
        assert "$WORKSPACE" in HEAVY_INDEX_REBUILD_PROMPT
        assert "{memory_type}" in HEAVY_INDEX_REBUILD_PROMPT


# ---------------------------------------------------------------------------
# run_index_rebuild
# ---------------------------------------------------------------------------


class TestRunIndexRebuild:
    """Verify run_index_rebuild calls query_and_consume with correct args."""

    async def test_calls_query_and_consume(self, mocker: MockerFixture) -> None:
        """AC: run_index_rebuild calls query_and_consume."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "facts")

        mock_query.assert_awaited_once()

    async def test_includes_agent_in_tools(self, mocker: MockerFixture) -> None:
        """AC: Tools list includes Agent for spawning description workers."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "facts")

        call_kwargs = mock_query.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
        assert "Agent" in tools

    async def test_scopes_allow_rules_to_target_directory(
        self, mocker: MockerFixture
    ) -> None:
        """AC: Allow rules are scoped to memories/<memory_type>/ directory."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "facts")

        call_kwargs = mock_query.call_args
        allow = call_kwargs.kwargs.get("allow") or call_kwargs[1].get("allow")
        # Should contain scoped rules for facts directory
        allow_str = str(allow)
        assert "facts" in allow_str

    async def test_uses_utility_bash_hook(self, mocker: MockerFixture) -> None:
        """AC: Uses UTILITY_BASH_HOOK for bash command gating."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "facts")

        call_kwargs = mock_query.call_args
        hooks = call_kwargs.kwargs.get("pre_tool_use_hooks") or call_kwargs[1].get(
            "pre_tool_use_hooks"
        )
        assert UTILITY_BASH_HOOK in hooks

    async def test_uses_processor_model(self, mocker: MockerFixture) -> None:
        """AC: Uses processor_model (haiku) from agent_defaults."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "facts")

        call_kwargs = mock_query.call_args
        model = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert model == "haiku"

    async def test_replaces_workspace_in_prompt(self, mocker: MockerFixture) -> None:
        """AC: $WORKSPACE is replaced with actual cwd in the prompt."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "facts")

        call_kwargs = mock_query.call_args
        prompt = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
        assert "$WORKSPACE" not in prompt
        assert str(cwd) in prompt

    async def test_replaces_type_in_prompt(self, mocker: MockerFixture) -> None:
        """AC: {memory_type} is replaced with the memory_type in the prompt."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "preferences")

        call_kwargs = mock_query.call_args
        prompt = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
        assert "{memory_type}" not in prompt
        assert "preferences" in prompt

    async def test_includes_description_worker_prompt(
        self, mocker: MockerFixture
    ) -> None:
        """AC: Prompt includes the description worker prompt for sub-agents."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "facts")

        call_kwargs = mock_query.call_args
        prompt = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
        assert "description_worker_prompt" in prompt
        assert "<file" in prompt


# ---------------------------------------------------------------------------
# INDEX_UPDATE_SECTION
# ---------------------------------------------------------------------------


class TestIndexUpdateSection:
    """Verify INDEX_UPDATE_SECTION contains required content."""

    def test_contains_entry_format(self) -> None:
        """AC: Section specifies entry format with markdown link and description."""
        assert "[Name](./file.md)" in INDEX_UPDATE_SECTION or (
            "[" in INDEX_UPDATE_SECTION
            and "](./" in INDEX_UPDATE_SECTION
            and "):" in INDEX_UPDATE_SECTION
        )

    def test_contains_create_rule(self) -> None:
        """AC: Section instructs to add entry when creating a file."""
        assert "CREATE" in INDEX_UPDATE_SECTION
        assert "add" in INDEX_UPDATE_SECTION.lower()

    def test_contains_modify_rule(self) -> None:
        """AC: Section instructs to update description when modifying a file."""
        assert "MODIFY" in INDEX_UPDATE_SECTION
        assert "update" in INDEX_UPDATE_SECTION.lower()

    def test_contains_delete_rule(self) -> None:
        """AC: Section instructs to remove entry when deleting a file."""
        assert "DELETE" in INDEX_UPDATE_SECTION
        assert "remove" in INDEX_UPDATE_SECTION.lower()

    def test_contains_description_length_limit(self) -> None:
        """AC: Section specifies 80-character description limit."""
        assert "80" in INDEX_UPDATE_SECTION

    def test_contains_example_entries(self) -> None:
        """AC: Section provides at least one example entry."""
        # Should have example entries with the [Name](./file.md): Desc pattern
        assert "```" in INDEX_UPDATE_SECTION
        assert "# Memory Index" in INDEX_UPDATE_SECTION

    def test_contains_memory_index_header(self) -> None:
        """AC: Section references the # Memory Index header."""
        assert "# Memory Index" in INDEX_UPDATE_SECTION


# ---------------------------------------------------------------------------
# Facts & Preferences prompt integration
# ---------------------------------------------------------------------------


class TestExtractionPromptIndexIntegration:
    """Verify extraction prompts include the index update section."""

    @pytest.mark.parametrize(
        "prompt_name,prompt",
        [("FACTS_PROMPT", FACTS_PROMPT), ("PREFERENCES_PROMPT", PREFERENCES_PROMPT)],
    )
    def test_contains_index_update_section(
        self, prompt_name: str, prompt: str
    ) -> None:
        """AC: Extraction prompt contains the index update instructions."""
        assert "## Memory Index" in prompt
        assert "MEMORY.md" in prompt

    @pytest.mark.parametrize(
        "prompt_name,prompt",
        [("FACTS_PROMPT", FACTS_PROMPT), ("PREFERENCES_PROMPT", PREFERENCES_PROMPT)],
    )
    def test_index_section_before_permissions(
        self, prompt_name: str, prompt: str
    ) -> None:
        """AC: Index section appears before the permissions section."""
        idx_index = prompt.find("## Memory Index")
        idx_perms = prompt.find("## Permissions")
        assert idx_index != -1, f"## Memory Index section not found in {prompt_name}"
        assert idx_perms != -1, f"## Permissions section not found in {prompt_name}"
        assert idx_index < idx_perms, (
            f"## Memory Index should appear before ## Permissions in {prompt_name}"
        )


# ---------------------------------------------------------------------------
# __init__.py export
# ---------------------------------------------------------------------------


class TestIndexModuleExport:
    """Verify run_index_rebuild is exported from the memory package."""

    def test_import_run_index_rebuild(self) -> None:
        """AC: run_index_rebuild can be imported from tachikoma.memory."""
        assert hasattr(tachikoma.memory, "run_index_rebuild")

    def test_in_all(self) -> None:
        """AC: run_index_rebuild is listed in __all__."""
        assert "run_index_rebuild" in tachikoma.memory.__all__


# ---------------------------------------------------------------------------
# format_memory_index
# ---------------------------------------------------------------------------


class TestFormatMemoryIndex:
    """Tests for the format_memory_index helper."""

    def test_valid_entries_produce_formatted_section(self) -> None:
        """AC: Valid entries produce a section with header, description, and bullets."""
        raw = (
            "# Memory Index\n\n"
            "[Restaurants](./restaurants.md): Favorite restaurants and food preferences\n"
            "[Coding Style](./coding-style.md): Preferred coding conventions\n"
        )
        result = format_memory_index("facts", raw)

        assert result is not None
        assert "## Facts Index" in result
        assert (
            "- [Restaurants](./restaurants.md): Favorite restaurants and food preferences"
            in result
        )
        assert "- [Coding Style](./coding-style.md): Preferred coding conventions" in result
        assert "Stable reference information" in result

    def test_empty_content_returns_none(self) -> None:
        """AC: Empty content returns None."""
        result = format_memory_index("facts", "")
        assert result is None

    def test_header_only_returns_none(self) -> None:
        """AC: Header-only content (no entries) returns None."""
        result = format_memory_index("facts", "# Memory Index\n")
        assert result is None

    def test_facts_type_description(self) -> None:
        """AC: facts type includes the correct description."""
        raw = "[Test](./test.md): A test entry\n"
        result = format_memory_index("facts", raw)
        assert result is not None
        assert "Stable reference information" in result

    def test_preferences_type_description(self) -> None:
        """AC: preferences type includes the correct description."""
        raw = "[Test](./test.md): A test entry\n"
        result = format_memory_index("preferences", raw)
        assert result is not None
        assert "Subjective choices" in result

    def test_mixed_valid_malformed_includes_only_valid(self) -> None:
        """AC: Mixed entries include only well-formed ones; malformed excluded."""
        raw = (
            "# Memory Index\n\n"
            "[Valid](./valid.md): A valid entry\n"
            "[Missing Colon](./no-colon.md) No colon separator\n"
            "Just a random line\n"
            "[Also Valid](./also-valid.md): Another good one\n"
        )
        result = format_memory_index("facts", raw)

        assert result is not None
        assert "- [Valid](./valid.md): A valid entry" in result
        assert "- [Also Valid](./also-valid.md): Another good one" in result
        # Malformed entries should NOT appear in the output
        assert "Missing Colon" not in result
        assert "Just a random line" not in result

    def test_all_malformed_returns_none(self) -> None:
        """AC: All malformed entries returns None."""
        raw = (
            "# Memory Index\n\n"
            "No entries here\n"
            "Just text\n"
            "[Bad format](missing-colon)\n"
        )
        result = format_memory_index("facts", raw)
        assert result is None

    def test_unknown_type_uses_fallback_description(self) -> None:
        """AC: Unknown memory type uses a fallback description."""
        raw = "[Test](./test.md): A test entry\n"
        result = format_memory_index("unknown_type", raw)
        assert result is not None
        assert "## Unknown_Type Index" in result
        assert "Browse the entries below" in result

    def test_entry_format_preserved_in_output(self) -> None:
        """AC: Well-formed entries are preserved as-is in the output bullets."""
        raw = (
            "[API Design](./api-design.md): API architecture decisions\n"
            "[Work Info](./work-info.md): Job details and schedule\n"
        )
        result = format_memory_index("facts", raw)
        assert result is not None
        assert "- [API Design](./api-design.md): API architecture decisions" in result
        assert "- [Work Info](./work-info.md): Job details and schedule" in result


# ---------------------------------------------------------------------------
# load_memory_indexes
# ---------------------------------------------------------------------------


class TestLoadMemoryIndexes:
    """Tests for the load_memory_indexes helper."""

    def test_both_files_present_returns_two_tuples(self, tmp_path: Path) -> None:
        """AC: Both facts and preferences MEMORY.md present → two tuples."""
        facts_dir = tmp_path / "memories" / "facts"
        prefs_dir = tmp_path / "memories" / "preferences"
        facts_dir.mkdir(parents=True)
        prefs_dir.mkdir(parents=True)

        (facts_dir / "MEMORY.md").write_text(
            "# Memory Index\n\n[Fact](./fact.md): A fact\n"
        )
        (prefs_dir / "MEMORY.md").write_text(
            "# Memory Index\n\n[Pref](./pref.md): A preference\n"
        )

        result = load_memory_indexes(tmp_path)

        assert len(result) == 2
        assert all(tag == "memory_index" for tag, _ in result)

    def test_one_file_missing_returns_one_tuple(self, tmp_path: Path) -> None:
        """AC: One file missing → one tuple, no error."""
        facts_dir = tmp_path / "memories" / "facts"
        facts_dir.mkdir(parents=True)
        (facts_dir / "MEMORY.md").write_text(
            "# Memory Index\n\n[Fact](./fact.md): A fact\n"
        )
        # preferences dir missing entirely

        result = load_memory_indexes(tmp_path)

        assert len(result) == 1
        assert result[0][0] == "memory_index"
        assert "## Facts Index" in result[0][1]

    def test_both_files_missing_returns_empty(self, tmp_path: Path) -> None:
        """AC: Both files missing → empty list."""
        # No memories directory at all
        result = load_memory_indexes(tmp_path)
        assert result == []

    def test_one_file_empty_skipped(self, tmp_path: Path) -> None:
        """AC: One file empty (header-only) → skipped, other included."""
        facts_dir = tmp_path / "memories" / "facts"
        prefs_dir = tmp_path / "memories" / "preferences"
        facts_dir.mkdir(parents=True)
        prefs_dir.mkdir(parents=True)

        (facts_dir / "MEMORY.md").write_text("# Memory Index\n")  # header-only
        (prefs_dir / "MEMORY.md").write_text(
            "# Memory Index\n\n[Pref](./pref.md): A preference\n"
        )

        result = load_memory_indexes(tmp_path)

        assert len(result) == 1
        assert "## Preferences Index" in result[0][1]

    def test_owner_tag_is_memory_index(self, tmp_path: Path) -> None:
        """AC: All returned tuples use 'memory_index' as the owner tag."""
        facts_dir = tmp_path / "memories" / "facts"
        prefs_dir = tmp_path / "memories" / "preferences"
        facts_dir.mkdir(parents=True)
        prefs_dir.mkdir(parents=True)

        (facts_dir / "MEMORY.md").write_text(
            "# Memory Index\n\n[Fact](./fact.md): A fact\n"
        )
        (prefs_dir / "MEMORY.md").write_text(
            "# Memory Index\n\n[Pref](./pref.md): A pref\n"
        )

        result = load_memory_indexes(tmp_path)

        for tag, _ in result:
            assert tag == "memory_index"

    def test_malformed_file_skipped(self, tmp_path: Path) -> None:
        """AC: File with only malformed entries → skipped (returns None from format)."""
        facts_dir = tmp_path / "memories" / "facts"
        facts_dir.mkdir(parents=True)
        (facts_dir / "MEMORY.md").write_text(
            "# Memory Index\n\nNo valid entries here\n"
        )

        result = load_memory_indexes(tmp_path)

        assert result == []
