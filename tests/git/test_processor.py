"""Tests for git post-processor.

Tests for DLT-020: Git module for workspace version tracking.
Tests updated for DLT-097: smart_push replaces bare push.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.processor import (
    GIT_ALLOW,
    GIT_BASH_HOOK,
    GIT_COMMIT_PROMPT,
    GIT_TOOLS,
    GitProcessor,
    query_and_consume,
)
from tachikoma.git.sync import PUSH_RESULT
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

    async def test_calls_query_when_workspace_dirty(self, mocker: MockerFixture) -> None:
        """AC: Processor calls query_and_consume when workspace is dirty."""
        mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            side_effect=[True, False],  # First call: dirty, second call: clean
        )
        mock_query = mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.git.processor.smart_push",
            new_callable=AsyncMock,
            return_value=PUSH_RESULT["PUSHED"],
        )

        processor = GitProcessor(AgentDefaults(cwd=Path("/workspace")))
        await processor.process(_make_session())

        expected_prompt = GIT_COMMIT_PROMPT.replace("$WORKSPACE", "/workspace")
        mock_query.assert_awaited_once_with(
            expected_prompt,
            AgentDefaults(cwd=Path("/workspace")),
            tools=GIT_TOOLS,
            allow=GIT_ALLOW,
            pre_tool_use_hooks=[GIT_BASH_HOOK],
        )

    async def test_no_op_when_workspace_clean(self, mocker: MockerFixture) -> None:
        """AC: Processor returns no-op when workspace is clean (no agent spawned)."""
        mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            return_value=False,
        )
        mock_query = mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )

        processor = GitProcessor(AgentDefaults(cwd=Path("/workspace")))
        await processor.process(_make_session())

        mock_query.assert_not_awaited()

    async def test_logs_warning_if_changes_remain(self, mocker: MockerFixture) -> None:
        """AC: Processor runs post-agent git status check and logs warning if changes remain."""
        mock_status = mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            side_effect=[True, True],  # First call: dirty, second call: still dirty
        )
        mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.git.processor.smart_push",
            new_callable=AsyncMock,
            return_value=PUSH_RESULT["PUSHED"],
        )

        processor = GitProcessor(AgentDefaults(cwd=Path("/workspace")))
        await processor.process(_make_session())

        # Should have called status twice (before and after agent)
        assert mock_status.call_count == 2

    async def test_calls_smart_push_after_commit(self, mocker: MockerFixture) -> None:
        """AC: Processor calls smart_push after committing (replaces bare push)."""
        mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            side_effect=[True, False],
        )
        mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )
        mock_smart_push = mocker.patch(
            "tachikoma.git.processor.smart_push",
            new_callable=AsyncMock,
            return_value=PUSH_RESULT["PUSHED"],
        )

        defaults = AgentDefaults(cwd=Path("/workspace"))
        processor = GitProcessor(defaults)
        await processor.process(_make_session())

        mock_smart_push.assert_awaited_once_with(
            Path("/workspace"),
            "origin",
            "HEAD",
            defaults,
        )

    async def test_handles_nothing_to_push(self, mocker: MockerFixture) -> None:
        """AC: Processor handles NOTHING_TO_PUSH gracefully."""
        mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            side_effect=[True, False],
        )
        mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.git.processor.smart_push",
            new_callable=AsyncMock,
            return_value=PUSH_RESULT["NOTHING_TO_PUSH"],
        )

        processor = GitProcessor(AgentDefaults(cwd=Path("/workspace")))
        await processor.process(_make_session())  # Should not raise

    async def test_handles_push_failure_gracefully(self, mocker: MockerFixture) -> None:
        """AC: Processor handles PUSH_FAILED/REBASE_FAILED gracefully."""
        mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            side_effect=[True, False],
        )
        mocker.patch(
            "tachikoma.git.processor.query_and_consume",
            new_callable=AsyncMock,
        )
        mocker.patch(
            "tachikoma.git.processor.smart_push",
            new_callable=AsyncMock,
            return_value=PUSH_RESULT["REBASE_FAILED"],
        )

        processor = GitProcessor(AgentDefaults(cwd=Path("/workspace")))
        await processor.process(_make_session())  # Should not raise

    async def test_no_push_when_workspace_clean(self, mocker: MockerFixture) -> None:
        """AC: No push attempted when workspace is clean (early return before push)."""
        mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            return_value=False,
        )
        mock_smart_push = mocker.patch(
            "tachikoma.git.processor.smart_push",
            new_callable=AsyncMock,
        )

        processor = GitProcessor(AgentDefaults(cwd=Path("/workspace")))
        await processor.process(_make_session())

        mock_smart_push.assert_not_awaited()


class TestQueryAndConsume:
    """Tests for query_and_consume helper."""

    async def test_calls_query_with_correct_options(self, mocker: MockerFixture) -> None:
        """AC: query_and_consume calls query() with correct options."""
        mock_query = mocker.patch("tachikoma.git.processor.stderr_aware_query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        defaults = AgentDefaults(cwd=Path("/workspace"))
        await query_and_consume("test prompt", defaults)

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        assert call_kwargs[1]["prompt"] == "test prompt"

        options = call_kwargs[1]["options"]
        assert options.model == "haiku"
        assert options.cwd == Path("/workspace")
        assert options.permission_mode == "bypassPermissions"

    async def test_consumes_full_async_iterator(self, mocker: MockerFixture) -> None:
        """AC: query_and_consume fully consumes async iterator."""
        consume_count = 0

        async def fake_query(*args, **kwargs):
            nonlocal consume_count
            for i in range(3):
                consume_count += 1
                yield MagicMock(msg=i)

        mocker.patch("tachikoma.git.processor.stderr_aware_query", side_effect=fake_query)

        await query_and_consume("prompt", AgentDefaults(cwd=Path("/workspace")))

        assert consume_count == 3

    async def test_propagates_query_error(self, mocker: MockerFixture) -> None:
        """AC: query_and_consume propagates query() errors."""

        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.git.processor.stderr_aware_query", side_effect=failing_query)

        with pytest.raises(RuntimeError, match="SDK error"):
            await query_and_consume("prompt", AgentDefaults(cwd=Path("/workspace")))


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


class TestGitBashHook:
    """Tests for GIT_BASH_HOOK allow-list."""

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git diff --cached",
            "ls -la",
            "find . -name '*.md'",
            "file /workspace/.tachikoma/tachikoma.db",
            'echo "---"',
            "date +%Y-%m-%d",
            "cat README.md",
            "head -20 log.txt",
            "tail -f log.txt",
            "wc -l file.txt",
            "stat pyproject.toml",
            "cd /workspace",
            "cd",
            "pwd",
        ],
    )
    async def test_allows_inspection_and_git_commands(self, command: str) -> None:
        """Allow-list permits git commands and read-only inspection utilities."""
        # HookMatcher.hooks is a list of callables; invoke the first one directly
        hook_fn = GIT_BASH_HOOK.hooks[0]
        result = await hook_fn(
            {"tool_input": {"command": command}},
            None,
            None,
        )
        # Empty dict means allow (no permission override)
        assert result == {}

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /workspace",
            "curl https://example.com",
            "python -c 'print(1)'",
            "sh -c 'echo test'",
            "bash -c 'echo test'",
            "mkdir newdir",
            "touch file",
        ],
    )
    async def test_denies_non_git_non_inspection_commands(self, command: str) -> None:
        """Destructive/arbitrary commands are denied with a reason."""
        hook_fn = GIT_BASH_HOOK.hooks[0]
        result = await hook_fn(
            {"tool_input": {"command": command}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "allowed" in result["hookSpecificOutput"]["permissionDecisionReason"]


class TestGitBashHookCompoundCommands:
    """Tests for compound command splitting in GIT_BASH_HOOK.

    Compound commands (joined by &&, ||, |, ;) are split and each
    sub-command is checked independently. If any sub-command fails,
    the entire command is denied.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "git status && git diff",
            "cd /workspace && git status",
            "cd && pwd",
            "git status | head -5",
            "ls -la && cat README.md",
            "git status; git diff",
        ],
    )
    async def test_allows_compound_commands_with_all_allowed_parts(self, command: str) -> None:
        """Compound commands where every sub-command is in the allow-list are allowed."""
        hook_fn = GIT_BASH_HOOK.hooks[0]
        result = await hook_fn(
            {"tool_input": {"command": command}},
            None,
            None,
        )
        assert result == {}

    @pytest.mark.parametrize(
        "command",
        [
            "git status && rm -rf /workspace",
            "git status || python -c 'print(1)'",
            "git status | grep modified",
            "ls -la; rm -rf /workspace",
            "cd /workspace && curl https://example.com",
        ],
    )
    async def test_denies_compound_commands_with_disallowed_parts(self, command: str) -> None:
        """Compound commands where any sub-command is not in the allow-list are denied."""
        hook_fn = GIT_BASH_HOOK.hooks[0]
        result = await hook_fn(
            {"tool_input": {"command": command}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
