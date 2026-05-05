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
| R10 | Skill and script configuration: per-skill config directories under `.tachikoma/config/<skill-name>/` |
| R11 | Update checking configuration: `[updates]` section for enabled flag and check interval |
| R12 | Configuration disambiguation: system prompt must distinguish between Tachikoma config (TOML file) and Claude Code config (settings.json) so the agent routes settings requests to the correct system |
| R13 | Plugin source declaration: `[plugins.<alias>]` sections in TOML config, each declaring a git/url/local source with alias validation and discriminated-union type enforcement |
| R14 | Plugin user config values: `[plugins.<alias>.config]` sub-table for plugin-specific configuration values, extracted during plugin source validation and attached to source models |

## Behaviors

### Configuration Loading (R1, R2)

The system loads parameters from the TOML config file at startup, applying defaults for any unspecified values. Supported sections include `[workspace]`, `[agent]`, `[logging]`, `[telegram]`, `[tasks]`, `[updates]`, and `[plugins.<alias>]`.

**Acceptance Criteria**:
- Given a valid TOML config file, when the application starts, then all parameters are loaded and available to components
- Given a config file with no `[workspace]` section, when loaded, then `workspace.path` defaults to `~/tachikoma`
- Given a config file with no `[agent]` section, when loaded, then `agent.model` defaults to `None` (SDK default), `agent.searcher_model` defaults to `"opus"` (smart retrieval: memory search, skills classification, boundary detection), `agent.processor_model` defaults to `"haiku"` (mechanical post-processing: memory/context/git extraction, per-message summary, rebase resolver), `agent.classifier_model` defaults to `"haiku"` (rule-based classification: task evaluator), `agent.allowed_tools` defaults to `["Read", "Glob", "Grep"]`, `agent.disallowed_tools` effectively defaults to `["AskUserQuestion", "CronCreate", "CronDelete", "CronList", "Skill"]` (user default merged with system-blocked tools), `agent.cli_path` defaults to `None` (SDK bundled binary), `agent.session_resume_window` defaults to `86400` (1 day in seconds), `agent.session_idle_timeout` defaults to `900` (15 min; 0 disables idle close), and `agent.env` defaults to `{}` (empty dict)
- Given a config file with an `[agent.env]` section containing string key-value pairs, when loaded, then `agent.env` contains those values
- Given a config file with an `[agent.env]` section containing non-string values (e.g., `FOO = 42`), when the application starts, then it exits with a clear validation error
- Given a config file with no `[logging]` section, when loaded, then `logging.level` defaults to `"INFO"` and `logging.console` defaults to `false`
- Given a completely empty config file, when loaded, then all non-secret parameters use their defaults and the application starts successfully
- Given a config file with custom `agent.disallowed_tools` (e.g., `["AskUserQuestion", "WebSearch"]`), when loaded, then the final list contains all user entries plus system-blocked tools (e.g., `["AskUserQuestion", "WebSearch", "Skill"]`)
- Given a config file with `agent.disallowed_tools = []`, when loaded, then system-blocked tools are still present in the final list
- Given a config file where the user already includes a system-blocked tool in `agent.disallowed_tools`, when loaded, then no duplicate entries exist in the final list

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
- Given a config file with no `[tasks]` section, when loaded, then `settings.tasks` is populated with default values: `idle_window=300`, `check_interval=300`, `max_iterations=10`, `max_concurrent_background=3`, `timezone` auto-detects the system's IANA timezone key (e.g. `America/Buenos_Aires`) when not explicitly configured
- Given a config file with a `[tasks]` section specifying custom values, when loaded, then those values override the defaults
- Given a config file with `tasks.timezone` set to an invalid value (e.g. `"Fake/Timezone"`), when the application starts, then it exits with a clear validation error
- Given the configured timezone value, when the agent environment is constructed, then `TZ` is set to that value in the subprocess environment (auto-injected as overridable default; see core-architecture R8)

### Update Checking Configuration (R11)

The `[updates]` section configures automatic update checking. Like `[tasks]`, `settings.updates` always has a default value — update checking operates with sensible defaults when no `[updates]` section is present.

**Acceptance Criteria**:
- Given a config file with no `[updates]` section, when loaded, then `settings.updates` is populated with default values: `enabled=true`, `check_interval=86400` (once per day)
- Given a config file with a `[updates]` section specifying custom values, when loaded, then those values override the defaults
- Given a config file with `updates.check_interval` set to a negative value, when the application starts, then it exits with a clear validation error
- Given the auto-generated default config, when created, then the `[updates]` section is included (commented out) with annotations

CLI flags can override configuration values at runtime without modifying the config file. Overrides apply via `SettingsManager.update_root()` followed by `reload()`.

**Acceptance Criteria**:
- Given `tachikoma run --channel telegram`, when the application starts, then `settings.channel` is "telegram" for that session regardless of TOML config
- Given a CLI override, when the application is running, then the override value is used but the config file is not modified
- Given a CLI override is applied, when `settings_manager.reload()` is called, then the frozen Settings snapshot reflects the merged result

### Skill and Script Configuration (R10)

Skills and scripts that need configuration store it in per-skill subdirectories under `.tachikoma/config/` within the workspace. This is distinct from the main application config at `~/.config/tachikoma/config.toml` — the main config holds operational parameters for Tachikoma itself, while `.tachikoma/config/` holds skill-specific settings that vary per workspace.

**Directory structure**:
```
.tachikoma/config/
├── my-skill/
│   └── config.toml
└── another-skill/
    └── config.toml
```

**Conventions**:
- Each skill gets its own subdirectory named after the skill (matching the skill folder name)
- Configuration files use TOML format (loadable via `tomllib` from stdlib)
- The primary config file is named `config.toml`
- Skills must create their config directory on first use if it doesn't exist

**Separation from state**: Configuration (`.tachikoma/config/`) holds user-defined settings. Runtime state (`.tachikoma/state/`) holds data the skill produces during operation. Skills must not read or write inside their own skill directory — skill directories are authoring artifacts.

**Acceptance Criteria**:
- Given a skill that needs configuration, when the skill runs, then it reads config from `.tachikoma/config/<skill-name>/config.toml`
- Given a skill's config directory doesn't exist, when the skill runs for the first time, then it creates the directory and any default config file
- Given the main application config at `~/.config/tachikoma/config.toml`, when a skill reads config, then it does not read from or write to the main config file
- Given a skill that needs persistent state, when the skill writes state, then it writes to `.tachikoma/state/<skill-name>/` not `.tachikoma/config/<skill-name>/`

### Plugin Configuration (R13)

Plugin sources are declared as `[plugins.<alias>]` sub-tables in the TOML config file. Each sub-table describes one plugin source — a discriminated union of git, url, or local variants. The alias must match `[a-z0-9][a-z0-9-]*`. The `SettingsManager` provides dedicated methods for adding and removing plugin entries while preserving comments in the config file.

**Source variants**:
- **Git**: `git` key (URL or `gh:owner/repo` / `github:owner/repo` shorthand), optional `subdir`, required `ref` (branch, tag, or commit — SHA-shaped refs rejected at validation)
- **URL**: `url` key (HTTPS-only with recognized archive extension `.tar.gz`/`.tgz`/`.zip`), optional `subdir`
- **Local**: `path` key (absolute path to local directory)

**Acceptance Criteria**:
- Given a config file with no `[plugins]` section, when loaded, then `settings.plugins` is an empty dict
- Given a config file with `[plugins.my-plugin]` containing a valid git source, when loaded, then `settings.plugins["my-plugin"]` is a `GitPluginSource` with the parsed fields
- Given a config file with `[plugins.my-plugin]` containing a valid url source, when loaded, then `settings.plugins["my-plugin"]` is a `UrlPluginSource`
- Given a config file with `[plugins.my-plugin]` containing a valid local source, when loaded, then `settings.plugins["my-plugin"]` is a `LocalPluginSource`
- Given a plugin alias that doesn't match `[a-z0-9][a-z0-9-]*`, when loaded, then validation fails with a clear error
- Given a `[plugins.<alias>]` entry with more than one of `git`/`url`/`path`, when loaded, then validation fails with a clear error indicating mutual exclusion
- Given a `[plugins.<alias>]` entry with none of `git`/`url`/`path`, when loaded, then validation fails with a clear error indicating at least one source is required
- Given a git source with a SHA-shaped ref (40 hex chars), when loaded, then validation fails with a clear error (shallow clone requires a ref resolvable to a branch or tag)
- Given `settings_manager.update_plugin_entry(alias, source)` is called, when saved, then the `[plugins.<alias>]` sub-table is created or replaced in the TOML file while preserving comments
- Given `settings_manager.remove_plugin_entry(alias)` is called, when saved, then the `[plugins.<alias>]` sub-table is removed from the TOML file while preserving surrounding comments and other plugin entries

### Plugin User Config (R14)

The `[plugins.<alias>]` sub-table may contain an optional `config` sub-table holding user-provided configuration values for the plugin. These values are raw TOML values carried on the source model and validated against the plugin's declared config schema during discovery (see plugin-loading spec, R15).

**Acceptance Criteria**:
- Given `[plugins.weather.config]` with `api_key = "sk-..."` and `timeout = 60`, when loaded, the weather plugin's source model has `config = {"api_key": "sk-...", "timeout": 60}`
- Given a plugin alias with no `.config` sub-table, when loaded, the source model has `config = None`
- Given `[plugins.weather]` with source fields (`git`, `ref`) and a `[plugins.weather.config]` sub-table, when parsed, source fields and config values are cleanly separated — source fields drive source model construction, config values populate the `config` field

### Configuration Disambiguation (R12)

The agent distinguishes between Tachikoma's TOML config and Claude Code's settings system, routing settings requests to the correct mechanism based on which category is being configured. Tachikoma categories (workspace, agent, logging, telegram, tasks, updates, skill config) are edited in the TOML file. Claude Code categories (permissions, hooks, tool access) are managed through the `/update-config` skill. When a request is ambiguous, the agent asks the user for clarification.

**Acceptance Criteria**:
- Given the system preamble is rendered, when it includes the Configuration section, then it contains subsections for each config system (Tachikoma and Claude Code) with their respective settings categories listed
- Given a user asks to change a setting that maps to a known Tachikoma category, when the agent processes the request, then it reads and edits `~/.config/tachikoma/config.toml` using Read/Write tools
- Given a user asks to change a setting that maps to a known Claude Code category, when the agent processes the request, then it uses the `/update-config` skill
- Given a user makes a genuinely ambiguous settings request, when the agent processes the request, then it asks the user which config system they mean before making changes
