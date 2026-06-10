"""Tests for the memory index module and index-related prompt integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from pytest_mock import MockerFixture

import tachikoma.memory
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.facts import FACTS_PROMPT
from tachikoma.memory.index import (
    DESCRIPTION_WORKER_PROMPT,
    HEAVY_INDEX_REBUILD_PROMPT,
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
        """AC: <type> is replaced with the memory_type in the prompt."""
        mock_query = mocker.patch(
            "tachikoma.memory.index.query_and_consume",
            new_callable=AsyncMock,
        )
        cwd = Path("/workspace")
        defaults = AgentDefaults(cwd=cwd)

        await run_index_rebuild(defaults, "preferences")

        call_kwargs = mock_query.call_args
        prompt = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
        assert "<type>" not in prompt
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


class TestFactsPromptIndexIntegration:
    """Verify FACTS_PROMPT includes the index update section."""

    def test_contains_index_update_section(self) -> None:
        """AC: FACTS_PROMPT contains the index update instructions."""
        assert "## Memory Index" in FACTS_PROMPT
        assert "MEMORY.md" in FACTS_PROMPT

    def test_index_section_before_permissions(self) -> None:
        """AC: Index section appears before the permissions section."""
        idx_index = FACTS_PROMPT.find("## Memory Index")
        idx_perms = FACTS_PROMPT.find("## Permissions")
        assert idx_index != -1, "## Memory Index section not found in FACTS_PROMPT"
        assert idx_perms != -1, "## Permissions section not found in FACTS_PROMPT"
        assert idx_index < idx_perms, (
            "## Memory Index should appear before ## Permissions"
        )


class TestPreferencesPromptIndexIntegration:
    """Verify PREFERENCES_PROMPT includes the index update section."""

    def test_contains_index_update_section(self) -> None:
        """AC: PREFERENCES_PROMPT contains the index update instructions."""
        assert "## Memory Index" in PREFERENCES_PROMPT
        assert "MEMORY.md" in PREFERENCES_PROMPT

    def test_index_section_before_permissions(self) -> None:
        """AC: Index section appears before the permissions section."""
        idx_index = PREFERENCES_PROMPT.find("## Memory Index")
        idx_perms = PREFERENCES_PROMPT.find("## Permissions")
        assert idx_index != -1, (
            "## Memory Index section not found in PREFERENCES_PROMPT"
        )
        assert idx_perms != -1, "## Permissions section not found in PREFERENCES_PROMPT"
        assert idx_index < idx_perms, (
            "## Memory Index should appear before ## Permissions"
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
