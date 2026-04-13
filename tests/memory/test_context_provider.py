"""Tests for memory context provider.

Tests for DLT-076: Re-evaluate memory context per message.
"""

from pathlib import Path

from claude_agent_sdk.types import ResultMessage
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.context_provider import (
    _NO_RELEVANT_MEMORIES,
    MEMORIES_OWNER,
    MEMORY_PATH_META_KEY,
    MEMORY_SEARCH_PROMPT,
    MemoryContextProvider,
    ParsedMemoryEntry,
    extract_memory_paths,
    parse_memory_entries,
)
from tachikoma.sessions.model import SessionContextEntry


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


def _make_entry(
    owner: str = "memories",
    content: str = "test",
    metadata: dict | None = None,
) -> SessionContextEntry:
    """Create a test context entry."""
    return SessionContextEntry(
        id=1,
        session_id="s1",
        owner=owner,
        content=content,
        metadata=metadata,
    )


class TestExtractMemoryPaths:
    """Tests for extract_memory_paths helper."""

    def test_empty_entries_returns_empty_set(self) -> None:
        """AC: Empty entries returns empty set."""
        assert extract_memory_paths([]) == set()

    def test_entries_without_metadata_return_empty_set(self) -> None:
        """AC: Entries with None metadata are skipped."""
        entries = [_make_entry(metadata=None)]
        assert extract_memory_paths(entries) == set()

    def test_extracts_memory_paths_from_metadata(self) -> None:
        """AC: Extracts memory_path from metadata where owner='memories'."""
        entries = [
            _make_entry(metadata={"memory_path": "memories/facts/a.md"}),
            _make_entry(metadata={"memory_path": "memories/facts/b.md"}),
        ]
        assert extract_memory_paths(entries) == {
            "memories/facts/a.md",
            "memories/facts/b.md",
        }

    def test_ignores_non_memories_owner(self) -> None:
        """AC: Entries with owner != 'memories' are ignored."""
        entries = [
            _make_entry(owner="skills", metadata={"memory_path": "should-not-appear"}),
            _make_entry(owner="memories", metadata={"memory_path": "should-appear"}),
        ]
        assert extract_memory_paths(entries) == {"should-appear"}

    def test_ignores_entries_without_memory_path_in_metadata(self) -> None:
        """AC: Entries with metadata but no memory_path key are skipped."""
        entries = [
            _make_entry(metadata={"other_key": "value"}),
            _make_entry(metadata={"memory_path": "visible"}),
        ]
        assert extract_memory_paths(entries) == {"visible"}

    def test_mixed_entries(self) -> None:
        """AC: Correctly handles mix of all entry types."""
        entries = [
            _make_entry(owner="skills", content="some skill", metadata=None),
            _make_entry(owner="memories", metadata={"memory_path": "memories/facts/a.md"}),
            _make_entry(owner="memories", metadata=None),
            _make_entry(owner="memories", metadata={"memory_path": "memories/facts/b.md"}),
            _make_entry(owner="foundational", content="soul", metadata=None),
        ]
        assert extract_memory_paths(entries) == {
            "memories/facts/a.md",
            "memories/facts/b.md",
        }


class TestMemoryContextProvider:
    """Tests for MemoryContextProvider."""

    async def test_standalone_query_without_session_id(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: When sdk_session_id is None, query() is called without fork/resume."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result(_NO_RELEVANT_MEMORIES)

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        await provider.provide("Hello")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        options = call_kwargs["options"]

        assert not options.fork_session
        assert options.resume is None

    async def test_fork_query_with_session_id(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: When sdk_session_id provided, options include fork_session=True and resume=id."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result(_NO_RELEVANT_MEMORIES)

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        await provider.provide("Hello", sdk_session_id="session-123")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        options = call_kwargs["options"]

        assert options.fork_session is True
        assert options.resume == "session-123"

    async def test_returns_per_file_results(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Self-closing tags -> provider reads files -> one ContextResult per file."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        # Create memory files on disk
        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "restaurants.md").write_text("Italian places")
        (memories_dir / "hobbies.md").write_text("Reading and hiking")

        mock_query.return_value = _make_query_result(
            '<memory path="memories/facts/restaurants.md" />\n'
            '<memory path="memories/facts/hobbies.md" />',
        )

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("What restaurants do I like?")

        assert result is not None
        assert len(result) == 2

        paths = {r.metadata[MEMORY_PATH_META_KEY] for r in result}
        assert paths == {"memories/facts/restaurants.md", "memories/facts/hobbies.md"}

        for r in result:
            assert r.tag == MEMORIES_OWNER
            assert r.metadata is not None
            assert MEMORY_PATH_META_KEY in r.metadata

    async def test_dedup_skips_already_loaded_paths(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Existing entries with memory_path metadata are filtered out."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "restaurants.md").write_text("Italian places")
        (memories_dir / "hobbies.md").write_text("Reading")

        mock_query.return_value = _make_query_result(
            '<memory path="memories/facts/restaurants.md" />\n'
            '<memory path="memories/facts/hobbies.md" />',
        )

        existing = [
            _make_entry(metadata={MEMORY_PATH_META_KEY: "memories/facts/restaurants.md"}),
        ]

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide(
            "What hobbies do I have?",
            existing_entries=existing,
        )

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata[MEMORY_PATH_META_KEY] == "memories/facts/hobbies.md"

    async def test_path_validation_rejects_outside_memories(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Agent returns path outside memories/ → rejected with warning."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        mock_query.return_value = _make_query_result(
            '<memory path="memories/facts/ok.md" />\n<memory path="../../etc/passwd" />',
        )

        # Create the valid memory file
        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "ok.md").write_text("OK content")

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata[MEMORY_PATH_META_KEY] == "memories/facts/ok.md"

    async def test_returns_none_for_sentinel(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Agent returns NO_RELEVANT_MEMORIES → provider returns None."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result(_NO_RELEVANT_MEMORIES)

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("What's the weather?")

        assert result is None

    async def test_returns_none_on_exception(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Returns None when query raises an exception."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.side_effect = RuntimeError("SDK error")

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is None

    async def test_returns_none_on_error_result(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Returns None when ResultMessage has is_error=True."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result("Error occurred", is_error=True)

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is None

    async def test_skips_deleted_file_gracefully(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: File deleted between search and read → skipped gracefully."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        # Only create one file, agent returns two paths
        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "exists.md").write_text("I exist")

        mock_query.return_value = _make_query_result(
            '<memory path="memories/facts/exists.md" />\n'
            '<memory path="memories/facts/deleted.md" />',
        )

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata[MEMORY_PATH_META_KEY] == "memories/facts/exists.md"

    async def test_calls_query_with_correct_options(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: query() called with correct model, effort, max_turns, allowed_tools, cwd."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result(_NO_RELEVANT_MEMORIES)

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        await provider.provide("What restaurant did I like?")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        options = call_kwargs["options"]

        assert options.model == "opus"
        assert options.effort == "low"
        assert options.max_turns == 12
        assert options.tools == ["Read", "Glob", "Grep"]
        assert options.allowed_tools == ["Read", "Glob", "Grep"]
        assert options.permission_mode is None
        assert options.extra_args == {"permission-mode": "dontAsk"}
        assert options.settings is not None
        assert options.cwd == tmp_path

    async def test_returns_none_when_all_paths_already_loaded(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: When all returned paths are already loaded, returns None."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "a.md").write_text("Content A")

        mock_query.return_value = _make_query_result(
            '<memory path="memories/facts/a.md" />',
        )

        existing = [
            _make_entry(metadata={MEMORY_PATH_META_KEY: "memories/facts/a.md"}),
        ]

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello", existing_entries=existing)

        assert result is None

    async def test_returns_none_when_none_result(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Returns None when ResultMessage.result is None."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result(None)

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is None


class TestMemorySearchPrompt:
    """Tests for MEMORY_SEARCH_PROMPT constant."""

    def test_prompt_references_memory_directories(self) -> None:
        """AC: Prompt mentions episodic, facts, and preferences directories."""
        assert "memories/episodic" in MEMORY_SEARCH_PROMPT
        assert "memories/facts" in MEMORY_SEARCH_PROMPT
        assert "memories/preferences" in MEMORY_SEARCH_PROMPT

    def test_prompt_instructs_xml_memory_format(self) -> None:
        """AC: Prompt instructs agent to return XML memory elements."""
        assert "<memory" in MEMORY_SEARCH_PROMPT
        assert "path=" in MEMORY_SEARCH_PROMPT

    def test_prompt_instructs_no_relevant_memories_sentinel(self) -> None:
        """AC: Prompt mentions NO_RELEVANT_MEMORIES sentinel."""
        assert "NO_RELEVANT_MEMORIES" in MEMORY_SEARCH_PROMPT

    def test_prompt_includes_conversation_context_guidance(self) -> None:
        """AC: Prompt includes guidance for evaluating conversation context."""
        assert "previous conversation messages" in MEMORY_SEARCH_PROMPT.lower()

    def test_prompt_includes_message_placeholder(self) -> None:
        """AC: Prompt has {message} placeholder for embedding user message."""
        assert "{message}" in MEMORY_SEARCH_PROMPT

    def test_prompt_includes_scope_guardrails(self) -> None:
        """AC: Prompt enforces memory-only scope and no-action boundaries."""
        assert "ONLY search files under" in MEMORY_SEARCH_PROMPT
        assert "Do NOT attempt to answer" in MEMORY_SEARCH_PROMPT

    def test_prompt_mentions_already_covered_case(self) -> None:
        """AC: Prompt covers the 'already covered' case in sentinel instruction."""
        assert "already covers" in MEMORY_SEARCH_PROMPT

    def test_prompt_distinguishes_episodic_from_other_types(self) -> None:
        """AC: Prompt instructs snippet extraction for episodic, full load for others."""
        assert "self-closing" in MEMORY_SEARCH_PROMPT.lower()
        assert "snippet" in MEMORY_SEARCH_PROMPT.lower()


class TestParseMemoryEntries:
    """Tests for parse_memory_entries parser."""

    def test_self_closing_tags(self) -> None:
        """AC: Self-closing tags parsed as full-file entries."""
        raw = '<memory path="memories/facts/a.md" />'
        result = parse_memory_entries(raw)

        assert len(result) == 1
        assert result[0] == ParsedMemoryEntry(path="memories/facts/a.md", snippet=None)

    def test_open_close_tags_with_snippet(self) -> None:
        """AC: Open/close tags extract snippet content."""
        raw = (
            '<memory path="memories/episodic/2026-04-06.md">\n'
            "## Relevant Section\n"
            "Some important content\n"
            "</memory>"
        )
        result = parse_memory_entries(raw)

        assert len(result) == 1
        assert result[0].path == "memories/episodic/2026-04-06.md"
        assert result[0].snippet == "## Relevant Section\nSome important content"

    def test_mixed_tags(self) -> None:
        """AC: Both self-closing and open/close in one output."""
        raw = (
            '<memory path="memories/facts/a.md" />\n'
            '<memory path="memories/episodic/2026-04-06.md">\n'
            "Snippet content\n"
            "</memory>"
        )
        result = parse_memory_entries(raw)

        assert len(result) == 2
        assert result[0].snippet is None
        assert result[1].snippet == "Snippet content"

    def test_empty_body_treated_as_full_load(self) -> None:
        """AC: Empty body -> snippet=None (same as self-closing)."""
        raw = '<memory path="memories/facts/a.md"></memory>'
        result = parse_memory_entries(raw)

        assert len(result) == 1
        assert result[0].snippet is None

    def test_unclosed_tag_skipped(self) -> None:
        """AC: Unclosed tag is skipped gracefully."""
        raw = '<memory path="memories/episodic/a.md">\nContent without closing'
        result = parse_memory_entries(raw)

        assert len(result) == 0

    def test_no_tags_returns_empty_list(self) -> None:
        """AC: Random text with no memory tags returns empty list."""
        raw = "Just some random text\nwith no tags"
        result = parse_memory_entries(raw)

        assert result == []

    def test_multiple_open_close_tags(self) -> None:
        """AC: Two consecutive open/close snippets are both parsed."""
        raw = (
            '<memory path="memories/episodic/2026-04-05.md">\n'
            "First snippet\n"
            "</memory>\n"
            '<memory path="memories/episodic/2026-04-06.md">\n'
            "Second snippet\n"
            "</memory>"
        )
        result = parse_memory_entries(raw)

        assert len(result) == 2
        assert result[0].path == "memories/episodic/2026-04-05.md"
        assert result[0].snippet == "First snippet"
        assert result[1].path == "memories/episodic/2026-04-06.md"
        assert result[1].snippet == "Second snippet"

    def test_multiline_snippet(self) -> None:
        """AC: Multi-line snippet preserved correctly."""
        raw = (
            '<memory path="memories/episodic/2026-04-06.md">\n'
            "## Section 1\n"
            "Line 1\n"
            "Line 2\n"
            "\n"
            "## Section 2\n"
            "Line 3\n"
            "</memory>"
        )
        result = parse_memory_entries(raw)

        assert len(result) == 1
        assert "## Section 1" in result[0].snippet
        assert "## Section 2" in result[0].snippet
        assert "Line 3" in result[0].snippet


class TestSnippetBehavior:
    """Tests for snippet vs full-file content in results."""

    async def test_episodic_snippet_used_as_content(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Episodic snippet is used directly, not the full file."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        episodic_dir = tmp_path / "memories" / "episodic"
        episodic_dir.mkdir(parents=True)
        (episodic_dir / "2026-04-06.md").write_text("Very long file " * 1000)

        mock_query.return_value = _make_query_result(
            '<memory path="memories/episodic/2026-04-06.md">\n'
            "## Relevant Part\n"
            "Just the important bit\n"
            "</memory>",
        )

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("What happened on April 6?")

        assert result is not None
        assert len(result) == 1
        assert "Just the important bit" in result[0].content
        assert "Very long file" not in result[0].content

    async def test_snippet_includes_source_reference(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Snippet content starts with source path reference."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        episodic_dir = tmp_path / "memories" / "episodic"
        episodic_dir.mkdir(parents=True)
        (episodic_dir / "2026-04-06.md").write_text("content")

        mock_query.return_value = _make_query_result(
            '<memory path="memories/episodic/2026-04-06.md">\nSnippet\n</memory>',
        )

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is not None
        assert result[0].content.startswith(
            "[Source: memories/episodic/2026-04-06.md]",
        )

    async def test_self_closing_reads_full_file(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Self-closing tag causes full file read."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        facts_dir = tmp_path / "memories" / "facts"
        facts_dir.mkdir(parents=True)
        (facts_dir / "restaurants.md").write_text("Italian places")

        mock_query.return_value = _make_query_result(
            '<memory path="memories/facts/restaurants.md" />',
        )

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("What restaurants?")

        assert result is not None
        assert result[0].content == "Italian places"

    async def test_mixed_snippet_and_full_file(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Mix of snippet and full-file entries work together."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        facts_dir = tmp_path / "memories" / "facts"
        facts_dir.mkdir(parents=True)
        (facts_dir / "restaurants.md").write_text("Italian places")

        episodic_dir = tmp_path / "memories" / "episodic"
        episodic_dir.mkdir(parents=True)
        (episodic_dir / "2026-04-06.md").write_text("Long content " * 500)

        mock_query.return_value = _make_query_result(
            '<memory path="memories/facts/restaurants.md" />\n'
            '<memory path="memories/episodic/2026-04-06.md">\n'
            "Just the snippet\n"
            "</memory>",
        )

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Tell me about food and April 6")

        assert result is not None
        assert len(result) == 2

        facts_entry = next(
            r for r in result if r.metadata[MEMORY_PATH_META_KEY] == "memories/facts/restaurants.md"
        )
        episodic_entry = next(
            r
            for r in result
            if r.metadata[MEMORY_PATH_META_KEY] == "memories/episodic/2026-04-06.md"
        )

        assert facts_entry.content == "Italian places"
        assert "Just the snippet" in episodic_entry.content
        assert "Long content" not in episodic_entry.content

    async def test_malformed_output_returns_none(
        self,
        mocker: MockerFixture,
        tmp_path: Path,
    ) -> None:
        """AC: Garbled agent response with no valid tags returns None."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)

        mock_query.return_value = _make_query_result(
            "Here are some memories:\n- restaurants.md\n- hobbies.md",
        )

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is None
