"""Tests for memory context provider.

Tests for DLT-076: Re-evaluate memory context per message.
"""

from pathlib import Path

from claude_agent_sdk.types import ResultMessage
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.memory.context_provider import (
    MEMORIES_OWNER,
    MEMORY_PATH_META_KEY,
    MEMORY_SEARCH_PROMPT,
    MemoryContextProvider,
    extract_memory_paths,
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
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: When sdk_session_id is None, query() is called without fork/resume."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result("NO_RELEVANT_MEMORIES")

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        await provider.provide("Hello")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        options = call_kwargs["options"]

        assert not options.fork_session
        assert options.resume is None

    async def test_fork_query_with_session_id(
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: When sdk_session_id provided, options include fork_session=True and resume=id."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result("NO_RELEVANT_MEMORIES")

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        await provider.provide("Hello", sdk_session_id="session-123")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        options = call_kwargs["options"]

        assert options.fork_session is True
        assert options.resume == "session-123"

    async def test_returns_per_file_results(
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: Agent returns file paths → provider reads files → one ContextResult per file."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        # Create memory files on disk
        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "restaurants.md").write_text("Italian places")
        (memories_dir / "hobbies.md").write_text("Reading and hiking")

        mock_query.return_value = _make_query_result(
            "memories/facts/restaurants.md\nmemories/facts/hobbies.md",
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
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: Existing entries with memory_path metadata are filtered out."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "restaurants.md").write_text("Italian places")
        (memories_dir / "hobbies.md").write_text("Reading")

        mock_query.return_value = _make_query_result(
            "memories/facts/restaurants.md\nmemories/facts/hobbies.md",
        )

        existing = [
            _make_entry(metadata={MEMORY_PATH_META_KEY: "memories/facts/restaurants.md"}),
        ]

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide(
            "What hobbies do I have?", existing_entries=existing,
        )

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata[MEMORY_PATH_META_KEY] == "memories/facts/hobbies.md"

    async def test_path_validation_rejects_outside_memories(
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: Agent returns path outside memories/ → rejected with warning."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        mock_query.return_value = _make_query_result(
            "memories/facts/ok.md\n../../etc/passwd",
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
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: Agent returns NO_RELEVANT_MEMORIES → provider returns None."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result("NO_RELEVANT_MEMORIES")

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("What's the weather?")

        assert result is None

    async def test_returns_none_on_exception(
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: Returns None when query raises an exception."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.side_effect = RuntimeError("SDK error")

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is None

    async def test_returns_none_on_error_result(
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: Returns None when ResultMessage has is_error=True."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result("Error occurred", is_error=True)

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is None

    async def test_skips_deleted_file_gracefully(
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: File deleted between search and read → skipped gracefully."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        # Only create one file, agent returns two paths
        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "exists.md").write_text("I exist")

        mock_query.return_value = _make_query_result(
            "memories/facts/exists.md\nmemories/facts/deleted.md",
        )

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello")

        assert result is not None
        assert len(result) == 1
        assert result[0].metadata[MEMORY_PATH_META_KEY] == "memories/facts/exists.md"

    async def test_calls_query_with_correct_options(
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: query() called with correct model, effort, max_turns, allowed_tools, cwd."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")
        mock_query.return_value = _make_query_result("NO_RELEVANT_MEMORIES")

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        await provider.provide("What restaurant did I like?")

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        options = call_kwargs["options"]

        assert options.model == "opus"
        assert options.effort == "low"
        assert options.max_turns == 8
        assert options.allowed_tools == ["Read", "Glob", "Grep"]
        assert options.permission_mode == "bypassPermissions"
        assert options.cwd == tmp_path

    async def test_returns_none_when_all_paths_already_loaded(
        self, mocker: MockerFixture, tmp_path: Path,
    ) -> None:
        """AC: When all returned paths are already loaded, returns None."""
        mock_query = mocker.patch("tachikoma.memory.context_provider.query")

        memories_dir = tmp_path / "memories" / "facts"
        memories_dir.mkdir(parents=True)
        (memories_dir / "a.md").write_text("Content A")

        mock_query.return_value = _make_query_result("memories/facts/a.md")

        existing = [
            _make_entry(metadata={MEMORY_PATH_META_KEY: "memories/facts/a.md"}),
        ]

        provider = MemoryContextProvider(AgentDefaults(cwd=tmp_path))
        result = await provider.provide("Hello", existing_entries=existing)

        assert result is None

    async def test_returns_none_when_none_result(
        self, mocker: MockerFixture, tmp_path: Path,
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

    def test_prompt_instructs_bare_file_paths(self) -> None:
        """AC: Prompt instructs agent to return bare file paths."""
        assert "one per line" in MEMORY_SEARCH_PROMPT.lower()

    def test_prompt_instructs_no_relevant_memories_sentinel(self) -> None:
        """AC: Prompt mentions NO_RELEVANT_MEMORIES sentinel."""
        assert "NO_RELEVANT_MEMORIES" in MEMORY_SEARCH_PROMPT

    def test_prompt_includes_conversation_context_guidance(self) -> None:
        """AC: Prompt includes guidance for evaluating conversation context."""
        assert "previous conversation messages" in MEMORY_SEARCH_PROMPT.lower()

    def test_prompt_includes_message_placeholder(self) -> None:
        """AC: Prompt has {message} placeholder for embedding user message."""
        assert "{message}" in MEMORY_SEARCH_PROMPT

    def test_prompt_mentions_already_covered_case(self) -> None:
        """AC: Prompt covers the 'already covered' case in sentinel instruction."""
        assert "already covers" in MEMORY_SEARCH_PROMPT
