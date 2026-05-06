"""Plugin source materialization with atomic directory replacement.

Three async materializers (git, URL, local) each write content into a staging
directory, which the reconciler then swaps into the install location via
``_atomic_replace_dir`` (pip-style triple-step rename).

Each materializer returns a :class:`MaterializationResult` carrying the staging
path and a version hash (git: commit SHA, url: content SHA-256, local: None).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

from loguru import logger

from tachikoma.git.sync import run_git, run_git_capture
from tachikoma.plugins.sources import GitPluginSource, LocalPluginSource, UrlPluginSource

_log = logger.bind(component="plugins")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MaterializeError(Exception):
    """Structured error raised when a plugin source cannot be materialized.

    Attributes:
        alias: The plugin alias (set by the reconciler, not the materializer).
        source: Human-readable description of the source.
        cause: The underlying exception or diagnostic message.
    """

    def __init__(self, alias: str, source: str, cause: str | Exception) -> None:
        self.alias = alias
        self.source = source
        self.cause = cause
        detail = f"MaterializeError({alias!r}, {source!r}): {cause}"
        super().__init__(detail)


@dataclass(frozen=True)
class MaterializationResult:
    """Result of a plugin materialization operation.

    Attributes:
        staging_dir: Path to the staging directory containing the materialized plugin.
        version: Version hash of the materialized content. Git: 40-char commit SHA,
            URL: SHA-256 hex digest of the archive, Local: None (symlinks have no version).
    """

    staging_dir: Path
    version: str | None


# ---------------------------------------------------------------------------
# Atomic directory swap
# ---------------------------------------------------------------------------


def _atomic_replace_dir(new: Path, dst: Path) -> None:
    """Atomically replace *dst* directory with *new*.

    Uses the pip-style triple-step:
    1. If *dst* exists, rename it to ``<dst>.old``.
    2. Rename *new* to *dst* (this is atomic on POSIX when both are on the
       same filesystem).  On failure, rename ``<dst>.old`` back and re-raise.
    3. Remove ``<dst>.old`` (non-atomic but safe -- live path is already stable).
    """
    backup = dst.with_name(dst.name + ".old")

    if dst.exists():
        os.rename(dst, backup)

    try:
        os.rename(new, dst)
    except BaseException:
        # Rollback: restore the old directory if we moved it.
        if backup.exists():
            try:
                os.rename(backup, dst)
            except BaseException:
                _log.warning(
                    "Failed to restore backup {} -> {}: original directory may be lost",
                    backup,
                    dst,
                )
        raise

    # Cleanup old copy (best-effort, non-atomic but post live-path stable).
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


# ---------------------------------------------------------------------------
# Materializers
# ---------------------------------------------------------------------------


async def materialize_local(
    source: LocalPluginSource,
    staging: Path,
    *,
    alias: str = "<unknown>",
) -> MaterializationResult:
    """Create a symlink from *staging* to the local source path.

    Local plugins are always current via symlink — no copy needed.

    Raises :class:`MaterializeError` on failure (missing path, permissions).
    """
    if not source.path.exists():
        raise MaterializeError(
            alias,
            f"local:{source.path}",
            FileNotFoundError(f"Source path does not exist: {source.path}"),
        )
    try:
        os.symlink(source.path, staging)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise MaterializeError(alias, f"local:{source.path}", exc) from exc

    return MaterializationResult(staging_dir=staging, version=None)


async def materialize_git(
    source: GitPluginSource,
    staging: Path,
    *,
    alias: str = "<unknown>",
) -> MaterializationResult:
    """Shallow-clone a git repo and copy into *staging*.

    Captures the resolved commit SHA (40-char) via ``git rev-parse HEAD``.

    Raises :class:`MaterializeError` on failure.
    """
    loop = asyncio.get_running_loop()

    try:
        with tempfile.TemporaryDirectory() as tmp_clone_dir:
            tmp_clone = Path(tmp_clone_dir) / "repo"
            await run_git(
                "clone",
                "--depth",
                "1",
                "--branch",
                source.ref,
                source.git,
                str(tmp_clone),
                cwd=Path(tmp_clone_dir),
            )

            # Capture the resolved commit SHA.
            rc, stdout = await run_git_capture("rev-parse", "HEAD", cwd=tmp_clone)
            sha = stdout.strip() if rc == 0 else None

            src_root = tmp_clone

            if source.subdir is not None:
                _validate_subdir(tmp_clone, source.subdir)
                src_root = tmp_clone / source.subdir

            await loop.run_in_executor(None, shutil.copytree, src_root, staging)

    except MaterializeError:
        raise
    except (RuntimeError, OSError) as exc:
        raise MaterializeError(alias, f"git:{source.git}@{source.ref}", exc) from exc

    return MaterializationResult(staging_dir=staging, version=sha)


def _validate_subdir(base: Path, subdir: str) -> None:
    """Reject *subdir* paths with ``..`` segments or that resolve outside *base*."""
    posix = PurePosixPath(subdir)
    if ".." in posix.parts:
        raise ValueError(f"subdir must not contain '..' segments, got {subdir!r}")
    resolved = (base / subdir).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise ValueError(f"subdir {subdir!r} resolves outside the clone directory")


async def materialize_url(
    source: UrlPluginSource,
    staging: Path,
    *,
    alias: str = "<unknown>",
) -> MaterializationResult:
    """Download an archive from a URL, extract, auto-strip, and copy into *staging*.

    Captures the SHA-256 content hash of the downloaded archive.

    Raises :class:`MaterializeError` on failure.
    """
    loop = asyncio.get_running_loop()
    content_hash: str | None = None

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)

            # Download archive to a temp file.
            archive_path = tmp / "archive"
            await loop.run_in_executor(None, _download, source.url, archive_path)

            # Compute SHA-256 content hash of the archive.
            content_hash = hashlib.file_digest(archive_path.open("rb"), "sha256").hexdigest()

            # Extract into an extraction root.
            extract_root = tmp / "extract"
            extract_root.mkdir()
            await loop.run_in_executor(
                None, _extract_archive, archive_path, extract_root, source.url
            )

            # Auto-strip: if extract_root has exactly one entry and it is a
            # directory, rebind extract_root to that single entry (AC-REC-12).
            entries = list(extract_root.iterdir())
            if len(entries) == 1 and entries[0].is_dir():
                extract_root = entries[0]

            # Apply subdir relative to post-auto-strip root (AC-REC-13).
            src_root = extract_root
            if source.subdir is not None:
                _validate_subdir(extract_root, source.subdir)
                src_root = extract_root / source.subdir

            await loop.run_in_executor(None, shutil.copytree, src_root, staging)

    except MaterializeError:
        raise
    except Exception as exc:
        raise MaterializeError(alias, f"url:{source.url}", exc) from exc

    return MaterializationResult(staging_dir=staging, version=content_hash)


def _download(url: str, dest: Path) -> None:
    """Download *url* to *dest* (blocking, runs in executor)."""
    try:
        with (
            urlopen(url) as response,  # noqa: S310
            open(dest, "wb") as f,
        ):
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as exc:
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc


def _extract_archive(archive_path: Path, extract_root: Path, url: str) -> None:
    """Extract *archive_path* into *extract_root* (blocking, runs in executor)."""
    lower = url.lower()
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        try:
            with tarfile.open(archive_path, mode="r:gz") as tf:
                tf.extractall(extract_root, filter="data")
        except Exception as exc:
            raise RuntimeError(f"Failed to extract tar.gz archive: {exc}") from exc
    elif lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(extract_root)
        except Exception as exc:
            raise RuntimeError(f"Failed to extract zip archive: {exc}") from exc
    else:
        raise RuntimeError(f"Unsupported archive format for URL: {url}")
