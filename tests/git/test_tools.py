"""Tests for git MCP tools (push, sync, scrub) and destructive-git deny patterns."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.sync import PUSH_RESULT, SYNC_RESULT
from tachikoma.git.tools import (
    DESTRUCTIVE_GIT_DENY_PATTERNS,
    PushArgs,
    create_git_tools_server,
    handle_push,
    handle_scrub,
    handle_sync,
    resolve_target,
)
from tachikoma.mcp_utils import decode_json_string_array


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


class TestPushArgs:
    """Validate PushArgs.scrub_paths is exposed as a JSON-encoded string.

    The SDK MCP transport's client-side schema validator rejects array-typed
    arguments, so the field is declared as ``str | None``. Decoding into a
    list happens in the tool wrapper via ``_decode_scrub_paths`` (covered
    separately in TestDecodeScrubPaths).
    """

    def test_scrub_paths_string_passthrough(self) -> None:
        args = PushArgs.model_validate(
            {"type": "project", "target": "x", "scrub_paths": '["a.ogg", "b.json"]'}
        )

        # Stored verbatim — wrapper decodes later
        assert args.scrub_paths == '["a.ogg", "b.json"]'

    def test_scrub_paths_omitted_is_none(self) -> None:
        assert PushArgs.model_validate({"type": "workspace"}).scrub_paths is None

    def test_scrub_paths_explicit_none(self) -> None:
        args = PushArgs.model_validate({"type": "project", "target": "x", "scrub_paths": None})

        assert args.scrub_paths is None

    def test_scrub_paths_schema_advertises_string_not_array(self) -> None:
        """Schema must advertise string | null. An array variant would be
        rejected by the SDK MCP transport's client-side schema validator.
        """
        schema = PushArgs.model_json_schema()["properties"]["scrub_paths"]
        variants = schema.get("anyOf", [])
        types = {v.get("type") for v in variants}

        assert "string" in types
        assert "null" in types
        assert "array" not in types


class TestDecodeScrubPaths:
    """Validate the parse-and-validate helper used by the push tool wrapper."""

    def test_valid_json_array_returns_list(self) -> None:
        assert decode_json_string_array('["a.ogg", "b.json"]', "scrub_paths") == ["a.ogg", "b.json"]

    def test_empty_array_returns_empty_list(self) -> None:
        # Helper accepts []; the non-empty rule lives in handle_scrub.
        assert decode_json_string_array("[]", "scrub_paths") == []

    def test_invalid_json_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="JSON-encoded array of strings"):
            decode_json_string_array("not json", "scrub_paths")

    def test_non_array_json_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="must encode an array"):
            decode_json_string_array('{"a": 1}', "scrub_paths")

    def test_array_with_non_string_items_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="only strings"):
            decode_json_string_array("[1, 2]", "scrub_paths")


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

    @patch("tachikoma.git.tools.smart_push", new_callable=AsyncMock)
    async def test_push_without_scrub_paths_unchanged(
        self,
        mock_push: AsyncMock,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        mock_push.return_value = PUSH_RESULT["PUSHED"]
        result = await handle_push("workspace", None, workspace, agent_defaults, scrub_paths=None)

        assert "is_error" not in result or result["is_error"] is False
        mock_push.assert_awaited_once()

    @patch("tachikoma.git.tools.handle_scrub", new_callable=AsyncMock)
    async def test_push_with_scrub_paths_delegates_to_handle_scrub(
        self,
        mock_scrub: AsyncMock,
        workspace: Path,
        agent_defaults: AgentDefaults,
    ) -> None:
        mock_scrub.return_value = {"content": [{"type": "text", "text": "ok"}]}
        result = await handle_push(
            "project",
            "my-app",
            workspace,
            agent_defaults,
            scrub_paths=["path/to/file"],
        )

        mock_scrub.assert_awaited_once_with(
            "project",
            "my-app",
            workspace,
            ["path/to/file"],
        )
        assert result == mock_scrub.return_value


def _make_mock_process(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> MagicMock:
    """Create a mock subprocess that awaits communicate()."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    return proc


class TestHandleScrub:
    async def test_scrub_rejected_for_workspace_type(self) -> None:
        result = await handle_scrub("workspace", None, Path("/tmp"), ["file.txt"])

        assert result["is_error"] is True
        assert "only supported for project targets" in result["content"][0]["text"]

    async def test_scrub_rejected_for_empty_list(
        self,
        workspace: Path,
    ) -> None:
        result = await handle_scrub("project", "my-app", workspace, [])

        assert result["is_error"] is True
        assert "non-empty list" in result["content"][0]["text"]

    async def test_scrub_rejected_for_unresolvable_target(
        self,
        workspace: Path,
    ) -> None:
        result = await handle_scrub("project", "nonexistent", workspace, ["file.txt"])

        assert result["is_error"] is True
        assert "could not resolve" in result["content"][0]["text"]

    @patch("tachikoma.git.tools.has_uncommitted_changes", new_callable=AsyncMock)
    async def test_scrub_rejected_for_dirty_working_tree(
        self,
        mock_dirty: AsyncMock,
        workspace: Path,
    ) -> None:
        _register_project(workspace, "my-app")
        mock_dirty.return_value = True

        result = await handle_scrub("project", "my-app", workspace, ["file.txt"])

        assert result["is_error"] is True
        assert "uncommitted changes" in result["content"][0]["text"]

    @patch("tachikoma.git.tools.run_git_capture", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.has_uncommitted_changes", new_callable=AsyncMock)
    async def test_scrub_rejected_for_paths_not_in_history(
        self,
        mock_dirty: AsyncMock,
        mock_capture: AsyncMock,
        workspace: Path,
    ) -> None:
        _register_project(workspace, "my-app")
        mock_dirty.return_value = False
        mock_capture.return_value = (0, "")

        result = await handle_scrub("project", "my-app", workspace, ["nonexistent.txt"])

        assert result["is_error"] is True
        assert "not found in git history" in result["content"][0]["text"]
        assert "nonexistent.txt" in result["content"][0]["text"]

    @patch("tachikoma.git.tools.run_git_capture", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.has_uncommitted_changes", new_callable=AsyncMock)
    async def test_scrub_rejected_for_no_origin_remote(
        self,
        mock_dirty: AsyncMock,
        mock_capture: AsyncMock,
        workspace: Path,
    ) -> None:
        _register_project(workspace, "my-app")
        mock_dirty.return_value = False
        # log finds the path, but remote get-url fails
        mock_capture.side_effect = [
            (0, "abc123"),  # log -1 --all -- path (path exists)
            (128, ""),  # remote get-url origin (no remote)
        ]

        result = await handle_scrub("project", "my-app", workspace, ["path.txt"])

        assert result["is_error"] is True
        assert "no origin remote" in result["content"][0]["text"]

    @patch("tachikoma.git.tools.run_git", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.run_git_capture", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.has_uncommitted_changes", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.asyncio.create_subprocess_exec")
    async def test_scrub_runs_filter_repo_and_force_pushes(
        self,
        mock_exec: AsyncMock,
        mock_dirty: AsyncMock,
        mock_capture: AsyncMock,
        mock_git: AsyncMock,
        workspace: Path,
    ) -> None:
        project = _register_project(workspace, "my-app")

        mock_dirty.return_value = False
        filter_proc = _make_mock_process(returncode=0, stderr=b"")
        mock_exec.return_value = filter_proc

        # run_git_capture:
        # 1. log -1 --all -- path → path exists
        # 2. remote get-url origin → returns URL
        mock_capture.side_effect = [
            (0, "abc123"),
            (0, "git@github.com:user/my-app.git"),
        ]

        result = await handle_scrub("project", "my-app", workspace, ["old/file.ogg"])

        assert "is_error" not in result or result.get("is_error") is False
        assert "scrub" in result["content"][0]["text"]
        assert "old/file.ogg" in result["content"][0]["text"]

        # Verify filter-repo was called with correct args
        filter_call = mock_exec.call_args_list[0]
        assert filter_call[0][0] == "git"
        assert filter_call[0][1] == "filter-repo"
        assert "--invert-paths" in filter_call[0]
        assert "--path" in filter_call[0]
        assert "old/file.ogg" in filter_call[0]
        assert "--force" in filter_call[0]
        assert filter_call[1]["cwd"] == project

        # Verify remote was re-added and force push was called
        mock_git.assert_any_await(
            "remote",
            "add",
            "origin",
            "git@github.com:user/my-app.git",
            cwd=project,
        )
        mock_git.assert_any_await(
            "push",
            "--force",
            "origin",
            "HEAD",
            cwd=project,
        )

    @patch("tachikoma.git.tools.run_git", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.run_git_capture", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.has_uncommitted_changes", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.asyncio.create_subprocess_exec")
    async def test_scrub_multiple_paths_single_invocation(
        self,
        mock_exec: AsyncMock,
        mock_dirty: AsyncMock,
        mock_capture: AsyncMock,
        mock_git: AsyncMock,
        workspace: Path,
    ) -> None:
        _register_project(workspace, "my-app")

        mock_dirty.return_value = False
        filter_proc = _make_mock_process(returncode=0, stderr=b"")
        mock_exec.return_value = filter_proc

        # Both paths exist in history
        mock_capture.side_effect = [
            (0, "abc"),  # log -1 for first path
            (0, "def"),  # log -1 for second path
            (0, "git@github.com:user/repo.git"),  # remote get-url
        ]

        result = await handle_scrub(
            "project",
            "my-app",
            workspace,
            ["audio/one.ogg", "audio/two.ogg"],
        )

        assert "is_error" not in result or result.get("is_error") is False

        # Verify single filter-repo invocation with both --path flags
        filter_call = mock_exec.call_args_list[0]
        filter_args = filter_call[0]
        assert filter_args[0] == "git"
        assert filter_args[1] == "filter-repo"
        path_count = list(filter_args).count("--path")
        assert path_count == 2

    @patch("tachikoma.git.tools.run_git_capture", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.has_uncommitted_changes", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.asyncio.create_subprocess_exec")
    async def test_scrub_filter_repo_failure_returns_error(
        self,
        mock_exec: AsyncMock,
        mock_dirty: AsyncMock,
        mock_capture: AsyncMock,
        workspace: Path,
    ) -> None:
        _register_project(workspace, "my-app")

        mock_dirty.return_value = False
        mock_exec.return_value = _make_mock_process(returncode=1, stderr=b"filter-repo error")

        mock_capture.side_effect = [
            (0, "abc123"),
            (0, "git@github.com:user/repo.git"),
        ]

        result = await handle_scrub("project", "my-app", workspace, ["file.txt"])

        assert result["is_error"] is True
        assert "filter-repo failed" in result["content"][0]["text"]

    @patch("tachikoma.git.tools.run_git", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.run_git_capture", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.has_uncommitted_changes", new_callable=AsyncMock)
    @patch("tachikoma.git.tools.asyncio.create_subprocess_exec")
    async def test_scrub_force_push_failure_returns_error(
        self,
        mock_exec: AsyncMock,
        mock_dirty: AsyncMock,
        mock_capture: AsyncMock,
        mock_git: AsyncMock,
        workspace: Path,
    ) -> None:
        _register_project(workspace, "my-app")

        mock_dirty.return_value = False
        mock_exec.return_value = _make_mock_process(returncode=0, stderr=b"")

        mock_capture.side_effect = [
            (0, "abc123"),
            (0, "git@github.com:user/repo.git"),
        ]

        # run_git succeeds for remote add, raises for push
        mock_git.side_effect = [
            None,
            RuntimeError("git push --force origin HEAD failed: push rejected"),
        ]

        result = await handle_scrub("project", "my-app", workspace, ["file.txt"])

        assert result["is_error"] is True
        assert "force push failed" in result["content"][0]["text"]
        assert "local repo has been rewritten" in result["content"][0]["text"]
        assert mock_git.await_count == 2


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
            "git filter-repo --invert-paths --path foo --force",
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
