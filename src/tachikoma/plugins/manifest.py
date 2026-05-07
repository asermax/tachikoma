"""Plugin manifest parsing for Tachikoma-native and Claude Code formats.

Reads ``tachikoma-plugin.toml`` (native) and ``.claude-plugin/plugin.json``
(CC), validates them, resolves skill directory paths, and produces a unified
``PluginManifest`` dataclass consumed by the discovery layer.

Native manifests take precedence over CC manifests (AC-MP-5).
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tachikoma.plugins.config_schema import ConfigFieldSchema
from tachikoma.plugins.sources import ALIAS_PATTERN


def _validate_manifest_name(name: str) -> str:
    """Validate a manifest name using the alias pattern (AC-PSD-6)."""
    if not ALIAS_PATTERN.match(name):
        raise ValueError(f"manifest name must match [a-z0-9][a-z0-9-]*, got {name!r}")
    return name


# ---------------------------------------------------------------------------
# Path-traversal protection helpers
# ---------------------------------------------------------------------------

# Known Claude Code contribution-type keys that we ignore (skills-only scope).
_CC_CONTRIBUTION_TYPES: tuple[str, ...] = (
    "commands",
    "mcpServers",
    "agents",
    "hooks",
    "events",
    "lspServers",
    "monitors",
    "themes",
    "outputStyles",
    "context_providers",
)


def _validate_skill_paths(paths: list[str]) -> list[str]:
    """Reject skill paths with empty strings, absolute paths, or ``..`` segments.

    This validator is applied at the Pydantic model level so malformed paths
    are caught *before* we resolve against a plugin directory.
    """
    for p in paths:
        if not p:
            raise ValueError("skill paths must not contain empty strings")
        posix = PurePosixPath(p)
        if posix.is_absolute():
            raise ValueError(f"skill path must be relative, got absolute: {p!r}")
        if ".." in posix.parts:
            raise ValueError(f"skill path must not contain '..' segments, got {p!r}")
    return paths


def _validate_cc_skill_path(p: str | None) -> str | None:
    """Same as :func:`_validate_skill_paths` but for a single optional string."""
    if p is None:
        return p
    if not p:
        raise ValueError("skill path must not be empty")
    posix = PurePosixPath(p)
    if posix.is_absolute():
        raise ValueError(f"skill path must be relative, got absolute: {p!r}")
    if ".." in posix.parts:
        raise ValueError(f"skill path must not contain '..' segments, got {p!r}")
    return p


def _resolve_stays_within(plugin_dir: Path, rel: str) -> Path:
    """Resolve *rel* inside *plugin_dir* and verify the result stays within.

    Raises ``ValueError`` on escape.
    """
    resolved = (plugin_dir / rel).resolve()
    if not str(resolved).startswith(str(plugin_dir.resolve())):
        raise ValueError(f"skill path {rel!r} resolves outside the plugin directory")
    return resolved


def _validate_bare_module_names(values: dict[str, str]) -> dict[str, str]:
    """Reject module names with empty strings, path separators, or ``..`` segments."""
    for key, name in values.items():
        if not name:
            raise ValueError(f"Module name for '{key}' must not be empty")
        posix = PurePosixPath(name)
        if posix.is_absolute():
            raise ValueError(f"Module name for '{key}' must be relative, got absolute: {name!r}")
        if ".." in posix.parts:
            raise ValueError(
                f"Module name for '{key}' must not contain '..' segments, got {name!r}"
            )
        if len(posix.parts) > 1:
            raise ValueError(
                f"Module name for '{key}' must be a bare name (no path separators), got {name!r}"
            )
    return values


# ---------------------------------------------------------------------------
# Pydantic manifest models
# ---------------------------------------------------------------------------


class TachikomaManifest(BaseModel):
    """Parsed ``tachikoma-plugin.toml`` — strict validation (extra forbidden).

    Fields mirror the native manifest spec: name, version, description, and
    a list of relative skill directory paths.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str | None = None
    description: str
    skills: list[str] = Field(default_factory=list)
    config: dict[str, ConfigFieldSchema] = Field(default_factory=dict)
    hooks: dict[str, str] = Field(default_factory=dict)
    events: dict[str, str] = Field(default_factory=dict)
    context_providers: dict[str, str] = Field(default_factory=dict)

    # -- validators ----------------------------------------------------------

    @field_validator("name", mode="after")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _validate_manifest_name(v)

    @field_validator("skills", mode="after")
    @classmethod
    def _validate_skills(cls, v: list[str]) -> list[str]:
        return _validate_skill_paths(v)

    @field_validator("hooks", "events", "context_providers", mode="after")
    @classmethod
    def _validate_handler_names(cls, v: dict[str, str]) -> dict[str, str]:
        return _validate_bare_module_names(v)


class CcManifest(BaseModel):
    """Parsed ``.claude-plugin/plugin.json`` — tolerant (extra ignored).

    Only ``name`` is required per the Claude Code reference. Non-skill
    contribution keys (commands, mcpServers, etc.) pass through silently;
    callers inspect the raw JSON dict for those.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    version: str | None = None
    description: str | None = None
    skills: str | None = None

    @field_validator("skills", mode="after")
    @classmethod
    def _validate_skills(cls, v: str | None) -> str | None:
        return _validate_cc_skill_path(v)


# ---------------------------------------------------------------------------
# Unified internal manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginManifest:
    """Unified manifest consumed by the discovery / loading layer.

    ``skill_dirs`` holds *resolved absolute* paths to skill directories within
    the plugin's install directory.

    ``ignored_cc_contributions`` lists the CC contribution-type keys that were
    present in a CC manifest and silently ignored (logged at INFO by the
    caller). Empty for Tachikoma-native manifests.
    """

    name: str
    version: str | None
    description: str | None
    source_format: Literal["tachikoma", "cc"]
    skill_dirs: list[Path]
    ignored_cc_contributions: list[str] = field(default_factory=list)
    config_schema: dict[str, ConfigFieldSchema] = field(default_factory=dict)
    hooks: dict[str, str] = field(default_factory=dict)
    events: dict[str, str] = field(default_factory=dict)
    context_providers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_log = logger.bind(component="plugins")


def parse_manifest(plugin_dir: Path) -> PluginManifest | None:
    """Parse the plugin manifest from *plugin_dir*.

    Returns ``None`` if neither manifest file is present (AC-MP-6).
    Raises ``ValueError`` (or Pydantic ``ValidationError``) on malformed input
    (AC-MP-7).

    When both manifests exist the Tachikoma-native one wins (AC-MP-5).
    """
    native_path = plugin_dir / "tachikoma-plugin.toml"
    cc_path = plugin_dir / ".claude-plugin" / "plugin.json"

    # --- Native takes precedence (AC-MP-5) --------------------------------
    if native_path.exists():
        return _parse_native(plugin_dir, native_path)

    # --- CC fallback -------------------------------------------------------
    if cc_path.exists():
        return _parse_cc(plugin_dir, cc_path)

    # Neither manifest exists (AC-MP-6)
    return None


def log_ignored_cc_contributions(alias: str, manifest: PluginManifest) -> list[str]:
    """Emit one INFO log per ignored CC contribution type.

    Returns the list of contribution types logged so tests can assert
    without depending on loguru / ``caplog`` (DES-002 logging; AC-MP-3).
    """
    logged: list[str] = []
    for ctype in manifest.ignored_cc_contributions:
        _log.bind(plugin=alias).info(
            "Ignoring CC plugin contribution: alias={} type={}",
            alias,
            ctype,
        )
        logged.append(ctype)
    return logged


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_native(plugin_dir: Path, manifest_path: Path) -> PluginManifest:
    """Parse a ``tachikoma-plugin.toml`` and return a :class:`PluginManifest`."""
    try:
        raw = manifest_path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to read tachikoma-plugin.toml: {exc}") from exc

    parsed = TachikomaManifest.model_validate(data)

    # Resolve skill paths and verify they stay within plugin_dir.
    resolved_dirs: list[Path] = []
    for rel in parsed.skills:
        resolved_dirs.append(_resolve_stays_within(plugin_dir, rel))

    return PluginManifest(
        name=parsed.name,
        version=parsed.version,
        description=parsed.description,
        source_format="tachikoma",
        skill_dirs=resolved_dirs,
        config_schema=parsed.config,
        hooks=parsed.hooks,
        events=parsed.events,
        context_providers=parsed.context_providers,
    )


def _parse_cc(plugin_dir: Path, manifest_path: Path) -> PluginManifest:
    """Parse a ``.claude-plugin/plugin.json`` and return a :class:`PluginManifest`."""
    try:
        raw = manifest_path.read_bytes()
        data = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"Failed to read .claude-plugin/plugin.json: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(".claude-plugin/plugin.json must contain a JSON object")

    parsed = CcManifest.model_validate(data)

    # Resolve skill directories.
    skill_dirs = _resolve_cc_skill_dirs(plugin_dir, parsed.skills)

    # Accumulate ignored CC contribution-type keys.
    ignored = [key for key in _CC_CONTRIBUTION_TYPES if key in data and data[key]]

    return PluginManifest(
        name=parsed.name,
        version=parsed.version,
        description=parsed.description,
        source_format="cc",
        skill_dirs=skill_dirs,
        ignored_cc_contributions=ignored,
    )


def _resolve_cc_skill_dirs(plugin_dir: Path, skills_field: str | None) -> list[Path]:
    """Resolve skill directories for a CC plugin.

    - If ``skills`` is set in the manifest, treat it as a single relative path.
    - Else, check for a conventional ``skills/`` directory.
    - Otherwise return an empty list (AC-MP-4).
    """
    if skills_field is not None:
        return [_resolve_stays_within(plugin_dir, skills_field)]

    conventional = plugin_dir / "skills"
    if conventional.is_dir():
        return [conventional]

    return []
