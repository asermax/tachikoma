"""Plugin discovery: scan install directory, parse manifests, validate skill dirs.

Runs after reconciliation and produces a list of :class:`LoadedPlugin` records
consumed by the bootstrap hook and the plugin manager.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger

from tachikoma.per_message_pre_processing import MessageContextProvider
from tachikoma.plugins.config_schema import ConfigDiagnostic, validate_config
from tachikoma.plugins.manifest import (
    PluginManifest,
    log_ignored_cc_contributions,
    parse_manifest,
)
from tachikoma.plugins.reconciler import ReconcileOutcome, ReconciliationReport
from tachikoma.plugins.registry import get_event_type
from tachikoma.post_processing import (
    _VALID_PHASES,
    MAIN_PHASE,
    PostProcessor,
)
from tachikoma.pre_processing import ContextProvider

if TYPE_CHECKING:
    from bubus import BaseEvent

    from tachikoma.agent_defaults import AgentDefaults
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
        config: Validated config values. Populated when a plugin declares a
            ``[config]`` schema in its manifest. Empty dict when no schema or
            no user values.
    """

    alias: str
    source: PluginSource
    manifest: PluginManifest | None
    status: Literal["loaded", "stale-fallback", "failed"]
    diagnostic: str | None
    plugin_dir: Path
    contributed_skills: list = field(default_factory=list)
    config: dict[str, str | int | bool | float] = field(default_factory=dict)
    init_hook: Callable[..., Any] | None = None
    event_handlers: dict[type[BaseEvent], Callable[..., Any]] = field(default_factory=dict)
    event_wrappers: list = field(default_factory=list)
    context_providers: list[ContextProvider] = field(default_factory=list)
    message_context_providers: list[MessageContextProvider] = field(default_factory=list)
    post_processors: list[PostProcessor] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover(
    install_dir: Path,
    report: ReconciliationReport,
    plugin_sources: dict[str, PluginSource],
    agent_defaults: AgentDefaults,
) -> list[LoadedPlugin]:
    """Discover loaded plugins from the install directory.

    For each outcome in *report*, parses the manifest, validates skill
    directories exist, and builds a :class:`LoadedPlugin`.  Per-plugin
    try/except ensures one bad plugin never aborts discovery (R9).
    """
    results: list[LoadedPlugin] = []

    for outcome in report.outcomes:
        try:
            plugin = _discover_one(outcome, install_dir, plugin_sources, agent_defaults)
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


def _format_diagnostics(diagnostics: list[ConfigDiagnostic]) -> str:
    """Join diagnostic messages into a single string."""
    return "\n".join(d.message for d in diagnostics)


def _import_handler_module(path: Path, module_key: str) -> types.ModuleType:
    """Import a handler module from a file path with a unique key in ``sys.modules``."""
    spec = importlib.util.spec_from_file_location(module_key, path)
    if spec is None:
        raise ValueError(f"Could not create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        sys.modules.pop(module_key, None)
        raise ValueError(f"Failed to import handler module {path}: {exc}") from exc
    return module


def _count_positional_params(func: Callable) -> int:
    """Count positional-or-keyword parameters of *func*."""
    return sum(
        1
        for p in inspect.signature(func).parameters.values()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    )


def _validate_handlers(
    manifest: PluginManifest,
    plugin_dir: Path,
    alias: str,
) -> tuple[Callable[..., Any] | None, dict[type[BaseEvent], Callable[..., Any]]]:
    """Validate and resolve hook/event handlers declared in the manifest.

    Returns ``(init_hook, event_handlers)`` on success.
    Raises ``ValueError`` on any validation failure.
    """
    init_hook: Callable[..., Any] | None = None
    event_handlers: dict[type[BaseEvent], Callable[..., Any]] = {}

    for hook_type, module_name in manifest.hooks.items():
        handler_path = plugin_dir / "hooks" / f"{module_name}.py"
        if not handler_path.exists():
            raise ValueError(f"Hook handler file not found: {handler_path}")

        module_key = f"tachikoma_plugin.{alias}.hooks.{module_name}"
        module = _import_handler_module(handler_path, module_key)

        func = getattr(module, hook_type, None)
        if func is None or not callable(func):
            raise ValueError(
                f"Hook module hooks/{module_name}.py does not contain "
                f"a callable '{hook_type}' function"
            )

        n_params = _count_positional_params(func)
        if n_params != 1:
            raise ValueError(
                f"Hook handler '{hook_type}' in hooks/{module_name}.py must "
                f"accept exactly 1 parameter, got {n_params}"
            )

        if hook_type == "init":
            init_hook = func

    for event_name, module_name in manifest.events.items():
        try:
            event_type = get_event_type(event_name)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc

        handler_path = plugin_dir / "events" / f"{module_name}.py"
        if not handler_path.exists():
            raise ValueError(f"Event handler file not found: {handler_path}")

        module_key = f"tachikoma_plugin.{alias}.events.{module_name}"
        module = _import_handler_module(handler_path, module_key)

        func = getattr(module, "handle", None)
        if func is None or not callable(func):
            raise ValueError(
                f"Event handler module events/{module_name}.py does not contain "
                f"a callable 'handle' function"
            )

        n_params = _count_positional_params(func)
        if n_params != 2:
            raise ValueError(
                f"Event handler 'handle' in events/{module_name}.py must "
                f"accept exactly 2 parameters, got {n_params}"
            )

        event_handlers[event_type] = func

    return init_hook, event_handlers


def _find_concrete_subclasses(
    module: types.ModuleType,
    base_classes: tuple[type, ...],
) -> list[type]:
    """Find concrete (non-abstract) subclasses of *base_classes* in *module*."""
    found: list[type] = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if not inspect.isclass(attr):
            continue
        if inspect.isabstract(attr):
            continue
        if issubclass(attr, base_classes):
            found.append(attr)
    return found


def _validate_providers(
    manifest: PluginManifest,
    plugin_dir: Path,
    alias: str,
    validated_config: dict[str, Any],
) -> tuple[list[ContextProvider], list[MessageContextProvider]]:
    """Validate and instantiate context providers declared in the manifest.

    For each entry in ``manifest.context_providers``, imports the module,
    discovers concrete provider classes, validates constructor signatures,
    and instantiates with ``config=validated_config``.

    Returns ``(session_providers, message_providers)`` on success.
    Raises ``ValueError`` on any validation failure.
    """
    if not manifest.context_providers:
        return ([], [])

    session_providers: list[ContextProvider] = []
    message_providers: list[MessageContextProvider] = []

    for module_name in manifest.context_providers.values():
        provider_path = plugin_dir / "context_providers" / f"{module_name}.py"
        if not provider_path.exists():
            raise ValueError(f"Provider module file not found: {provider_path}")

        module_key = f"tachikoma_plugin.{alias}.context_providers.{module_name}"
        module = _import_handler_module(provider_path, module_key)

        found_classes = _find_concrete_subclasses(module, (ContextProvider, MessageContextProvider))

        if not found_classes:
            raise ValueError(
                f"No concrete class implementing ContextProvider or "
                f"MessageContextProvider found in module '{module_name}'"
            )

        for cls in found_classes:
            sig = inspect.signature(cls.__init__)
            if "config" not in sig.parameters:
                raise ValueError(
                    f"Provider class '{cls.__name__}' __init__ must accept "
                    f"'config' as a keyword argument"
                )

            try:
                instance = cls(config=validated_config)
            except TypeError as exc:
                raise ValueError(
                    f"Provider class '{cls.__name__}' failed to instantiate: {exc}"
                ) from exc

            if issubclass(cls, ContextProvider):
                session_providers.append(instance)
            if issubclass(cls, MessageContextProvider):
                message_providers.append(instance)

    return (session_providers, message_providers)


def _validate_post_processors(
    manifest: PluginManifest,
    plugin_dir: Path,
    alias: str,
    validated_config: dict[str, Any],
    agent_defaults: AgentDefaults,
) -> list[PostProcessor]:
    """Validate and instantiate post-processors declared in the manifest.

    For each entry in ``manifest.post_processors``, imports the module,
    discovers concrete PostProcessor subclasses, validates constructor
    signatures and phase values, and instantiates with config and
    conditionally with agent_defaults.

    Returns the list of instantiated processors on success.
    Raises ``ValueError`` on any validation failure.
    """
    if not manifest.post_processors:
        return []

    processors: list[PostProcessor] = []

    for module_name in manifest.post_processors.values():
        processor_path = plugin_dir / "post_processors" / f"{module_name}.py"
        if not processor_path.exists():
            raise ValueError(f"Post-processor module file not found: {processor_path}")

        module_key = f"tachikoma_plugin.{alias}.post_processors.{module_name}"
        module = _import_handler_module(processor_path, module_key)

        found_classes = _find_concrete_subclasses(module, (PostProcessor,))

        if not found_classes:
            raise ValueError(
                f"No concrete class implementing PostProcessor found in module '{module_name}'"
            )

        for cls in found_classes:
            sig = inspect.signature(cls.__init__)
            if "config" not in sig.parameters:
                raise ValueError(
                    f"Post-processor class '{cls.__name__}' __init__ must accept "
                    f"'config' as a keyword argument"
                )

            phase_value = getattr(cls, "phase", MAIN_PHASE)
            if phase_value not in _VALID_PHASES:
                valid_list = ", ".join(sorted(_VALID_PHASES))
                raise ValueError(
                    f"Post-processor class '{cls.__name__}' declares invalid "
                    f"phase '{phase_value}'. Valid phases: {valid_list}"
                )

            kwargs: dict[str, Any] = {"config": validated_config}
            if "agent_defaults" in sig.parameters:
                kwargs["agent_defaults"] = agent_defaults

            try:
                instance = cls(**kwargs)
            except TypeError as exc:
                raise ValueError(
                    f"Post-processor class '{cls.__name__}' failed to instantiate: {exc}"
                ) from exc

            processors.append(instance)

    return processors


def _discover_one(
    outcome: ReconcileOutcome,
    install_dir: Path,
    plugin_sources: dict[str, PluginSource],
    agent_defaults: AgentDefaults,
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
                "No manifest found (expected tachikoma-plugin.toml or .claude-plugin/plugin.json)"
            ),
            plugin_dir=plugin_dir,
        )

    # Skill directories that don't exist are excluded rather than failing.

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

    if validated_manifest.config_schema:
        user_values = source.config if source.config is not None else {}
        result = validate_config(validated_manifest.config_schema, user_values)
        if not result.is_valid:
            return LoadedPlugin(
                alias=alias,
                source=source,
                manifest=validated_manifest,
                status="failed",
                diagnostic=_format_diagnostics(result.diagnostics),
                plugin_dir=plugin_dir,
            )
        validated_config = result.values
    else:
        validated_config = {}

    # Status remains as reconciler set it (loaded or stale-fallback).

    init_hook_val: Callable[..., Any] | None = None
    event_handlers_val: dict[type[BaseEvent], Callable[..., Any]] = {}
    if validated_manifest.source_format == "tachikoma" and (
        validated_manifest.hooks or validated_manifest.events
    ):
        try:
            init_hook_val, event_handlers_val = _validate_handlers(
                validated_manifest, plugin_dir, alias
            )
        except ValueError as exc:
            _log.bind(plugin=alias).warning("Handler validation failed: {}", exc)
            return LoadedPlugin(
                alias=alias,
                source=source,
                manifest=validated_manifest,
                status="failed",
                diagnostic=f"Handler validation error: {exc}",
                plugin_dir=plugin_dir,
            )

    session_providers: list[ContextProvider] = []
    message_providers: list[MessageContextProvider] = []
    if validated_manifest.source_format == "tachikoma" and validated_manifest.context_providers:
        try:
            session_providers, message_providers = _validate_providers(
                validated_manifest, plugin_dir, alias, validated_config
            )
        except ValueError as exc:
            _log.bind(plugin=alias).warning("Provider validation failed: {}", exc)
            return LoadedPlugin(
                alias=alias,
                source=source,
                manifest=validated_manifest,
                status="failed",
                diagnostic=f"Provider validation error: {exc}",
                plugin_dir=plugin_dir,
            )

    post_processors: list[PostProcessor] = []
    if validated_manifest.source_format == "tachikoma" and validated_manifest.post_processors:
        try:
            post_processors = _validate_post_processors(
                validated_manifest, plugin_dir, alias, validated_config, agent_defaults
            )
        except ValueError as exc:
            _log.bind(plugin=alias).warning("Post-processor validation failed: {}", exc)
            return LoadedPlugin(
                alias=alias,
                source=source,
                manifest=validated_manifest,
                status="failed",
                diagnostic=f"Post-processor validation error: {exc}",
                plugin_dir=plugin_dir,
            )

    return LoadedPlugin(
        alias=alias,
        source=source,
        manifest=validated_manifest,
        status=outcome.status,
        diagnostic=outcome.diagnostic,
        plugin_dir=plugin_dir,
        config=validated_config,
        init_hook=init_hook_val,
        event_handlers=event_handlers_val,
        context_providers=session_providers,
        message_context_providers=message_providers,
        post_processors=post_processors,
    )
