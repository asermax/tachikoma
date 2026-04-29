# Plugin Loading

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A directory-based plugin system that allows users to extend Tachikoma with third-party skills. Users declare plugin sources in a `[plugins]` config section — git repositories, HTTPS-served archives, or local filesystem paths. At startup, a bootstrap step reconciles the install directory with declared sources, parses each plugin's manifest, and registers contributed skill directories with the existing `SkillRegistry` under namespaced names of the form `<alias>:<skill-name>`. Three MCP tools (`install_plugin`, `list_plugins`, `remove_plugin`) let the agent manage plugins on the user's behalf without restart. Failures are isolated per plugin and never block startup.

This capability is scoped to **skills only**. Plugin contributions of MCP tool servers, slash commands, context providers, post-processors, secondary channels, and boundary hooks are out of scope and tracked by future deltas.

## User Stories

- As a Tachikoma user, I want to install third-party plugins that contribute skills so that I can extend the agent with capabilities authored by the community without modifying core code
- As a plugin author, I want to declare my plugin's skills in a manifest so that Tachikoma can discover and register them automatically
- As the agent, I want to manage plugins via MCP tools so that I can install, list, and remove plugins on the user's behalf

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
| R11 | MCP tool `list_plugins` returns all installed plugins with metadata (alias, source spec, ref, validation status, diagnostic message if non-loaded, and the namespaced names of contributed skills) |
| R12 | MCP tool `remove_plugin` removes a plugin's config entry and cleans up its install directory; the registry refreshes so the plugin's namespaced skills are no longer available |
| R13 | The plugins install directory (`workspace/.tachikoma/plugins/`) is added to workspace `.gitignore` since it is regenerable from config |

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

### MCP Tools — list (R11)

The `list_plugins` MCP tool returns all installed plugins with metadata.

**Acceptance Criteria**:
- Given plugins are installed, when `list_plugins` is invoked, then it returns one entry per plugin with alias, source spec, ref, status, diagnostic, and namespaced skill names
- Given no plugins are installed, when `list_plugins` is invoked, then it returns an empty list
- Given a plugin is in `stale-fallback` state, when listed, then its status and diagnostic message are included

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

## Out of Scope

- Plugin contributions of MCP tool servers, slash commands, context providers, post-processors, secondary channels, and boundary hooks (deferred to future deltas)
