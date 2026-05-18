"""Tests for git post-processor.

Git module for workspace version tracking.
smart_push replaces bare push.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.processor import (
    _COMMIT_RETRY_PROMPT,
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
            model="haiku",
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
        """AC: Processor retries once when changes remain, logs warning if still dirty."""
        mock_status = mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            side_effect=[True, True, True],  # dirty → still dirty → still dirty after retry
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

        # Two query calls: initial commit + retry
        assert mock_query.await_count == 2
        mock_query.assert_any_await(
            _COMMIT_RETRY_PROMPT,
            AgentDefaults(cwd=Path("/workspace")),
            tools=GIT_TOOLS,
            allow=GIT_ALLOW,
            pre_tool_use_hooks=[GIT_BASH_HOOK],
            model="haiku",
        )
        # Three status checks: initial, after first pass, after retry
        assert mock_status.call_count == 3

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

    async def test_retries_once_when_changes_remain_after_first_pass(
        self,
        mocker: MockerFixture,
    ) -> None:
        """AC: Processor spawns a cleanup agent once when changes remain after first pass."""
        mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            side_effect=[True, True, False],  # dirty → still dirty → clean after retry
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

        # Two query calls: initial commit + retry
        assert mock_query.await_count == 2
        # Second call uses the cleanup prompt
        mock_query.assert_any_await(
            _COMMIT_RETRY_PROMPT,
            AgentDefaults(cwd=Path("/workspace")),
            tools=GIT_TOOLS,
            allow=GIT_ALLOW,
            pre_tool_use_hooks=[GIT_BASH_HOOK],
            model="haiku",
        )

    async def test_no_retry_when_first_pass_commits_everything(
        self,
        mocker: MockerFixture,
    ) -> None:
        """AC: No retry when the first commit pass leaves the working tree clean."""
        mocker.patch(
            "tachikoma.git.processor.has_uncommitted_changes",
            new_callable=AsyncMock,
            side_effect=[True, False],  # dirty → clean after first pass
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

        # Only one query call — no retry needed
        mock_query.assert_awaited_once()


class TestQueryAndConsume:
    """Tests for query_and_consume helper."""

    async def test_calls_query_with_correct_options(self, mocker: MockerFixture) -> None:
        """AC: query_and_consume calls query() with correct options."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        defaults = AgentDefaults(cwd=Path("/workspace"))
        await query_and_consume("test prompt", defaults, model="haiku")

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

        mocker.patch("tachikoma.post_processing.stderr_aware_query", side_effect=fake_query)

        await query_and_consume("prompt", AgentDefaults(cwd=Path("/workspace")))

        assert consume_count == 3

    async def test_propagates_query_error(self, mocker: MockerFixture) -> None:
        """AC: query_and_consume propagates query() errors."""

        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.post_processing.stderr_aware_query", side_effect=failing_query)

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

    def test_instructs_verification_step(self) -> None:
        """AC: Prompt requires verifying clean working tree after committing."""
        assert "git status" in GIT_COMMIT_PROMPT
        assert "verify the working tree" in GIT_COMMIT_PROMPT
        assert "is clean" in GIT_COMMIT_PROMPT
        assert "No files may be left behind" in GIT_COMMIT_PROMPT

    def test_commit_retry_prompt_exists(self) -> None:
        """AC: Cleanup retry prompt is defined."""
        assert _COMMIT_RETRY_PROMPT
        assert "remaining" in _COMMIT_RETRY_PROMPT.lower()
        assert "git status" in _COMMIT_RETRY_PROMPT


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
            "git status | grep modified",
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

    async def test_pipe_in_quoted_echo_pattern_not_split(self) -> None:
        """Pipe inside quoted argument is not split as a compound operator."""
        hook_fn = GIT_BASH_HOOK.hooks[0]
        result = await hook_fn(
            {"tool_input": {"command": 'echo "pattern1|pattern2"'}},
            None,
            None,
        )

        assert result == {}

    async def test_actual_pipe_after_quoted_arg_splits(self) -> None:
        """Real pipe operator after a quoted argument still splits correctly."""
        hook_fn = GIT_BASH_HOOK.hooks[0]
        result = await hook_fn(
            {"tool_input": {"command": 'echo "a|b" | cat'}},
            None,
            None,
        )

        assert result == {}
