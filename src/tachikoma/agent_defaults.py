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


def merge_env(config_env: dict[str, str]) -> dict[str, str]:
    """Merge user-provided env with hardcoded defaults, rejecting collisions.

    Args:
        config_env: Environment variables from the ``[agent.env]`` config section.

    Returns:
        Merged dict with hardcoded defaults and config values.

    Raises:
        ValueError: If config_env contains keys that collide with hardcoded defaults.
    """
    collisions = set(HARDCODED_ENV) & set(config_env)

    if collisions:
        keys = ", ".join(sorted(collisions))
        raise ValueError(
            f"[agent.env] contains reserved keys: {keys}"
        )

    return {**HARDCODED_ENV, **config_env}
