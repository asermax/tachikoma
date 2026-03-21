"""Tests for git helpers in the projects package."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tachikoma.projects.git import (
    add_submodule,
    checkout_branch,
    current_branch,
    current_commit_short,
    fetch,
    has_uncommitted_changes_detail,
    init_submodule,
    is_dirty,
    list_submodules,
    pull,
    push,
    remove_submodule,
    resolve_default_branch,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def submodule_path(tmp_path: Path) -> Path:
    """Create a temporary submodule directory."""
    return tmp_path / "submodule"


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


@pytest.mark.asyncio
class TestListSubmodules:
    """Tests for list_submodules function."""

    async def test_returns_empty_list_when_no_submodules(
        self, workspace: Path
    ) -> None:
        """Returns empty list when git submodule status returns nothing."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b""),
        ):
            result = await list_submodules(workspace)
            assert result == []

    async def test_parses_submodule_paths(self, workspace: Path) -> None:
        """Parses submodule paths from git submodule status output."""
        output = b" abc1234 projects/my-app (heads/main)\n def5678 projects/other (heads/master)\n"
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=output),
        ):
            result = await list_submodules(workspace)
            assert result == ["projects/my-app", "projects/other"]

    async def test_handles_status_indicators(self, workspace: Path) -> None:
        """Handles status indicators (+, -, U) in submodule status output."""
        output = b"+abc1234 projects/added\n-def5678 projects/removed\nUghi890 projects/conflict\n"
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=output),
        ):
            result = await list_submodules(workspace)
            assert result == ["projects/added", "projects/removed", "projects/conflict"]

    async def test_returns_empty_on_nonzero_exit(self, workspace: Path) -> None:
        """Returns empty list when command fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1, stderr=b"error"),
        ):
            result = await list_submodules(workspace)
            assert result == []


@pytest.mark.asyncio
class TestInitSubmodule:
    """Tests for init_submodule function."""

    async def test_succeeds_on_zero_exit(self, workspace: Path) -> None:
        """Succeeds when git returns exit code 0."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await init_submodule(workspace, "projects/my-app")  # Should not raise

    async def test_raises_on_nonzero_exit(self, workspace: Path) -> None:
        """Raises RuntimeError when git fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(
                returncode=1, stderr=b"submodule not found"
            ),
        ):
            with pytest.raises(RuntimeError, match="git submodule update --init failed"):
                await init_submodule(workspace, "projects/my-app")


@pytest.mark.asyncio
class TestResolveDefaultBranch:
    """Tests for resolve_default_branch function."""

    async def test_resolves_from_symbolic_ref(self, submodule_path: Path) -> None:
        """Resolves branch from git symbolic-ref output."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(
                returncode=0, stdout=b"refs/remotes/origin/main\n"
            ),
        ):
            result = await resolve_default_branch(submodule_path)
            assert result == "main"

    async def test_resolves_master_from_symbolic_ref(
        self, submodule_path: Path
    ) -> None:
        """Resolves master branch from git symbolic-ref output."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(
                returncode=0, stdout=b"refs/remotes/origin/master\n"
            ),
        ):
            result = await resolve_default_branch(submodule_path)
            assert result == "master"

    async def test_fallback_to_remote_show(self, submodule_path: Path) -> None:
        """Falls back to git remote show origin when symbolic-ref fails."""
        # First call (symbolic-ref) fails, second call (remote show) succeeds
        calls = [
            AsyncSubprocessMock(returncode=1),
            AsyncSubprocessMock(
                returncode=0, stdout=b"HEAD branch: develop\nother output\n"
            ),
        ]
        call_count = [0]

        async def mock_exec(*args: object, **kwargs: object) -> AsyncSubprocessMock:
            result = calls[call_count[0]]
            call_count[0] += 1
            return result

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await resolve_default_branch(submodule_path)
            assert result == "develop"

    async def test_returns_main_as_final_fallback(
        self, submodule_path: Path
    ) -> None:
        """Returns 'main' when both methods fail."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1),
        ):
            result = await resolve_default_branch(submodule_path)
            assert result == "main"


@pytest.mark.asyncio
class TestCheckoutBranch:
    """Tests for checkout_branch function."""

    async def test_succeeds_on_zero_exit(self, submodule_path: Path) -> None:
        """Succeeds when git returns exit code 0."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await checkout_branch(submodule_path, "main")  # Should not raise

    async def test_raises_on_nonzero_exit(self, submodule_path: Path) -> None:
        """Raises RuntimeError when git fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1, stderr=b"branch not found"),
        ):
            with pytest.raises(RuntimeError, match="git checkout failed"):
                await checkout_branch(submodule_path, "nonexistent")


@pytest.mark.asyncio
class TestFetch:
    """Tests for fetch function."""

    async def test_succeeds_on_zero_exit(self, submodule_path: Path) -> None:
        """Succeeds when git returns exit code 0."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await fetch(submodule_path)  # Should not raise

    async def test_raises_on_nonzero_exit(self, submodule_path: Path) -> None:
        """Raises RuntimeError when git fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1, stderr=b"network error"),
        ):
            with pytest.raises(RuntimeError, match="git fetch failed"):
                await fetch(submodule_path)


@pytest.mark.asyncio
class TestPull:
    """Tests for pull function."""

    async def test_succeeds_on_zero_exit(self, submodule_path: Path) -> None:
        """Succeeds when git returns exit code 0."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await pull(submodule_path)  # Should not raise

    async def test_raises_on_nonzero_exit(self, submodule_path: Path) -> None:
        """Raises RuntimeError when git fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1, stderr=b"merge conflict"),
        ):
            with pytest.raises(RuntimeError, match="git pull failed"):
                await pull(submodule_path)


@pytest.mark.asyncio
class TestIsDirty:
    """Tests for is_dirty function."""

    async def test_returns_false_when_clean(self, submodule_path: Path) -> None:
        """Returns False when git status --porcelain is empty."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b""),
        ):
            result = await is_dirty(submodule_path)
            assert result is False

    async def test_returns_true_when_dirty(self, submodule_path: Path) -> None:
        """Returns True when git status --porcelain has output."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b"M file.txt\n"),
        ):
            result = await is_dirty(submodule_path)
            assert result is True


@pytest.mark.asyncio
class TestPush:
    """Tests for push function."""

    async def test_succeeds_on_zero_exit(self, submodule_path: Path) -> None:
        """Succeeds when git returns exit code 0."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await push(submodule_path)  # Should not raise

    async def test_raises_on_nonzero_exit(self, submodule_path: Path) -> None:
        """Raises RuntimeError when git fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(
                returncode=1, stderr=b"non-fast-forward"
            ),
        ):
            with pytest.raises(RuntimeError, match="git push failed"):
                await push(submodule_path)


@pytest.mark.asyncio
class TestAddSubmodule:
    """Tests for add_submodule function."""

    async def test_succeeds_on_zero_exit(self, workspace: Path) -> None:
        """Succeeds when git returns exit code 0."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await add_submodule(workspace, "my-app", "git@github.com:user/repo.git")

    async def test_raises_on_nonzero_exit(self, workspace: Path) -> None:
        """Raises RuntimeError when git fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1, stderr=b"already exists"),
        ):
            with pytest.raises(RuntimeError, match="git submodule add failed"):
                await add_submodule(workspace, "my-app", "git@github.com:user/repo.git")


@pytest.mark.asyncio
class TestRemoveSubmodule:
    """Tests for remove_submodule function."""

    async def test_succeeds_on_all_commands(self, workspace: Path) -> None:
        """Succeeds when all git commands return exit code 0."""
        # Also need to mock shutil.rmtree
        with (
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=AsyncSubprocessMock(returncode=0),
            ),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            # Create a fake .git/modules path
            modules_path = workspace / ".git" / "modules" / "projects" / "my-app"
            modules_path.mkdir(parents=True, exist_ok=True)

            await remove_submodule(workspace, "my-app")

            mock_rmtree.assert_called_once_with(modules_path)

    async def test_raises_on_deinit_failure(self, workspace: Path) -> None:
        """Raises RuntimeError when git submodule deinit fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1, stderr=b"not initialized"),
        ):
            with pytest.raises(RuntimeError, match="git submodule deinit failed"):
                await remove_submodule(workspace, "my-app")


@pytest.mark.asyncio
class TestCurrentBranch:
    """Tests for current_branch function."""

    async def test_returns_branch_name(self, submodule_path: Path) -> None:
        """Returns branch name when on a branch."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b"main\n"),
        ):
            result = await current_branch(submodule_path)
            assert result == "main"

    async def test_returns_none_on_detached_head(
        self, submodule_path: Path
    ) -> None:
        """Returns None when in detached HEAD state."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1),
        ):
            result = await current_branch(submodule_path)
            assert result is None

    async def test_returns_none_on_empty_output(
        self, submodule_path: Path
    ) -> None:
        """Returns None when output is empty."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b""),
        ):
            result = await current_branch(submodule_path)
            assert result is None


@pytest.mark.asyncio
class TestCurrentCommitShort:
    """Tests for current_commit_short function."""

    async def test_returns_short_hash(self, submodule_path: Path) -> None:
        """Returns short commit hash."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b"abc1234\n"),
        ):
            result = await current_commit_short(submodule_path)
            assert result == "abc1234"

    async def test_returns_unknown_on_failure(self, submodule_path: Path) -> None:
        """Returns 'unknown' when command fails."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=1),
        ):
            result = await current_commit_short(submodule_path)
            assert result == "unknown"


@pytest.mark.asyncio
class TestHasUncommittedChangesDetail:
    """Tests for has_uncommitted_changes_detail function."""

    async def test_returns_none_when_clean(self, submodule_path: Path) -> None:
        """Returns None when no uncommitted changes."""
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0, stdout=b""),
        ):
            result = await has_uncommitted_changes_detail(submodule_path)
            assert result is None

    async def test_returns_status_output_when_dirty(
        self, submodule_path: Path
    ) -> None:
        """Returns status output when there are uncommitted changes."""
        status_output = "M file.txt\n?? new_file.py\n"
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(
                returncode=0, stdout=status_output.encode()
            ),
        ):
            result = await has_uncommitted_changes_detail(submodule_path)
            assert result == status_output.strip()
