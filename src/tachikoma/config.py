"""Configuration system: typed settings backed by a TOML file.

Provides a single-file configuration system at ~/.config/tachikoma/config.toml.
All parameters have sensible defaults. A commented default config file is
auto-generated on first run.
"""

import sys
import tomllib
from pathlib import Path
from typing import Any, Literal, Union, cast, get_args

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIG_PATH = Path.home() / ".config" / "tachikoma" / "config.toml"


class WorkspaceSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    path: Path = Field(
        default="~/tachikoma",
        validate_default=True,
        description="Root directory for agent workspace data",
    )

    @field_validator("path", mode="before")
    @classmethod
    def expand_home(cls, v: object) -> object:
        if isinstance(v, str):
            return Path(v).expanduser()

        if isinstance(v, Path):
            return v.expanduser()

        return v

    @property
    def data_path(self) -> Path:
        return self.path / ".tachikoma"


class AgentSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    model: str | None = Field(
        default="opus",
        description="Claude model to use (None = SDK default)",
    )
    allowed_tools: list[str] = Field(
        default=["Read", "Glob", "Grep"],
        description="Tools the agent is allowed to use",
    )
    cli_path: str | None = Field(
        default=None,
        description="Path to claude binary (None = SDK bundled binary)",
    )
    sub_agent_model: str = Field(
        default="opus",
        description="Model used by background sub-agents (memory, summary, boundary, skills)",
    )
    session_resume_window: int = Field(
        default=86400,
        description="Lookup window for session resumption matching, in seconds (default: 1 day)",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables passed to all Claude SDK sessions",
    )

    @field_validator("env", mode="before")
    @classmethod
    def validate_env_values(cls, v: object) -> object:
        if not isinstance(v, dict):
            return v

        non_string = {k: type(val).__name__ for k, val in v.items() if not isinstance(val, str)}

        if non_string:
            details = ", ".join(f"{k} ({t})" for k, t in non_string.items())
            raise ValueError(f"All env values must be strings, got non-string values: {details}")

        return v


class LoggingSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    console: bool = Field(
        default=False,
        description="Enable colored stderr output for development",
    )


class TelegramSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    bot_token: str = Field(
        description="Telegram bot token from @BotFather",
    )
    authorized_chat_id: int = Field(
        description="Authorized Telegram chat ID (only this user can interact with the bot)",
    )


class TaskSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    idle_window: int = Field(
        default=300,
        description="Seconds before session tasks fire (user must be idle)",
    )
    check_interval: int = Field(
        default=300,
        description="Session task check interval in seconds",
    )
    max_iterations: int = Field(
        default=10,
        description="Max evaluator iterations for background tasks",
    )
    max_concurrent_background: int = Field(
        default=3,
        description="Max concurrent background tasks",
    )
    timezone: str = Field(
        default="",
        description="Timezone for cron evaluation (empty = system tz)",
    )


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    channel: Literal["repl", "telegram"] = Field(
        default="repl",
        description="Communication channel to use (repl or telegram)",
    )
    telegram: TelegramSettings | None = Field(
        default=None,
        description="Telegram bot configuration (required when channel is telegram)",
    )
    tasks: TaskSettings = Field(default_factory=TaskSettings)


class SettingsManager:
    """Read-write access to the configuration system.

    Wraps config loading and provides update()/save() for persisting
    changes back to the TOML file while preserving comments and formatting.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path if config_path is not None else CONFIG_PATH
        self._settings = load_settings(self._config_path)
        self._doc = tomlkit.parse(self._config_path.read_text())

    @property
    def settings(self) -> Settings:
        return self._settings

    def update(self, section: str, key: str, value: object) -> None:
        """Modify a setting value in memory. Call save() to persist."""
        if section not in Settings.model_fields:
            raise KeyError(f"Unknown section: {section}")

        section_model = Settings.model_fields[section].annotation

        # Unwrap union types (e.g., TelegramSettings | None -> TelegramSettings)
        origin = getattr(section_model, "__origin__", None)
        if origin is Union:
            # Extract the non-None type from the union
            args = get_args(section_model)
            section_model = next((arg for arg in args if arg is not type(None)), section_model)

        if key not in section_model.model_fields:
            raise KeyError(f"Unknown key '{key}' in section '{section}'")

        if section not in self._doc:
            self._doc.add(section, tomlkit.table())

        cast(dict[str, Any], self._doc[section])[key] = value

    def update_root(self, key: str, value: object) -> None:
        """Modify a root-level setting value in memory. Call reload() to apply.

        Use for runtime-only overrides (no file persistence).
        """
        if key not in Settings.model_fields:
            raise KeyError(f"Unknown root key: {key}")

        field_info = Settings.model_fields[key]

        # Prevent using update_root for section-level fields (BaseModel subclasses)
        if hasattr(field_info.annotation, "model_fields"):
            raise KeyError(
                f"'{key}' is a section, use update('{key}', ...) instead of update_root()"
            )

        self._doc[key] = value

    def reload(self) -> None:
        """Reload settings from the in-memory TOML document without file I/O.

        Use after update_root() for runtime-only changes.
        """
        # Convert tomlkit document to dict and re-validate
        data = dict(self._doc)
        self._settings = Settings.model_validate(data)

    def save(self) -> None:
        """Write current state to the config file and reload settings."""
        self._config_path.write_text(tomlkit.dumps(self._doc))
        self._settings = load_settings(self._config_path)


def _generate_default_config(config_path: Path = CONFIG_PATH) -> None:
    """Generate a commented default config file with all parameters documented.

    The generated file parses to an empty dict (all values are comments),
    so all defaults apply when loaded.
    """
    doc = tomlkit.document()
    doc.add(tomlkit.comment("Tachikoma configuration file"))
    doc.add(tomlkit.comment("Uncomment and modify values to override defaults."))
    doc.add(tomlkit.nl())

    # [workspace] section
    doc.add(tomlkit.comment("[workspace]"))

    for name, field_info in WorkspaceSettings.model_fields.items():
        desc = field_info.description or ""
        default = field_info.default

        doc.add(tomlkit.comment(f"{desc}"))
        doc.add(tomlkit.comment(f'{name} = "{default}"'))

    doc.add(tomlkit.nl())

    # [agent] section
    doc.add(tomlkit.comment("[agent]"))

    for name, field_info in AgentSettings.model_fields.items():
        # env is a sub-table, handled separately below
        if name == "env":
            continue

        desc = field_info.description or ""
        default = field_info.default

        doc.add(tomlkit.comment(f"{desc}"))

        if isinstance(default, list):
            items = ", ".join(f'"{item}"' for item in default)
            doc.add(tomlkit.comment(f"{name} = [{items}]"))
        elif isinstance(default, bool):
            doc.add(tomlkit.comment(f"{name} = {str(default).lower()}"))
        elif isinstance(default, int):
            doc.add(tomlkit.comment(f"{name} = {default}"))
        elif default is None:
            doc.add(tomlkit.comment(f"{name} ="))
        else:
            doc.add(tomlkit.comment(f'{name} = "{default}"'))

    doc.add(tomlkit.nl())

    # [agent.env] sub-table
    doc.add(tomlkit.comment("[agent.env]"))
    doc.add(tomlkit.comment("Extra environment variables passed to all Claude SDK sessions"))
    doc.add(tomlkit.comment('FOO = "bar"'))

    doc.add(tomlkit.nl())

    # [logging] section
    doc.add(tomlkit.comment("[logging]"))

    for name, field_info in LoggingSettings.model_fields.items():
        desc = field_info.description or ""
        default = field_info.default

        doc.add(tomlkit.comment(f"{desc}"))

        if isinstance(default, bool):
            doc.add(tomlkit.comment(f"{name} = {str(default).lower()}"))
        else:
            doc.add(tomlkit.comment(f'{name} = "{default}"'))

    doc.add(tomlkit.nl())

    # Root-level channel field
    doc.add(tomlkit.comment("Communication channel to use (repl or telegram)"))
    doc.add(tomlkit.comment('channel = "repl"'))

    doc.add(tomlkit.nl())

    # [telegram] section
    doc.add(tomlkit.comment("[telegram]"))

    for name, field_info in TelegramSettings.model_fields.items():
        desc = field_info.description or ""

        doc.add(tomlkit.comment(f"{desc}"))

        if name == "bot_token":
            doc.add(tomlkit.comment('bot_token = ""'))
        elif name == "authorized_chat_id":
            doc.add(tomlkit.comment("authorized_chat_id = 0"))

    doc.add(tomlkit.nl())

    # [tasks] section
    doc.add(tomlkit.comment("[tasks]"))

    for name, field_info in TaskSettings.model_fields.items():
        desc = field_info.description or ""
        default = field_info.default

        doc.add(tomlkit.comment(f"{desc}"))

        if isinstance(default, int):
            doc.add(tomlkit.comment(f"{name} = {default}"))
        elif isinstance(default, str):
            doc.add(tomlkit.comment(f'{name} = "{default}"'))

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(
            f"Cannot create config directory: Permission denied: {config_path.parent}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    config_path.write_text(tomlkit.dumps(doc))


def _format_validation_error(err: ValidationError) -> str:
    """Format a Pydantic ValidationError into a user-friendly message."""
    parts = []

    for error in err.errors():
        loc = " -> ".join(str(part) for part in error["loc"])
        msg = error["msg"]
        value = error.get("input")
        parts.append(f"  {loc}: {msg} (got {value!r})")

    return "Configuration error:\n" + "\n".join(parts)


def load_settings(config_path: Path | None = None) -> Settings:
    """Load and validate settings from the TOML config file.

    If no config file exists, generates a commented default file first.
    Exits with a clear error message on any failure.
    """
    path = config_path if config_path is not None else CONFIG_PATH

    if not path.exists():
        _generate_default_config(path)

    elif not path.is_file():
        print(
            f"Config path is not a regular file: {path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except PermissionError:
        print(
            f"Cannot read config file: Permission denied: {path}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except tomllib.TOMLDecodeError as e:
        print(
            f"Invalid TOML in config file {path}: {e}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        return Settings.model_validate(data)
    except ValidationError as e:
        print(_format_validation_error(e), file=sys.stderr)
        raise SystemExit(1)
