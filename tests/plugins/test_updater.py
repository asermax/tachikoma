"""Tests for plugin update detection (updater module)."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.sources import GitPluginSource, UrlPluginSource
from tachikoma.plugins.state import PluginState, PluginStateRepository
from tachikoma.plugins.updater import (
    GitCheckError,
    _resolve_ref_spec,
    check_git_update,
    compute_url_hash,
    run_daily_git_check,
)

from .conftest import make_plugin

# ---------------------------------------------------------------------------
# _resolve_ref_spec
# ---------------------------------------------------------------------------


class TestResolveRefSpec:
    def test_main_branch(self) -> None:
        assert _resolve_ref_spec("main") == "refs/heads/main"

    def test_master_branch(self) -> None:
        assert _resolve_ref_spec("master") == "refs/heads/master"

    def test_feature_branch_with_slash(self) -> None:
        assert _resolve_ref_spec("feature/foo") == "refs/heads/feature/foo"

    def test_version_tag(self) -> None:
        # Tags without slashes, not a well-known branch -> refs/tags with ^{}
        assert _resolve_ref_spec("v1.0.0") == "refs/tags/v1.0.0^{}"

    def test_numeric_tag(self) -> None:
        assert _resolve_ref_spec("2.0") == "refs/tags/2.0^{}"

    def test_develop_branch(self) -> None:
        # "develop" is not in _BRANCH_REFS, so treated as tag
        assert _resolve_ref_spec("develop") == "refs/tags/develop^{}"


# ---------------------------------------------------------------------------
# check_git_update
# ---------------------------------------------------------------------------


def _make_git_source(**overrides) -> GitPluginSource:
    defaults = {"git": "https://github.com/example/plugin.git", "ref": "main"}
    defaults.update(overrides)
    return GitPluginSource.model_validate(defaults)


def _mock_proc(returncode: int, stdout: str, stderr: str = "") -> MagicMock:
    """Create a mock subprocess with the given return code and output."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    return proc


class TestCheckGitUpdate:
    @patch("tachikoma.plugins.updater.asyncio.create_subprocess_exec")
    async def test_returns_none_when_same_version(self, mock_exec) -> None:
        sha = "a" * 40
        mock_exec.return_value = _mock_proc(0, f"{sha}\trefs/heads/main\n")
        source = _make_git_source()

        result = await check_git_update(source, sha, alias="test-plugin")
        assert result is None

    @patch("tachikoma.plugins.updater.asyncio.create_subprocess_exec")
    async def test_returns_remote_sha_when_different(self, mock_exec) -> None:
        old_sha = "a" * 40
        new_sha = "b" * 40
        mock_exec.return_value = _mock_proc(0, f"{new_sha}\trefs/heads/main\n")
        source = _make_git_source()

        result = await check_git_update(source, old_sha, alias="test-plugin")
        assert result == new_sha

    @patch("tachikoma.plugins.updater.asyncio.create_subprocess_exec")
    async def test_raises_on_nonzero_exit(self, mock_exec) -> None:
        mock_exec.return_value = _mock_proc(128, "", "fatal: repository not found")
        source = _make_git_source()

        with pytest.raises(GitCheckError) as exc_info:
            await check_git_update(source, "a" * 40, alias="test-plugin")

        assert exc_info.value.alias == "test-plugin"
        assert "fatal: repository not found" in str(exc_info.value)

    @patch("tachikoma.plugins.updater.asyncio.create_subprocess_exec")
    async def test_raises_on_empty_output(self, mock_exec) -> None:
        mock_exec.return_value = _mock_proc(0, "")
        source = _make_git_source()

        with pytest.raises(GitCheckError, match="no output"):
            await check_git_update(source, "a" * 40, alias="test-plugin")

    @patch("tachikoma.plugins.updater.asyncio.create_subprocess_exec")
    async def test_uses_branch_ref_for_main(self, mock_exec) -> None:
        mock_exec.return_value = _mock_proc(0, f"{'a' * 40}\trefs/heads/main\n")
        source = _make_git_source(ref="main")
        await check_git_update(source, "b" * 40, alias="test")

        args = mock_exec.call_args[0]
        assert "refs/heads/main" in args

    @patch("tachikoma.plugins.updater.asyncio.create_subprocess_exec")
    async def test_uses_tag_ref_for_version_tag(self, mock_exec) -> None:
        mock_exec.return_value = _mock_proc(0, f"{'a' * 40}\trefs/tags/v1.0.0^{{}}\n")
        source = _make_git_source(ref="v1.0.0")
        await check_git_update(source, "b" * 40, alias="test")

        args = mock_exec.call_args[0]
        assert "refs/tags/v1.0.0^{}" in args


# ---------------------------------------------------------------------------
# compute_url_hash
# ---------------------------------------------------------------------------


class TestComputeUrlHash:
    async def test_returns_sha256_hex(self, tmp_path) -> None:
        content = b"hello world"
        source = UrlPluginSource.model_validate({"url": "https://example.com/test.tar.gz"})

        with patch("tachikoma.plugins.updater._download_to") as mock_dl:
            mock_dl.side_effect = lambda url, dest: dest.write_bytes(content)
            result = await compute_url_hash(source)

        expected = hashlib.sha256(content).hexdigest()
        assert result == expected


# ---------------------------------------------------------------------------
# run_daily_git_check
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> PluginState:
    defaults = {
        "alias": "test-plugin",
        "installed_version": "a" * 40,
        "update_status": "unknown",
        "available_version": None,
        "last_checked_at": None,
        "diagnostic": None,
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return PluginState(**defaults)


def _make_git_plugin(alias: str, **source_overrides) -> LoadedPlugin:
    return LoadedPlugin(
        alias=alias,
        source=_make_git_source(**source_overrides),
        manifest=None,
        status="loaded",
        diagnostic=None,
        plugin_dir=Path(f"/tmp/plugins/{alias}"),
    )


class TestRunDailyGitCheck:
    async def test_skips_non_git_plugins(self) -> None:
        state_repo = AsyncMock(spec=PluginStateRepository)
        local_plugin = make_plugin("local-one")
        plugins = {"local-one": local_plugin}

        result = await run_daily_git_check(plugins, state_repo)
        assert result == []
        state_repo.get.assert_not_called()

    async def test_skips_non_loaded_plugins(self) -> None:
        state_repo = AsyncMock(spec=PluginStateRepository)
        git_plugin = LoadedPlugin(
            alias="broken",
            source=_make_git_source(),
            manifest=None,
            status="failed",
            diagnostic="broken",
            plugin_dir=Path("/tmp/plugins/broken"),
        )
        plugins = {"broken": git_plugin}

        result = await run_daily_git_check(plugins, state_repo)
        assert result == []
        state_repo.get.assert_not_called()

    @patch("tachikoma.plugins.updater.check_git_update")
    async def test_detects_available_update(self, mock_check) -> None:
        new_sha = "b" * 40
        mock_check.return_value = new_sha

        state_repo = AsyncMock(spec=PluginStateRepository)
        state_repo.get.return_value = _make_state()
        state_repo.upsert.return_value = _make_state(
            update_status="update-available",
            available_version=new_sha,
        )

        plugins = {"test-plugin": _make_git_plugin("test-plugin")}

        result = await run_daily_git_check(plugins, state_repo)

        assert len(result) == 1
        assert result[0].alias == "test-plugin"
        assert result[0].available_version == new_sha

        upsert_call = state_repo.upsert.call_args[0][0]
        assert upsert_call.update_status == "update-available"
        assert upsert_call.available_version == new_sha

    @patch("tachikoma.plugins.updater.check_git_update")
    async def test_marks_up_to_date_when_same(self, mock_check) -> None:
        mock_check.return_value = None

        state_repo = AsyncMock(spec=PluginStateRepository)
        state_repo.get.return_value = _make_state()

        plugins = {"test-plugin": _make_git_plugin("test-plugin")}

        result = await run_daily_git_check(plugins, state_repo)

        assert result == []

        upsert_call = state_repo.upsert.call_args[0][0]
        assert upsert_call.update_status == "up-to-date"
        assert upsert_call.available_version is None

    @patch("tachikoma.plugins.updater.check_git_update")
    async def test_retains_status_on_error(self, mock_check) -> None:
        mock_check.side_effect = GitCheckError(
            "test-plugin", "https://example.com/repo.git", "network error"
        )

        state_repo = AsyncMock(spec=PluginStateRepository)
        state_repo.get.return_value = _make_state(update_status="up-to-date")

        plugins = {"test-plugin": _make_git_plugin("test-plugin")}

        result = await run_daily_git_check(plugins, state_repo)

        assert result == []

        upsert_call = state_repo.upsert.call_args[0][0]
        assert upsert_call.update_status == "up-to-date"
        assert upsert_call.last_checked_at is not None

    async def test_skips_plugin_with_no_state(self) -> None:
        state_repo = AsyncMock(spec=PluginStateRepository)
        state_repo.get.return_value = None

        plugins = {"test-plugin": _make_git_plugin("test-plugin")}

        result = await run_daily_git_check(plugins, state_repo)
        assert result == []

    async def test_skips_plugin_with_no_installed_version(self) -> None:
        state_repo = AsyncMock(spec=PluginStateRepository)
        state_repo.get.return_value = _make_state(installed_version=None)

        plugins = {"test-plugin": _make_git_plugin("test-plugin")}

        result = await run_daily_git_check(plugins, state_repo)
        assert result == []

    @patch("tachikoma.plugins.updater.check_git_update")
    async def test_checks_multiple_git_plugins(self, mock_check) -> None:
        sha_a = "a" * 40
        sha_b_new = "c" * 40
        mock_check.side_effect = [None, sha_b_new]

        state_repo = AsyncMock(spec=PluginStateRepository)
        state_repo.get.side_effect = [
            _make_state(alias="plugin-a", installed_version=sha_a),
            _make_state(alias="plugin-b", installed_version="b" * 40),
        ]

        plugins = {
            "plugin-a": _make_git_plugin("plugin-a"),
            "plugin-b": _make_git_plugin(
                "plugin-b", git="https://github.com/example/other.git"
            ),
            "local-c": make_plugin("local-c"),
        }

        result = await run_daily_git_check(plugins, state_repo)

        assert len(result) == 1
        assert result[0].alias == "plugin-b"
        assert result[0].available_version == sha_b_new
