"""Plugin reconciliation: walk config, materialize sources, handle stale-fallback.

Walks ``[plugins]`` config, dispatches each source to the appropriate
materializer, performs atomic swap on success, and handles stale-fallback
when a source is unreachable but a valid prior install exists.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
) -> ReconciliationReport:
    """Reconcile the plugin install directory with the declared config.

    Steps:
    1. Compute and create the install directory if missing.
    2. Remove orphan directories (subdirs of install_dir not in config, excluding ``.staging``).
    3. For each (alias, source): materialize, atomic-swap, record outcome.
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
            await _reconcile_one(alias, source, install_dir)
            outcomes.append(
                ReconcileOutcome(alias=alias, status="loaded", diagnostic=None)
            )
        except MaterializeError as exc:
            outcomes.append(_handle_stale_fallback(alias, install_dir, exc))
        except Exception as exc:
            # Catch unexpected errors so one bad plugin never blocks others (R9).
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
) -> None:
    """Materialize a single plugin and atomic-swap it into the install dir."""
    staging = install_dir / f"{alias}.new"

    # Clean up any leftover staging dir from a prior failed attempt.
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    try:
        if isinstance(source, GitPluginSource):
            await materialize_git(source, staging, alias=alias)
        elif isinstance(source, UrlPluginSource):
            await materialize_url(source, staging, alias=alias)
        elif isinstance(source, LocalPluginSource):
            await materialize_local(source, staging, alias=alias)
        else:
            raise MaterializeError(
                alias,
                str(source),
                f"Unknown source type: {type(source).__name__}",
            )

        _atomic_replace_dir(staging, install_dir / alias)
    except BaseException:
        # Clean up staging dir on any failure.
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _remove_orphans(install_dir: Path, configured_aliases: set[str]) -> None:
    """Remove subdirectories of *install_dir* not matching a configured alias.

    Skips ``.staging`` (internal staging area). Errors are logged and
    continued (best-effort).
    """
    if not install_dir.is_dir():
        return

    for entry in install_dir.iterdir():
        if not entry.is_dir():
            continue
        if entry.name == ".staging":
            continue
        if entry.name not in configured_aliases:
            _log.bind(plugin=entry.name).info("Removing orphan plugin directory: {}", entry)
            try:
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
