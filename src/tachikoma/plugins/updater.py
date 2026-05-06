"""Plugin update detection: git ls-remote comparison and daily check tick.

Provides lightweight remote ref resolution for git-source plugins via
``git ls-remote`` (no object download), and a daily-check orchestrator
that iterates all git-source plugins, updates their state, and returns
aliases with available updates.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.materializer import _download as _download_archive
from tachikoma.plugins.sources import GitPluginSource, UrlPluginSource
from tachikoma.plugins.state import PluginState, PluginStateRepository

_log = logger.bind(component="plugins.updater")

# Well-known branch names that should use refs/heads/ even though they
# don't contain a slash.
_BRANCH_REFS = frozenset({"main", "master"})


class GitCheckError(Exception):
    """Raised when ``git ls-remote`` fails for a plugin source.

    Attributes:
        alias: The plugin alias.
        source_url: The git remote URL that failed.
    """

    def __init__(self, alias: str, source_url: str, detail: str = "") -> None:
        self.alias = alias
        self.source_url = source_url
        self.detail = detail
        msg = f"git ls-remote failed for {alias!r} ({source_url})"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


def _resolve_ref_spec(ref: str) -> str:
    """Map a user-facing ref to a ``git ls-remote`` ref spec.

    Branches (contain ``/`` or are well-known names) resolve under
    ``refs/heads/``. Everything else is treated as a tag and resolved
    under ``refs/tags/`` with the ``^{}`` suffix to dereference
    annotated tags to their commit SHA.
    """
    if "/" in ref or ref in _BRANCH_REFS:
        return f"refs/heads/{ref}"
    return f"refs/tags/{ref}^{{}}"


async def check_git_update(
    source: GitPluginSource,
    installed_version: str,
    *,
    alias: str,
) -> str | None:
    """Check a git remote for a newer version of a plugin.

    Runs ``git ls-remote <url> <ref_spec>`` and parses the first output
    line to extract the remote commit SHA.  Compares against
    *installed_version*.

    Returns:
        The remote SHA if it differs from *installed_version*, or ``None``
        if the versions match (plugin is up-to-date).

    Raises:
        GitCheckError: If the subprocess fails or produces no output.
    """
    ref_spec = _resolve_ref_spec(source.ref)

    proc = await asyncio.create_subprocess_exec(
        "git",
        "ls-remote",
        source.git,
        ref_spec,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode().strip()
        raise GitCheckError(alias, source.git, err or f"exit code {proc.returncode}")

    output = stdout.decode().strip()
    if not output:
        raise GitCheckError(alias, source.git, "no output from ls-remote")

    # ls-remote may return multiple matching refs; take the first
    first_line = output.splitlines()[0]
    remote_sha = first_line.split("\t", 1)[0].strip()

    if not remote_sha:
        raise GitCheckError(alias, source.git, "empty SHA in ls-remote output")

    if remote_sha == installed_version:
        return None

    return remote_sha


async def compute_url_hash(source: UrlPluginSource) -> str:
    """Download the archive at *source.url* and return its SHA-256 hex digest.

    The download goes to a temporary file that is cleaned up automatically.
    """
    loop = asyncio.get_running_loop()

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = Path(tmp_dir) / "archive"
        await loop.run_in_executor(None, _download_archive, source.url, archive_path)
        return hashlib.file_digest(archive_path.open("rb"), "sha256").hexdigest()


@dataclass(frozen=True)
class PluginUpdateInfo:
    """Information about a plugin with an available update."""

    alias: str
    available_version: str


@dataclass(frozen=True)
class UpdateResult:
    """Result of a single plugin update attempt."""

    alias: str
    status: str  # "updated", "failed", "skipped"
    error: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class UpdateSummary:
    """Aggregated result of a bulk plugin update."""

    total: int
    updated: int
    skipped: int
    failed: int
    results: list[UpdateResult]


async def run_daily_git_check(
    plugins: dict[str, LoadedPlugin],
    state_repo: PluginStateRepository,
) -> list[PluginUpdateInfo]:
    """Check all git-source plugins for updates.

    Iterates every loaded plugin whose source is :class:`GitPluginSource`
    and whose status is ``"loaded"``.  For each, runs
    :func:`check_git_update` and persists the result via *state_repo*.

    Returns:
        A list of :class:`PluginUpdateInfo` for plugins with available
        updates.
    """
    updates: list[PluginUpdateInfo] = []
    now = datetime.now(UTC)

    for alias, plugin in plugins.items():
        if plugin.status != "loaded":
            continue
        if not isinstance(plugin.source, GitPluginSource):
            continue

        state = await state_repo.get(alias)
        if state is None:
            _log.warning(
                "No PluginState for loaded git plugin {alias}, skipping",
                alias=alias,
            )
            continue

        installed_version = state.installed_version
        if installed_version is None:
            _log.debug(
                "No installed_version for {alias}, skipping daily check",
                alias=alias,
            )
            continue

        try:
            remote_sha = await check_git_update(
                plugin.source,
                installed_version,
                alias=alias,
            )
        except GitCheckError as exc:
            _log.warning(
                "Update check failed for {alias}: {err}",
                alias=alias,
                err=str(exc),
            )
            # Update last_checked_at but retain previous status
            await state_repo.upsert(
                PluginState(
                    alias=alias,
                    installed_version=state.installed_version,
                    update_status=state.update_status,
                    available_version=state.available_version,
                    last_checked_at=now,
                    diagnostic=state.diagnostic,
                    created_at=state.created_at,
                )
            )
            continue

        if remote_sha is not None:
            # Update available
            await state_repo.upsert(
                PluginState(
                    alias=alias,
                    installed_version=state.installed_version,
                    update_status="update-available",
                    available_version=remote_sha,
                    last_checked_at=now,
                    diagnostic=None,
                    created_at=state.created_at,
                )
            )
            updates.append(PluginUpdateInfo(alias=alias, available_version=remote_sha))
            _log.info(
                "Update available for {alias}: {old} -> {new}",
                alias=alias,
                old=installed_version[:8],
                new=remote_sha[:8],
            )
        else:
            # Up to date
            await state_repo.upsert(
                PluginState(
                    alias=alias,
                    installed_version=state.installed_version,
                    update_status="up-to-date",
                    available_version=None,
                    last_checked_at=now,
                    diagnostic=None,
                    created_at=state.created_at,
                )
            )

    return updates
