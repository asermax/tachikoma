"""Bootstrap: formal workspace initialization with named hooks.

Provides a registry-based bootstrap system that runs idempotent hooks
on every launch. Hooks self-determine whether they need to act.
"""

from collections.abc import Callable
from dataclasses import dataclass

from tachikoma.config import SettingsManager


class BootstrapError(Exception):
    """Raised when a bootstrap hook fails. Names the failing hook."""


@dataclass(frozen=True)
class BootstrapContext:
    settings_manager: SettingsManager
    prompt: Callable[[str], str]


BootstrapHook = Callable[[BootstrapContext], None]


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

    def register(self, name: str, hook: BootstrapHook) -> None:
        self._hooks.append((name, hook))

    def run(self) -> None:
        for name, hook in self._hooks:
            try:
                hook(self._context)
            except Exception as exc:
                raise BootstrapError(f"Hook '{name}' failed: {exc}") from exc


def workspace_hook(ctx: BootstrapContext) -> None:
    """Create workspace root and .tachikoma/ data folder if missing."""
    settings = ctx.settings_manager.settings
    workspace_path = settings.workspace.path

    if workspace_path.exists() and not workspace_path.is_dir():
        raise RuntimeError(
            f"Workspace path exists but is not a directory: {workspace_path}"
        )

    if not workspace_path.exists():
        try:
            workspace_path.mkdir(parents=True)
        except PermissionError as exc:
            raise RuntimeError(
                f"Cannot create workspace directory: Permission denied: {workspace_path}"
            ) from exc

    data_path = settings.workspace.data_path

    if not data_path.exists():
        try:
            data_path.mkdir()
        except PermissionError as exc:
            raise RuntimeError(
                f"Cannot create data directory: Permission denied: {data_path}"
            ) from exc
