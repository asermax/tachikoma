"""Bootstrap: formal workspace initialization with named hooks.

Provides a registry-based bootstrap system that runs idempotent hooks
on every launch. Hooks self-determine whether they need to act.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from tachikoma.config import SettingsManager

_log = logger.bind(component="bootstrap")


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
            _log.debug("Running hook: name={name}", name=name)

            try:
                await hook(self._context)
            except Exception as exc:
                # Walk the cause chain so the originating error (e.g. a SQL
                # OperationalError wrapped in a domain exception) is visible
                # in the message, not just the outermost layer.
                causes = []
                cause: BaseException | None = exc.__cause__
                while cause is not None:
                    causes.append(f"{type(cause).__name__}: {cause}")
                    cause = cause.__cause__

                detail = f"{exc}"
                if causes:
                    detail += " (caused by " + " <- ".join(causes) + ")"

                raise BootstrapError(f"Hook '{name}' failed: {detail}") from exc

            _log.debug("Hook completed: name={name}", name=name)
