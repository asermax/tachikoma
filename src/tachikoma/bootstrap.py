"""Bootstrap: formal workspace initialization with named hooks.

Provides a registry-based bootstrap system that runs idempotent hooks
on every launch. Hooks self-determine whether they need to act.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from tachikoma.config import SettingsManager


class BootstrapError(Exception):
    """Raised when a bootstrap hook fails. Names the failing hook."""


@dataclass(frozen=True)
class BootstrapContext:
    settings_manager: SettingsManager
    prompt: Callable[[str], str]
    # Mutable bag for hooks to pass objects back to the caller.
    # frozen=True prevents swapping the dict itself; dict contents are freely mutable.
    extras: dict[str, Any] = field(default_factory=dict)


BootstrapHook = Callable[[BootstrapContext], Awaitable[None]]


class Bootstrap:
    """Registry for named bootstrap hooks, executed in registration order."""

    def __init__(
        self,
        settings_manager: SettingsManager,
        prompt: Callable[[str], str] = input,
    ) -> None:
        self._context = BootstrapContext(
            settings_manager=settings_manager,
            prompt=prompt,
        )
        self._hooks: list[tuple[str, BootstrapHook]] = []

    @property
    def extras(self) -> dict[str, Any]:
        """Expose the context extras bag so callers can retrieve hook outputs."""
        return self._context.extras

    def register(self, name: str, hook: BootstrapHook) -> None:
        self._hooks.append((name, hook))

    async def run(self) -> None:
        for name, hook in self._hooks:
            try:
                await hook(self._context)
            except Exception as exc:
                raise BootstrapError(f"Hook '{name}' failed: {exc}") from exc


async def workspace_hook(ctx: BootstrapContext) -> None:
    """Create workspace root and .tachikoma/ data folder if missing."""
    settings = ctx.settings_manager.settings
    workspace_path = settings.workspace.path

    try:
        workspace_path.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        raise RuntimeError(
            f"Workspace path exists but is not a directory: {workspace_path}"
        )
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot create workspace directory: Permission denied: {workspace_path}"
        ) from exc

    try:
        settings.workspace.data_path.mkdir(exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot create data directory: Permission denied: {settings.workspace.data_path}"
        ) from exc
