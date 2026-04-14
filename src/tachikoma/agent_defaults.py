"""Common defaults threaded through all SDK construction sites.

Groups cwd, cli_path, and env into a single frozen object so that adding
a new common option means changing one dataclass instead of 10+ signatures.
"""

from dataclasses import dataclass, field
from pathlib import Path

HARDCODED_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
"""Environment variables that are always set and cannot be overridden via config."""


@dataclass(frozen=True)
class AgentDefaults:
    """Default options passed to every ``ClaudeAgentOptions`` construction site.

    Attributes:
        cwd: Workspace directory for the agent.
        cli_path: Optional path to the Claude CLI binary (None = SDK bundled).
        env: Environment variables forwarded to CLI subprocesses.
        model: Default model for sub-agents (memory, summary, boundary, skills).
    """

    cwd: Path
    cli_path: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    model: str = "opus"


def agent_defaults_from_settings(settings) -> AgentDefaults:
    """Build AgentDefaults from application settings.

    Centralizes the merge_env + AgentDefaults construction pattern used
    across bootstrap hooks and __main__.py.

    Args:
        settings: Application settings (SettingsManager.settings).

    Returns:
        AgentDefaults with merged env and settings-derived values.
    """
    merged_env = merge_env(
        settings.agent.env,
        auto_injected={"TZ": settings.tasks.timezone},
    )

    return AgentDefaults(
        cwd=settings.workspace.path,
        cli_path=settings.agent.cli_path,
        env=merged_env,
        model=settings.agent.sub_agent_model,
    )


def merge_env(
    config_env: dict[str, str],
    *,
    auto_injected: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge user-provided env with auto-injected and hardcoded defaults.

    Layering order (highest priority wins): hardcoded > config_env > auto_injected.
    Auto-injected defaults are silently overridable by the user via config_env.
    Hardcoded defaults reject collisions with config_env (startup error).

    Args:
        config_env: Environment variables from the ``[agent.env]`` config section.
        auto_injected: Overridable defaults injected from runtime config (e.g. TZ).

    Returns:
        Merged dict with all layers applied.

    Raises:
        ValueError: If config_env contains keys that collide with hardcoded defaults.
    """
    collisions = set(HARDCODED_ENV) & set(config_env)

    if collisions:
        keys = ", ".join(sorted(collisions))
        raise ValueError(f"[agent.env] contains reserved keys: {keys}")

    return {**(auto_injected or {}), **config_env, **HARDCODED_ENV}
