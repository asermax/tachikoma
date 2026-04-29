"""Tests for plugin reconciliation.

Covers AC-REC-1 through AC-REC-10, AC-REC-11 through REC-14 (URL flows):
- AC-REC-1: git source materialization + atomic swap
- AC-REC-2: local source materialization + atomic swap
- AC-REC-3: materialization failure leaves prior install untouched
- AC-REC-4: orphan directory removal
- AC-REC-5: stale-fallback retains valid copy
- AC-REC-6: failed when no valid copy exists
- AC-REC-7: one plugin failure does not block another
- AC-REC-9: fresh workspace creates install directory
- AC-REC-10: idempotent re-run
- AC-REC-11-14: URL materialization wired through
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tachikoma.plugins.reconciler import (
    reconcile,
)
from tachikoma.plugins.sources import GitPluginSource, LocalPluginSource, UrlPluginSource


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


class TestReconcileBasic:
    """Core reconciliation behavior."""

    @pytest.mark.asyncio
    async def test_ac_rec9_fresh_workspace_creates_dir(self, tmp_path: Path) -> None:
        """AC-REC-9: .tachikoma/plugins/ is created when it does not exist."""
        workspace = tmp_path / "workspace"
        # workspace dir itself doesn't need to exist; reconcile creates install_dir.
        report = await reconcile(workspace, {})
        assert (workspace / ".tachikoma" / "plugins").is_dir()
        assert report.outcomes == []

    @pytest.mark.asyncio
    async def test_ac_rec4_orphan_removal(self, tmp_path: Path) -> None:
        """AC-REC-4: orphan directories not in config are removed."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins"
        install_dir.mkdir(parents=True)
        (install_dir / "orphan").mkdir()
        (install_dir / "orphan" / "file.txt").write_text("leftover")

        report = await reconcile(workspace, {})
        assert not (install_dir / "orphan").exists()
        assert report.outcomes == []

    @pytest.mark.asyncio
    async def test_ac_rec4_staging_dir_preserved(self, tmp_path: Path) -> None:
        """The .staging directory is not treated as an orphan."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins"
        install_dir.mkdir(parents=True)
        (install_dir / ".staging").mkdir()

        await reconcile(workspace, {})
        assert (install_dir / ".staging").exists()

    @pytest.mark.asyncio
    async def test_ac_rec2_local_source(self, tmp_path: Path) -> None:
        """AC-REC-2: local source is copied and atomic-swapped."""
        workspace = tmp_path / "workspace"
        source_dir = tmp_path / "my-plugin-src"
        source_dir.mkdir()
        _write_native_manifest(source_dir, name="my-plugin")

        plugins = {
            "my-plugin": LocalPluginSource(path=source_dir),
        }

        report = await reconcile(workspace, plugins)

        assert len(report.outcomes) == 1
        assert report.outcomes[0].alias == "my-plugin"
        assert report.outcomes[0].status == "loaded"
        assert report.outcomes[0].diagnostic is None

        install_dir = workspace / ".tachikoma" / "plugins"
        assert (install_dir / "my-plugin" / "tachikoma-plugin.toml").exists()

    @pytest.mark.asyncio
    async def test_ac_rec1_git_source(self, tmp_path: Path) -> None:
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

        with patch("tachikoma.plugins.materializer.run_git", side_effect=fake_run_git):
            report = await reconcile(workspace, plugins)

        assert report.outcomes[0].status == "loaded"
        install_dir = workspace / ".tachikoma" / "plugins"
        assert (install_dir / "code-review" / "tachikoma-plugin.toml").exists()

    @pytest.mark.asyncio
    async def test_ac_rec3_materialize_failure_leaves_prior(self, tmp_path: Path) -> None:
        """AC-REC-3: materialization failure leaves existing install untouched."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="my-plugin")
        (install_dir / "existing.txt").write_text("prior content")

        # Use a missing local path to trigger MaterializeError.
        plugins = {
            "my-plugin": LocalPluginSource(path=workspace / "nonexistent"),
        }

        report = await reconcile(workspace, plugins)

        # Should fall back to stale since existing manifest matches alias.
        assert report.outcomes[0].status == "stale-fallback"
        # Prior content should be untouched.
        assert (install_dir / "existing.txt").read_text() == "prior content"

    @pytest.mark.asyncio
    async def test_ac_rec5_stale_fallback(self, tmp_path: Path) -> None:
        """AC-REC-5: unreachable source + valid existing copy = stale-fallback."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="my-plugin")

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
            report = await reconcile(workspace, plugins)

        outcome = report.outcomes[0]
        assert outcome.status == "stale-fallback"
        assert outcome.diagnostic is not None
        assert (
            "network error" in outcome.diagnostic.lower()
            or "stale" in outcome.diagnostic.lower()
        )

    @pytest.mark.asyncio
    async def test_ac_rec6_failed_no_valid_copy(self, tmp_path: Path) -> None:
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
            report = await reconcile(workspace, plugins)

        outcome = report.outcomes[0]
        assert outcome.status == "failed"
        assert outcome.diagnostic is not None

    @pytest.mark.asyncio
    async def test_ac_rec6_failed_manifest_name_mismatch(self, tmp_path: Path) -> None:
        """AC-REC-6: existing copy with mismatched manifest name = failed."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "my-plugin"
        _write_native_manifest(install_dir, name="different-name")

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
            report = await reconcile(workspace, plugins)

        outcome = report.outcomes[0]
        assert outcome.status == "failed"

    @pytest.mark.asyncio
    async def test_ac_rec7_one_failure_does_not_block_another(self, tmp_path: Path) -> None:
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
            report = await reconcile(workspace, plugins)

        statuses = {o.alias: o.status for o in report.outcomes}
        assert statuses["bad-plugin"] == "failed"
        assert statuses["good-plugin"] == "loaded"

    @pytest.mark.asyncio
    async def test_ac_rec10_idempotent_rerun(self, tmp_path: Path) -> None:
        """AC-REC-10: running reconciliation twice produces equivalent state."""
        workspace = tmp_path / "workspace"
        source_dir = tmp_path / "plugin-src"
        source_dir.mkdir()
        _write_native_manifest(source_dir, name="my-plugin")

        plugins = {
            "my-plugin": LocalPluginSource(path=source_dir),
        }

        report1 = await reconcile(workspace, plugins)
        report2 = await reconcile(workspace, plugins)

        assert report1.outcomes[0].status == "loaded"
        assert report2.outcomes[0].status == "loaded"

        install_dir = workspace / ".tachikoma" / "plugins"
        # Content should be equivalent.
        assert (install_dir / "my-plugin" / "tachikoma-plugin.toml").exists()


class TestReconcileUrlFlows:
    """URL materialization integration with reconciliation (AC-REC-11..14)."""

    @pytest.mark.asyncio
    async def test_ac_rec11_url_source_loaded(self, tmp_path: Path) -> None:
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
            report = await reconcile(workspace, plugins)

        assert report.outcomes[0].status == "loaded"
        install_dir = workspace / ".tachikoma" / "plugins"
        assert (install_dir / "url-plugin" / "tachikoma-plugin.toml").exists()

    @pytest.mark.asyncio
    async def test_ac_rec14_url_failure_stale_fallback(self, tmp_path: Path) -> None:
        """AC-REC-14: URL download failure falls back to existing install."""
        workspace = tmp_path / "workspace"
        install_dir = workspace / ".tachikoma" / "plugins" / "url-plugin"
        _write_native_manifest(install_dir, name="url-plugin")

        plugins = {
            "url-plugin": UrlPluginSource(url="https://example.com/missing.tar.gz"),
        }

        with patch("tachikoma.plugins.materializer.urlopen", side_effect=Exception("404")):
            report = await reconcile(workspace, plugins)

        assert report.outcomes[0].status == "stale-fallback"
