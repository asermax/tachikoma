"""Plugin manager: in-memory state, install/remove/list operations.

Owns the ``_loaded`` dict, serializes operations with an ``asyncio.Lock``, and
dispatches plugin lifecycle events on the bubus EventBus. Does NOT depend on
``SkillRegistry`` or ``session_registry`` — those are handled by the plugin-
event listener in the skills module.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from tachikoma.plugins.context import PluginContext
from tachikoma.plugins.events import PluginInstalled, PluginRemoved, PluginRemoving
from tachikoma.plugins.lifecycle import (
    _invoke_handler,
    subscribe_plugin_events,
    unsubscribe_plugin_events,
)
from tachikoma.plugins.loader import LoadedPlugin
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
    validate_alias,
)

if TYPE_CHECKING:
    from bubus import EventBus

    from tachikoma.config import SettingsManager

_log = logger.bind(component="plugins")


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class PluginAliasCollisionError(Exception):
    """Raised when a plugin alias is already in use.

    Attributes:
        alias: The colliding alias.
        suggest_retry_with_alias: True when *alias* was derived from the
            manifest name (no explicit alias was provided), suggesting the
            caller should retry with an explicit alias argument.
    """

    def __init__(self, alias: str, *, suggest_retry_with_alias: bool = False) -> None:
        self.alias = alias
        self.suggest_retry_with_alias = suggest_retry_with_alias
        msg = f"Plugin alias '{alias}' is already installed"
        if suggest_retry_with_alias:
            msg += "; retry with an explicit alias argument"
        super().__init__(msg)


class PluginNotFoundError(Exception):
    """Raised when a plugin alias is not found in the loaded set."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        super().__init__(f"Plugin '{alias}' is not installed")


class PluginInstallError(Exception):
    """Umbrella error for install failures (materialize / manifest / validation).

    Attributes:
        alias: The resolved alias (or best-effort identifier).
        cause: The underlying exception.
    """

    def __init__(self, alias: str, cause: Exception) -> None:
        self.alias = alias
        self.cause = cause
        super().__init__(f"Failed to install plugin '{alias}': {cause}")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class PluginManager:
    """Runtime owner of plugin state and install/remove/list operations.

    Args:
        settings_manager: For config read/write (``update_plugin_entry``,
            ``remove_plugin_entry``).
        bus: EventBus for dispatching plugin lifecycle events.
        workspace_path: The workspace root (``.tachikoma/plugins/`` lives here).
        loaded: Initial set of loaded plugins (from bootstrap discovery).
    """

    def __init__(
        self,
        *,
        settings_manager: SettingsManager,
        bus: EventBus,
        workspace_path: Path,
        loaded: dict[str, LoadedPlugin],
    ) -> None:
        self._settings_manager = settings_manager
        self._bus = bus
        self._workspace_path = workspace_path
        self._loaded: dict[str, LoadedPlugin] = dict(loaded)
        self._lock = asyncio.Lock()

    def list_plugins(self) -> list[LoadedPlugin]:
        """Return all loaded plugins (caller should not mutate)."""
        return list(self._loaded.values())

    def failed_plugins(self) -> list[LoadedPlugin]:
        """Return plugins that failed to load (status != "loaded")."""
        return [p for p in self._loaded.values() if p.status != "loaded"]

    async def install(
        self,
        source: PluginSource,
        alias: str | None = None,
    ) -> LoadedPlugin:
        """Install a plugin from a source spec.

        Under the manager's ``_lock``:
        1. Materialize to a temp staging dir.
        2. Parse the manifest.
        3. Resolve alias (explicit > manifest.name).
        4. Check for collision.
        5. Atomic-swap into install dir.
        6. Write config entry.
        7. Record in ``_loaded``.
        8. Dispatch ``PluginInstalled`` event.

        Raises:
            PluginAliasCollisionError: Alias already in use.
            PluginInstallError: Materialization or manifest parse failure.
        """
        async with self._lock:
            return await self._install_locked(source, alias)

    async def _install_locked(
        self,
        source: PluginSource,
        alias: str | None,
    ) -> LoadedPlugin:
        install_dir = self._workspace_path / ".tachikoma" / "plugins"
        staging_root = install_dir / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)

        staging = staging_root / f"install-{alias or 'unknown'}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

        try:
            if isinstance(source, GitPluginSource):
                await materialize_git(source, staging, alias=alias or "<unknown>")
            elif isinstance(source, UrlPluginSource):
                await materialize_url(source, staging, alias=alias or "<unknown>")
            elif isinstance(source, LocalPluginSource):
                await materialize_local(source, staging, alias=alias or "<unknown>")
        except MaterializeError as exc:
            _cleanup(staging)
            raise PluginInstallError(alias or "<unknown>", exc) from exc

        try:
            manifest = parse_manifest(staging)
        except Exception as exc:
            _cleanup(staging)
            raise PluginInstallError(alias or "<unknown>", exc) from exc

        if manifest is None:
            _cleanup(staging)
            raise PluginInstallError(
                alias or "<unknown>",
                ValueError(
                    "No manifest found "
                    "(expected tachikoma-plugin.toml or .claude-plugin/plugin.json)"
                ),
            )

        resolved_alias = alias or manifest.name
        try:
            validate_alias(resolved_alias)
        except ValueError as exc:
            _cleanup(staging)
            raise PluginInstallError(resolved_alias, exc) from exc

        if (
            resolved_alias in self._loaded
            or resolved_alias in self._settings_manager.settings.plugins
        ):
            _cleanup(staging)
            raise PluginAliasCollisionError(
                resolved_alias,
                suggest_retry_with_alias=(alias is None),
            )

        target = install_dir / resolved_alias
        try:
            _atomic_replace_dir(staging, target)
        except Exception as exc:
            _cleanup(staging)
            raise PluginInstallError(resolved_alias, exc) from exc

        self._settings_manager.update_plugin_entry(resolved_alias, source)

        plugin = LoadedPlugin(
            alias=resolved_alias,
            source=source,
            manifest=manifest,
            status="loaded",
            diagnostic=None,
            plugin_dir=target,
        )
        self._loaded[resolved_alias] = plugin

        # Run init hook and subscribe events for the newly installed plugin.
        ctx = PluginContext(
            config=plugin.config,
            event_bus=self._bus,
            alias=resolved_alias,
            install_path=target,
        )
        if plugin.init_hook is not None:
            try:
                async with asyncio.timeout(30):
                    await _invoke_handler(plugin.init_hook, ctx)
                subscribe_plugin_events(plugin, self._bus, ctx)
            except TimeoutError:
                _log.bind(plugin=resolved_alias).warning(
                    "Init hook timed out after 30s during install"
                )
            except Exception:
                _log.bind(plugin=resolved_alias).exception(
                    "Init hook failed during install"
                )
        elif plugin.event_handlers:
            subscribe_plugin_events(plugin, self._bus, ctx)

        await self._bus.dispatch(
            PluginInstalled(alias=resolved_alias, plugin=plugin)
        )

        return plugin

    async def remove(self, alias: str) -> str | None:
        """Remove a plugin by alias.

        Under the manager's ``_lock``:
        1. Check existence.
        2. Collect namespaced skill names.
        3. Dispatch ``PluginRemoving`` (listener marks active session entries).
        4. Remove config entry.
        5. Remove install directory (best-effort; non-fatal on failure).
        6. Delete from ``_loaded``.
        7. Dispatch ``PluginRemoved``.

        Returns:
            A diagnostic string if directory removal failed, otherwise ``None``.

        Raises:
            PluginNotFoundError: If the alias is not installed.
        """
        async with self._lock:
            return await self._remove_locked(alias)

    async def _remove_locked(self, alias: str) -> str | None:
        plugin = self._loaded.get(alias)
        if plugin is None:
            raise PluginNotFoundError(alias)

        namespaced_skills = [s.qualified_name for s in plugin.contributed_skills]


        await self._bus.dispatch(
            PluginRemoving(alias=alias, namespaced_skill_names=namespaced_skills)
        )

        # Deactivate event wrappers so subsequent dispatches skip this plugin.
        if plugin.event_wrappers:
            unsubscribe_plugin_events(plugin.event_wrappers)

        rmtree_diagnostic: str | None = None
        try:
            self._settings_manager.remove_plugin_entry(alias)
        except KeyError:
            _log.bind(plugin=alias).warning(
                "Plugin alias not found in config during removal (may be stale-fallback)"
            )

        try:
            target = self._workspace_path / ".tachikoma" / "plugins" / alias
            if target.exists():
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, shutil.rmtree, target)
        except OSError as exc:
            rmtree_diagnostic = f"Directory removal failed: {exc}"
            _log.bind(plugin=alias).error(
                "Failed to remove plugin directory: {}", exc
            )

        del self._loaded[alias]


        await self._bus.dispatch(PluginRemoved(alias=alias))

        return rmtree_diagnostic


def _cleanup(path: Path) -> None:
    """Remove a staging directory, ignoring errors."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
