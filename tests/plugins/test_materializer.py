"""Tests for plugin source materialization and atomic directory swap.

Covers:
- _atomic_replace_dir happy path and rollback
- materialize_local happy and failure paths (now symlink-based)
- materialize_git with mocked run_git (captures commit SHA)
- materialize_url with fixture-built archives, auto-strip, subdir (captures content hash)
- MaterializationResult return type
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tachikoma.plugins.materializer import (
    MaterializationResult,
    MaterializeError,
    _atomic_replace_dir,
    materialize_git,
    materialize_local,
    materialize_url,
)
from tachikoma.plugins.sources import (
    GitPluginSource,
    LocalPluginSource,
    UrlPluginSource,
)


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


def _fake_url_open(url: str, archive_path: Path) -> _FakeResponse:
    """Return a fake HTTP response serving *archive_path* content."""
    return _FakeResponse(archive_path.read_bytes())


# ===========================================================================
# _atomic_replace_dir
# ===========================================================================


class TestAtomicReplaceDir:
    """Tests for the pip-style triple-step atomic directory replacement."""

    def test_happy_path_replaces_existing(self, tmp_path: Path) -> None:
        """When dst exists, it is replaced by new."""
        dst = tmp_path / "target"
        dst.mkdir()
        (dst / "old.txt").write_text("old content")

        new = tmp_path / "target.new"
        new.mkdir()
        (new / "new.txt").write_text("new content")

        _atomic_replace_dir(new, dst)

        assert dst.exists()
        assert (dst / "new.txt").read_text() == "new content"
        assert not (dst / "old.txt").exists()
        # Backup should be cleaned up.
        assert not (tmp_path / "target.old").exists()

    def test_happy_path_creates_new(self, tmp_path: Path) -> None:
        """When dst does not exist, new is renamed to dst."""
        new = tmp_path / "target.new"
        new.mkdir()
        (new / "file.txt").write_text("hello")

        dst = tmp_path / "target"
        assert not dst.exists()

        _atomic_replace_dir(new, dst)

        assert dst.exists()
        assert (dst / "file.txt").read_text() == "hello"

    def test_rollback_on_step2_failure(self, tmp_path: Path) -> None:
        """When step 2 (rename new -> dst) fails, dst is restored from backup."""
        dst = tmp_path / "target"
        dst.mkdir()
        (dst / "original.txt").write_text("original")

        new = tmp_path / "target.new"
        # Do NOT create new -- rename will fail.

        with pytest.raises(OSError):
            _atomic_replace_dir(new, dst)

        # dst should be restored from .old backup.
        assert dst.exists()
        assert (dst / "original.txt").read_text() == "original"


# ===========================================================================
# MaterializationResult
# ===========================================================================


class TestMaterializationResult:
    """Tests for the MaterializationResult dataclass."""

    def test_frozen(self) -> None:
        result = MaterializationResult(staging_dir=Path("/tmp/staging"), version="abc")
        with pytest.raises(AttributeError):
            result.version = "def"  # type: ignore[misc]

    def test_version_can_be_none(self) -> None:
        result = MaterializationResult(staging_dir=Path("/tmp/staging"), version=None)
        assert result.version is None


# ===========================================================================
# materialize_local
# ===========================================================================


class TestMaterializeLocal:
    """Tests for local-path materialization (symlink-based)."""

    @pytest.mark.asyncio
    async def test_happy_path_creates_symlink(self, tmp_path: Path) -> None:
        """Local source creates a symlink pointing to source directory."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("hello")

        staging = tmp_path / "staging"
        source = LocalPluginSource(path=source_dir)
        result = await materialize_local(source, staging)

        assert isinstance(result, MaterializationResult)
        assert result.version is None
        assert staging.is_symlink()
        assert os.readlink(str(staging)) == str(source_dir)
        # Verify symlink resolves to source content.
        assert (staging / "file.txt").read_text() == "hello"

    @pytest.mark.asyncio
    async def test_missing_path_raises(self, tmp_path: Path) -> None:
        """MaterializeError when the source path does not exist."""
        source = LocalPluginSource(path=tmp_path / "nonexistent")
        staging = tmp_path / "staging"

        with pytest.raises(MaterializeError) as exc_info:
            await materialize_local(source, staging)

        assert "nonexistent" in str(exc_info.value.cause)

    @pytest.mark.asyncio
    async def test_permission_error(self, tmp_path: Path) -> None:
        """MaterializeError when os.symlink raises PermissionError."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text("hello")

        staging = tmp_path / "staging"
        source = LocalPluginSource(path=source_dir)

        with (
            patch(
                "tachikoma.plugins.materializer.os.symlink",
                side_effect=PermissionError("denied"),
            ),
            pytest.raises(MaterializeError) as exc_info,
        ):
            await materialize_local(source, staging, alias="test-plugin")

        assert exc_info.value.alias == "test-plugin"


# ===========================================================================
# materialize_git
# ===========================================================================


class TestMaterializeGit:
    """Tests for git-source materialization."""

    @pytest.mark.asyncio
    async def test_happy_path_no_subdir(self, tmp_path: Path) -> None:
        """Shallow clone is copied into staging with no subdir."""
        source = GitPluginSource(git="https://github.com/example/plugin.git", ref="v1.0.0")
        staging = tmp_path / "staging"
        expected_sha = "a" * 40

        async def fake_run_git(*args: str, cwd: Path) -> None:
            clone_path = Path(args[-1])
            clone_path.parent.mkdir(parents=True, exist_ok=True)
            clone_path.mkdir()
            (clone_path / "plugin.txt").write_text("from git")

        async def fake_run_git_capture(*args: str, cwd: Path) -> tuple[int, str]:
            return 0, expected_sha

        with (
            patch("tachikoma.plugins.materializer.run_git", side_effect=fake_run_git),
            patch(
                "tachikoma.plugins.materializer.run_git_capture",
                side_effect=fake_run_git_capture,
            ),
        ):
            result = await materialize_git(source, staging, alias="my-plugin")

        assert (staging / "plugin.txt").read_text() == "from git"
        assert isinstance(result, MaterializationResult)
        assert result.version == expected_sha

    @pytest.mark.asyncio
    async def test_with_subdir(self, tmp_path: Path) -> None:
        """Only the configured subdir is copied into staging."""
        source = GitPluginSource(
            git="https://github.com/example/mono.git",
            subdir="plugin",
            ref="v2.0.0",
        )
        staging = tmp_path / "staging"

        async def fake_run_git(*args: str, cwd: Path) -> None:
            clone_path = Path(args[-1])
            clone_path.parent.mkdir(parents=True, exist_ok=True)
            clone_path.mkdir()
            (clone_path / "other.txt").write_text("other")
            sub = clone_path / "plugin"
            sub.mkdir()
            (sub / "content.txt").write_text("plugin content")

        async def fake_run_git_capture(*args: str, cwd: Path) -> tuple[int, str]:
            return 0, "b" * 40

        with (
            patch("tachikoma.plugins.materializer.run_git", side_effect=fake_run_git),
            patch(
                "tachikoma.plugins.materializer.run_git_capture",
                side_effect=fake_run_git_capture,
            ),
        ):
            result = await materialize_git(source, staging, alias="subdir-plugin")

        assert (staging / "content.txt").read_text() == "plugin content"
        assert not (staging / "other.txt").exists()
        assert result.version == "b" * 40

    @pytest.mark.asyncio
    async def test_subdir_traversal_rejected(self, tmp_path: Path) -> None:
        """Subdir with '..' segments is rejected."""
        source = GitPluginSource(
            git="https://github.com/example/repo.git",
            subdir="../etc",
            ref="v1.0.0",
        )
        staging = tmp_path / "staging"

        with pytest.raises(MaterializeError):
            await materialize_git(source, staging, alias="traversal")

    @pytest.mark.asyncio
    async def test_clone_failure(self, tmp_path: Path) -> None:
        """MaterializeError when run_git raises RuntimeError."""
        source = GitPluginSource(git="https://github.com/example/bad.git", ref="v1.0.0")
        staging = tmp_path / "staging"

        with patch(
            "tachikoma.plugins.materializer.run_git",
            side_effect=RuntimeError("git clone failed: not found"),
        ), pytest.raises(MaterializeError) as exc_info:
            await materialize_git(source, staging, alias="fail-plugin")

        assert exc_info.value.alias == "fail-plugin"

    @pytest.mark.asyncio
    async def test_rev_parse_failure_returns_none_version(self, tmp_path: Path) -> None:
        """When git rev-parse fails, version is None (non-fatal)."""
        source = GitPluginSource(git="https://github.com/example/plugin.git", ref="v1.0.0")
        staging = tmp_path / "staging"

        async def fake_run_git(*args: str, cwd: Path) -> None:
            clone_path = Path(args[-1])
            clone_path.parent.mkdir(parents=True, exist_ok=True)
            clone_path.mkdir()
            (clone_path / "plugin.txt").write_text("from git")

        async def fake_run_git_capture(*args: str, cwd: Path) -> tuple[int, str]:
            return 1, ""

        with (
            patch("tachikoma.plugins.materializer.run_git", side_effect=fake_run_git),
            patch(
                "tachikoma.plugins.materializer.run_git_capture",
                side_effect=fake_run_git_capture,
            ),
        ):
            result = await materialize_git(source, staging, alias="my-plugin")

        assert result.version is None


# ===========================================================================
# materialize_url
# ===========================================================================


def _build_tar_gz(archive_path: Path, entries: dict[str, str]) -> None:
    """Build a .tar.gz archive with the given entries.

    *entries* maps relative file paths to content strings.
    """
    with tarfile.open(archive_path, "w:gz") as tf:
        for rel, content in entries.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _build_zip(archive_path: Path, entries: dict[str, str]) -> None:
    """Build a .zip archive with the given entries."""
    with zipfile.ZipFile(archive_path, "w") as zf:
        for rel, content in entries.items():
            zf.writestr(rel, content)


class TestMaterializeUrl:
    """Tests for URL archive materialization."""

    @pytest.mark.asyncio
    async def test_tar_gz_auto_strip(self, tmp_path: Path) -> None:
        """Single top-level directory is auto-stripped (AC-REC-12)."""
        archive_file = tmp_path / "plugin.tar.gz"
        _build_tar_gz(
            archive_file,
            {
                "my-plugin-1.0/tachikoma-plugin.toml": 'name = "my-plugin"\ndescription = "test"',
                "my-plugin-1.0/skills/code/SKILL.md": "# Code skill",
            },
        )

        source = UrlPluginSource(url="https://example.com/plugin.tar.gz")
        staging = tmp_path / "staging"

        with patch(
            "tachikoma.plugins.materializer.urlopen",
            side_effect=lambda url: _fake_url_open(url, archive_file),
        ):
            result = await materialize_url(source, staging, alias="url-plugin")

        # Auto-strip: my-plugin-1.0 should be stripped, so content is at staging root.
        assert (staging / "tachikoma-plugin.toml").exists()
        assert (staging / "skills" / "code" / "SKILL.md").exists()
        assert isinstance(result, MaterializationResult)
        assert result.version is not None
        assert len(result.version) == 64  # SHA-256 hex digest

    @pytest.mark.asyncio
    async def test_zip_no_auto_strip(self, tmp_path: Path) -> None:
        """Multiple top-level entries: no auto-strip (AC-REC-12)."""
        archive_file = tmp_path / "plugin.zip"
        _build_zip(
            archive_file,
            {
                "tachikoma-plugin.toml": 'name = "flat"\ndescription = "flat"',
                "README.md": "# Readme",
            },
        )

        source = UrlPluginSource(url="https://example.com/plugin.zip")
        staging = tmp_path / "staging"

        with patch(
            "tachikoma.plugins.materializer.urlopen",
            side_effect=lambda url: _fake_url_open(url, archive_file),
        ):
            result = await materialize_url(source, staging, alias="flat-plugin")

        assert (staging / "tachikoma-plugin.toml").exists()
        assert (staging / "README.md").exists()
        assert isinstance(result, MaterializationResult)
        assert result.version is not None
        # Verify the hash is correct.
        expected_hash = hashlib.file_digest(archive_file.open("rb"), "sha256").hexdigest()
        assert result.version == expected_hash

    @pytest.mark.asyncio
    async def test_subdir_after_auto_strip(self, tmp_path: Path) -> None:
        """subdir resolves relative to post-auto-strip root (AC-REC-13)."""
        archive_file = tmp_path / "mono.tar.gz"
        _build_tar_gz(
            archive_file,
            {
                "mono-1.0/plugin/tachikoma-plugin.toml": 'name = "sub"\ndescription = "sub"',
                "mono-1.0/plugin/skills/x/SKILL.md": "# X",
                "mono-1.0/other/stuff.txt": "other",
            },
        )

        source = UrlPluginSource(
            url="https://example.com/mono.tar.gz",
            subdir="plugin",
        )
        staging = tmp_path / "staging"

        with patch(
            "tachikoma.plugins.materializer.urlopen",
            side_effect=lambda url: _fake_url_open(url, archive_file),
        ):
            result = await materialize_url(source, staging, alias="subdir-url")

        # Auto-strip promotes mono-1.0, then subdir="plugin" is resolved inside.
        assert (staging / "tachikoma-plugin.toml").exists()
        assert not (staging / "other").exists()
        assert result.version is not None

    @pytest.mark.asyncio
    async def test_download_failure(self, tmp_path: Path) -> None:
        """MaterializeError when download fails."""
        source = UrlPluginSource(url="https://example.com/missing.tar.gz")
        staging = tmp_path / "staging"

        with patch(
            "tachikoma.plugins.materializer.urlopen",
            side_effect=Exception("404 Not Found"),
        ), pytest.raises(MaterializeError) as exc_info:
            await materialize_url(source, staging, alias="dl-fail")

        assert exc_info.value.alias == "dl-fail"

    @pytest.mark.asyncio
    async def test_subdir_traversal_rejected(self, tmp_path: Path) -> None:
        """subdir with '..' segments is rejected in URL materializer."""
        archive_file = tmp_path / "plugin.tar.gz"
        _build_tar_gz(archive_file, {"root-1.0/file.txt": "hello"})

        source = UrlPluginSource(
            url="https://example.com/plugin.tar.gz",
            subdir="../etc",
        )
        staging = tmp_path / "staging"

        with patch(
            "tachikoma.plugins.materializer.urlopen",
            side_effect=lambda url: _fake_url_open(url, archive_file),
        ), pytest.raises(MaterializeError):
            await materialize_url(source, staging, alias="traversal")
