"""Plugin reconciliation: walk config, materialize sources, handle stale-fallback.

Walks ``[plugins]`` config, dispatches each source to the appropriate
materializer, performs atomic swap on success, and handles stale-fallback
when a source is unreachable but a valid prior install exists.

Reconciliation is first-time-only: already-installed plugins are left untouched.
Local-source plugins installed as copies are migrated to symlinks.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger

from tachikoma.plugins.manifest import parse_manifest
from tachikoma.plugins.materializer import (
    MaterializeError,
    _atomic_replace_dir,
    materialize_git,
    materialize_local,
    materialize_url,
)
from tachikoma.plugins.sources import (
    GitPluginSource,
    LocalPluginSource,
    PluginSource,
    UrlPluginSource,
)
from tachikoma.plugins.state import PluginState

if TYPE_CHECKING:
    from tachikoma.plugins.state import PluginStateRepository

_log = logger.bind(component="plugins")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconcileOutcome:
    """Per-plugin reconciliation result.

    Attributes:
        alias: The plugin alias (``[plugins.<alias>]`` key).
        status: ``"loaded"`` on success, ``"stale-fallback"`` when the source
            was unreachable but a valid prior install exists, or ``"failed"``
            when no valid install is available.
        diagnostic: Human-readable diagnostic message. ``None`` when status
            is ``"loaded"``.
    """

    alias: str
    status: Literal["loaded", "stale-fallback", "failed"]
    diagnostic: str | None


@dataclass(frozen=True)
class ReconciliationReport:
    """Aggregated reconciliation results for all configured plugins."""

    outcomes: list[ReconcileOutcome]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def reconcile(
    workspace_path: Path,
    plugins: dict[str, PluginSource],
    state_repo: PluginStateRepository,
) -> ReconciliationReport:
    """Reconcile the plugin install directory with the declared config.

    Steps:
    1. Compute and create the install directory if missing.
    2. Remove orphan directories (subdirs of install_dir not in config, excluding ``.staging``).
    3. For each (alias, source):
       - If already installed: skip materialization (migrate local copies to symlinks).
       - If first-time: materialize, atomic-swap, persist initial PluginState.
       On failure: attempt stale-fallback or mark failed.
    """
    install_dir = workspace_path / ".tachikoma" / "plugins"
    install_dir.mkdir(parents=True, exist_ok=True)

    # --- Orphan cleanup ---
    _remove_orphans(install_dir, set(plugins.keys()))

    # --- Per-plugin reconciliation ---
    outcomes: list[ReconcileOutcome] = []
    for alias, source in plugins.items():
        try:
            await _reconcile_one(alias, source, install_dir, state_repo)
            outcomes.append(
                ReconcileOutcome(alias=alias, status="loaded", diagnostic=None)
            )
        except MaterializeError as exc:
            outcomes.append(_handle_stale_fallback(alias, install_dir, exc))
        except Exception as exc:
            # Catch unexpected errors so one bad plugin never blocks others.
            msg = f"Unexpected error: {exc}"
            _log.bind(plugin=alias).error("Reconciliation failed: {}", msg)
            outcomes.append(
                _handle_stale_fallback(
                    alias,
                    install_dir,
                    MaterializeError(alias, str(source), msg),
                )
            )

    return ReconciliationReport(outcomes=outcomes)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _reconcile_one(
    alias: str,
    source: PluginSource,
    install_dir: Path,
    state_repo: PluginStateRepository,
) -> None:
    """Reconcile a single plugin: skip if installed, migrate local symlinks, or materialize."""
    target = install_dir / alias

    # --- Already installed: skip materialization ---
    if target.is_dir() or target.is_symlink():
        manifest = None
        if target.is_dir() and not target.is_symlink():
            with contextlib.suppress(Exception):
                manifest = parse_manifest(target)

        if manifest is not None:
            # Symlink migration for local sources installed as copies.
            if isinstance(source, LocalPluginSource) and not target.is_symlink():
                await _migrate_local_to_symlink(alias, source, target, state_repo)
            # Ensure PluginState row exists for pre-existing installations.
            await _ensure_state_row(alias, state_repo)
            return

    # --- First-time install ---
    staging = install_dir / f"{alias}.new"

    # Clean up any leftover staging dir from a prior failed attempt.
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    try:
        if isinstance(source, GitPluginSource):
            result = await materialize_git(source, staging, alias=alias)
        elif isinstance(source, UrlPluginSource):
            result = await materialize_url(source, staging, alias=alias)
        elif isinstance(source, LocalPluginSource):
            result = await materialize_local(source, staging, alias=alias)
        else:
            raise MaterializeError(
                alias,
                str(source),
                f"Unknown source type: {type(source).__name__}",
            )

        _atomic_replace_dir(staging, target)

        # Persist initial PluginState with installed version.
        initial_state = PluginState(
            alias=alias,
            installed_version=result.version,
            update_status="unknown",
            available_version=None,
            last_checked_at=None,
            diagnostic=None,
            created_at=datetime.now(UTC),
        )
        await state_repo.upsert(initial_state)
    except BaseException:
        # Clean up staging dir on any failure.
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


async def _migrate_local_to_symlink(
    alias: str,
    source: LocalPluginSource,
    target: Path,
    state_repo: PluginStateRepository,
) -> None:
    """Migrate a local-source plugin from a directory copy to a symlink.

    If the configured source path exists: replace directory with symlink atomically.
    If the source path is gone: retain the copy and mark as stale-fallback.
    """
    if source.path.exists():
        backup = target.with_name(target.name + ".migrate-old")
        try:
            os.rename(target, backup)
            os.symlink(source.path, target)
            # Verify the symlink resolves.
            if not target.resolve().is_dir():
                # Rollback: source path exists but isn't a valid dir.
                os.remove(target)
                os.rename(backup, target)
                _log.bind(plugin=alias).warning(
                    "Symlink target is not a valid directory: {}", source.path
                )
                return
            # Success: remove backup.
            shutil.rmtree(backup, ignore_errors=True)
            _log.bind(plugin=alias).info(
                "Migrated local plugin from copy to symlink: {}", source.path
            )
        except OSError as exc:
            # On partial failure, retain the original directory.
            if backup.exists() and not target.exists():
                try:
                    os.rename(backup, target)
                except OSError:
                    _log.bind(plugin=alias).warning(
                        "Failed to restore backup during symlink migration: {}", exc
                    )
            _log.bind(plugin=alias).warning(
                "Symlink migration failed, retaining copy: {}", exc
            )
    else:
        # Source path gone: retain copy, mark as stale-fallback.
        _log.bind(plugin=alias).warning(
            "Local source path no longer exists: {}, retaining copy", source.path
        )
        state = PluginState(
            alias=alias,
            installed_version=None,
            update_status="stale-fallback",
            available_version=None,
            last_checked_at=None,
            diagnostic=f"Source path no longer exists: {source.path}",
            created_at=datetime.now(UTC),
        )
        await state_repo.upsert(state)


async def _ensure_state_row(alias: str, state_repo: PluginStateRepository) -> None:
    """Ensure a PluginState row exists for pre-existing installations."""
    existing = await state_repo.get(alias)
    if existing is None:
        state = PluginState(
            alias=alias,
            installed_version=None,
            update_status="unknown",
            available_version=None,
            last_checked_at=None,
            diagnostic=None,
            created_at=datetime.now(UTC),
        )
        await state_repo.upsert(state)


def _remove_orphans(install_dir: Path, configured_aliases: set[str]) -> None:
    """Remove subdirectories of *install_dir* not matching a configured alias.

    Skips ``.staging`` (internal staging area). Errors are logged and
    continued (best-effort).
    """
    if not install_dir.is_dir():
        return

    for entry in install_dir.iterdir():
        if not entry.is_dir() and not entry.is_symlink():
            continue
        if entry.name == ".staging":
            continue
        if entry.name not in configured_aliases:
            _log.bind(plugin=entry.name).info("Removing orphan plugin directory: {}", entry)
            try:
                if entry.is_symlink():
                    os.remove(entry)
                else:
                    shutil.rmtree(entry)
            except OSError as exc:
                _log.bind(plugin=entry.name).warning(
                    "Failed to remove orphan directory {}: {}", entry, exc
                )


def _handle_stale_fallback(
    alias: str,
    install_dir: Path,
    error: MaterializeError,
) -> ReconcileOutcome:
    """Check for a valid prior install and return stale-fallback or failed."""
    existing = install_dir / alias
    cause_msg = str(error.cause) if error.cause else "unknown error"

    if existing.is_dir():
        try:
            manifest = parse_manifest(existing)
        except Exception:
            manifest = None

        if manifest is not None and manifest.name == alias:
            _log.bind(plugin=alias).warning(
                "Source unreachable, retaining stale copy: {}", cause_msg
            )
            return ReconcileOutcome(
                alias=alias,
                status="stale-fallback",
                diagnostic=f"Source unreachable, using cached copy: {cause_msg}",
            )

    _log.bind(plugin=alias).error("Reconciliation failed, no valid fallback: {}", cause_msg)
    return ReconcileOutcome(
        alias=alias,
        status="failed",
        diagnostic=f"Failed to materialize plugin: {cause_msg}",
    )
