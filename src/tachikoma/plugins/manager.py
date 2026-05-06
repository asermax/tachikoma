"""Plugin manager: in-memory state, install/remove/list operations.

Owns the ``_loaded`` dict, serializes operations with an ``asyncio.Lock``, and
dispatches plugin lifecycle events on the bubus EventBus. Does NOT depend on
``SkillRegistry`` or ``session_registry`` — those are handled by the plugin-
event listener in the skills module.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from tachikoma.plugins.events import (
    PluginInstalled,
    PluginRemoved,
    PluginRemoving,
)
from tachikoma.plugins.lifecycle import (
    init_plugin,
    unsubscribe_plugin_events,
)
from tachikoma.plugins.loader import LoadedPlugin, _validate_handlers
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
from tachikoma.plugins.updater import UpdateResult, UpdateSummary

if TYPE_CHECKING:
    from bubus import EventBus

    from tachikoma.config import SettingsManager

from tachikoma.plugins.state import PluginState, PluginStateRepository

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
        state_repo: PluginStateRepository,
    ) -> None:
        self._settings_manager = settings_manager
        self._bus = bus
        self._workspace_path = workspace_path
        self._loaded: dict[str, LoadedPlugin] = dict(loaded)
        self._state_repo = state_repo
        self._lock = asyncio.Lock()
        self._update_locks: dict[str, asyncio.Lock] = {}

    def _get_update_lock(self, alias: str) -> asyncio.Lock:
        """Return (or create) the per-plugin update lock for *alias*."""
        return self._update_locks.setdefault(alias, asyncio.Lock())

    def _cleanup_update_lock(self, alias: str) -> None:
        """Remove the per-plugin update lock entry (called after plugin removal)."""
        self._update_locks.pop(alias, None)

    def list_plugins(self) -> list[LoadedPlugin]:
        """Return all loaded plugins (caller should not mutate)."""
        return list(self._loaded.values())

    def failed_plugins(self) -> list[LoadedPlugin]:
        """Return plugins that failed to load (status != "loaded")."""
        return [p for p in self._loaded.values() if p.status != "loaded"]

    async def update(self, alias: str) -> UpdateResult:
        """Update a single plugin.

        Re-materializes from source, atomic-swaps, persists new version,
        and re-registers the plugin's skills and events.

        Returns:
            An :class:`UpdateResult` indicating the outcome.
        """
        plugin = self._loaded.get(alias)
        if plugin is None:
            raise PluginNotFoundError(alias)

        if isinstance(plugin.source, LocalPluginSource):
            return UpdateResult(
                alias=alias,
                status="skipped",
                message="Local plugins are always current (symlink-based). No update needed.",
            )

        lock = self._get_update_lock(alias)
        if lock.locked():
            return UpdateResult(
                alias=alias,
                status="failed",
                error=f"Update already in progress for plugin '{alias}'",
            )

        async with lock:
            return await self._update_locked(alias)

    async def _update_locked(self, alias: str) -> UpdateResult:
        """Core update logic, called while holding the per-plugin lock."""
        plugin = self._loaded[alias]
        source = plugin.source
        install_dir = self._workspace_path / ".tachikoma" / "plugins"
        staging_root = install_dir / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)

        staging = staging_root / f"update-{alias}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

        try:
            if isinstance(source, GitPluginSource):
                result = await materialize_git(source, staging, alias=alias)
            elif isinstance(source, UrlPluginSource):
                result = await materialize_url(source, staging, alias=alias)
            else:
                return UpdateResult(
                    alias=alias,
                    status="skipped",
                    message="Unsupported source type",
                )
        except MaterializeError as exc:
            _cleanup(staging)
            return UpdateResult(alias=alias, status="failed", error=str(exc))

        # Parse manifest and validate handlers at the staging path.
        try:
            manifest = parse_manifest(staging)
        except Exception as exc:
            _cleanup(staging)
            return UpdateResult(alias=alias, status="failed", error=f"Manifest parse error: {exc}")

        if manifest is None:
            _cleanup(staging)
            return UpdateResult(
                alias=alias,
                status="failed",
                error="No manifest found in updated plugin",
            )

        init_hook_val = None
        event_handlers_val: dict = {}
        if manifest.source_format == "tachikoma" and (manifest.hooks or manifest.events):
            try:
                init_hook_val, event_handlers_val = _validate_handlers(manifest, staging, alias)
            except ValueError as exc:
                _cleanup(staging)
                return UpdateResult(
                    alias=alias,
                    status="failed",
                    error=f"Handler validation error: {exc}",
                )

        # URL content-hash comparison: skip if same content.
        if isinstance(source, UrlPluginSource):
            state = await self._state_repo.get(alias)
            if state is not None and state.installed_version == result.version:
                _cleanup(staging)
                return UpdateResult(
                    alias=alias,
                    status="skipped",
                    message="Plugin is already up-to-date.",
                )

        # Atomic swap.
        target = install_dir / alias
        try:
            _atomic_replace_dir(result.staging_dir, target)
        except Exception as exc:
            _cleanup(staging)
            return UpdateResult(alias=alias, status="failed", error=f"Atomic swap failed: {exc}")

        # Persist new version state.
        existing_state = await self._state_repo.get(alias)
        created_at = existing_state.created_at if existing_state else datetime.now(UTC)
        await self._state_repo.upsert(
            PluginState(
                alias=alias,
                installed_version=result.version,
                update_status="up-to-date",
                available_version=None,
                last_checked_at=None,
                diagnostic=None,
                created_at=created_at,
            )
        )

        # Re-register plugin (skills, events, init hook).
        new_plugin = LoadedPlugin(
            alias=alias,
            source=source,
            manifest=manifest,
            status="loaded",
            diagnostic=None,
            plugin_dir=target,
            init_hook=init_hook_val,
            event_handlers=event_handlers_val,
        )

        reregister_error = await self._reregister_plugin(plugin, new_plugin)

        if reregister_error is not None:
            # Re-registration failed: keep new version on disk, retain old skills/events.
            old_state = await self._state_repo.get(alias)
            await self._state_repo.upsert(
                PluginState(
                    alias=alias,
                    installed_version=result.version,
                    update_status=old_state.update_status if old_state else "unknown",
                    available_version=old_state.available_version if old_state else None,
                    last_checked_at=old_state.last_checked_at if old_state else None,
                    diagnostic=f"Re-registration failed: {reregister_error}",
                    created_at=old_state.created_at if old_state else created_at,
                )
            )
            return UpdateResult(
                alias=alias,
                status="failed",
                error=f"Re-registration failed: {reregister_error}",
            )

        return UpdateResult(alias=alias, status="updated")

    async def _reregister_plugin(
        self, old_plugin: LoadedPlugin, new_plugin: LoadedPlugin
    ) -> str | None:
        """Unregister old plugin and register the new one.

        Returns ``None`` on success, or an error string on failure.
        On failure, the old plugin's skills and events remain active.
        """
        try:
            # Signal that the old plugin is being removed.
            namespaced_skills = [s.qualified_name for s in old_plugin.contributed_skills]
            await self._bus.dispatch(
                PluginRemoving(alias=old_plugin.alias, namespaced_skill_names=namespaced_skills)
            )

            # Deactivate old event wrappers.
            if old_plugin.event_wrappers:
                unsubscribe_plugin_events(old_plugin.event_wrappers)

            # Remove old skills from registry.
            await self._bus.dispatch(PluginRemoved(alias=old_plugin.alias))

            # Store new plugin in loaded map.
            self._loaded[new_plugin.alias] = new_plugin

            # Register new skills and subscribe events.
            await init_plugin(new_plugin, self._bus)

            await self._bus.dispatch(PluginInstalled(alias=new_plugin.alias, plugin=new_plugin))

            return None
        except Exception as exc:
            # Re-registration failed: restore old plugin in loaded map.
            self._loaded[old_plugin.alias] = old_plugin
            _log.bind(plugin=old_plugin.alias).exception("Re-registration failed: {}", exc)
            return str(exc)

    async def update_all(self) -> UpdateSummary:
        """Update all plugins with available updates.

        Iterates all loaded plugins sequentially. Skips local plugins and
        those already up-to-date. Continues on individual failures.

        Returns:
            An :class:`UpdateSummary` with per-plugin results.
        """
        results: list[UpdateResult] = []

        for alias in list(self._loaded.keys()):
            plugin = self._loaded[alias]

            if isinstance(plugin.source, LocalPluginSource):
                results.append(
                    UpdateResult(
                        alias=alias,
                        status="skipped",
                        message="Local plugins are always current.",
                    )
                )
                continue

            state = await self._state_repo.get(alias)
            if state is not None and state.update_status != "update-available":
                results.append(
                    UpdateResult(
                        alias=alias,
                        status="skipped",
                        message="Plugin is already up-to-date.",
                    )
                )
                continue

            result = await self.update(alias)
            results.append(result)

        updated = sum(1 for r in results if r.status == "updated")
        skipped = sum(1 for r in results if r.status == "skipped")
        failed = sum(1 for r in results if r.status == "failed")

        return UpdateSummary(
            total=len(results),
            updated=updated,
            skipped=skipped,
            failed=failed,
            results=results,
        )

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

        # Validate handlers for Tachikoma-native plugins.
        init_hook_val = None
        event_handlers_val: dict = {}
        if manifest.source_format == "tachikoma" and (manifest.hooks or manifest.events):
            try:
                init_hook_val, event_handlers_val = _validate_handlers(
                    manifest, target, resolved_alias
                )
            except ValueError as exc:
                _log.bind(plugin=resolved_alias).warning(
                    "Handler validation failed during install: {}", exc
                )

        plugin = LoadedPlugin(
            alias=resolved_alias,
            source=source,
            manifest=manifest,
            status="loaded",
            diagnostic=None,
            plugin_dir=target,
            init_hook=init_hook_val,
            event_handlers=event_handlers_val,
        )
        self._loaded[resolved_alias] = plugin

        await init_plugin(plugin, self._bus)

        await self._bus.dispatch(PluginInstalled(alias=resolved_alias, plugin=plugin))

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
            if target.is_symlink():
                os.remove(target)
            elif target.exists():
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, shutil.rmtree, target)
        except OSError as exc:
            rmtree_diagnostic = f"Directory removal failed: {exc}"
            _log.bind(plugin=alias).error("Failed to remove plugin directory: {}", exc)

        del self._loaded[alias]

        self._cleanup_update_lock(alias)

        await self._bus.dispatch(PluginRemoved(alias=alias))

        return rmtree_diagnostic


def _cleanup(path: Path) -> None:
    """Remove a staging directory, ignoring errors."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
