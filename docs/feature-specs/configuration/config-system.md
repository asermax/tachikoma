# Configuration System

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A typed configuration system backed by a single TOML file at `~/.config/tachikoma/config.toml`. All tunable parameters live in this file with sensible defaults. On first run, a commented default config file is auto-generated so users can see what's configurable. The system validates all values at startup and provides clear error messages. Additionally, the system supports write-back — modules can update settings values and persist them to the config file while preserving comments and formatting.

The `ANTHROPIC_API_KEY` is not managed by this system — the Claude SDK reads it natively from the environment. Only Tachikoma-specific parameters go in the config file.

## User Stories

- As a developer deploying Tachikoma, I want all tunable parameters managed in a single configuration file so that I can customize behavior without modifying code

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Clean separation between operational configuration and code |
| R1 | All parameters managed via a single TOML config file at `~/.config/tachikoma/config.toml` |
| R2 | Sensible defaults for all non-secret parameters |
| R3 | Startup validation with clear error messages for missing/invalid values |
| R4 | Auto-generate a commented default config file when none exists |
| R5 | Easy extensibility — adding new config parameters is low-friction |
| R6 | Write-back capability: modules can update and persist configuration values at runtime |
| R7 | Telegram configuration: optional `[telegram]` section for bot token and authorized chat ID |
| R8 | CLI override capability: runtime-only overrides via CLI flags without file persistence |
| R9 | Task scheduler configuration: `[tasks]` section for idle window, check interval, max iterations, max concurrent background, and timezone |

## Behaviors

### Configuration Loading (R1, R2)

The system loads parameters from the TOML config file at startup, applying defaults for any unspecified values. Supported sections include `[workspace]`, `[agent]`, `[logging]`, and `[telegram]`.

**Acceptance Criteria**:
- Given a valid TOML config file, when the application starts, then all parameters are loaded and available to components
- Given a config file with no `[workspace]` section, when loaded, then `workspace.path` defaults to `~/tachikoma`
- Given a config file with no `[agent]` section, when loaded, then `agent.model` defaults to `None` (SDK default), `agent.sub_agent_model` defaults to `"opus"` (sub-agent default), `agent.allowed_tools` defaults to `["Read", "Glob", "Grep"]`, `agent.disallowed_tools` defaults to `["AskUserQuestion"]`, `agent.cli_path` defaults to `None` (SDK bundled binary), `agent.session_resume_window` defaults to `86400` (1 day in seconds), `agent.session_idle_timeout` defaults to `900` (15 min; 0 disables idle close), and `agent.env` defaults to `{}` (empty dict)
- Given a config file with an `[agent.env]` section containing string key-value pairs, when loaded, then `agent.env` contains those values
- Given a config file with an `[agent.env]` section containing non-string values (e.g., `FOO = 42`), when the application starts, then it exits with a clear validation error
- Given a config file with no `[logging]` section, when loaded, then `logging.level` defaults to `"INFO"` and `logging.console` defaults to `false`
- Given a completely empty config file, when loaded, then all non-secret parameters use their defaults and the application starts successfully

### Startup Validation (R3)

The system validates all configuration values at startup, exiting with clear error messages on failure.

**Acceptance Criteria**:
- Given a config file with an invalid value (e.g. wrong type), when the application starts, then it exits with a clear error naming the field and expected type
- Given a config file with unknown keys, when loaded, then the unknown keys are silently ignored (forward-compatible)
- Given invalid TOML syntax, when the application starts, then it exits with a clear parse error
- Given the config file is not readable (permission denied), when the application starts, then it exits with a clear error
- Given the config path is not a regular file, when the application starts, then it exits with a clear error

### Auto-Generation (R4)

When no config file exists, the system creates a commented default file so users can discover what's configurable.

**Acceptance Criteria**:
- Given no config file exists, when the application starts, then a default config file is created with all parameters commented out and annotated
- Given no config directory exists, when the application starts, then the directory is created before writing the default config
- Given the config directory cannot be created (permission denied), when the application starts, then it exits with a clear error
- Given a config file already exists, when the application starts, then it is loaded as-is (never overwritten)

### Settings Write-Back (R6)

Modules can update settings values in memory and persist them to the TOML config file. Write-back preserves existing comments and formatting. This enables bootstrap hooks and other modules to prompt users for values and save them.

**Acceptance Criteria**:
- Given a module updates a setting value, when the change is saved, then the TOML config file is updated while preserving existing comments and formatting
- Given a setting is updated and saved, when settings are subsequently read, then the new value is reflected
- Given a module attempts to update a non-existent section or key, then a clear error is raised
- Given multiple settings are updated before saving, when save is called, then all changes are persisted in a single write

### Extensibility (R5)

Adding new configuration parameters is low-friction and backward-compatible.

**Acceptance Criteria**:
- Given a settings model with a new field that has a default value, when a config file without that field is loaded, then the new field uses its default without error

### Telegram Configuration (R7)

The optional `[telegram]` section configures the Telegram bot channel. When the section is absent, `settings.telegram` is None. When present, both fields are required.

**Acceptance Criteria**:
- Given a config file with a `[telegram]` section, when loaded, then `telegram.bot_token` and `telegram.authorized_chat_id` are available
- Given a config file with no `[telegram]` section, when loaded, then `settings.telegram` is None
- Given a config file with a `[telegram]` section missing a required field, when loaded, then validation fails with a clear error
- Given the auto-generated default config, when created, then the `[telegram]` section is included (commented out) with annotations

### Task Scheduler Configuration (R9)

The `[tasks]` section configures task scheduler parameters. Unlike `[telegram]`, `settings.tasks` always has a default value (never None) — the task subsystem operates with sensible defaults when no `[tasks]` section is present.

**Acceptance Criteria**:
- Given a config file with no `[tasks]` section, when loaded, then `settings.tasks` is populated with default values: `idle_window=300`, `check_interval=300`, `max_iterations=10`, `max_concurrent_background=3`, `timezone=None` (system timezone)
- Given a config file with a `[tasks]` section specifying custom values, when loaded, then those values override the defaults

### CLI Override (R8)

CLI flags can override configuration values at runtime without modifying the config file. Overrides apply via `SettingsManager.update_root()` followed by `reload()`.

**Acceptance Criteria**:
- Given a `--channel telegram` flag, when the application starts, then `settings.channel` is "telegram" for that session regardless of TOML config
- Given a CLI override, when the application is running, then the override value is used but the config file is not modified
- Given a CLI override is applied, when `settings_manager.reload()` is called, then the frozen Settings snapshot reflects the merged result
