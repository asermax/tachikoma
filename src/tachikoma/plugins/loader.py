"""Plugin discovery: scan install directory, parse manifests, validate skill dirs.

Runs after reconciliation and produces a list of :class:`LoadedPlugin` records
consumed by the bootstrap hook and the plugin manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger

from tachikoma.plugins.manifest import (
    PluginManifest,
    log_ignored_cc_contributions,
    parse_manifest,
)
from tachikoma.plugins.reconciler import ReconcileOutcome, ReconciliationReport

if TYPE_CHECKING:
    from tachikoma.plugins.sources import PluginSource

_log = logger.bind(component="plugins")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedPlugin:
    """A plugin that has been reconciled and (optionally) discovered.

    Attributes:
        alias: The plugin alias (``[plugins.<alias>]`` key).
        source: The configured source spec.
        manifest: Parsed manifest, or ``None`` if status is ``"failed"``.
        status: ``"loaded"`` when fully validated, ``"stale-fallback"`` when
            the source was unreachable but a prior install exists, or
            ``"failed"`` when no valid install is available.
        diagnostic: Human-readable diagnostic. ``None`` when status is
            ``"loaded"``.
        plugin_dir: Absolute path to the plugin install directory.
        contributed_skills: Mutable list populated later by the skills
            listener after registry registration.  Frozen dataclasses still
            permit in-place mutation of mutable field values.
    """

    alias: str
    source: PluginSource
    manifest: PluginManifest | None
    status: Literal["loaded", "stale-fallback", "failed"]
    diagnostic: str | None
    plugin_dir: Path
    contributed_skills: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover(
    install_dir: Path,
    report: ReconciliationReport,
    plugin_sources: dict[str, PluginSource],
) -> list[LoadedPlugin]:
    """Discover loaded plugins from the install directory.

    For each outcome in *report*, parses the manifest, validates skill
    directories exist, and builds a :class:`LoadedPlugin`.  Per-plugin
    try/except ensures one bad plugin never aborts discovery (R9).
    """
    results: list[LoadedPlugin] = []

    for outcome in report.outcomes:
        try:
            plugin = _discover_one(outcome, install_dir, plugin_sources)
        except Exception as exc:
            _log.bind(plugin=outcome.alias).error(
                "Discovery failed for plugin {}: {}", outcome.alias, exc
            )
            plugin = LoadedPlugin(
                alias=outcome.alias,
                source=plugin_sources[outcome.alias],
                manifest=None,
                status="failed",
                diagnostic=f"Discovery error: {exc}",
                plugin_dir=install_dir / outcome.alias,
            )
        results.append(plugin)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _discover_one(
    outcome: ReconcileOutcome,
    install_dir: Path,
    plugin_sources: dict[str, PluginSource],
) -> LoadedPlugin:
    """Build a :class:`LoadedPlugin` for a single reconciliation outcome."""
    alias = outcome.alias
    plugin_dir = install_dir / alias
    source = plugin_sources[alias]

    # If already marked failed by reconciler, propagate without parsing.
    if outcome.status == "failed":
        return LoadedPlugin(
            alias=alias,
            source=source,
            manifest=None,
            status="failed",
            diagnostic=outcome.diagnostic,
            plugin_dir=plugin_dir,
        )

    # --- Parse manifest ---
    manifest: PluginManifest | None = None
    try:
        manifest = parse_manifest(plugin_dir)
    except Exception as exc:
        _log.bind(plugin=alias).warning("Manifest parse failed: {}", exc)
        return LoadedPlugin(
            alias=alias,
            source=source,
            manifest=None,
            status="failed",
            diagnostic=f"Manifest parse error: {exc}",
            plugin_dir=plugin_dir,
        )

    if manifest is None:
        _log.bind(plugin=alias).warning("No manifest found in plugin directory")
        return LoadedPlugin(
            alias=alias,
            source=source,
            manifest=None,
            status="failed",
            diagnostic=(
                "No manifest found "
                "(expected tachikoma-plugin.toml or .claude-plugin/plugin.json)"
            ),
            plugin_dir=plugin_dir,
        )

    # --- Validate skill directories (AC-MP-8) ---
    valid_skill_dirs: list[Path] = []
    for skill_dir in manifest.skill_dirs:
        if skill_dir.is_dir():
            valid_skill_dirs.append(skill_dir)
        else:
            _log.bind(plugin=alias).warning(
                "Declared skill directory does not exist, excluding: {}",
                skill_dir,
            )

    # Build a new manifest with only valid skill dirs.
    validated_manifest = _dc_replace(manifest, skill_dirs=valid_skill_dirs)

    # Log ignored CC contributions (AC-MP-3).
    if validated_manifest.ignored_cc_contributions:
        log_ignored_cc_contributions(alias, validated_manifest)

    # Status remains as reconciler set it (loaded or stale-fallback).
    return LoadedPlugin(
        alias=alias,
        source=source,
        manifest=validated_manifest,
        status=outcome.status,
        diagnostic=outcome.diagnostic,
        plugin_dir=plugin_dir,
    )
