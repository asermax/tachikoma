"""Tests for plugin reconciliation.

Covers AC-REC-1 through AC-REC-10, AC-REC-11 through REC-14 (URL flows):
- AC-REC-1: git source materialization + atomic swap
- AC-REC-2: local source materialization + atomic swap (symlink)
- AC-REC-3: materialization failure leaves prior install untouched
- AC-REC-4: orphan directory removal
- AC-REC-5: stale-fallback retains valid copy
- AC-REC-6: failed when no valid copy exists
- AC-REC-7: one plugin failure does not block another
- AC-REC-9: fresh workspace creates install directory
- AC-REC-10: idempotent re-run
- AC-REC-11-14: URL materialization wired through
- R7: first-time-only install (skip re-materialization)
- R3: local symlink creation and migration
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tachikoma.database import Database
from tachikoma.plugins.reconciler import (
    reconcile,
)
from tachikoma.plugins.sources import GitPluginSource, LocalPluginSource, UrlPluginSource
from tachikoma.plugins.state import PluginStateRepository


class _FakeResponse:
    """Minimal file-like object that mimics urlopen response for tests."""

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


def _write_native_manifest(
    plugin_dir: Path,
    *,
    name: str = "test-plugin",
    description: str = "A test",
) -> None:
    """Write a minimal tachikoma-plugin.toml into *plugin_dir*."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "tachikoma-plugin.toml").write_text(
        f'name = "{name}"\ndescription = "{description}"\n'
    )


@pytest.fixture
async def state_repo(tmp_path: Path) -> PluginStateRepository:
    """Initialized PluginStateRepository backed by a temp SQLite file."""
    database = Database(tmp_path / "tachikoma.db")
    await database.initialize()
    yield PluginStateRepository(database.session_factory)
    await database.close()


class TestReconcileBasic:
    """Core reconciliation behavior."""

    @pytest.mark.asyncio
    async def test_ac_rec9_fresh_workspace_creates_dir(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """AC-REC-9: .tachikoma/plugins/ is created when it does not exist."""
        workspace = tmp_path / "workspace"
        report = await reconcile(workspace, {}, state_repo)
        assert (workspace / ".tachikoma" / "plugins").is_dir()
        assert report.outcomes == []

    @pytest.mark.asyncio
    async def test_ac_rec4_orphan_removal(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """AC-REC-4: orphan directories not in config are removed."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins"
        install_dir.mkdir(parents=True)
        (install_dir / "orphan").mkdir()
        (install_dir / "orphan" / "file.txt").write_text("leftover")

        report = await reconcile(workspace, {}, state_repo)
        assert not (install_dir / "orphan").exists()
        assert report.outcomes == []

    @pytest.mark.asyncio
    async def test_ac_rec4_staging_dir_preserved(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """The .staging directory is not treated as an orphan."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins"
        install_dir.mkdir(parents=True)
        (install_dir / ".staging").mkdir()

        await reconcile(workspace, {}, state_repo)
        assert (install_dir / ".staging").exists()

    @pytest.mark.asyncio
    async def test_ac_rec2_local_source_symlink(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """AC-REC-2/R3: local source creates symlink instead of copy."""
        workspace = tmp_path / "workspace"
        source_dir = tmp_path / "my-plugin-src"
        source_dir.mkdir()
        _write_native_manifest(source_dir, name="my-plugin")

        plugins = {
            "my-plugin": LocalPluginSource(path=source_dir),
        }

        report = await reconcile(workspace, plugins, state_repo)

        assert len(report.outcomes) == 1
        assert report.outcomes[0].alias == "my-plugin"
        assert report.outcomes[0].status == "loaded"
        assert report.outcomes[0].diagnostic is None

        install_dir = workspace / ".tachikoma" / "plugins"
        target = install_dir / "my-plugin"
        assert target.is_symlink()
        assert os.readlink(str(target)) == str(source_dir)

    @pytest.mark.asyncio
    async def test_ac_rec1_git_source(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """AC-REC-1: git source is cloned and atomic-swapped."""
        workspace = tmp_path / "workspace"
        plugins = {
            "code-review": GitPluginSource(
                git="https://github.com/example/code-review.git",
                ref="v1.0.0",
            ),
        }

        async def fake_run_git(*args: str, cwd: Path) -> None:
            clone_path = Path(args[-1])
            clone_path.parent.mkdir(parents=True, exist_ok=True)
            clone_path.mkdir()
            _write_native_manifest(clone_path, name="code-review")

        async def fake_run_git_capture(*args: str, cwd: Path) -> tuple[int, str]:
            return 0, "a" * 40

        with (
            patch("tachikoma.plugins.materializer.run_git", side_effect=fake_run_git),
            patch(
                "tachikoma.plugins.materializer.run_git_capture",
                side_effect=fake_run_git_capture,
            ),
        ):
            report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        install_dir = workspace / ".tachikoma" / "plugins"
        assert (install_dir / "code-review" / "tachikoma-plugin.toml").exists()

    @pytest.mark.asyncio
    async def test_ac_rec3_existing_install_skipped_on_failure(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R7: existing install is left untouched even when source is missing."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="my-plugin")
        (install_dir / "existing.txt").write_text("prior content")

        # Source is missing but reconciler skips since install already exists.
        plugins = {
            "my-plugin": LocalPluginSource(path=workspace / "nonexistent"),
        }

        report = await reconcile(workspace, plugins, state_repo)

        # First-time-only: existing valid install is left untouched.
        assert report.outcomes[0].status == "loaded"
        assert (install_dir / "existing.txt").read_text() == "prior content"

    @pytest.mark.asyncio
    async def test_ac_rec5_existing_install_skipped_when_unreachable(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R7: existing install is skipped when git source is unreachable."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="my-plugin")

        plugins = {
            "my-plugin": GitPluginSource(
                git="https://github.com/example/unreachable.git",
                ref="v1.0.0",
            ),
        }

        # git clone would fail, but reconciler never calls it (first-time-only).
        with patch(
            "tachikoma.plugins.materializer.run_git",
            side_effect=RuntimeError("git clone failed: network error"),
        ):
            report = await reconcile(workspace, plugins, state_repo)

        # Existing valid install is skipped.
        assert report.outcomes[0].status == "loaded"

    @pytest.mark.asyncio
    async def test_ac_rec6_failed_no_valid_copy(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """AC-REC-6: unreachable source + no valid copy = failed."""
        workspace = tmp_path / "workspace"

        plugins = {
            "my-plugin": GitPluginSource(
                git="https://github.com/example/unreachable.git",
                ref="v1.0.0",
            ),
        }

        with patch(
            "tachikoma.plugins.materializer.run_git",
            side_effect=RuntimeError("git clone failed: network error"),
        ):
            report = await reconcile(workspace, plugins, state_repo)

        outcome = report.outcomes[0]
        assert outcome.status == "failed"
        assert outcome.diagnostic is not None

    @pytest.mark.asyncio
    async def test_ac_rec6_existing_install_with_manifest_is_loaded(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R7: existing install with any valid manifest is loaded (first-time-only)."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="different-name")

        plugins = {
            "my-plugin": GitPluginSource(
                git="https://github.com/example/unreachable.git",
                ref="v1.0.0",
            ),
        }

        # git would fail, but reconciler skips since manifest exists.
        with patch(
            "tachikoma.plugins.materializer.run_git",
            side_effect=RuntimeError("git clone failed: network error"),
        ):
            report = await reconcile(workspace, plugins, state_repo)

        outcome = report.outcomes[0]
        # First-time-only: any existing valid install is loaded.
        assert outcome.status == "loaded"

    @pytest.mark.asyncio
    async def test_ac_rec7_one_failure_does_not_block_another(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """AC-REC-7: one plugin failure does not block another from loading."""
        workspace = tmp_path / "workspace"

        source_dir = tmp_path / "good-src"
        source_dir.mkdir()
        _write_native_manifest(source_dir, name="good-plugin")

        plugins = {
            "bad-plugin": GitPluginSource(
                git="https://github.com/example/bad.git",
                ref="v1.0.0",
            ),
            "good-plugin": LocalPluginSource(path=source_dir),
        }

        with patch(
            "tachikoma.plugins.materializer.run_git",
            side_effect=RuntimeError("git clone failed"),
        ):
            report = await reconcile(workspace, plugins, state_repo)

        statuses = {o.alias: o.status for o in report.outcomes}
        assert statuses["bad-plugin"] == "failed"
        assert statuses["good-plugin"] == "loaded"

    @pytest.mark.asyncio
    async def test_ac_rec10_idempotent_rerun(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """AC-REC-10: running reconciliation twice produces equivalent state."""
        workspace = tmp_path / "workspace"
        source_dir = tmp_path / "plugin-src"
        source_dir.mkdir()
        _write_native_manifest(source_dir, name="my-plugin")

        plugins = {
            "my-plugin": LocalPluginSource(path=source_dir),
        }

        report1 = await reconcile(workspace, plugins, state_repo)
        report2 = await reconcile(workspace, plugins, state_repo)

        assert report1.outcomes[0].status == "loaded"
        assert report2.outcomes[0].status == "loaded"

        install_dir = workspace / ".tachikoma" / "plugins"
        assert (install_dir / "my-plugin" / "tachikoma-plugin.toml").exists()


class TestReconcileUrlFlows:
    """URL materialization integration with reconciliation (AC-REC-11..14)."""

    @pytest.mark.asyncio
    async def test_ac_rec11_url_source_loaded(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """AC-REC-11: URL source is downloaded, extracted, and installed."""
        workspace = tmp_path / "workspace"

        # Build a small tar.gz archive.
        archive_path = tmp_path / "plugin.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            data = b'name = "url-plugin"\ndescription = "from url"\n'
            info = tarfile.TarInfo(name="url-plugin-1.0/tachikoma-plugin.toml")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        plugins = {
            "url-plugin": UrlPluginSource(url="https://example.com/plugin.tar.gz"),
        }

        def fake_urlopen(url: str):
            return _FakeResponse(archive_path.read_bytes())

        with patch("tachikoma.plugins.materializer.urlopen", side_effect=fake_urlopen):
            report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        install_dir = workspace / ".tachikoma" / "plugins"
        assert (install_dir / "url-plugin" / "tachikoma-plugin.toml").exists()

    @pytest.mark.asyncio
    async def test_ac_rec14_existing_url_install_skipped(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R7: existing URL plugin install is skipped when URL is unreachable."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "url-plugin"
        _write_native_manifest(install_dir, name="url-plugin")

        plugins = {
            "url-plugin": UrlPluginSource(url="https://example.com/missing.tar.gz"),
        }

        # URL would fail, but reconciler skips since install already exists.
        with patch("tachikoma.plugins.materializer.urlopen", side_effect=Exception("404")):
            report = await reconcile(workspace, plugins, state_repo)

        # First-time-only: existing valid install is loaded.
        assert report.outcomes[0].status == "loaded"


class TestFirstTimeOnlyInstall:
    """R7: Startup no longer re-materializes already-installed plugins."""

    @pytest.mark.asyncio
    async def test_existing_plugin_not_rematerialized(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R7: existing plugin directory is left untouched during reconciliation."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="my-plugin")
        (install_dir / "marker.txt").write_text("original")

        plugins = {
            "my-plugin": GitPluginSource(
                git="https://github.com/example/plugin.git",
                ref="v1.0.0",
            ),
        }

        # Even though run_git would fail, reconciliation should skip materialization.
        with patch(
            "tachikoma.plugins.materializer.run_git",
            side_effect=RuntimeError("should not be called"),
        ):
            report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        # Marker file should still be there — not re-materialized.
        assert (install_dir / "marker.txt").read_text() == "original"

    @pytest.mark.asyncio
    async def test_existing_plugin_creates_state_row(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """Pre-existing installations get a PluginState row created."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="my-plugin")

        plugins = {
            "my-plugin": GitPluginSource(
                git="https://github.com/example/plugin.git",
                ref="v1.0.0",
            ),
        }

        with patch(
            "tachikoma.plugins.materializer.run_git",
            side_effect=RuntimeError("should not be called"),
        ):
            await reconcile(workspace, plugins, state_repo)

        state = await state_repo.get("my-plugin")
        assert state is not None
        assert state.update_status == "unknown"


class TestLocalSymlinkMigration:
    """R3: Local-source plugins migrate from copies to symlinks."""

    @pytest.mark.asyncio
    async def test_migrate_copy_to_symlink(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R3: existing local copy is replaced with symlink when source exists."""
        workspace = tmp_path / "workspace"
        source_dir = tmp_path / "my-plugin-src"
        source_dir.mkdir()
        _write_native_manifest(source_dir, name="my-plugin")

        # Simulate a pre-existing copy (not a symlink).
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="my-plugin")
        (install_dir / "old-copy-marker.txt").write_text("from old copy")

        plugins = {
            "my-plugin": LocalPluginSource(path=source_dir),
        }

        report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        # Should now be a symlink pointing to source.
        target = install_dir
        assert target.is_symlink()
        assert os.readlink(str(target)) == str(source_dir)

    @pytest.mark.asyncio
    async def test_migrate_source_gone_retains_copy(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R3: when source path is gone, copy is retained and marked stale-fallback."""
        workspace = tmp_path / "workspace"
        # Source path does not exist.
        source_dir = tmp_path / "nonexistent-src"

        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="my-plugin")
        (install_dir / "old-copy-marker.txt").write_text("from old copy")

        plugins = {
            "my-plugin": LocalPluginSource(path=source_dir),
        }

        report = await reconcile(workspace, plugins, state_repo)

        assert report.outcomes[0].status == "loaded"
        # Copy should be retained (not a symlink).
        target = install_dir
        assert not target.is_symlink()
        assert (target / "old-copy-marker.txt").exists()
        # PluginState should be stale-fallback.
        state = await state_repo.get("my-plugin")
        assert state is not None
        assert state.update_status == "stale-fallback"
        assert state.diagnostic is not None
        assert "nonexistent-src" in state.diagnostic or "no longer exists" in state.diagnostic


class TestFirstInstallPersistsState:
    """First-time install persists initial PluginState with version hash."""

    @pytest.mark.asyncio
    async def test_git_install_persists_version(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R11: git install captures commit SHA as installed_version."""
        workspace = tmp_path / "workspace"
        sha = "a" * 40

        plugins = {
            "code-review": GitPluginSource(
                git="https://github.com/example/plugin.git",
                ref="v1.0.0",
            ),
        }

        async def fake_run_git(*args: str, cwd: Path) -> None:
            clone_path = Path(args[-1])
            clone_path.parent.mkdir(parents=True, exist_ok=True)
            clone_path.mkdir()
            _write_native_manifest(clone_path, name="code-review")

        async def fake_run_git_capture(*args: str, cwd: Path) -> tuple[int, str]:
            return 0, sha

        with (
            patch("tachikoma.plugins.materializer.run_git", side_effect=fake_run_git),
            patch(
                "tachikoma.plugins.materializer.run_git_capture",
                side_effect=fake_run_git_capture,
            ),
        ):
            await reconcile(workspace, plugins, state_repo)

        state = await state_repo.get("code-review")
        assert state is not None
        assert state.installed_version == sha
        assert state.update_status == "unknown"

    @pytest.mark.asyncio
    async def test_url_install_persists_hash(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R11: URL install captures SHA-256 as installed_version."""
        workspace = tmp_path / "workspace"

        archive_path = tmp_path / "plugin.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            data = b'name = "url-plugin"\ndescription = "test"\n'
            info = tarfile.TarInfo(name="url-plugin-1.0/tachikoma-plugin.toml")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        plugins = {
            "url-plugin": UrlPluginSource(url="https://example.com/plugin.tar.gz"),
        }

        def fake_urlopen(url: str):
            return _FakeResponse(archive_path.read_bytes())

        with patch("tachikoma.plugins.materializer.urlopen", side_effect=fake_urlopen):
            await reconcile(workspace, plugins, state_repo)

        state = await state_repo.get("url-plugin")
        assert state is not None
        assert state.installed_version is not None
        assert len(state.installed_version) == 64  # SHA-256 hex digest
        assert state.update_status == "unknown"

    @pytest.mark.asyncio
    async def test_local_install_version_is_none(
        self, tmp_path: Path, state_repo: PluginStateRepository
    ) -> None:
        """R11: local install has null version (symlinks have no version state)."""
        workspace = tmp_path / "workspace"
        source_dir = tmp_path / "my-plugin-src"
        source_dir.mkdir()
        _write_native_manifest(source_dir, name="my-plugin")

        plugins = {
            "my-plugin": LocalPluginSource(path=source_dir),
        }

        await reconcile(workspace, plugins, state_repo)

        state = await state_repo.get("my-plugin")
        assert state is not None
        assert state.installed_version is None
        assert state.update_status == "unknown"
