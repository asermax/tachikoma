"""Tests for git MCP tools (push, sync) and destructive-git deny patterns."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.sync import PUSH_RESULT, SYNC_RESULT
from tachikoma.git.tools import (
    DESTRUCTIVE_GIT_DENY_PATTERNS,
    create_git_tools_server,
    handle_push,
    handle_sync,
    resolve_target,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a tmp workspace marked as a git repo."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def agent_defaults(workspace: Path) -> AgentDefaults:
    return AgentDefaults(cwd=workspace)


def _register_project(workspace: Path, name: str) -> Path:
    project_path = workspace / "projects" / name
    project_path.mkdir(parents=True)
    (project_path / ".git").mkdir()
    return project_path


class TestResolveTarget:
    def test_workspace_always_resolves_to_workspace_root(self, workspace: Path) -> None:
        assert resolve_target("workspace", None, workspace) == workspace
        assert resolve_target("workspace", "ignored", workspace) == workspace

    def test_project_resolves_when_registered(self, workspace: Path) -> None:
        project = _register_project(workspace, "my-app")

        assert resolve_target("project", "my-app", workspace) == project

    def test_project_returns_none_when_target_missing(self, workspace: Path) -> None:
        assert resolve_target("project", None, workspace) is None
        assert resolve_target("project", "", workspace) is None

    def test_project_returns_none_when_not_a_git_repo(self, workspace: Path) -> None:
        # Directory exists but without .git marker
        (workspace / "projects" / "orphan").mkdir(parents=True)

        assert resolve_target("project", "orphan", workspace) is None

    def test_project_returns_none_for_unknown_name(self, workspace: Path) -> None:
        assert resolve_target("project", "nonexistent", workspace) is None


class TestHandlePush:
    @patch(
        "tachikoma.git.tools.smart_push",
        new_callable=AsyncMock,
        return_value=PUSH_RESULT["PUSHED"],
    )
    async def test_workspace_push(
        self,
        mock_push: AsyncMock,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        result = await handle_push("workspace", None, workspace, agent_defaults)

        assert "is_error" not in result or result["is_error"] is False
        assert "workspace" in result["content"][0]["text"]
        assert PUSH_RESULT["PUSHED"] in result["content"][0]["text"]
        mock_push.assert_awaited_once_with(workspace, "origin", "HEAD", agent_defaults)

    @patch(
        "tachikoma.git.tools.smart_push",
        new_callable=AsyncMock,
        return_value=PUSH_RESULT["PUSHED"],
    )
    async def test_project_push(
        self,
        mock_push: AsyncMock,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        project = _register_project(workspace, "my-app")

        result = await handle_push("project", "my-app", workspace, agent_defaults)

        assert "is_error" not in result or result["is_error"] is False
        assert "my-app" in result["content"][0]["text"]
        mock_push.assert_awaited_once_with(project, "origin", "HEAD", agent_defaults)

    async def test_project_push_without_target_errors(
        self,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        with patch("tachikoma.git.tools.smart_push", new_callable=AsyncMock) as mock_push:
            result = await handle_push("project", None, workspace, agent_defaults)

        assert result["is_error"] is True
        assert "could not resolve" in result["content"][0]["text"]
        mock_push.assert_not_awaited()

    async def test_project_push_unknown_target_errors(
        self,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        with patch("tachikoma.git.tools.smart_push", new_callable=AsyncMock) as mock_push:
            result = await handle_push("project", "ghost", workspace, agent_defaults)

        assert result["is_error"] is True
        assert "ghost" in result["content"][0]["text"]
        mock_push.assert_not_awaited()

    @patch(
        "tachikoma.git.tools.smart_push",
        new_callable=AsyncMock,
        return_value=PUSH_RESULT["PUSH_FAILED"],
    )
    async def test_push_failure_flagged_as_error(
        self,
        mock_push: AsyncMock,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        result = await handle_push("workspace", None, workspace, agent_defaults)

        assert result["is_error"] is True
        assert PUSH_RESULT["PUSH_FAILED"] in result["content"][0]["text"]


class TestHandleSync:
    @patch(
        "tachikoma.git.tools.smart_push",
        new_callable=AsyncMock,
        return_value=PUSH_RESULT["PUSHED"],
    )
    @patch(
        "tachikoma.git.tools.smart_pull",
        new_callable=AsyncMock,
        return_value=(SYNC_RESULT["UP_TO_DATE"], []),
    )
    async def test_sync_runs_pull_then_push(
        self,
        mock_pull: AsyncMock,
        mock_push: AsyncMock,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        result = await handle_sync("workspace", None, workspace, agent_defaults)

        assert "is_error" not in result or result["is_error"] is False
        text = result["content"][0]["text"]
        assert SYNC_RESULT["UP_TO_DATE"] in text
        assert PUSH_RESULT["PUSHED"] in text
        mock_pull.assert_awaited_once()
        mock_push.assert_awaited_once()

    @patch(
        "tachikoma.git.tools.smart_push",
        new_callable=AsyncMock,
    )
    @patch(
        "tachikoma.git.tools.smart_pull",
        new_callable=AsyncMock,
        return_value=(SYNC_RESULT["DIRTY_SKIPPED"], []),
    )
    async def test_sync_short_circuits_on_dirty_skipped(
        self,
        mock_pull: AsyncMock,
        mock_push: AsyncMock,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        result = await handle_sync("workspace", None, workspace, agent_defaults)

        assert result["is_error"] is True
        assert SYNC_RESULT["DIRTY_SKIPPED"] in result["content"][0]["text"]
        assert "push skipped" in result["content"][0]["text"]
        mock_pull.assert_awaited_once()
        mock_push.assert_not_awaited()

    @patch(
        "tachikoma.git.tools.smart_push",
        new_callable=AsyncMock,
    )
    @patch(
        "tachikoma.git.tools.smart_pull",
        new_callable=AsyncMock,
        return_value=(SYNC_RESULT["SYNC_FAILED"], []),
    )
    async def test_sync_short_circuits_on_sync_failed(
        self,
        mock_pull: AsyncMock,
        mock_push: AsyncMock,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        result = await handle_sync("workspace", None, workspace, agent_defaults)

        assert result["is_error"] is True
        assert SYNC_RESULT["SYNC_FAILED"] in result["content"][0]["text"]
        mock_push.assert_not_awaited()

    async def test_sync_errors_on_unresolvable_target(
        self,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        with (
            patch("tachikoma.git.tools.smart_pull", new_callable=AsyncMock) as mock_pull,
            patch("tachikoma.git.tools.smart_push", new_callable=AsyncMock) as mock_push,
        ):
            result = await handle_sync("project", None, workspace, agent_defaults)

        assert result["is_error"] is True
        mock_pull.assert_not_awaited()
        mock_push.assert_not_awaited()


class TestCreateGitToolsServer:
    def test_returns_sdk_mcp_server(
        self,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        server = create_git_tools_server(workspace, agent_defaults)

        assert isinstance(server, dict)
        assert server["type"] == "sdk"
        assert server["name"] == "git-tools"

    def test_does_not_register_commit_tool(
        self,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        """AC8: the git-tools MCP server must not expose a `commit` tool."""
        from tachikoma.git import tools as git_tools_module  # noqa: PLC0415

        create_git_tools_server(workspace, agent_defaults)

        assert not hasattr(git_tools_module, "handle_commit")


class TestDestructiveGitDenyPatterns:
    @pytest.mark.parametrize(
        "command",
        [
            "git push",
            "git push origin main",
            "git push --force origin main",
            "git push -f",
            "git push origin main --force-with-lease",
            "git reset",
            "git reset --hard",
            "git reset HEAD~1",
            "git checkout .",
            "git restore .",
            "git clean",
            "git clean -fd",
            "git remote add origin git@github.com:x/y.git",
            "git remote remove foo",
            "git remote rm foo",
            "git remote rename a b",
            "git remote set-url origin git@x:y/z.git",
            "git remote set-head origin main",
            "git remote set-branches origin main",
            "git remote prune origin",
        ],
    )
    def test_destructive_commands_are_denied(self, command: str) -> None:
        matched = [p for p in DESTRUCTIVE_GIT_DENY_PATTERNS if p.match(command)]
        assert matched, f"expected {command!r} to match a deny pattern"

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git status --porcelain",
            "git log --oneline",
            "git diff",
            "git show HEAD",
            "git fetch origin",
            "git remote",
            "git remote -v",
            "git branch",
            "git branch --list",
            "git clone git@github.com:x/y.git /tmp/foo",
            "git add file.txt",
            "git commit -m 'msg'",
            "git checkout main",
            "git restore --staged file.txt",
            "ls -la",
            "echo hi",
        ],
    )
    def test_safe_commands_are_not_denied(self, command: str) -> None:
        matched = [p for p in DESTRUCTIVE_GIT_DENY_PATTERNS if p.match(command)]
        assert not matched, f"expected {command!r} to pass, matched: {[p.pattern for p in matched]}"
