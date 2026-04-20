"""Tests for git sync utilities.

Tests for DLT-097: Keep local repositories in sync with remotes.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.sync import (
    DIVERGENCE_STATUS,
    PUSH_RESULT,
    SYNC_RESULT,
    _abort_stale_rebase,
    _agent_rebase,
    _retry_rebase_with_stash,
    _try_naive_rebase,
    detect_divergence,
    has_uncommitted_changes,
    smart_pull,
    smart_push,
)


def _mock_git_capture(*args: str, cwd: Path | None = None) -> AsyncMock:
    """Helper to mock run_git_capture for HEAD capture and diff."""
    if "rev-parse" in args:
        return AsyncMock(return_value=(0, "abc123"))
    if "diff" in args:
        return AsyncMock(return_value=(0, ""))
    return AsyncMock(return_value=(0, ""))


class AsyncSubprocessMock:
    """Mock for asyncio.subprocess.Process."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    """Create a temporary repository directory with .git."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    return tmp_path


@pytest.fixture
def agent_defaults(tmp_path: Path) -> AgentDefaults:
    """Create test AgentDefaults."""
    return AgentDefaults(cwd=tmp_path)


# --- Enum Tests ---


class TestDivergenceStatus:
    """Tests for DivergenceStatus enum values."""

    def test_up_to_date(self) -> None:
        assert DIVERGENCE_STATUS["UP_TO_DATE"] == "UP_TO_DATE"

    def test_ahead(self) -> None:
        assert DIVERGENCE_STATUS["AHEAD"] == "AHEAD"

    def test_behind(self) -> None:
        assert DIVERGENCE_STATUS["BEHIND"] == "BEHIND"

    def test_diverged(self) -> None:
        assert DIVERGENCE_STATUS["DIVERGED"] == "DIVERGED"


class TestPushResult:
    """Tests for PushResult enum values."""

    def test_pushed(self) -> None:
        assert PUSH_RESULT["PUSHED"] == "PUSHED"

    def test_nothing_to_push(self) -> None:
        assert PUSH_RESULT["NOTHING_TO_PUSH"] == "NOTHING_TO_PUSH"

    def test_rebase_succeeded(self) -> None:
        assert PUSH_RESULT["REBASE_SUCCEEDED"] == "REBASE_SUCCEEDED"

    def test_agent_resolved(self) -> None:
        assert PUSH_RESULT["AGENT_RESOLVED"] == "AGENT_RESOLVED"

    def test_push_failed(self) -> None:
        assert PUSH_RESULT["PUSH_FAILED"] == "PUSH_FAILED"

    def test_rebase_failed(self) -> None:
        assert PUSH_RESULT["REBASE_FAILED"] == "REBASE_FAILED"


class TestSyncResult:
    """Tests for SyncResult enum values."""

    def test_up_to_date(self) -> None:
        assert SYNC_RESULT["UP_TO_DATE"] == "UP_TO_DATE"

    def test_fast_forwarded(self) -> None:
        assert SYNC_RESULT["FAST_FORWARDED"] == "FAST_FORWARDED"

    def test_rebase_succeeded(self) -> None:
        assert SYNC_RESULT["REBASE_SUCCEEDED"] == "REBASE_SUCCEEDED"

    def test_agent_resolved(self) -> None:
        assert SYNC_RESULT["AGENT_RESOLVED"] == "AGENT_RESOLVED"

    def test_sync_failed(self) -> None:
        assert SYNC_RESULT["SYNC_FAILED"] == "SYNC_FAILED"

    def test_dirty_skipped(self) -> None:
        assert SYNC_RESULT["DIRTY_SKIPPED"] == "DIRTY_SKIPPED"


# --- Helper Tests ---


@pytest.mark.asyncio
class TestHasUncommittedChanges:
    """Tests for has_uncommitted_changes."""

    async def test_returns_true_when_dirty(self, repo_path: Path) -> None:
        """Returns True when git status --porcelain has output."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b"M file.txt\n"),
        ):
            result = await has_uncommitted_changes(repo_path)
            assert result is True

    async def test_returns_false_when_clean(self, repo_path: Path) -> None:
        """Returns False when git status --porcelain is empty."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b""),
        ):
            result = await has_uncommitted_changes(repo_path)
            assert result is False


@pytest.mark.asyncio
class TestAbortStaleRebase:
    """Tests for _abort_stale_rebase."""

    async def test_aborts_rebase_merge_dir(self, repo_path: Path) -> None:
        """Aborts when .git/rebase-merge/ exists."""
        (repo_path / ".git" / "rebase-merge").mkdir()

        with patch(
            "tachikoma.git.sync.run_git",
            new_callable=AsyncMock,
        ) as mock_run:
            result = await _abort_stale_rebase(repo_path)

        assert result is True
        mock_run.assert_awaited_once_with("rebase", "--abort", cwd=repo_path)

    async def test_aborts_rebase_apply_dir(self, repo_path: Path) -> None:
        """Aborts when .git/rebase-apply/ exists."""
        (repo_path / ".git" / "rebase-apply").mkdir()

        with patch(
            "tachikoma.git.sync.run_git",
            new_callable=AsyncMock,
        ):
            result = await _abort_stale_rebase(repo_path)

        assert result is True

    async def test_no_op_when_clean(self, repo_path: Path) -> None:
        """Returns False when no rebase dirs exist."""
        result = await _abort_stale_rebase(repo_path)
        assert result is False


# --- Core Function Tests ---


@pytest.mark.asyncio
class TestDetectDivergence:
    """Tests for detect_divergence."""

    async def test_up_to_date(self, repo_path: Path) -> None:
        """Both ancestors → UP_TO_DATE (same commit)."""
        # Both merge-base checks return 0 (both are ancestors of each other)
        calls = [
            AsyncSubprocessMock(returncode=0),
            AsyncSubprocessMock(returncode=0),
        ]
        call_idx = [0]

        async def mock_exec(*args: object, **kwargs: object) -> AsyncSubprocessMock:
            result = calls[call_idx[0]]
            call_idx[0] += 1
            return result

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await detect_divergence(repo_path)
        assert result == DIVERGENCE_STATUS["UP_TO_DATE"]

    async def test_ahead(self, repo_path: Path) -> None:
        """Only remote is ancestor of HEAD → AHEAD."""
        calls = [
            AsyncSubprocessMock(returncode=0),  # remote is ancestor of HEAD
            AsyncSubprocessMock(returncode=1),  # HEAD is NOT ancestor of remote
        ]
        call_idx = [0]

        async def mock_exec(*args: object, **kwargs: object) -> AsyncSubprocessMock:
            result = calls[call_idx[0]]
            call_idx[0] += 1
            return result

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await detect_divergence(repo_path)
        assert result == DIVERGENCE_STATUS["AHEAD"]

    async def test_behind(self, repo_path: Path) -> None:
        """Only HEAD is ancestor of remote → BEHIND."""
        calls = [
            AsyncSubprocessMock(returncode=1),  # remote is NOT ancestor of HEAD
            AsyncSubprocessMock(returncode=0),  # HEAD IS ancestor of remote
        ]
        call_idx = [0]

        async def mock_exec(*args: object, **kwargs: object) -> AsyncSubprocessMock:
            result = calls[call_idx[0]]
            call_idx[0] += 1
            return result

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await detect_divergence(repo_path)
        assert result == DIVERGENCE_STATUS["BEHIND"]

    async def test_diverged(self, repo_path: Path) -> None:
        """Neither is ancestor → DIVERGED."""
        calls = [
            AsyncSubprocessMock(returncode=1),  # remote is NOT ancestor of HEAD
            AsyncSubprocessMock(returncode=1),  # HEAD is NOT ancestor of remote
        ]
        call_idx = [0]

        async def mock_exec(*args: object, **kwargs: object) -> AsyncSubprocessMock:
            result = calls[call_idx[0]]
            call_idx[0] += 1
            return result

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await detect_divergence(repo_path)
        assert result == DIVERGENCE_STATUS["DIVERGED"]


@pytest.mark.asyncio
class TestTryNaiveRebase:
    """Tests for _try_naive_rebase."""

    async def test_returns_true_on_success(self, repo_path: Path) -> None:
        """Returns True when rebase exits 0 (clean rebase)."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            result = await _try_naive_rebase(repo_path, "origin/main")
        assert result is True

    async def test_returns_false_and_aborts_on_conflict(self, repo_path: Path) -> None:
        """Returns False when rebase fails, aborts the rebase."""
        # Simulate rebase state dirs existing (conflicts detected)
        (repo_path / ".git" / "rebase-merge").mkdir()

        calls = [
            AsyncSubprocessMock(returncode=1),  # rebase fails
            AsyncSubprocessMock(returncode=0),  # rebase --abort succeeds
        ]
        call_idx = [0]

        async def mock_exec(*args: object, **kwargs: object) -> AsyncSubprocessMock:
            result = calls[call_idx[0]]
            call_idx[0] += 1
            return result

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await _try_naive_rebase(repo_path, "origin/main")
        assert result is False
        assert call_idx[0] == 2  # Both rebase and abort called

    async def test_returns_false_without_abort_when_no_conflict(self, repo_path: Path) -> None:
        """Returns False without aborting when rebase fails but no rebase state exists."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1),
        ):
            result = await _try_naive_rebase(repo_path, "origin/main")
        assert result is False
        # Only rebase called (no abort since no rebase-merge/rebase-apply dirs)


# --- Smart Push Tests ---


@pytest.mark.asyncio
class TestSmartPush:
    """Tests for smart_push."""

    async def test_nothing_to_push_when_up_to_date(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns NOTHING_TO_PUSH when up to date."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["UP_TO_DATE"],
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["NOTHING_TO_PUSH"]

    async def test_nothing_to_push_when_behind(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns NOTHING_TO_PUSH when behind."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["BEHIND"],
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["NOTHING_TO_PUSH"]

    async def test_pushed_when_ahead(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns PUSHED when ahead (fast-forward push)."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["AHEAD"],
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["PUSHED"]

    async def test_rebase_succeeded_when_clean_rebase(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns REBASE_SUCCEEDED when naive rebase works cleanly."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["REBASE_SUCCEEDED"]

    async def test_agent_resolved_when_agent_succeeds(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns AGENT_RESOLVED when naive rebase fails but agent succeeds."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=True,
            ),
            patch(
                "tachikoma.git.sync._agent_rebase",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["AGENT_RESOLVED"]

    async def test_rebase_failed_when_both_fail(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns REBASE_FAILED when both naive and agent rebase fail."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=True,
            ),
            patch(
                "tachikoma.git.sync._agent_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["REBASE_FAILED"]

    async def test_push_failed_when_push_fails_after_rebase(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns PUSH_FAILED when rebase succeeds but push itself fails."""

        async def run_git_side_effect(*args: str, cwd: Path) -> None:
            if "push" in args:
                raise RuntimeError("push rejected")

        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.run_git",
                new_callable=AsyncMock,
                side_effect=run_git_side_effect,
            ),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["PUSH_FAILED"]

    async def test_returns_rebase_failed_without_agent_defaults(
        self,
        repo_path: Path,
    ) -> None:
        """Returns REBASE_FAILED when diverged and no agent_defaults provided."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=None)
        assert result == PUSH_RESULT["REBASE_FAILED"]

    async def test_rebase_failed_without_conflicts(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns REBASE_FAILED without spawning agent when no rebase state exists."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._retry_rebase_with_stash",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._agent_rebase",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_agent,
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["REBASE_FAILED"]
        mock_agent.assert_not_called()

    async def test_catches_fetch_failure(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns REBASE_FAILED when fetch fails (network error)."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.run_git",
                new_callable=AsyncMock,
                side_effect=RuntimeError("network error"),
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["REBASE_FAILED"]


    async def test_stash_retry_succeeds_on_dirty_tree(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """AC5: Stash-assisted rebase succeeds on dirty tree → REBASE_SUCCEEDED."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tachikoma.git.sync._retry_rebase_with_stash",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["REBASE_SUCCEEDED"]

    async def test_stash_retry_fails_then_rebase_failed(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """AC6: Stash-assisted rebase also fails → REBASE_FAILED."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tachikoma.git.sync._retry_rebase_with_stash",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["REBASE_FAILED"]

    async def test_clean_tree_skips_stash_retry(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Stash retry is skipped when tree is clean despite rebase failing without starting."""
        with (
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._retry_rebase_with_stash",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_stash_retry,
        ):
            result = await smart_push(repo_path, agent_defaults=agent_defaults)
        assert result == PUSH_RESULT["REBASE_FAILED"]
        mock_stash_retry.assert_not_called()


# --- Stash Retry Helper Tests ---


@pytest.mark.asyncio
class TestRetryRebaseWithStash:
    """Tests for _retry_rebase_with_stash."""

    async def test_stashes_rebases_and_restores(self, repo_path: Path) -> None:
        """Stash, rebase succeeds, stash pop called."""
        with (
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_rebase,
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock) as mock_run_git,
        ):
            result = await _retry_rebase_with_stash(repo_path, "origin/main")

        assert result is True
        mock_rebase.assert_awaited_once_with(repo_path, "origin/main")
        mock_run_git.assert_any_await("stash", cwd=repo_path)
        mock_run_git.assert_any_await("stash", "pop", cwd=repo_path)

    async def test_restores_stash_on_rebase_failure(self, repo_path: Path) -> None:
        """Stash pop is called even when rebase fails."""
        with (
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock) as mock_run_git,
        ):
            result = await _retry_rebase_with_stash(repo_path, "origin/main")

        assert result is False
        mock_run_git.assert_any_await("stash", cwd=repo_path)
        mock_run_git.assert_any_await("stash", "pop", cwd=repo_path)

    async def test_returns_false_when_stash_fails(self, repo_path: Path) -> None:
        """Returns False when git stash fails."""
        with (
            patch(
                "tachikoma.git.sync.run_git",
                new_callable=AsyncMock,
                side_effect=RuntimeError("stash failed"),
            ),
        ):
            result = await _retry_rebase_with_stash(repo_path, "origin/main")

        assert result is False

    async def test_restores_stash_on_rebase_exception(self, repo_path: Path) -> None:
        """Stash pop is called even when rebase raises."""
        with (
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                side_effect=RuntimeError("rebase crashed"),
            ),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock) as mock_run_git,
            pytest.raises(RuntimeError, match="rebase crashed"),
        ):
            await _retry_rebase_with_stash(repo_path, "origin/main")

        # Stash was created and restored despite the exception
        mock_run_git.assert_any_await("stash", cwd=repo_path)
        mock_run_git.assert_any_await("stash", "pop", cwd=repo_path)


# --- Smart Pull Tests ---


@pytest.mark.asyncio
class TestSmartPull:
    """Tests for smart_pull."""

    async def test_dirty_skipped_when_uncommitted_changes(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns (DIRTY_SKIPPED, []) when working tree has uncommitted changes."""
        with patch(
            "tachikoma.git.sync.has_uncommitted_changes",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result, changed = await smart_pull(repo_path, agent_defaults=agent_defaults)
        assert result == SYNC_RESULT["DIRTY_SKIPPED"]
        assert changed == []

    async def test_up_to_date_when_no_divergence(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns (UP_TO_DATE, []) when local matches remote."""
        with (
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["UP_TO_DATE"],
            ),
        ):
            result, changed = await smart_pull(repo_path, agent_defaults=agent_defaults)
        assert result == SYNC_RESULT["UP_TO_DATE"]
        assert changed == []

    async def test_fast_forwarded_when_behind(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns (FAST_FORWARDED, changed) when local is behind remote."""
        with (
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.run_git_capture",
                new_callable=AsyncMock,
                side_effect=[
                    (0, "abc123"),  # rev-parse HEAD
                    (0, "file1.txt\nfile2.txt"),  # diff --name-only
                ],
            ),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["BEHIND"],
            ),
        ):
            result, changed = await smart_pull(repo_path, agent_defaults=agent_defaults)
        assert result == SYNC_RESULT["FAST_FORWARDED"]
        assert changed == ["file1.txt", "file2.txt"]

    async def test_rebase_succeeded_when_clean_rebase(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns (REBASE_SUCCEEDED, changed) when naive rebase works cleanly."""
        with (
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.run_git_capture",
                new_callable=AsyncMock,
                side_effect=[
                    (0, "abc123"),  # rev-parse HEAD
                    (0, ".tachikoma/db-dump/sessions.ndjson"),  # diff --name-only
                ],
            ),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result, changed = await smart_pull(repo_path, agent_defaults=agent_defaults)
        assert result == SYNC_RESULT["REBASE_SUCCEEDED"]
        assert ".tachikoma/db-dump/sessions.ndjson" in changed

    async def test_agent_resolved_when_agent_succeeds(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns (AGENT_RESOLVED, changed) when naive fails but agent succeeds."""
        with (
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.run_git_capture",
                new_callable=AsyncMock,
                side_effect=[
                    (0, "abc123"),  # rev-parse HEAD
                    (0, ""),  # diff --name-only (empty)
                ],
            ),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=True,
            ),
            patch(
                "tachikoma.git.sync._agent_rebase",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result, changed = await smart_pull(repo_path, agent_defaults=agent_defaults)
        assert result == SYNC_RESULT["AGENT_RESOLVED"]
        assert changed == []

    async def test_sync_failed_when_both_fail(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns (SYNC_FAILED, []) when both naive and agent rebase fail."""
        with (
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=True,
            ),
            patch(
                "tachikoma.git.sync._agent_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            result, changed = await smart_pull(repo_path, agent_defaults=agent_defaults)
        assert result == SYNC_RESULT["SYNC_FAILED"]
        assert changed == []

    async def test_catches_fetch_failure(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns (SYNC_FAILED, []) when fetch fails (network error)."""
        with (
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.run_git",
                new_callable=AsyncMock,
                side_effect=RuntimeError("network error"),
            ),
        ):
            result, changed = await smart_pull(repo_path, agent_defaults=agent_defaults)
        assert result == SYNC_RESULT["SYNC_FAILED"]
        assert changed == []

    async def test_sync_failed_without_conflicts(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns SYNC_FAILED without spawning agent when no rebase state exists."""
        with (
            patch(
                "tachikoma.git.sync.has_uncommitted_changes",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("tachikoma.git.sync._abort_stale_rebase", new_callable=AsyncMock),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
            patch(
                "tachikoma.git.sync.run_git_capture",
                new_callable=AsyncMock,
                side_effect=[
                    (0, "abc123"),
                    (0, ""),
                ],
            ),
            patch(
                "tachikoma.git.sync.detect_divergence",
                new_callable=AsyncMock,
                return_value=DIVERGENCE_STATUS["DIVERGED"],
            ),
            patch(
                "tachikoma.git.sync._try_naive_rebase",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._rebase_in_progress",
                return_value=False,
            ),
            patch(
                "tachikoma.git.sync._agent_rebase",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_agent,
        ):
            result, changed = await smart_pull(repo_path, agent_defaults=agent_defaults)
        assert result == SYNC_RESULT["SYNC_FAILED"]
        assert changed == []
        mock_agent.assert_not_called()


# --- Agent Rebase Tests ---


@pytest.mark.asyncio
class TestAgentRebase:
    """Tests for _agent_rebase."""

    async def test_returns_true_when_rebase_completed(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns True when agent completes and rebase-merge dir is gone."""

        async def fake_query(*args: object, **kwargs: object):
            yield MagicMock()

        with patch("tachikoma.git.sync.stderr_aware_query", side_effect=fake_query):
            result = await _agent_rebase(repo_path, "origin/main", agent_defaults)
        assert result is True

    async def test_returns_false_when_rebase_still_in_progress(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns False when agent completes but rebase-merge dir still exists."""
        # Create rebase-merge dir (simulates incomplete rebase)
        (repo_path / ".git" / "rebase-merge").mkdir()

        async def fake_query(*args: object, **kwargs: object):
            yield MagicMock()

        with (
            patch("tachikoma.git.sync.stderr_aware_query", side_effect=fake_query),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
        ):
            result = await _agent_rebase(repo_path, "origin/main", agent_defaults)
        assert result is False

    async def test_returns_false_when_agent_crashes(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """Returns False when agent raises an exception."""
        # Create rebase-merge dir (simulates incomplete rebase)
        (repo_path / ".git" / "rebase-merge").mkdir()

        async def failing_query(*args: object, **kwargs: object):
            raise RuntimeError("API error")
            yield  # make it a generator

        with (
            patch("tachikoma.git.sync.stderr_aware_query", side_effect=failing_query),
            patch("tachikoma.git.sync.run_git", new_callable=AsyncMock),
        ):
            result = await _agent_rebase(repo_path, "origin/main", agent_defaults)
        assert result is False

    async def test_fully_consumes_query_generator(
        self,
        repo_path: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """DES-005: query() generator is fully consumed (no break/return)."""
        consume_count = 0

        async def fake_query(*args: object, **kwargs: object):
            nonlocal consume_count
            for i in range(5):
                consume_count += 1
                yield MagicMock(msg=i)

        with patch("tachikoma.git.sync.stderr_aware_query", side_effect=fake_query):
            await _agent_rebase(repo_path, "origin/main", agent_defaults)

        assert consume_count == 5
