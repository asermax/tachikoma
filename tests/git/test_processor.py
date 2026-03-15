"""Tests for git post-processor.

Tests for DLT-020: Git module for workspace version tracking.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tachikoma.git.processor import GIT_COMMIT_PROMPT, GitProcessor, query_and_consume
from tachikoma.sessions.model import Session


def _make_session() -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        sdk_session_id="sdk-123",
    )


class TestGitProcessor:
    """Tests for GitProcessor."""

    async def test_calls_query_when_workspace_dirty(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Processor calls query_and_consume when workspace is dirty."""
        mocker.patch(
            "tachikoma.git.processor._check_git_status",
            new_callable=AsyncMock,
            side_effect=[True, False],  # First call: dirty, second call: clean
        )
        mock_query = mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )

        processor = GitProcessor(cwd=Path("/workspace"))
        await processor.process(_make_session())

        mock_query.assert_awaited_once_with(GIT_COMMIT_PROMPT, Path("/workspace"))

    async def test_no_op_when_workspace_clean(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Processor returns no-op when workspace is clean (no agent spawned)."""
        mocker.patch(
            "tachikoma.git.processor._check_git_status",
            new_callable=AsyncMock,
            return_value=False,
        )
        mock_query = mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )

        processor = GitProcessor(cwd=Path("/workspace"))
        await processor.process(_make_session())

        mock_query.assert_not_awaited()

    async def test_logs_warning_if_changes_remain(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: Processor runs post-agent git status check and logs warning if changes remain."""
        mock_status = mocker.patch(
            "tachikoma.git.processor._check_git_status",
            new_callable=AsyncMock,
            side_effect=[True, True],  # First call: dirty, second call: still dirty
        )
        mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )

        processor = GitProcessor(cwd=Path("/workspace"))
        await processor.process(_make_session())

        # Should have called status twice (before and after agent)
        assert mock_status.call_count == 2


class TestQueryAndConsume:
    """Tests for query_and_consume helper."""

    async def test_calls_query_with_correct_options(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: query_and_consume calls query() with correct options."""
        mock_query = mocker.patch("tachikoma.git.processor.query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        await query_and_consume("test prompt", Path("/workspace"))

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        assert call_kwargs[1]["prompt"] == "test prompt"

        options = call_kwargs[1]["options"]
        assert options.model == "haiku"
        assert options.cwd == Path("/workspace")
        assert options.permission_mode == "bypassPermissions"

    async def test_consumes_full_async_iterator(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: query_and_consume fully consumes async iterator."""
        consume_count = 0

        async def fake_query(*args, **kwargs):
            nonlocal consume_count
            for i in range(3):
                consume_count += 1
                yield MagicMock(msg=i)

        mocker.patch("tachikoma.git.processor.query", side_effect=fake_query)

        await query_and_consume("prompt", Path("/workspace"))

        assert consume_count == 3

    async def test_propagates_query_error(self, mocker: pytest.MockerFixture) -> None:
        """AC: query_and_consume propagates query() errors."""
        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.git.processor.query", side_effect=failing_query)

        with pytest.raises(RuntimeError, match="SDK error"):
            await query_and_consume("prompt", Path("/workspace"))


class TestGitCommitPrompt:
    """Tests for GIT_COMMIT_PROMPT content."""

    def test_references_safe_git_commands(self) -> None:
        """AC: Prompt references safe git commands (status, diff, add, commit)."""
        assert "git status" in GIT_COMMIT_PROMPT
        assert "git diff" in GIT_COMMIT_PROMPT
        assert "git add" in GIT_COMMIT_PROMPT
        assert "git commit" in GIT_COMMIT_PROMPT

    def test_instructs_grouping_by_subdirectory(self) -> None:
        """AC: Prompt instructs grouping by subdirectory."""
        assert "memories/episodic" in GIT_COMMIT_PROMPT.lower()
        assert "group" in GIT_COMMIT_PROMPT.lower()

    def test_instructs_not_to_use_destructive_commands(self) -> None:
        """AC: Prompt instructs not to use destructive commands (push, branch, etc.)."""
        assert "git push" in GIT_COMMIT_PROMPT
        assert "NOT" in GIT_COMMIT_PROMPT or "Do NOT" in GIT_COMMIT_PROMPT

    def test_instructs_no_confirmation(self) -> None:
        """AC: Prompt instructs not to ask for confirmation."""
        assert "confirmation" in GIT_COMMIT_PROMPT.lower()

    def test_includes_all_changes(self) -> None:
        """AC: Prompt instructs to include all non-ignored changes."""
        assert "untracked" in GIT_COMMIT_PROMPT.lower()
