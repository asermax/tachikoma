"""Plugin lifecycle: init hook execution, event subscription and unsubscription.

Provides ``run_plugin_init_hooks()`` for startup lifecycle (called from
``__main__.py`` after bootstrap), and subscription/unsubscription helpers
used by both startup and runtime install/remove flows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger

from tachikoma.plugins.context import PluginContext
from tachikoma.plugins.loader import LoadedPlugin

_log = logger.bind(component="plugins")

_INIT_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _invoke_handler(func: Callable, *args: Any) -> Any:
    """Call *func* with *args*, awaiting the result if it is a coroutine."""
    result = func(*args)
    if asyncio.iscoroutine(result):
        result = await result
    return result


# ---------------------------------------------------------------------------
# Init hook execution
# ---------------------------------------------------------------------------


async def init_plugin(plugin: LoadedPlugin, bus: Any) -> bool:
    """Run a single plugin's init hook and subscribe its event handlers.

    Returns ``True`` on success, ``False`` on failure (timeout or exception).
    On failure, event handlers are *not* subscribed.
    """
    ctx = PluginContext(
        config=plugin.config,
        event_bus=bus,
        alias=plugin.alias,
        install_path=plugin.plugin_dir,
    )

    if plugin.init_hook is not None:
        try:
            async with asyncio.timeout(_INIT_TIMEOUT_SECONDS):
                await _invoke_handler(plugin.init_hook, ctx)
            _log.bind(plugin=plugin.alias).info("Init hook completed")
            subscribe_plugin_events(plugin, bus, ctx)
            return True
        except TimeoutError:
            _log.bind(plugin=plugin.alias).warning(
                "Init hook timed out after {}s", _INIT_TIMEOUT_SECONDS
            )
            return False
        except Exception:
            _log.bind(plugin=plugin.alias).exception("Init hook failed")
            return False

    if plugin.event_handlers:
        subscribe_plugin_events(plugin, bus, ctx)
    return True


async def run_plugin_init_hooks(
    plugins: list[LoadedPlugin],
    bus: Any,
) -> None:
    """Run init hooks for all loaded plugins, then subscribe event handlers.

    Called from ``__main__.py`` after ``bootstrap.run()`` completes and before
    the channel starts.  Plugins are processed in alphabetical order by alias.
    Init hooks have a 30-second timeout and per-plugin error isolation.
    """
    loaded = sorted(
        [p for p in plugins if p.status == "loaded"],
        key=lambda p: p.alias,
    )

    successes = 0
    failures = 0

    for plugin in loaded:
        if await init_plugin(plugin, bus):
            successes += 1
        else:
            failures += 1

    _log.info(
        "Plugin init hooks complete: initialized={} failures={}",
        successes,
        failures,
    )


# ---------------------------------------------------------------------------
# Event subscription
# ---------------------------------------------------------------------------


def subscribe_plugin_events(
    plugin: LoadedPlugin,
    bus: Any,
    ctx: PluginContext,
) -> None:
    """Subscribe all plugin event handlers on the event bus.

    Each handler is wrapped for error isolation and PluginContext injection.
    Wrappers are appended to ``plugin.event_wrappers`` for later deactivation.
    """
    for event_type, handler in plugin.event_handlers.items():
        wrapper = _make_event_wrapper(handler, ctx, plugin.alias, event_type)
        bus.on(event_type, wrapper)
        plugin.event_wrappers.append(wrapper)
        _log.bind(plugin=plugin.alias).debug(
            "Subscribed to event: {}", event_type.__name__
        )


def _make_event_wrapper(
    handler: Callable,
    ctx: PluginContext,
    alias: str,
    event_type: type,
) -> Callable:
    """Create an error-isolated wrapper around an event handler.

    The wrapper:
    - Checks an ``_active`` flag (supports deactivation on plugin removal).
    - Passes the event payload and PluginContext to the handler.
    - Catches and logs exceptions without propagating them.
    """
    _active = True

    async def _wrapper(event: Any) -> None:
        if not _active:
            return
        try:
            await _invoke_handler(handler, event, ctx)
        except Exception:
            _log.bind(plugin=alias).exception(
                "Event handler failed: event={}", event_type.__name__
            )

    def _deactivate() -> None:
        nonlocal _active
        _active = False

    _wrapper._deactivate = _deactivate  # type: ignore[attr-defined]
    _wrapper._event_type = event_type  # type: ignore[attr-defined]
    return _wrapper


def unsubscribe_plugin_events(wrappers: list) -> None:
    """Deactivate all event wrappers for a plugin being removed.

    Since bubus has no ``off()`` API, deactivation sets the wrapper's
    internal ``_active`` flag to ``False`` so subsequent calls become no-ops.
    """
    for wrapper in wrappers:
        wrapper._deactivate()
