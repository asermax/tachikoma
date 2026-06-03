"""Configuration system: typed settings backed by a TOML file.

Provides a single-file configuration system at ~/.config/tachikoma/config.toml.
All parameters have sensible defaults. A commented default config file is
auto-generated on first run.
"""

import sys
import tomllib
import types
from pathlib import Path
from typing import Any, Literal, Union, cast, get_args
from zoneinfo import ZoneInfo

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tachikoma.agent_defaults import SYSTEM_DISALLOWED_TOOLS
from tachikoma.plugins.sources import (
    GitPluginSource,
    LocalPluginSource,
    PluginSource,
    UrlPluginSource,
    parse_plugin_source,
    validate_alias,
)

CONFIG_PATH = Path.home() / ".config" / "tachikoma" / "config.toml"


def _detect_system_timezone() -> str:
    """Resolve the system timezone from /etc/localtime symlink.

    Extracts the IANA timezone name from the symlink target path.
    Falls back to "UTC" if resolution fails.
    """
    localtime = Path("/etc/localtime")

    try:
        target = localtime.resolve()
        target_str = str(target)

        # Extract everything after "zoneinfo/" in the path
        marker = "zoneinfo/"
        idx = target_str.find(marker)
        if idx != -1:
            return target_str[idx + len(marker) :]
    except Exception:
        pass

    return "UTC"


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
        default=["Read", "Glob", "Grep", "Edit(.claude/**)"],
        description="Tools the agent is allowed to use",
    )
    disallowed_tools: list[str] = Field(
        default=["AskUserQuestion"],
        validate_default=True,
        description="Tools the agent is blocked from using",
    )
    cli_path: str | None = Field(
        default=None,
        description="Path to claude binary (None = SDK bundled binary)",
    )
    searcher_model: str = Field(
        default="opus",
        description=(
            "Model for sub-agents doing smart retrieval "
            "(memory search, skills classification, boundary detection)"
        ),
    )
    processor_model: str = Field(
        default="haiku",
        description=(
            "Model for sub-agents doing mechanical post-processing "
            "(memory/context/git extraction, per-message summary, rebase resolver)"
        ),
    )
    classifier_model: str = Field(
        default="haiku",
        description="Model for sub-agents doing rule-based classification (task evaluator)",
    )
    session_resume_window: int = Field(
        default=86400,
        description="Lookup window for session resumption matching, in seconds (default: 1 day)",
    )
    session_idle_timeout: int = Field(
        default=900,
        description="Seconds of inactivity before auto-closing session (0 = disabled)",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables passed to all Claude SDK sessions",
    )

    @field_validator("disallowed_tools", mode="after")
    @classmethod
    def merge_system_disallowed_tools(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys([*v, *SYSTEM_DISALLOWED_TOOLS]))

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


class SendFileSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    extra_roots: list[Path] = Field(
        default_factory=list,
        description=(
            "Absolute roots outside workspace and system temp that send_file "
            "will also accept; entries may not exist at load time"
        ),
    )

    @field_validator("extra_roots", mode="before")
    @classmethod
    def _expand_home(cls, v: object) -> object:
        if not isinstance(v, list):
            return v

        return [Path(p).expanduser() if isinstance(p, (str, Path)) else p for p in v]

    @field_validator("extra_roots", mode="after")
    @classmethod
    def _validate_absolute(cls, v: list[Path]) -> list[Path]:
        non_absolute = [p for p in v if not p.is_absolute()]

        if non_absolute:
            names = ", ".join(f"'{p}'" for p in non_absolute)
            raise ValueError(f"extra_roots entries must be absolute paths: {names}")

        return v


class TelegramSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    bot_token: str = Field(
        description="Telegram bot token from @BotFather",
    )
    authorized_chat_id: int = Field(
        description="Authorized Telegram chat ID (only this user can interact with the bot)",
    )
    push_notifications: bool = Field(
        default=True,
        description="Enable post-response push notifications via copy+delete",
    )
    inbound_reactions: bool = Field(
        default=True,
        description="Enable interpreting emoji reactions as conversational turns",
    )
    send_file: SendFileSettings = Field(
        default_factory=SendFileSettings,
        description="send_file tool configuration",
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
    wait_timeout: int = Field(
        default=7200,
        description="Seconds a task may wait for user input before failing",
    )
    workflow_wait_timeout: int = Field(
        default=604800,
        description="Seconds a workflow step task may wait for user input (default 7 days)",
    )
    running_timeout: int = Field(
        default=1800,
        ge=60,
        description="Seconds a running task may execute before being marked stuck",
    )
    timezone: str = Field(
        default="",
        validate_default=True,
        description="Timezone for cron evaluation (empty = system tz)",
    )
    cleanup_retention_hours: int = Field(
        default=48,
        ge=0,
        description="Hours to retain completed one-shot task definitions before deletion",
    )

    @field_validator("timezone", mode="before")
    @classmethod
    def _resolve_empty_timezone(cls, v: object) -> object:
        if isinstance(v, str) and v == "":
            return _detect_system_timezone()
        return v

    @field_validator("timezone", mode="after")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except KeyError:
            raise ValueError(f"'{v}' is not a valid IANA timezone") from None
        return v


class PriorityTiming(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    idle_window_seconds: float = Field(
        description="Seconds the conversation must be idle before delivery",
    )
    max_hold_seconds: float | None = Field(
        default=None,
        description="Max seconds before force-delivery (None = never force-deliver)",
    )


class BufferSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    urgent: PriorityTiming = Field(
        default_factory=lambda: PriorityTiming(idle_window_seconds=30, max_hold_seconds=120),
    )
    normal: PriorityTiming = Field(
        default_factory=lambda: PriorityTiming(idle_window_seconds=120, max_hold_seconds=900),
    )
    low: PriorityTiming = Field(
        default_factory=lambda: PriorityTiming(idle_window_seconds=300, max_hold_seconds=None),
    )


class UpdatesSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = Field(
        default=True,
        description="Whether automatic update checking is enabled",
    )
    check_interval: int = Field(
        default=86400,
        ge=0,
        description="How often to check for updates in seconds (default: 86400 = once per day)",
    )


class MaintenanceSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = Field(
        default=True,
        description="Whether memory maintenance scheduler jobs are enabled",
    )
    schedule: str = Field(
        default="0 3 * * *",
        description="Cron expression for nightly maintenance (shared across all three jobs)",
    )
    recent_days: int = Field(
        default=15,
        ge=1,
        description="Days to keep as recent daily entries (cleaned for verbosity only)",
    )
    weekly_threshold_months: int = Field(
        default=3,
        ge=1,
        description="Months after which weekly summaries are consolidated into monthly",
    )
    monthly_threshold_months: int = Field(
        default=12,
        ge=1,
        description="Months after which monthly summaries are deleted",
    )


class MemorySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    maintenance: MaintenanceSettings = Field(
        default_factory=MaintenanceSettings,
        description="Memory maintenance configuration",
    )


class SchedulerSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    max_concurrent_low: int = Field(
        default=1,
        ge=1,
        description=(
            "Max concurrent low-priority scheduler jobs "
            "(default 1: low-priority jobs run sequentially). "
            "Heavy/agent-spawning system ticks register as low-priority; "
            "lightweight sweeps stay high-priority and bypass the limit."
        ),
    )


class DetachedProcessesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    default_memory_limit_mb: int | None = Field(
        default=None,
        ge=1,
        description="Default memory limit in MB for detached processes (None = no limit)",
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
    updates: UpdatesSettings = Field(default_factory=UpdatesSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    buffer: BufferSettings = Field(default_factory=BufferSettings)
    detached_processes: DetachedProcessesConfig = Field(
        default_factory=DetachedProcessesConfig,
        description="Detached process memory limiting configuration",
    )
    plugins: dict[str, PluginSource] = Field(
        default_factory=dict,
        description="Plugin sources indexed by alias ([plugins.<alias>] sub-tables)",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_plugins(cls, data: object) -> object:
        """Validate plugin aliases and parse source variants before field assignment."""
        if not isinstance(data, dict) or "plugins" not in data:
            return data

        raw = cast(dict[str, Any], data)
        plugins_raw = raw.get("plugins")
        if not isinstance(plugins_raw, dict):
            return data

        validated: dict[str, PluginSource] = {}
        for alias, value in plugins_raw.items():
            if not isinstance(alias, str):
                continue
            validate_alias(alias)
            if isinstance(value, dict):
                validated[alias] = parse_plugin_source(value)
            elif isinstance(value, (GitPluginSource, UrlPluginSource, LocalPluginSource)):
                # Already parsed (e.g., nested validation pass)
                validated[alias] = value
            else:
                validated[alias] = parse_plugin_source(
                    dict(value) if isinstance(value, dict) else {}
                )

        raw["plugins"] = validated
        return data


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
        if origin is Union or isinstance(section_model, types.UnionType):
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

    def update_plugin_entry(self, alias: str, source: PluginSource) -> None:
        """Add or update a ``[plugins.<alias>]`` sub-table in the config file.

        Validates the alias, ensures the ``[plugins]`` super-table exists,
        builds a child table from the source model, assigns it, and saves.
        Comments and formatting in surrounding sections are preserved.

        Args:
            alias: Plugin alias (must match ``[a-z0-9][a-z0-9-]*``).
            source: A validated :class:`PluginSource` instance.

        Raises:
            ValueError: If the alias is invalid.
        """
        validate_alias(alias)

        # Ensure [plugins] super-table exists
        if "plugins" not in self._doc:
            self._doc.add("plugins", tomlkit.table(is_super_table=True))

        plugins_table = cast(dict[str, Any], self._doc["plugins"])

        # Build child table from source model
        entry = tomlkit.table()
        dump = source.model_dump(exclude_none=True)

        # Ensure 'path' is serialized as string for TOML compatibility
        if "path" in dump and isinstance(dump["path"], Path):
            dump["path"] = str(dump["path"])

        for key, value in dump.items():
            entry.add(key, value)

        plugins_table[alias] = entry
        self.save()

    def remove_plugin_entry(self, alias: str) -> None:
        """Remove a ``[plugins.<alias>]`` sub-table from the config file.

        Deletes the sub-table; if the resulting ``[plugins]`` super-table is
        empty, removes the parent key for cleanliness. Comments and formatting
        in surrounding sections are preserved.

        Args:
            alias: The plugin alias to remove.

        Raises:
            KeyError: If the alias is not found in ``[plugins]``.
        """
        if "plugins" not in self._doc:
            raise KeyError(alias)

        plugins_table = cast(dict[str, Any], self._doc["plugins"])

        if alias not in plugins_table:
            raise KeyError(alias)

        del plugins_table[alias]

        # Collapse empty parent super-table
        if len(plugins_table) == 0:
            del self._doc["plugins"]

        self.save()


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

    agent_defaults = AgentSettings()

    for name, field_info in AgentSettings.model_fields.items():
        # env is a sub-table, handled separately below
        if name == "env":
            continue

        desc = field_info.description or ""
        default = getattr(agent_defaults, name)

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
        if name == "send_file":
            continue

        desc = field_info.description or ""
        default = field_info.default

        doc.add(tomlkit.comment(f"{desc}"))

        if name == "bot_token":
            doc.add(tomlkit.comment('bot_token = ""'))
        elif name == "authorized_chat_id":
            doc.add(tomlkit.comment("authorized_chat_id = 0"))
        elif isinstance(default, bool):
            doc.add(tomlkit.comment(f"{name} = {str(default).lower()}"))

    doc.add(tomlkit.nl())

    doc.add(tomlkit.comment("[telegram.send_file]"))
    extra_roots_desc = SendFileSettings.model_fields["extra_roots"].description or ""
    doc.add(tomlkit.comment(extra_roots_desc))
    doc.add(tomlkit.comment('extra_roots = ["~/exports"]'))

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

    doc.add(tomlkit.nl())

    # [updates] section
    doc.add(tomlkit.comment("[updates]"))

    for name, field_info in UpdatesSettings.model_fields.items():
        desc = field_info.description or ""
        default = field_info.default

        doc.add(tomlkit.comment(f"{desc}"))

        if isinstance(default, bool):
            doc.add(tomlkit.comment(f"{name} = {str(default).lower()}"))
        elif isinstance(default, int):
            doc.add(tomlkit.comment(f"{name} = {default}"))

    doc.add(tomlkit.nl())

    # [detached_processes] section
    doc.add(tomlkit.comment("[detached_processes]"))

    for name, field_info in DetachedProcessesConfig.model_fields.items():
        desc = field_info.description or ""
        suggested = 1024

        doc.add(tomlkit.comment(f"{desc}"))
        doc.add(tomlkit.comment(f"{name} = {suggested}"))

    doc.add(tomlkit.nl())

    # [buffer] section
    doc.add(tomlkit.comment("[buffer.urgent]"))
    doc.add(tomlkit.comment("Seconds the conversation must be idle before delivery"))
    doc.add(tomlkit.comment("idle_window_seconds = 30"))
    doc.add(tomlkit.comment("Max seconds before force-delivery (None = never force-deliver)"))
    doc.add(tomlkit.comment("max_hold_seconds = 120"))
    doc.add(tomlkit.nl())
    doc.add(tomlkit.comment("[buffer.normal]"))
    doc.add(tomlkit.comment("idle_window_seconds = 120"))
    doc.add(tomlkit.comment("max_hold_seconds = 900"))
    doc.add(tomlkit.nl())
    doc.add(tomlkit.comment("[buffer.low]"))
    doc.add(tomlkit.comment("idle_window_seconds = 300"))
    doc.add(tomlkit.comment("max_hold_seconds ="))

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
