# Plugin Loading

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A directory-based plugin system that allows users to extend Tachikoma with third-party skills. Users declare plugin sources in a `[plugins]` config section — git repositories, HTTPS-served archives, or local filesystem paths. Plugins may declare typed configuration schemas in their manifests; users provide values in `[plugins.<alias>.config]` sub-tables. At startup, a bootstrap step reconciles the install directory with declared sources, parses each plugin's manifest (including config schemas), validates user config values against schemas, and registers contributed skill directories with the existing `SkillRegistry` under namespaced names of the form `<alias>:<skill-name>`. Three MCP tools (`install_plugin`, `list_plugins`, `remove_plugin`) let the agent manage plugins on the user's behalf without restart. Failures are isolated per plugin and never block startup; all plugin failures are consolidated into a single startup notification.

This capability covers **skills, lifecycle hooks, and event subscriptions**. Plugin contributions of MCP tool servers, slash commands, context providers, post-processors, secondary channels, and boundary hooks are out of scope and tracked by future deltas.

## User Stories

- As a Tachikoma user, I want to install third-party plugins that contribute skills so that I can extend the agent with capabilities authored by the community without modifying core code
- As a plugin author, I want to declare my plugin's skills in a manifest so that Tachikoma can discover and register them automatically
- As a plugin author, I want to declare typed configuration settings in my plugin manifest so that users can configure my plugin through Tachikoma's config.toml
- As a plugin user, I want to configure plugin settings alongside built-in configuration in config.toml so that all settings live in one place
- As the agent, I want to manage plugins via MCP tools so that I can install, list, and remove plugins on the user's behalf
- As a plugin author, I want to declare init hooks that run once after all plugins load so that my plugin can perform startup tasks before any messages are processed
- As a plugin author, I want to subscribe to system events so that my plugin can react to coordinator idle, notifications, and other plugin lifecycle changes
- As a plugin author, I want my plugin's hook and handler failures to be isolated so that they never block other plugins or application startup
- As a Claude Code plugin author, I want my hooks/events contributions to be silently ignored so that my CC plugin remains installable even though Tachikoma doesn't support CC-style hooks/events

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Provide a plugin system that lets users extend Tachikoma with skills contributed by third-party plugins, declared in config and reconciled at startup |
| R1 | `[plugins]` TOML config section uses sub-table form `[plugins.<alias>]`; each entry declares one of three mutually-exclusive source variants: a **git source** (`git` URL or `gh:owner/repo` / `github:owner/repo` shorthand, `subdir` optional, `ref` required and constrained to branch/tag names), a **url source** (`url` field pointing to an HTTPS-served `.tar.gz` / `.tgz` / `.zip` archive, `subdir` optional), or a **local source** (`path` field, absolute filesystem path) |
| R2 | At startup, a bootstrap step reconciles `workspace/.tachikoma/plugins/` with `[plugins]` config: always re-materialize each plugin from its source (overwriting the install directory atomically); orphaned directories not declared in config are removed |
| R3 | Plugins use a Tachikoma-native manifest file `tachikoma-plugin.toml` at the plugin folder root that declares plugin metadata (name, version, description) and contributed skill directory paths (relative to the plugin folder) |
| R4 | Plugins shaped as Claude Code plugins are recognized: `.claude-plugin/plugin.json` is read and the name/version/description fields are aliased into the internal model; CC contribution declarations beyond skills are silently ignored with one INFO-level log entry per skipped contribution type per plugin |
| R5 | Discovered plugin skill directories are added as sources to the existing `SkillRegistry`; plugin-contributed skills are registered under namespaced names of the form `<alias>:<skill-name>` |
| R6 | Plugin alias is the `[plugins.<alias>]` sub-table key; aliases must match the regex `[a-z0-9][a-z0-9-]*` |
| R7 | Validate each plugin's manifest and declared skill directories before loading; reject malformed plugins with a diagnostic naming the offending file/field/issue |
| R8 | Resilient reconciliation: when a configured plugin's source cannot be re-materialized but the install directory still contains a valid manifest matching the alias, retain that copy, mark its status as `stale-fallback`, and log a warning |
| R9 | Fail-safe loading: a plugin that fails to fetch, validate, or load is logged and skipped without affecting other plugins or core startup |
| R10 | MCP tool `install_plugin` validates the source spec, materializes the plugin, parses its manifest, writes the config entry, and triggers a registry refresh |
| R11 | MCP tool `list_plugins` returns all installed plugins with metadata (alias, source spec, ref, validation status, diagnostic message if non-loaded, the namespaced names of contributed skills, and config schema with current values) |
| R12 | MCP tool `remove_plugin` removes a plugin's config entry and cleans up its install directory; the registry refreshes so the plugin's namespaced skills are no longer available |
| R13 | The plugins install directory (`workspace/.tachikoma/plugins/`) is added to workspace `.gitignore` since it is regenerable from config |
| R14 | Plugins declare typed configuration settings in manifest `[config.<field_name>]` sections with basic types (string, integer, boolean, float), optional defaults, and human-readable descriptions |
| R15 | User config values live in `[plugins.<alias>.config]` sub-tables, validated against the manifest schema at load time; invalid config causes the plugin to fail with a diagnostic |
| R16 | Plugin accesses validated config through a `config` dict on `LoadedPlugin`; plugins without config schema load normally with an empty dict |
| R17 | `list_plugins` MCP tool output includes config schema, current values, and validation diagnostics for each plugin |
| R18 | All failed plugins are grouped into a single startup notification; startup notifications are consolidated — plugin failures merged with restart notification into a single delivered message |
| R19 | Plugins can declare `[hooks]` and `[events]` sections in `tachikoma-plugin.toml` with module-name entry points resolved against `hooks/` and `events/` directories relative to the plugin install directory |
| R20 | Init hooks run once after all plugins are loaded but before the coordinator processes any message; hooks run sequentially in plugin alias alphabetical order |
| R21 | Init hooks receive a frozen `PluginContext` dataclass containing: the plugin's validated config dict, the event bus reference, the plugin alias, and the plugin install path |
| R22 | Event handlers receive the typed event payload and the same `PluginContext` as init hooks |
| R23 | Handler entry points are validated at discovery time — missing files, import errors, missing functions, and invalid signatures cause the plugin to fail with a diagnostic |
| R24 | CC plugins may include `hooks` or `events` contributions in their manifest but they are silently ignored |
| R25 | Init hooks have a 30-second timeout; hooks exceeding it are cancelled and logged |

## Behaviors

### Plugin Source Declaration (R1, R6)

Users declare plugin sources in the `[plugins]` TOML config section using sub-table form. Each sub-table key is the plugin alias. Three mutually-exclusive source variants are supported: git, url, and local.

**Acceptance Criteria**:
- Given a `[plugins.code-review]` sub-table with `git`, `subdir`, and `ref` fields, when settings load, then the entry is parsed as a git-source plugin with alias `code-review`
- Given a `[plugins.dev-plugin]` sub-table with a `path` field pointing to an absolute filesystem path, when settings load, then the entry is parsed as a local-source plugin
- Given a sub-table with more than one source-discriminator field populated, when settings load, then validation fails with an error naming the populated fields and stating they are mutually exclusive
- Given a git-source sub-table without a `ref` field, when settings load, then validation fails with an error naming the missing `ref` field
- Given a local-path source with a relative path, when settings load, then validation fails with an error stating that an absolute path is required
- Given a sub-table key containing reserved characters or uppercase letters, when settings load, then validation fails with an error stating the alias must match `[a-z0-9][a-z0-9-]*`
- Given the user's sub-table key differs from the manifest-declared name, when the plugin loads, then the active alias is the sub-table key
- Given a git-source sub-table whose `git` field is `gh:owner/repo` or `github:owner/repo`, when settings load, then the value is expanded to `https://github.com/owner/repo.git` in the in-memory model while the original TOML form is preserved on disk
- Given a git-source sub-table whose `ref` matches a SHA-shaped regex, when settings load, then validation fails with an error stating that `ref` must be a branch or tag name, not a commit SHA
- Given a url-source sub-table whose `url` field uses any scheme other than `https://`, when settings load, then validation fails
- Given a url-source sub-table whose `url` field has an extension other than `.tar.gz`, `.tgz`, or `.zip`, when settings load, then validation fails

### Plugin Reconciliation at Startup (R2, R8, R9)

At startup, a bootstrap step reconciles `workspace/.tachikoma/plugins/` with declared sources by always re-materializing each plugin. Failures are isolated per plugin.

**Acceptance Criteria**:
- Given a git-source entry, when reconciliation runs, then a shallow clone is performed, the configured `subdir` is copied if specified, and the install directory is replaced via atomic rename
- Given a local-path entry, when reconciliation runs, then the configured path's contents are copied and atomic-renamed over the install directory
- Given a url-source entry, when reconciliation runs, then the archive is downloaded over HTTPS, extracted, auto-stripped if a single leading directory exists, and atomic-renamed into the install directory
- Given a configured plugin's source cannot be re-materialized and the install directory contains a valid manifest matching the alias, when reconciliation runs, then the existing copy is retained with status `stale-fallback`
- Given a configured plugin's source cannot be re-materialized and no valid install directory exists, when reconciliation runs, then the plugin is skipped with status `failed`
- Given `.tachikoma/plugins/orphan/` exists but no matching config entry remains, when reconciliation runs, then the orphan folder is removed
- Given reconciliation runs twice with no changes, when the second run completes, then state is equivalent and no spurious warnings are emitted

### Manifest Parsing (R3, R4, R7)

Plugins declare their metadata and skill contributions via a manifest file. Two formats are supported: Tachikoma-native and Claude Code.

**Acceptance Criteria**:
- Given a plugin folder with `tachikoma-plugin.toml` declaring metadata and skill directory paths, when the loader parses it, then the plugin's metadata and skill paths populate the internal model
- Given a plugin folder with `.claude-plugin/plugin.json` and a CC-format `skills/` directory but no Tachikoma manifest, when the loader parses it, then CC manifest values are aliased into the internal model and the `skills/` directory is recognized
- Given a CC plugin whose `plugin.json` declares non-skill contributions, when the loader parses it, then those declarations are silently ignored with one INFO-level log line per contribution type
- Given a plugin folder containing both manifests, when the loader parses it, then the Tachikoma-native manifest takes precedence
- Given a plugin folder missing both manifest formats, when the loader processes it, then validation fails with status `failed`
- Given a manifest declares a skill directory that does not exist on disk, when validation runs, then a WARNING is logged and the missing directory is excluded; the plugin still loads if at least one valid dir remains

### Skill Source Registration (R5)

Plugin skill directories are registered with the existing `SkillRegistry` under namespaced names, ensuring isolation from built-in, workspace, and other plugin skills.

**Acceptance Criteria**:
- Given a plugin with alias `code-review` contributes a skill named `linter`, when the skills hook completes, then the skill is registered under `code-review:linter`
- Given two plugins each contribute a skill named `deploy`, when the registry loads, then both are registered as separate namespaced skills with no collision
- Given a workspace skill named `linter` and a plugin contributes `linter`, when the registry loads, then both coexist as distinct skills
- Given the skills classifier returns a namespaced name, when the context provider runs, then the corresponding plugin skill content is loaded into context

### MCP Tools — install (R10)

The `install_plugin` MCP tool validates, materializes, and registers a plugin from a given source spec.

**Acceptance Criteria**:
- Given a valid source with no alias collision, when `install_plugin` runs, then the plugin is materialized, the manifest parsed, config is written, and the registry refreshes so the plugin is usable on the next message
- Given the manifest's name collides with an existing config entry and no explicit alias is provided, when `install_plugin` runs, then it returns an error suggesting retry with an explicit alias; config is not mutated
- Given install fails (source unreachable, manifest invalid), when the tool returns, then it returns an error naming the failure cause; config is not mutated
- Given install succeeds, when `list_plugins` is invoked, then the new plugin appears with status `loaded`

### MCP Tools — list (R11, R17)

The `list_plugins` MCP tool returns all installed plugins with metadata, including config schema and values.

**Acceptance Criteria**:
- Given plugins are installed, when `list_plugins` is invoked, then it returns one entry per plugin with alias, source spec, ref, status, diagnostic, namespaced skill names, and config info (when applicable)
- Given no plugins are installed, when `list_plugins` is invoked, then it returns an empty list
- Given a plugin is in `stale-fallback` state, when listed, then its status and diagnostic message are included
- Given a plugin with a config schema, when `list_plugins` is invoked, the entry includes a `config` key with schema (type, description, default, required), current value (user value, default if unset, or null if optional and unset)
- Given a plugin with no config schema, when `list_plugins` is invoked, the config section is absent from the output
- Given a plugin that failed config validation, when `list_plugins` is invoked, the output includes the config schema and a validation diagnostic
- Given a plugin with all optional fields with defaults and no user values, when `list_plugins` is invoked, all fields show their default values

### MCP Tools — remove (R12)

The `remove_plugin` MCP tool removes a plugin's config entry, install directory, and registry entries.

**Acceptance Criteria**:
- Given a plugin is installed and at least one of its skills is loaded into an active session's context, when `remove_plugin` runs, then the config entry is removed, the install directory is deleted, the registry is updated, and the active session's already-loaded skill entries are marked with a deletion notice but continue to function for the rest of the session
- Given the agent invokes `remove_plugin` for a non-existent alias, when the tool runs, then it returns a "not found" error
- Given a plugin's directory cannot be deleted (permission error), when `remove_plugin` runs, then the config entry is still removed and the orphan directory is cleaned up on next startup

### Gitignore Registration (R13)

The plugins install directory is excluded from version control since it is regenerable from config.

**Acceptance Criteria**:
- Given a workspace `.gitignore` without an entry for `.tachikoma/plugins/`, when the plugins bootstrap hook runs, then the entry is appended; running the hook again does not produce a duplicate entry

### Plugin Config Schema (R14, R15, R16)

Plugin authors declare typed configuration settings in the manifest's `[config.<field_name>]` sections. Users provide values in `[plugins.<alias>.config]` sub-tables. Values are validated at load time against the schema. Plugins with invalid configuration fail to load with a diagnostic.

**Manifest structure**:

```toml
[config.api_key]
type = "string"
description = "API key for the weather service"
required = true

[config.timeout]
type = "integer"
description = "Request timeout in seconds"
default = 30
```

**Acceptance Criteria**:
- Given a manifest with `[config.api_key]` declaring `type = "string"`, `required = true`, `description = "API key"`, when parsed, the plugin's config schema contains one required string field named `api_key`
- Given a manifest with `[config.timeout]` declaring `type = "integer"`, `default = 30`, when parsed, the field is optional with default 30
- Given a manifest with `[config.debug]` declaring `type = "boolean"`, `default = false`, when parsed, the field is optional with default false
- Given a manifest with `[config.rate]` declaring `type = "float"`, `default = 1.5`, when parsed, the field is optional with default 1.5
- Given a manifest without a `[config]` section, when parsed, the plugin loads normally with an empty config schema
- Given a manifest config field with an unsupported type value (e.g., `"array"`), when loaded, the plugin fails with a diagnostic naming the field, invalid type, and valid types
- Given a manifest config field with a default value that doesn't match the declared type, when parsed, the plugin fails with a diagnostic stating the default doesn't match
- Given a manifest config field with both `required = true` and a default, when parsed, the plugin fails with a diagnostic stating required fields cannot have defaults
- Given a manifest config field missing a description, when parsed, the plugin fails with a diagnostic stating description is required for all config fields
- Given a required string field with no user value and no default, when the plugin loads, it fails with a diagnostic stating the field is required
- Given a field declared as integer with a string user value, when the plugin loads, it fails with a diagnostic stating expected and actual types
- Given a field declared as boolean with an integer user value, when the plugin loads, it fails with a diagnostic about type mismatch
- Given a field declared as boolean with string `"true"`, when validated, validation fails (no string-to-bool coercion)
- Given a field declared as integer with float `30.0` (zero-fraction), when validated, the value is coerced to integer `30`
- Given a field declared as integer with float `30.5` (non-zero fraction), when validated, validation fails with a diagnostic about type mismatch
- Given a field with a default value and no user override, when the plugin loads, the default is used
- Given a field that is not required, has no default, and no user value, the runtime config dict does not contain a key for that field
- Given an optional string field with no default and user value `""` (empty string), the runtime config contains `{"field": ""}` — empty string is a valid value, distinct from unset
- Given a user provides a config key not declared in the schema, the plugin loads, a WARNING is logged with the unknown key name, and the key is not in runtime config
- Given a loaded plugin with validated config, when code accesses `loaded_plugin.config`, it returns a dict mapping field names to validated values
- Given a plugin with no config schema, when accessed at runtime, `loaded_plugin.config` returns an empty dict
- Given a plugin with a config schema where all fields have defaults and no user values, `loaded_plugin.config` returns a dict populated with default values
- Given `[plugins.weather.config]` with `api_key = "sk-..."` and `timeout = 60`, when config is loaded, the weather plugin receives `{"api_key": "sk-...", "timeout": 60}`
- Given `[plugins.weather]` with source fields and a `[plugins.weather.config]` sub-table, when config is parsed, source and config fields are cleanly separated
- Given a plugin alias with no `.config` sub-table, when config is loaded, the plugin receives only default values (or empty dict if no schema)
- Given a CC plugin with user config values in `[plugins.p.config]`, when the plugin loads, the config is not validated (CC plugins have no schema) and `loaded_plugin.config` returns an empty dict

### Startup Notification Consolidation (R18)

Failed plugins are grouped into a single startup notification. Startup notifications are consolidated — plugin failures merged with the restart notification into a single delivered message. This is implemented by the `handle_restart_notification` function in the updates subsystem, which consolidates both restart and plugin failure signals.

**Acceptance Criteria**:
- Given two plugins fail to load during startup, when startup completes, a single notification is dispatched listing both failures
- Given all plugins load successfully, when startup completes, no plugin failure notification is dispatched
- Given a restart notification marker exists and one plugin failed config validation, when startup completes, a single consolidated notification is dispatched containing both the back-online message and plugin failure details
- Given a restart notification marker exists and all plugins loaded successfully, when startup completes, the standard back-online notification is delivered (no plugin section)
- Given no restart notification and plugin failures exist, when startup completes, only the plugin failure notification is delivered
- Given neither restart notification nor plugin failures exist, when startup completes, no startup notification is dispatched
- Given the restart notification marker is cleared during consolidation, the DES-011 consume-once pattern is preserved

### Lifecycle Hooks and Event Subscriptions (R19-R25)

Plugins can declare lifecycle hooks and event subscriptions in their manifest. Init hooks run once after all plugins load. Event subscriptions route typed system events to handler functions. Handler execution is isolated per-plugin.

#### Manifest Extension (R19, R24)

**Acceptance Criteria**:
- Given a `tachikoma-plugin.toml` with `[hooks] init = "init"` and `[events] coordinator_idle = "on_idle"`, when the manifest is parsed, the internal model contains hook and event entries resolving to `hooks/init.py` and `events/on_idle.py`
- Given a `[hooks]` section with multiple entries, when parsed, all hook entry points are captured
- Given an `[events]` section with multiple entries, when parsed, all event subscriptions are captured as a mapping of event type name to handler module
- Given a CC plugin with a `"hooks"` or `"events"` contribution in `plugin.json`, when parsed, those contributions are silently ignored with an INFO log
- Given a module name in `[hooks]` or `[events]` containing `..` segments, path separators (`/` or `\`), empty string, or absolute paths, when discovery runs, the plugin fails with a diagnostic about invalid module name — only bare module names are accepted

#### Handler Validation (R23)

Handler entry points are validated end-to-end at discovery time. Any validation failure causes the plugin to fail with a diagnostic — no partial handler registration.

**Acceptance Criteria**:
- Given a manifest declaring a hook where `hooks/init.py` does not exist on disk, when discovery runs, the plugin fails with a diagnostic naming the missing file
- Given a manifest declaring an event subscription where `events/on_idle.py` does not exist on disk, when discovery runs, the plugin fails with a diagnostic naming the missing file
- Given a handler file that exists but cannot be imported (syntax error, missing dependency), when discovery runs, the plugin fails with a diagnostic including the import error message
- Given a manifest with `[hooks] init = "init"` where `hooks/init.py` exists and imports successfully but does not contain an `init` callable attribute, when discovery runs, the plugin fails with a diagnostic naming the missing function
- Given a manifest with `[events] coordinator_idle = "on_idle"` where `events/on_idle.py` exists and imports successfully but does not contain a `handle` callable attribute, when discovery runs, the plugin fails with a diagnostic naming the missing function
- Given a hook handler module where `init` accepts a number of arguments other than one, when discovery runs, the plugin fails with a diagnostic stating the expected signature
- Given an event handler module where `handle` accepts a number of arguments other than two, when discovery runs, the plugin fails with a diagnostic stating the expected signature
- Given an event type name in `[events]` that does not match a known system event, when discovery runs, the plugin fails with a diagnostic listing valid event type names

#### Init Hook Execution (R20, R21, R25)

Init hooks run after all plugins finish loading but before the coordinator processes any message. Hooks run sequentially in alias alphabetical order.

**Acceptance Criteria**:
- Given a plugin with an init hook, when all plugins finish loading, the init hook runs before the coordinator processes any message (user-initiated or background task)
- Given multiple plugins with init hooks, when hooks execute, they run sequentially in plugin alias alphabetical order
- Given an init hook that completes successfully, the plugin is considered fully initialized and its event subscriptions become active
- Given an init hook that raises an exception, when it runs, the exception is logged at ERROR level with the plugin alias, the plugin's event subscriptions are not registered, but other plugins' hooks and startup proceed normally
- Given an init hook that exceeds 30 seconds, when the timeout fires, the hook is cancelled, a WARNING is logged with the plugin alias and timeout duration, and the plugin's event subscriptions are not registered
- Given an init hook, when it runs, it receives a frozen `PluginContext` dataclass containing: the plugin's validated config dict, the event bus, the plugin alias, and the plugin install path
- Given a plugin with no `[hooks]` section, when loading completes, no init hook runs and the plugin's event subscriptions (if any) are registered immediately
- Given a plugin with `[hooks] init = "init"` but no `[events]` section, when the init hook completes successfully, no event subscriptions are registered and the plugin is considered fully initialized

#### Event Subscription (R22)

After a plugin's init hook completes (or immediately if no init hook), declared event handlers are subscribed on the event bus.

**Acceptance Criteria**:
- Given a plugin with `[events] coordinator_idle = "on_idle"`, when the init hook completes (or immediately if no init hook), the handler is subscribed to `CoordinatorIdle` events on the event bus
- Given a `CoordinatorIdle` event is dispatched, when a plugin has subscribed to it, the plugin's handler function is called with the typed event payload and a `PluginContext`
- Given a plugin subscribed to `Notification` events, when a notification is dispatched, the handler receives the full `Notification` event object
- Given an event handler that raises an exception, when the handler is invoked, the exception is logged at ERROR level with the plugin alias and event type, but the event dispatch continues to other subscribers
- Given a plugin subscribed to multiple event types, when events of different types are dispatched, each is routed to its corresponding handler function
- Given a plugin subscribed to `PluginInstalled` events, when another plugin is installed at runtime, the handler receives the `PluginInstalled` event
- Given a handler function that returns a value, when the handler is invoked, the return value is ignored

#### Event Type Registry

A central registry maps string event type names to `BaseEvent` subclasses, used by the loader to validate `[events]` entries.

**Acceptance Criteria**:
- Given the event type registry is built from auto-discovered `BaseEvent` subclasses, when queried for valid event names, it contains at least the following mappings: `coordinator_idle` → `CoordinatorIdle`, `notification` → `Notification`, `skills_changed` → `SkillsChanged`, `plugin_installed` → `PluginInstalled`, `plugin_removing` → `PluginRemoving`, `plugin_removed` → `PluginRemoved`, `buffered_delivery` → `BufferedDelivery`, `restart_requested` → `RestartRequested`
- Given an unknown event type name, when the registry is queried, it raises `KeyError` with valid names listed

#### Runtime Install and Remove

Runtime install runs init hooks and subscribes events before announcing. Runtime remove unsubscribes during the removal phase.

**Acceptance Criteria**:
- Given a plugin installed at runtime via `install_plugin` that declares an init hook, when installation completes, the init hook runs immediately before the `PluginInstalled` event is dispatched; event subscriptions are registered after the init hook succeeds
- Given a plugin with active event subscriptions, when `remove_plugin` runs, event handlers are unsubscribed during the `PluginRemoving` phase (before directory deletion)

#### Error Isolation

Init hook and event handler failures are contained per-plugin and never affect other plugins or application startup.

**Acceptance Criteria**:
- Given plugin A's init hook raises and plugin B has an init hook, when hooks execute, plugin A's failure is logged, plugin B's hook runs normally, and application startup is not blocked
- Given plugin A's event handler raises during a `CoordinatorIdle` dispatch, when the event is dispatched, plugin A's error is logged, other subscribers to `CoordinatorIdle` still receive the event, and the event dispatch completes normally
- Given a plugin whose init hook failed, when subsequent events are dispatched, the plugin's event handlers are not invoked (subscriptions were not registered)

## Out of Scope

- Plugin contributions of MCP tool servers, slash commands, context providers, post-processors, secondary channels, and boundary hooks (deferred to future deltas)
- Plugin config schema for Claude Code plugins (CC plugins have no config schema — `config_schema` is always empty dict)
- Runtime config reload (modifying config.toml while Tachikoma is running does not reload plugin configs; changes take effect on next startup)
- Environment variable substitution in config values (config values are literal TOML values)
- Enum/constraint types (string with allowed values, numeric ranges) for tighter validation
- Nested/grouped settings (tables within plugin config) for complex plugin configurations
- Auto-generated config file includes plugin config keys with descriptions
- Sensitive field marking and redaction (deferred to future delta)
- Config migration across plugin versions (unknown keys are warned and ignored; new fields use defaults)
- Plugin dispatch of custom events onto the event bus (plugins receive events only)
- New system events (e.g., session open/close) — this capability exposes existing events only
