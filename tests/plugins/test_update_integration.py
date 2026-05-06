"""End-to-end integration tests for plugin update mechanism.

Covers 14 scenarios from DLT-055 Batch 5 plan, exercising full lifecycle
flows across reconciliation, daily check, update application, and listing.
Uses real Database, real PluginStateRepository, real PluginManager with real
EventBus. Only external I/O (git subprocess, HTTP downloads) is mocked.

See: docs/delta-specs/DLT-055.md (acceptance criteria)
See: docs/delta-designs/DLT-055.md (scenarios)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bubus import EventBus

from tachikoma.database import Database
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manager import PluginManager
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.materializer import MaterializationResult, MaterializeError
from tachikoma.plugins.reconciler import reconcile
from tachikoma.plugins.sources import GitPluginSource, LocalPluginSource, UrlPluginSource
from tachikoma.plugins.state import PluginState, PluginStateRepository
from tachikoma.plugins.tools import handle_list_plugins
from tachikoma.plugins.updater import (
    UpdateResult,
    run_daily_git_check,
)

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    """Real Database with plugin_state table."""
    database = Database(tmp_path / "tachikoma.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def state_repo(db: Database) -> PluginStateRepository:
    """Real PluginStateRepository backed by temp SQLite."""
    return PluginStateRepository(db.session_factory)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Workspace directory for plugin installs."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _write_manifest(
    plugin_dir: Path,
    *,
    name: str = "test-plugin",
    description: str = "A test plugin",
) -> None:
    """Write a minimal tachikoma-plugin.toml."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "tachikoma-plugin.toml").write_text(
        f'name = "{name}"\ndescription = "{description}"\n'
    )


def _make_settings_manager() -> MagicMock:
    sm = MagicMock()
    sm.settings = MagicMock()
    sm.settings.plugins = {}
    sm.update_plugin_entry = MagicMock()
    sm.remove_plugin_entry = MagicMock()
    return sm


def _make_manager(
    *,
    workspace: Path,
    state_repo: PluginStateRepository,
    loaded: dict[str, LoadedPlugin] | None = None,
    bus: EventBus | None = None,
) -> PluginManager:
    return PluginManager(
        settings_manager=_make_settings_manager(),
        bus=bus or EventBus(),
        workspace_path=workspace,
        loaded=loaded or {},
        state_repo=state_repo,
    )


def _git_plugin(alias: str, *, ref: str = "main", plugin_dir: Path | None = None) -> LoadedPlugin:
    return LoadedPlugin(
        alias=alias,
        source=GitPluginSource(git=f"https://github.com/test/{alias}.git", ref=ref),
        manifest=PluginManifest(
            name=alias,
            version="1.0.0",
            description="Test",
            source_format="tachikoma",
            skill_dirs=[],
        ),
        status="loaded",
        diagnostic=None,
        plugin_dir=plugin_dir or Path(f"/nonexistent/{alias}"),
    )


def _url_plugin(alias: str, *, plugin_dir: Path | None = None) -> LoadedPlugin:
    return LoadedPlugin(
        alias=alias,
        source=UrlPluginSource(url=f"https://example.com/{alias}.tar.gz"),
        manifest=PluginManifest(
            name=alias,
            version="1.0.0",
            description="Test",
            source_format="tachikoma",
            skill_dirs=[],
        ),
        status="loaded",
        diagnostic=None,
        plugin_dir=plugin_dir or Path(f"/nonexistent/{alias}"),
    )


def _local_plugin(alias: str, source_path: Path, *, plugin_dir: Path | None = None) -> LoadedPlugin:
    return LoadedPlugin(
        alias=alias,
        source=LocalPluginSource(path=source_path),
        manifest=PluginManifest(
            name=alias,
            version="1.0.0",
            description="Test",
            source_format="tachikoma",
            skill_dirs=[],
        ),
        status="loaded",
        diagnostic=None,
        plugin_dir=plugin_dir or Path(f"/nonexistent/{alias}"),
    )


def _mock_ls_remote(sha: str) -> MagicMock:
    """Create a mock subprocess returning a git ls-remote line."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(f"{sha}\trefs/heads/main\n".encode(), b""))
    proc.returncode = 0
    return proc


def _mock_ls_remote_fail(code: int = 128, stderr: str = "fatal: network error") -> MagicMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    proc.returncode = code
    return proc


class _FakeResponse:
    """Minimal file-like for urlopen mocking."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        if size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
        return chunk

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _make_tar_gz(name: str, manifest_text: str | None = None) -> bytes:
    """Create a .tar.gz archive containing a plugin manifest."""
    buf = io.BytesIO()
    data = manifest_text or f'name = "{name}"\ndescription = "from archive"\n'
    data_bytes = data.encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name=f"{name}-1.0/tachikoma-plugin.toml")
        info.size = len(data_bytes)
        tf.addfile(info, io.BytesIO(data_bytes))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Scenario 1: git plugin install → daily check detects update → update_plugin
# ---------------------------------------------------------------------------


class TestGitPluginFullLifecycle:
    """Scenario 1: install → daily check detects update → update_plugin applies it.

    See: R1, R4, R8, R11, R12
    """

    async def test_full_update_lifecycle(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        old_sha = "a" * 40
        new_sha = "b" * 40
        alias = "code-review"
        bus = EventBus()

        # --- Reconcile: install git plugin ---
        plugins = {
            alias: GitPluginSource(git=f"https://github.com/test/{alias}.git", ref="main"),
        }

        async def fake_clone(*args, cwd, **kwargs):
            clone_path = Path(args[-1])
            clone_path.parent.mkdir(parents=True, exist_ok=True)
            clone_path.mkdir()
            _write_manifest(clone_path, name=alias)

        async def fake_rev_parse(*args, cwd, **kwargs):
            return 0, old_sha

        with (
            patch("tachikoma.plugins.materializer.run_git", side_effect=fake_clone),
            patch("tachikoma.plugins.materializer.run_git_capture", side_effect=fake_rev_parse),
        ):
            report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        state = await state_repo.get(alias)
        assert state.installed_version == old_sha
        assert state.update_status == "unknown"

        # --- Daily check: detect update ---
        install_dir = workspace / ".tachikoma" / "plugins" / alias
        loaded_plugins = {alias: _git_plugin(alias, plugin_dir=install_dir)}

        with patch(
            "tachikoma.plugins.updater.asyncio.create_subprocess_exec",
            return_value=_mock_ls_remote(new_sha),
        ):
            updates = await run_daily_git_check(loaded_plugins, state_repo)

        assert len(updates) == 1
        assert updates[0].available_version == new_sha
        state = await state_repo.get(alias)
        assert state.update_status == "update-available"

        # --- Update: apply via manager ---
        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded=loaded_plugins,
            bus=bus,
        )
        staging = workspace / ".tachikoma" / "plugins" / ".staging" / f"update-{alias}"

        with (
            patch(
                "tachikoma.plugins.manager.materialize_git",
                new_callable=AsyncMock,
                return_value=MaterializationResult(staging_dir=staging, version=new_sha),
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            mock_parse.return_value = PluginManifest(
                name=alias,
                version="2.0.0",
                description="Updated",
                source_format="tachikoma",
                skill_dirs=[],
            )
            result = await manager.update(alias)

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "updated"
        state = await state_repo.get(alias)
        assert state.installed_version == new_sha
        assert state.update_status == "up-to-date"
        assert state.available_version is None


# ---------------------------------------------------------------------------
# Scenario 2: daily check finds no update
# ---------------------------------------------------------------------------


class TestDailyCheckNoUpdate:
    """Scenario 2: daily check finds no update → status stays up-to-date.

    See: R1
    """

    async def test_same_sha_marks_up_to_date(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        sha = "a" * 40
        alias = "linter"

        await state_repo.upsert(
            PluginState(
                alias=alias,
                installed_version=sha,
                update_status="up-to-date",
                available_version=None,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        loaded_plugins = {alias: _git_plugin(alias)}

        with patch(
            "tachikoma.plugins.updater.asyncio.create_subprocess_exec",
            return_value=_mock_ls_remote(sha),
        ):
            updates = await run_daily_git_check(loaded_plugins, state_repo)

        assert updates == []
        state = await state_repo.get(alias)
        assert state.update_status == "up-to-date"


# ---------------------------------------------------------------------------
# Scenario 3: daily check network error
# ---------------------------------------------------------------------------


class TestDailyCheckNetworkError:
    """Scenario 3: daily check network error → previous status retained.

    See: R1
    """

    async def test_error_retains_previous_status(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        sha = "a" * 40
        alias = "linter"

        await state_repo.upsert(
            PluginState(
                alias=alias,
                installed_version=sha,
                update_status="up-to-date",
                available_version=None,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        loaded_plugins = {alias: _git_plugin(alias)}

        with patch(
            "tachikoma.plugins.updater.asyncio.create_subprocess_exec",
            return_value=_mock_ls_remote_fail(),
        ):
            updates = await run_daily_git_check(loaded_plugins, state_repo)

        assert updates == []
        state = await state_repo.get(alias)
        assert state.update_status == "up-to-date"
        assert state.last_checked_at is not None


# ---------------------------------------------------------------------------
# Scenario 4: pre-existing plugin → reconciliation skips materialization
# ---------------------------------------------------------------------------


class TestPreExistingReconciliation:
    """Scenario 4: pre-existing plugin → reconciliation skips materialization,
    PluginState created with unknown status.

    See: R7, R9
    """

    async def test_skips_materialization_creates_unknown_state(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        alias = "legacy-plugin"
        install_dir = workspace / ".tachikoma" / "plugins" / alias
        _write_manifest(install_dir, name=alias)
        (install_dir / "original.txt").write_text("untouched")

        plugins = {
            alias: GitPluginSource(git="https://github.com/test/legacy.git", ref="v1.0.0"),
        }

        with patch(
            "tachikoma.plugins.materializer.run_git",
            side_effect=RuntimeError("should not be called"),
        ):
            report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        assert (install_dir / "original.txt").read_text() == "untouched"

        state = await state_repo.get(alias)
        assert state is not None
        assert state.update_status == "unknown"
        assert state.installed_version is None


# ---------------------------------------------------------------------------
# Scenario 5: local copy → symlink migration
# ---------------------------------------------------------------------------


class TestLocalSymlinkMigration:
    """Scenario 5: local copy → symlink migration.

    See: R3
    """

    async def test_migrate_copy_to_symlink(
        self, workspace: Path, state_repo: PluginStateRepository, tmp_path: Path
    ) -> None:
        alias = "dev-tools"
        source_dir = tmp_path / "dev-tools-src"
        _write_manifest(source_dir, name=alias)

        install_dir = workspace / ".tachikoma" / "plugins" / alias
        _write_manifest(install_dir, name=alias)
        (install_dir / "old-copy.txt").write_text("from old copy")

        plugins = {alias: LocalPluginSource(path=source_dir)}
        report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        assert install_dir.is_symlink()
        assert os.readlink(str(install_dir)) == str(source_dir)


# ---------------------------------------------------------------------------
# Scenario 6: local copy → source gone → stale-fallback
# ---------------------------------------------------------------------------


class TestLocalSourceGone:
    """Scenario 6: local copy → source gone → stale-fallback.

    See: R3
    """

    async def test_source_gone_retains_copy_and_marks_stale(
        self, workspace: Path, state_repo: PluginStateRepository, tmp_path: Path
    ) -> None:
        alias = "dev-tools"
        source_dir = tmp_path / "nonexistent-src"

        install_dir = workspace / ".tachikoma" / "plugins" / alias
        _write_manifest(install_dir, name=alias)
        (install_dir / "old-copy.txt").write_text("from old copy")

        plugins = {alias: LocalPluginSource(path=source_dir)}
        report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        assert not install_dir.is_symlink()
        assert (install_dir / "old-copy.txt").exists()

        state = await state_repo.get(alias)
        assert state.update_status == "stale-fallback"
        assert state.diagnostic is not None


# ---------------------------------------------------------------------------
# Scenario 7: update_plugin during materialization failure
# ---------------------------------------------------------------------------


class TestUpdateMaterializationFailure:
    """Scenario 7: update_plugin during materialization failure → existing intact.

    See: R8
    """

    async def test_materialize_failure_preserves_existing(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        alias = "code-review"
        old_sha = "a" * 40
        bus = EventBus()

        install_dir = workspace / ".tachikoma" / "plugins" / alias
        _write_manifest(install_dir, name=alias)
        (install_dir / "existing.txt").write_text("original")

        await state_repo.upsert(
            PluginState(
                alias=alias,
                installed_version=old_sha,
                update_status="update-available",
                available_version="b" * 40,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        loaded = {alias: _git_plugin(alias, plugin_dir=install_dir)}
        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded=loaded,
            bus=bus,
        )

        with patch(
            "tachikoma.plugins.manager.materialize_git",
            new_callable=AsyncMock,
            side_effect=MaterializeError(alias, "git:test", RuntimeError("Network timeout")),
        ):
            result = await manager.update(alias)

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "failed"
        assert "Network timeout" in result.error
        assert (install_dir / "existing.txt").read_text() == "original"

        state = await state_repo.get(alias)
        assert state.installed_version == old_sha
        assert state.update_status == "update-available"


# ---------------------------------------------------------------------------
# Scenario 8: update_plugin succeeds but re-registration fails
# ---------------------------------------------------------------------------


class TestUpdateReregistrationFailure:
    """Scenario 8: update_plugin succeeds but re-registration fails → old skills
    active, new version on disk, diagnostic stored.

    See: R12
    """

    async def test_reregister_failure_retains_old(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        alias = "code-review"
        new_sha = "b" * 40
        bus = EventBus()

        install_dir = workspace / ".tachikoma" / "plugins" / alias
        _write_manifest(install_dir, name=alias)

        await state_repo.upsert(
            PluginState(
                alias=alias,
                installed_version="a" * 40,
                update_status="update-available",
                available_version=new_sha,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        old_plugin = _git_plugin(alias, plugin_dir=install_dir)
        mock_skill = MagicMock()
        mock_skill.qualified_name = f"{alias}:my-skill"
        old_plugin.contributed_skills.append(mock_skill)

        loaded = {alias: old_plugin}
        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded=loaded,
            bus=bus,
        )
        staging = workspace / ".tachikoma" / "plugins" / ".staging" / f"update-{alias}"

        with (
            patch(
                "tachikoma.plugins.manager.materialize_git",
                new_callable=AsyncMock,
                return_value=MaterializationResult(staging_dir=staging, version=new_sha),
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                side_effect=RuntimeError("skill name collision"),
            ),
        ):
            mock_parse.return_value = PluginManifest(
                name=alias,
                version="2.0.0",
                description="Updated",
                source_format="tachikoma",
                skill_dirs=[],
            )
            result = await manager.update(alias)

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "failed"
        assert "Re-registration failed" in result.error
        assert manager._loaded[alias] is old_plugin

        state = await state_repo.get(alias)
        assert state.diagnostic is not None
        assert "Re-registration failed" in state.diagnostic


# ---------------------------------------------------------------------------
# Scenario 9: update_all_plugins with mixed results
# ---------------------------------------------------------------------------


class TestUpdateAllMixedResults:
    """Scenario 9: update_all_plugins with mixed results → summary accuracy.

    See: R5
    """

    async def test_mixed_results_summary(
        self, workspace: Path, state_repo: PluginStateRepository, tmp_path: Path
    ) -> None:
        bus = EventBus()

        # code-review: git, update-available
        cr_dir = workspace / ".tachikoma" / "plugins" / "code-review"
        _write_manifest(cr_dir, name="code-review")
        await state_repo.upsert(
            PluginState(
                alias="code-review",
                installed_version="a" * 40,
                update_status="update-available",
                available_version="b" * 40,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )
        cr_plugin = _git_plugin("code-review", plugin_dir=cr_dir)

        # weather: url, up-to-date
        weather_dir = workspace / ".tachikoma" / "plugins" / "weather"
        _write_manifest(weather_dir, name="weather")
        await state_repo.upsert(
            PluginState(
                alias="weather",
                installed_version="hash1",
                update_status="up-to-date",
                available_version=None,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )
        weather_plugin = _url_plugin("weather", plugin_dir=weather_dir)

        # dev-tools: local
        source_dir = tmp_path / "dev-tools-src"
        _write_manifest(source_dir, name="dev-tools")
        dt_dir = workspace / ".tachikoma" / "plugins" / "dev-tools"
        _write_manifest(dt_dir, name="dev-tools")
        dt_plugin = _local_plugin("dev-tools", source_dir, plugin_dir=dt_dir)

        loaded = {
            "code-review": cr_plugin,
            "weather": weather_plugin,
            "dev-tools": dt_plugin,
        }
        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded=loaded,
            bus=bus,
        )

        # Mock update for code-review to succeed
        with patch.object(
            manager,
            "update",
            new_callable=AsyncMock,
            return_value=UpdateResult(alias="code-review", status="updated"),
        ):
            summary = await manager.update_all()

        await bus.wait_until_idle()
        await bus.stop()

        assert summary.total == 3
        assert summary.updated == 1
        assert summary.skipped == 2
        assert summary.failed == 0

        aliases_by_status = {}
        for r in summary.results:
            aliases_by_status.setdefault(r.status, []).append(r.alias)
        assert aliases_by_status["updated"] == ["code-review"]
        assert set(aliases_by_status["skipped"]) == {"weather", "dev-tools"}


# ---------------------------------------------------------------------------
# Scenario 10: list_plugins with various states
# ---------------------------------------------------------------------------


class TestListPluginsUpdateInfo:
    """Scenario 10: list_plugins with various states → update info displayed.

    See: R10
    """

    async def test_list_shows_update_info(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        bus = EventBus()

        # code-review: update-available
        cr_plugin = _git_plugin("code-review")
        await state_repo.upsert(
            PluginState(
                alias="code-review",
                installed_version="abc123",
                update_status="update-available",
                available_version="def456",
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        # Set up linter as up-to-date
        linter_plugin = _git_plugin("linter")
        await state_repo.upsert(
            PluginState(
                alias="linter",
                installed_version="aaa111",
                update_status="up-to-date",
                available_version=None,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        # dev-tools: local (no update info)
        source_path = Path("/tmp/some-local-src")
        dt_plugin = _local_plugin("dev-tools", source_path)

        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded={
                "code-review": cr_plugin,
                "linter": linter_plugin,
                "dev-tools": dt_plugin,
            },
            bus=bus,
        )

        result = await handle_list_plugins(manager)

        entries = json.loads(result["content"][0]["text"])

        by_alias = {e["alias"]: e for e in entries}

        cr = by_alias["code-review"]
        assert cr["update_status"] == "update-available"
        assert cr["installed_version"] == "abc123"
        assert cr["available_version"] == "def456"

        li = by_alias["linter"]
        assert li["update_status"] == "up-to-date"
        assert li["installed_version"] == "aaa111"

        dt = by_alias["dev-tools"]
        assert "update_status" not in dt
        assert "installed_version" not in dt


# ---------------------------------------------------------------------------
# Scenario 11: daily check finds updates → notification dispatched
# ---------------------------------------------------------------------------


class TestDailyCheckNotification:
    """Scenario 11: daily check finds updates → notification info returned.

    The daily check returns a list of PluginUpdateInfo that the caller uses
    to dispatch a notification. We verify the returned data is correct.

    See: R6, R13
    """

    async def test_daily_check_returns_update_info_for_notification(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        old_sha = "a" * 40
        new_sha = "b" * 40

        # Two git plugins, one with update
        for alias, new in [("code-review", new_sha), ("linter", old_sha)]:
            await state_repo.upsert(
                PluginState(
                    alias=alias,
                    installed_version=old_sha,
                    update_status="unknown",
                    available_version=None,
                    last_checked_at=None,
                    diagnostic=None,
                    created_at=datetime.now(UTC),
                )
            )

        loaded_plugins = {
            "code-review": _git_plugin("code-review"),
            "linter": _git_plugin("linter"),
        }

        def fake_ls_remote(*args, **kwargs):
            # args: ("git", "ls-remote", <url>, <ref_spec>)
            all_args = " ".join(str(a) for a in args)
            sha = new_sha if "code-review" in all_args else old_sha
            return _mock_ls_remote(sha)

        with patch(
            "tachikoma.plugins.updater.asyncio.create_subprocess_exec",
            side_effect=fake_ls_remote,
        ):
            updates = await run_daily_git_check(loaded_plugins, state_repo)

        assert len(updates) == 1
        assert updates[0].alias == "code-review"
        assert updates[0].available_version == new_sha

        # linter should be up-to-date
        linter_state = await state_repo.get("linter")
        assert linter_state.update_status == "up-to-date"


# ---------------------------------------------------------------------------
# Scenario 12: update_plugin for URL plugin → content hash comparison
# ---------------------------------------------------------------------------


class TestURLPluginUpdate:
    """Scenario 12: update_plugin for URL plugin → content hash comparison.

    See: R2, R8
    """

    async def test_url_update_same_hash_skipped(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        alias = "weather"
        content_hash = hashlib.sha256(b"old-content").hexdigest()
        bus = EventBus()

        install_dir = workspace / ".tachikoma" / "plugins" / alias
        _write_manifest(install_dir, name=alias)

        await state_repo.upsert(
            PluginState(
                alias=alias,
                installed_version=content_hash,
                update_status="up-to-date",
                available_version=None,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        loaded = {alias: _url_plugin(alias, plugin_dir=install_dir)}
        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded=loaded,
            bus=bus,
        )
        staging = workspace / ".tachikoma" / "plugins" / ".staging" / f"update-{alias}"

        with (
            patch(
                "tachikoma.plugins.manager.materialize_url",
                new_callable=AsyncMock,
                return_value=MaterializationResult(staging_dir=staging, version=content_hash),
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
        ):
            mock_parse.return_value = PluginManifest(
                name=alias,
                version="1.0.0",
                description="Weather",
                source_format="tachikoma",
                skill_dirs=[],
            )
            result = await manager.update(alias)

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "skipped"
        assert "already up-to-date" in (result.message or "")

    async def test_url_update_different_hash_updates(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        alias = "weather"
        old_hash = hashlib.sha256(b"old-content").hexdigest()
        new_hash = hashlib.sha256(b"new-content").hexdigest()
        bus = EventBus()

        install_dir = workspace / ".tachikoma" / "plugins" / alias
        _write_manifest(install_dir, name=alias)

        await state_repo.upsert(
            PluginState(
                alias=alias,
                installed_version=old_hash,
                update_status="update-available",
                available_version=None,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        loaded = {alias: _url_plugin(alias, plugin_dir=install_dir)}
        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded=loaded,
            bus=bus,
        )
        staging = workspace / ".tachikoma" / "plugins" / ".staging" / f"update-{alias}"

        with (
            patch(
                "tachikoma.plugins.manager.materialize_url",
                new_callable=AsyncMock,
                return_value=MaterializationResult(staging_dir=staging, version=new_hash),
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            mock_parse.return_value = PluginManifest(
                name=alias,
                version="2.0.0",
                description="Updated",
                source_format="tachikoma",
                skill_dirs=[],
            )
            result = await manager.update(alias)

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "updated"
        state = await state_repo.get(alias)
        assert state.installed_version == new_hash
        assert state.update_status == "up-to-date"


# ---------------------------------------------------------------------------
# Scenario 13: update_plugin for local plugin → informational message
# ---------------------------------------------------------------------------


class TestLocalPluginUpdate:
    """Scenario 13: update_plugin for local plugin → informational message.

    See: R3, R4
    """

    async def test_local_plugin_returns_informational(
        self, workspace: Path, state_repo: PluginStateRepository, tmp_path: Path
    ) -> None:
        source_dir = tmp_path / "dev-tools-src"
        _write_manifest(source_dir, name="dev-tools")
        install_dir = workspace / ".tachikoma" / "plugins" / "dev-tools"

        loaded = {"dev-tools": _local_plugin("dev-tools", source_dir, plugin_dir=install_dir)}
        bus = EventBus()
        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded=loaded,
            bus=bus,
        )

        result = await manager.update("dev-tools")

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "skipped"
        assert "always current" in (result.message or "")


# ---------------------------------------------------------------------------
# Scenario 14: concurrent update_plugin → already-in-progress error
# ---------------------------------------------------------------------------


class TestConcurrentUpdate:
    """Scenario 14: concurrent update_plugin → already-in-progress error.

    See: R4
    """

    async def test_concurrent_update_returns_error(
        self, workspace: Path, state_repo: PluginStateRepository
    ) -> None:
        alias = "code-review"
        bus = EventBus()

        install_dir = workspace / ".tachikoma" / "plugins" / alias
        _write_manifest(install_dir, name=alias)

        await state_repo.upsert(
            PluginState(
                alias=alias,
                installed_version="a" * 40,
                update_status="update-available",
                available_version="b" * 40,
                last_checked_at=None,
                diagnostic=None,
                created_at=datetime.now(UTC),
            )
        )

        loaded = {alias: _git_plugin(alias, plugin_dir=install_dir)}
        manager = _make_manager(
            workspace=workspace,
            state_repo=state_repo,
            loaded=loaded,
            bus=bus,
        )

        lock = manager._get_update_lock(alias)
        await lock.acquire()

        try:
            result = await manager.update(alias)
            assert result.status == "failed"
            assert "already in progress" in result.error
        finally:
            lock.release()

        await bus.wait_until_idle()
        await bus.stop()
