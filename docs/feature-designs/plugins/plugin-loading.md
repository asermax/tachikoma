# Design: Plugin Loading

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/plugins/plugin-loading.md](../../feature-specs/plugins/plugin-loading.md)
**Status**: Current

## Purpose

This document explains the design rationale for the plugin loading system: how plugin sources are modeled and materialized, how manifests are parsed, how skills are registered under namespaced names, how lifecycle hooks and event subscriptions extend the plugin system, how event-based decoupling keeps the plugin manager and skills subsystem cleanly separated, how MCP tools enable runtime plugin management, and how version tracking, update detection, and update application keep installed plugins current.

## Problem Context

Tachikoma's capabilities live in two layers — a core agent runtime and a per-workspace `skills/` directory. To grow the ecosystem without forcing every contribution through core, the assistant needs an extension surface that lets third-party plugins ship reusable skills declared in config, materialized at startup from a configured source, and registered into the existing `SkillRegistry` without colliding with built-in or workspace skills.

**Constraints:**
- **Reuse, don't parallel.** Plugin skills must flow into the same `SkillRegistry` consumed by the per-message classifier and the coordinator's agent derivation.
- **Skills only.** Plugins contributing MCP tool servers, slash commands, post-processors, secondary channels, and boundary hooks are deferred to future deltas. CC plugins that declare those surfaces install successfully, but their non-skill contributions are silently ignored. Plugins can declare init hooks, event subscriptions, and context providers.
- **Fail-safe.** A broken plugin must never block startup or affect other plugins. Per-plugin failure isolation applies in materialization, discovery, manifest parsing, and registry registration.
- **CC compatibility.** Plugins shaped as Claude Code plugins are recognized so Tachikoma can benefit from the existing CC plugin community.
- **Atomic on-disk state.** Mid-write failures during reconciliation must not leave a half-replaced plugin folder.
- **No new long-lived dependencies.** The codebase reuses existing `git` subprocess and `tomlkit` rather than adding GitPython, dulwich, or similar.
- **Fail-safe updates.** A failed update must leave the existing plugin intact and functional — no half-replaced directories. Updates use the existing atomic-swap pattern.

**Interactions:**
- **`SkillRegistry`** — gains `add_namespaced_source(alias, path)` and `remove_namespaced_source(alias)`; `Skill` gains `namespace` and `qualified_name`.
- **Bootstrap** — a new `plugins_hook` registers between `git_hook` and `skills_hook`.
- **Configuration** — adds `plugins: dict[str, PluginSource]` with `update_plugin_entry` / `remove_plugin_entry` helpers.
- **Event bus** (ADR-009) — three new events: `PluginInstalled`, `PluginRemoving`, `PluginRemoved`. Plugin event handlers subscribe via `bus.on(EventType, wrapped_handler)`.
- **Sessions** — on `PluginRemoving`, active session's already-injected plugin skill entries get a removal notice and `metadata["deleted"] = True`.
- **Lifecycle hooks** — plugins can declare init hooks (run post-bootstrap, before channel start) and event subscriptions (routed through the event bus with per-plugin isolation).
- **Handler validation** — hook and event handler entry points are validated at discovery time (file exists, imports, callable present, correct signature, known event types).
- **Context providers** — plugins can declare context providers in their manifest; providers implement the existing `ContextProvider` and/or `MessageContextProvider` ABCs and are registered into the appropriate pipeline(s) via event-driven listeners. Provider listeners subscribe to `PluginInstalled`/`PluginRemoving` events, keeping `PluginManager` decoupled from pipeline internals.
- **PluginManager** — gains update operations, per-plugin update locks, and `PluginStateRepository` dependency.
- **Materializer** — gains symlink path for local sources and `MaterializationResult` return type carrying version hashes.
- **Reconciler** — changes from always-re-materialize to first-time-only install.
- **MCP tools** — two new tools (`update_plugin`, `update_all_plugins`) alongside existing install/list/remove.
- **Scheduler** — one new `CronTrigger` job for daily update checks (DES-010 central scheduler, not TaskDefinition).
- **LoadedPlugin** — in-memory model unchanged; update status comes from `PluginState` lookup.

## Design Overview

The plugin system is a self-contained `tachikoma/plugins/` package whose components map onto the existing subsystem pattern (bootstrap hook, manager class, event types, MCP tool factory). It plugs into the `SkillRegistry` at exactly one point — `add_namespaced_source(alias, path)` — and otherwise communicates outward via three typed events on the project's event bus. A persistent version-tracking layer and update mechanism add daily detection (via the central scheduler, DES-010) and on-demand application (via MCP tools).

```
┌──────────────────────────────────────────────────────────────┐
│ Configuration                                                │
│  [plugins.<alias>]  git/url/local source                     │
│  SettingsManager (write-back via tomlkit)                    │
├──────────────────────────────────────────────────────────────┤
│ Bootstrap (registration order)                               │
│  git_hook → plugins_hook → skills_hook                       │
├──────────────────────────────────────────────────────────────┤
│ __main__.py                                                  │
│  After bootstrap.run(): run_plugin_init_hooks(manager, bus)  │
│  Scheduler job: plugin_update_check (CronTrigger, daily)     │
├──────────────────────────────────────────────────────────────┤
│ plugins/ package                                             │
│  reconciler.py   walks config, dispatches per source kind    │
│                  first-time-only + symlink migration          │
│  materializer.py git / url / local + atomic-swap             │
│                  MaterializationResult with version hash      │
│  loader.py       scans install dir, parses manifests         │
│                 + handler validation (hooks/events)          │
│  manager.py      state + install/remove/list/update          │
│                 + init+subscribe on install, unsubscribe      │
│                 + per-plugin update locks                     │
│  tools.py        MCP tools factory (5 tools)                 │
│  events.py       PluginInstalled / Removing / Removed        │
│  context.py      PluginContext frozen dataclass               │
│  registry.py     auto-discovered event type registry         │
│  lifecycle.py    init executor + subscribe/unsubscribe       │
│  state.py        PluginState model + PluginStateRepository   │
│  updater.py      update detection + daily check + results    │
│  provider_listeners.py  event-driven pipeline reg/unreg     │
├──────────────────────────────────────────────────────────────┤
│ PluginState (database table)                                 │
│  alias (PK), installed_version, update_status,               │
│  available_version, last_checked_at, diagnostic              │
├──────────────────────────────────────────────────────────────┤
│ skills/ package (extensions)                                 │
│  registry.py     add_namespaced_source(alias, path)          │
│  listeners.py    plugin-event subscriber                     │
│  SkillsChanged event                                         │
├──────────────────────────────────────────────────────────────┤
│ Session                                                      │
│  context entries (marked deleted on PluginRemoving)          │
├──────────────────────────────────────────────────────────────┤
│ bubus EventBus                                               │
│  bus.on(EventType, wrapped_handler)  — subscribe            │
│  wrapper._deactivate()               — unsubscribe (flag)   │
└──────────────────────────────────────────────────────────────┘
```

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/plugins/sources.py` | Pydantic source models: `GitPluginSource`, `UrlPluginSource`, `LocalPluginSource`, discriminated union | Lives separately from `config.py` to keep config module from accumulating plugin validators; `config` field carries raw user values |
| `src/tachikoma/plugins/config_schema.py` | `ConfigFieldSchema` Pydantic model; `ConfigDiagnostic` dataclass; `ConfigValidationResult` dataclass; `validate_config()` function | Separate module keeps config validation isolated from manifest parsing and loader orchestration |
| `src/tachikoma/plugins/manifest.py` | `TachikomaManifest` and `CcManifest` Pydantic models; `parse_manifest()` with native-takes-precedence | Native strict, CC tolerant (`extra="ignore"`); path-traversal protection; `config` field parses `[config.<field_name>]` sections; `hooks`/`events` fields map hook/event type names to module names; `"events"` added to CC ignored contribution types |
| `src/tachikoma/plugins/materializer.py` | `materialize_git()`, `materialize_url()`, `materialize_local()` async functions; `_atomic_replace_dir()` helper; `MaterializationResult` return type | Stdlib only — `urllib`, `tarfile`, `zipfile`, `shutil`, `os`. Atomic-swap follows pip pattern. `MaterializationResult` carries staging_dir + version hash (git SHA, URL hash, or null for local). Local sources use `os.symlink()` instead of `shutil.copytree()`. |
| `src/tachikoma/plugins/reconciler.py` | `reconcile()` walks config; dispatches to materializer; handles stale-fallback and orphan removal | First-time-only materialization (skips existing installs); local symlink migration; accepts `PluginStateRepository` for initial state persistence; per-plugin try/except |
| `src/tachikoma/plugins/loader.py` | `discover()` scans install dir; parses manifests; validates skill directories; validates config against schema; validates hook/event handler entry points | Runs after reconciler; failure of one plugin's manifest or config does not affect others; handler validation imports modules via `importlib.util` and checks signatures via `inspect` |
| `src/tachikoma/plugins/manager.py` | `PluginManager` class with `list()`, `install()`, `remove()`, `update()`, `update_all()`, `failed_plugins()`; owns `asyncio.Lock` and per-plugin update lock map; `PluginStateRepository` dependency | Single class matching tasks subsystem pattern; install runs init hook + subscribes events before `PluginInstalled`; remove unsubscribes during `PluginRemoving`; update uses per-plugin locks with non-blocking acquisition; re-registration dispatches `PluginRemoving` → atomic swap → `PluginInstalled` |
| `src/tachikoma/plugins/context.py` | `PluginContext` frozen dataclass | Frozen to prevent mutation by handlers; carries config + bus + alias + install_path |
| `src/tachikoma/plugins/registry.py` | `build_event_registry()` auto-discovers `BaseEvent` subclasses; `get_event_type(name)` lookup | Imports all known event modules explicitly, walks `__subclasses__()` recursively; snake_case derived via regex |
| `src/tachikoma/plugins/lifecycle.py` | `run_plugin_init_hooks()`, `init_plugin()`, `subscribe_plugin_events()`, `unsubscribe_plugin_events()` | Init hooks run with 30-second timeout; sync and async handlers both supported; active-flag wrapper for unsubscribe (bubus has no `off()`) |
| `src/tachikoma/plugins/events.py` | `PluginInstalled`, `PluginRemoving`, `PluginRemoved` event types | Follows ADR-009; `PluginRemoving` fires before directory deletion |
| `src/tachikoma/plugins/tools.py` | `create_plugin_tools_server()` factory; five closure-captured tool functions (`install_plugin`, `list_plugins`, `remove_plugin`, `update_plugin`, `update_all_plugins`) | Follows DES-006; extracted handlers for testability; `list_plugins` includes config schema/values/diagnostics + update status from PluginState lookup |
| `src/tachikoma/plugins/hooks.py` | `plugins_hook()` bootstrap callback | Follows DES-003; registered between git and skills hooks; creates `PluginStateRepository` and passes to reconciler and manager; stores `state_repo` in `ctx.extras` |
| `src/tachikoma/plugins/state.py` | `PluginState` domain model (frozen dataclass), `PluginStateModel` ORM model, `PluginStateRepository` | Follows ADR-007 persistence pattern; repository encapsulates all DB access with `get()`, `upsert()`, `remove()` |
| `src/tachikoma/plugins/updater.py` | `check_git_update()`, `compute_url_hash()`, `run_daily_git_check()`; `UpdateResult` and `UpdateSummary` dataclasses; `GitCheckError` exception | Update detection via `git ls-remote`; daily check iterates git-source plugins and updates PluginState; result types for MCP tool summaries |
| `src/tachikoma/plugins/provider_listeners.py` | `register_plugin_provider_listeners()` subscribes to plugin events for pipeline registration/unregistration | Follows `skills/listeners.py` decoupling pattern; registers providers into pipelines on `PluginInstalled`, unregisters on `PluginRemoving`; receives pipelines and `PluginManager` reference |
| `src/tachikoma/skills/listeners.py` | `register_plugin_event_listeners()` subscribes to plugin events | Lives in skills module per decoupling decision; owns all registry mutation triggered by plugin events |
| `src/tachikoma/skills/registry.py` (extension) | `add_namespaced_source()`, `remove_namespaced_source()`; `Skill.namespace` + `qualified_name`; `_namespaced_source_paths` tracking | Minimal-change extension — core `_discover` / `_load_skill` flow unchanged for default-namespace skills |
| `src/tachikoma/config.py` (extension) | `plugins: dict[str, PluginSource]` field; `update_plugin_entry()` / `remove_plugin_entry()` methods | tomlkit super-table for sub-table write-back |
| `src/tachikoma/__main__.py` (extension) | Calls `run_plugin_init_hooks()` after `bootstrap.run()`, before channel construction; wires provider listeners after pipeline creation; registers daily plugin update check as scheduler Job (CronTrigger, DES-010) | Post-bootstrap timing ensures all subsystems initialized before plugin hooks run; provider listeners wired before coordinator construction so providers are registered before any message processing begins |

### Cross-Layer Contracts

**Bootstrap → PluginManager → SkillRegistry → MCP tools contract:**

```
plugins_hook(ctx)
    ├── 1. Create .tachikoma/plugins/ (mkdir)
    ├── 2. Append .tachikoma/plugins/ to .gitignore (idempotent)
    ├── 3. state_repo = PluginStateRepository(db_session_factory)
    ├── 4. report = reconcile(workspace_path, settings.plugins, state_repo)
    │       └── For each [plugins.<alias>]:
    │           ├── If install_dir/<alias> exists: skip (symlink migration for local)
    │           ├── Else: materialize → atomic swap → state_repo.upsert(alias, version)
    │           └── Orphan cleanup (unchanged)
    ├── 5. plugins = discover(install_dir, plugin_sources)
    │       └── For each plugin: parse manifest → validate config schema
    │           → validate hook/event handlers → LoadedPlugin(init_hook, event_handlers)
    ├── 6. manager = PluginManager(settings_manager, bus, workspace_path, loaded, state_repo)
    ├── 7. ctx.extras["plugin_manager"] = manager
    ├── 8. ctx.extras["state_repo"] = state_repo
    └── 9. ctx.extras["plugin_skill_paths"] = [(alias, dir) for each loaded plugin skill dir]

skills_hook(ctx)
    ├── … existing default-source registry construction …
    ├── For (alias, path) in ctx.extras["plugin_skill_paths"]:
    │   └── registry.add_namespaced_source(alias, path)
    └── register_plugin_event_listeners(bus, registry, session_registry)

__main__.py (after bootstrap.run())
    ├── manager = bootstrap.extras["plugin_manager"]
    ├── bus = bootstrap.extras["event_bus"]
    ├── state_repo = bootstrap.extras["state_repo"]
    ├── await run_plugin_init_hooks(manager.list_plugins(), bus)
    │       ├── Sort loaded plugins by alias (alphabetical)
    │       ├── For each plugin with status="loaded":
    │       │   ├── If init_hook: create PluginContext, await with 30s timeout
    │       │   │   ├── On success → subscribe_plugin_events(plugin, bus)
    │       │   │   └── On failure → log ERROR, skip subscriptions
    │       │   └── Elif event_handlers: subscribe_plugin_events(plugin, bus)
    │       └── Log summary: initialized N, M failures
    ├── plugin_tools_server = create_plugin_tools_server(manager)
    └── Register scheduler Job: plugin_update_check (CronTrigger, daily)
            └── _plugin_check_tick(manager, state_repo, bus)
```

**install_plugin flow (validate-then-write):**

```
install_plugin(source, alias=None) [held under PluginManager._lock]
    ├── 1. tmp_dir = mkdtemp under .tachikoma/plugins/.staging/
    ├── 2. materialize_<kind>(source, tmp_dir)
    ├── 3. manifest = parse_manifest(tmp_dir)
    ├── 4. resolved_alias = alias or manifest.name
    ├── 5. If collision → return error (config not mutated)
    ├── 6. _atomic_replace_dir(tmp_dir, install_dir / alias)
    ├── 7. settings_manager.update_plugin_entry(alias, source); save()
    ├── 8. _validate_handlers(manifest, target, alias)  → init_hook, event_handlers
    ├── 9. _loaded[alias] = LoadedPlugin(..., init_hook, event_handlers)
    ├── 10. await init_plugin(plugin, bus)
    │       ├── If init_hook: await with 30s timeout, subscribe on success
    │       └── Elif event_handlers: subscribe immediately
    └── 11. await bus.dispatch(PluginInstalled(alias, plugin))
            └── skills listener: registry.add_namespaced_source + SkillsChanged
```

**remove_plugin flow (event-first directory cleanup):**

```
remove_plugin(alias) [held under PluginManager._lock]
    ├── 1. If not found → return error
    ├── 2. Collect namespaced_skill_names
    ├── 3. await bus.dispatch(PluginRemoving(alias, names))
    │       └── skills listener marks active session entries deleted
    ├── 4. unsubscribe_plugin_events(plugin.event_wrappers)
    │       └── wrapper._deactivate() for each registered wrapper
    ├── 5. settings_manager.remove_plugin_entry(alias); save()
    ├── 6. try _atomic_rmtree (errors recorded, not raised)
    ├── 7. del _loaded[alias]
    └── 8. await bus.dispatch(PluginRemoved(alias))
            └── skills listener: registry.remove_namespaced_source + SkillsChanged
```

**Update application contract (update_plugin):**

```
apply_update(alias, manager, state_repo, bus)
    ├── Validate: alias exists, not local-source, not already updating
    ├── Acquire per-plugin lock (non-blocking → error if held)
    ├── try:
    │   ├── result = materialize_<kind>(source, staging_dir)
    │   ├── manifest = parse_manifest(result.staging_dir)
    │   ├── validate_handlers(manifest, result.staging_dir, alias)
    │   ├── _atomic_replace_dir(result.staging_dir, install_dir / alias)
    │   ├── Update PluginState:
    │   │   ├── installed_version = result.version
    │   │   ├── update_status = "up-to-date"
    │   │   └── available_version = null
    │   └── Re-register plugin:
    │       ├── Dispatch PluginRemoving(alias, namespaced_skill_names)
    │       ├── Unsubscribe old event wrappers
    │       ├── Re-discover: parse manifest, validate handlers
    │       ├── Re-register skills via add_namespaced_source
    │       ├── Re-subscribe events
    │       └── Dispatch PluginInstalled(alias, new_plugin)
    ├── except: log error, existing plugin intact
    └── finally: Release per-plugin lock
```

**Daily check contract:**

```
plugin_update_check_tick(manager, state_repo, bus)
    ├── git_plugins = loaded plugins with GitPluginSource and status "loaded"
    ├── updates_found = []
    ├── For each git_plugin:
    │   ├── remote_sha = git ls-remote <url> <ref_spec>
    │   ├── If remote_sha == state.installed_version:
    │   │   └── state_repo.upsert(alias, update_status="up-to-date", last_checked_at=now)
    │   ├── If remote_sha != state.installed_version:
    │   │   ├── state_repo.upsert(alias, update_status="update-available", available_version=remote_sha, last_checked_at=now)
    │   │   └── updates_found.append(alias)
    │   └── If error: retain previous status, log warning, update last_checked_at
    └── If updates_found:
        └── dispatch notification listing aliases + available versions
```

**Bulk update contract (update_all_plugins):**

```
apply_all_updates(manager, state_repo, bus)
    ├── Collect all plugins with update_status = "update-available"
    ├── Skip local-source plugins (report as skipped)
    ├── results = []
    ├── For each plugin (sequential):
    │   ├── try: apply_update(alias, ...) → results.append({alias, status: "updated"})
    │   └── except: results.append({alias, status: "failed", error: str(e)})
    └── Return UpdateSummary: {total, updated, skipped, failed, results}
```

**Provider discovery contract (loader.py):**

```
_validate_providers(manifest, plugin_dir, alias, validated_config)
    → (list[ContextProvider], list[MessageContextProvider])

For each (name, module_name) in manifest.context_providers:
    1. Resolve plugin_dir / "context_providers" / "{module_name}.py"
    2. Check file exists (else: raise ValueError with diagnostic)
    3. Import via importlib.util.spec_from_file_location
       Module key: "tachikoma_plugin.{alias}.context_providers.{module_name}"
    4. Scan module for concrete provider classes:
       - Skip non-class, abstract classes (inspect.isabstract)
       - Check issubclass(cls, ContextProvider) and/or issubclass(cls, MessageContextProvider)
    5. For each discovered class:
       - Validate __init__ accepts "config" as keyword arg (inspect.signature)
       - Instantiate: cls(config=validated_config)
       - Catch TypeError from unimplemented abstract methods → re-raise as ValueError
       - Route to appropriate list based on ABC membership
    6. If no concrete provider classes found: raise ValueError with diagnostic
    Return (session_providers, message_providers)
```

**Provider event listener contract (provider_listeners.py):**

```
register_plugin_provider_listeners(bus, pre_pipeline, msg_pre_pipeline, plugin_manager)

on_plugin_installed(event):
    plugin = plugin_manager lookup by event.alias
    for provider in plugin.context_providers:
        pre_pipeline.register(provider)
    for provider in plugin.message_context_providers:
        msg_pre_pipeline.register(provider)

on_plugin_removing(event):
    plugin = plugin_manager lookup by event.alias
    for provider in plugin.context_providers:
        pre_pipeline.unregister(provider)
    for provider in plugin.message_context_providers:
        msg_pre_pipeline.unregister(provider)
```

### Shared Logic

- **`_atomic_replace_dir(new, dst)` helper**: shared between three materializer paths. Owns the pip-style triple-step swap with rollback.
- **`run_git()` import**: reused from `git/sync.py` for the git materializer path and for `git ls-remote` in the update detector.
- **`MaterializationResult` dataclass**: shared between reconciler (first-time install captures version) and updater (update captures version). Replaces the previous bare `Path` return from materialize functions.
- **`PluginStateRepository`**: shared between reconciler (initial state creation), daily check (status updates), updater (version updates), tools (status queries), and list_plugins (enrichment).
- **`ConfigFieldSchema`**: Pydantic model shared between manifest parsing (produces it) and config validation (consumes it). Lives in `config_schema.py` to avoid circular imports.
- **`ConfigDiagnostic`**: Frozen dataclass used by validation to report issues. Consumed by loader (sets `LoadedPlugin.diagnostic`) and tools (surfaces in `list_plugins` output).

## Modeling

### Source models (Pydantic discriminated union)

```
PluginSource = GitPluginSource | UrlPluginSource | LocalPluginSource

GitPluginSource (frozen Pydantic, extra="forbid")
├── git: str                  (URL after gh:/github: shorthand expansion)
├── subdir: str | None = None
├── ref: str                  (branch or tag name; SHA-shaped rejected)
└── config: dict[str, Any] | None = None   (raw user values from [plugins.<alias>.config])

UrlPluginSource (frozen Pydantic, extra="forbid")
├── url: str                  (HTTPS-only, recognized archive extension)
├── subdir: str | None = None
└── config: dict[str, Any] | None = None   (raw user values from [plugins.<alias>.config])

LocalPluginSource (frozen Pydantic, extra="forbid")
├── path: Path                (must be absolute)
└── config: dict[str, Any] | None = None   (raw user values from [plugins.<alias>.config])
```

Discriminated by the presence of `git` / `url` / `path`. Each variant uses `extra="forbid"` for fail-fast on misspelled keys. The `config` field is nullable — `None` when no `[plugins.<alias>.config]` sub-table exists.

### Config schema model

```
ConfigFieldSchema (frozen Pydantic, extra="forbid")
├── type: Literal["string", "integer", "boolean", "float"]
├── description: str
├── default: str | int | bool | float | None = None
├── required: bool = False
│
├── @model_validator(mode="after")
│   ├── If required=true AND default is not None → ValueError
│   └── If default is not None AND type(default) doesn't match declared type → ValueError

ConfigDiagnostic (frozen dataclass)
├── field: str | None          (None for non-field errors)
├── message: str               (Human-readable diagnostic)

ConfigValidationResult (frozen dataclass)
├── values: dict[str, str | int | bool | float]   (validated values)
├── diagnostics: list[ConfigDiagnostic]            (non-empty = failure)
├── unknown_keys: list[str]                         (logged as warnings, not errors)
└── is_valid: bool (property)                       (True when diagnostics is empty)
```

`validate_config()` always returns a `ConfigValidationResult` — never raises. When `is_valid` is false, `diagnostics` lists the failures. The caller checks `is_valid` to decide whether to use `values` or report the diagnostics.

### PluginState model (persistent version tracking)

```
PluginState (frozen dataclass)
├── alias: str
├── installed_version: str | None     (git: 40-char SHA, url: SHA-256 hex, local: null)
├── update_status: Literal["unknown", "up-to-date", "update-available", "stale-fallback"]
├── available_version: str | None     (remote SHA when update available)
├── last_checked_at: datetime | None
├── diagnostic: str | None            (re-registration failure info)
└── created_at: datetime

PluginStateModel (SQLAlchemy)
├── __tablename__ = "plugin_state"
├── alias: String (PK)
├── installed_version: String (nullable)
├── update_status: String (default "unknown")
├── available_version: String (nullable)
├── last_checked_at: DateTime (nullable)
├── diagnostic: String (nullable)
└── created_at: DateTime (server_default=now())

PluginStateRepository
├── __init__(session_factory)
├── get(alias) -> PluginState | None
├── upsert(state: PluginState) -> PluginState   (insert or update provided fields)
└── remove(alias) -> None                        (called during plugin removal)
```

### MaterializationResult

```
MaterializationResult (dataclass)
├── staging_dir: Path
└── version: str | None
    (git: full 40-char commit SHA from `git rev-parse HEAD` in clone)
    (url: SHA-256 hex digest of downloaded archive via hashlib.file_digest)
    (local: None — symlinks have no version state)
```

### Update result types

```
UpdateStatus = Literal["unknown", "up-to-date", "update-available", "stale-fallback"]

UpdateResult (frozen dataclass)
├── alias: str
├── status: Literal["updated", "failed", "skipped"]
├── error: str | None
└── message: str | None

UpdateSummary (frozen dataclass)
├── total: int
├── updated: int
├── skipped: int
├── failed: int
└── results: list[UpdateResult]
```

### Per-plugin lock map

```
PluginManager._update_locks: dict[str, asyncio.Lock]
    Lazily created per alias on first update attempt.
    Non-blocking acquisition: if lock is held, return "update already in progress" error.
    Released in finally block to ensure cleanup on all paths.
    Cleared when plugin is removed.
```

### Manifest models

```
TachikomaManifest (Pydantic)             [tachikoma-plugin.toml]
├── name: str
├── version: str | None = None
├── description: str
├── skills: list[str] = []                (relative paths to skill dirs)
├── config: dict[str, ConfigFieldSchema] = {}  (declared settings from [config.<field_name>])
├── hooks: dict[str, str] = {}            (hook_type → module_name, e.g. "init" → "init")
├── events: dict[str, str] = {}           (event_type_name → module_name, e.g. "coordinator_idle" → "on_idle")
└── context_providers: dict[str, str] = {}    (entry_name → module_name, e.g. "calendar" → "calendar")

    @field_validator("hooks", "events", "context_providers", mode="after")
    → values must be bare module names (no "..", no path separators, not empty)

CcManifest (Pydantic)                     [.claude-plugin/plugin.json]
├── name: str
├── version: str | None = None
├── description: str | None = None
├── skills: str | None = None
└── (non-skill contributions accepted but ignored)

PluginManifest (frozen dataclass)         [unified internal model]
├── name: str
├── version: str | None
├── description: str | None
├── source_format: Literal["tachikoma", "cc"]
├── skill_dirs: list[Path]                (resolved absolute paths)
├── ignored_cc_contributions: list[str]   (silently ignored CC declarations)
├── config_schema: dict[str, ConfigFieldSchema]   (empty dict for CC plugins)
├── hooks: dict[str, str] = field(default_factory=dict)
├── events: dict[str, str] = field(default_factory=dict)
└── context_providers: dict[str, str] = field(default_factory=dict)   (entry_name → module_name; CC plugins → ignored)
```

Native takes precedence when both manifests are present. Path-traversal protection rejects absolute paths, `..` segments, and paths resolving outside the plugin directory. CC plugins have no config schema — `config_schema` is always an empty dict.

### LoadedPlugin record

```
LoadedPlugin (frozen dataclass)
├── alias: str
├── source: PluginSource
├── manifest: PluginManifest | None
├── status: Literal["loaded", "stale-fallback", "failed"]
├── diagnostic: str | None
├── plugin_dir: Path
├── contributed_skills: list[Skill]       (namespaced skills registered by SkillRegistry)
├── config: dict[str, str | int | bool | float]   (validated values; empty dict if no schema)
├── init_hook: Callable[..., Any] | None = None   (resolved callable for "init" hook)
├── event_handlers: dict[type[BaseEvent], Callable] = field(default_factory=dict)   (event_type → handler)
├── event_wrappers: list = field(default_factory=list)   (wrapper objects with _deactivate() for unsubscribe)
├── context_providers: list[ContextProvider] = field(default_factory=list)   (instances registered in PreProcessingPipeline)
└── message_context_providers: list[MessageContextProvider] = field(default_factory=list)   (instances registered in MessagePreProcessingPipeline)
```
```

### Skill (existing — extended)

```
Skill (frozen dataclass)                   [extended]
├── name: str                              (bare folder name; unchanged)
├── description: str
├── body: str
├── path: Path
├── version: str | None = None
├── depends_on: tuple[str, ...] = ()
├── namespace: str | None = None           (NEW — plugin alias or None for default)
└── qualified_name: str (property)         (NEW — f"{namespace}:{name}" or bare name)
```

### Plugin events

```
PluginInstalled(BaseEvent[None])
├── alias: str
└── plugin: LoadedPlugin

PluginRemoving(BaseEvent[None])
├── alias: str
└── namespaced_skill_names: list[str]

PluginRemoved(BaseEvent[None])
└── alias: str
```

`PluginRemoving` fires before directory deletion so subscribers can access still-valid paths.

### PluginContext

```
PluginContext (frozen dataclass)
├── config: dict[str, str | int | bool | float]   (validated config values)
├── event_bus: EventBus                            (reference, subscribe only — plugins cannot dispatch)
├── alias: str                                     (plugin alias)
└── install_path: Path                             (absolute path to plugin install directory)
```

Frozen to prevent mutation by handlers. Shared between init hooks and event handlers — both receive the same context. Created once per plugin at lifecycle execution time.

### Event type registry

```
EVENT_REGISTRY: dict[str, type[BaseEvent]]
    Built by: import all known event modules → walk BaseEvent.__subclasses__() recursively
    → derive snake_case name from CamelCase class name via regex
    → map name → class

    Example mappings (at minimum):
        "coordinator_idle"  → CoordinatorIdle
        "notification"      → Notification
        "skills_changed"    → SkillsChanged
        "plugin_installed"  → PluginInstalled
        "plugin_removing"   → PluginRemoving
        "plugin_removed"    → PluginRemoved
        "buffered_delivery" → BufferedDelivery
        "restart_requested" → RestartRequested
```

Auto-discovered via `BaseEvent.__subclasses__()` walk. Adding a new event type to any imported module automatically makes it available to plugins. The import list is the only thing that needs updating when event modules are added.

### Handler resolution

```
HandlerSpec (internal, not persisted)
├── For hooks:
│   ├── manifest key: hook type name (e.g., "init")
│   ├── manifest value: module name (e.g., "startup")
│   ├── resolved path: plugin_dir / "hooks" / "startup.py"
│   ├── expected function name: hook type name (e.g., "init")
│   └── expected parameter count: 1
├── For events:
│   ├── manifest key: event type name (e.g., "coordinator_idle")
│   ├── manifest value: module name (e.g., "on_idle")
│   ├── resolved path: plugin_dir / "events" / "on_idle.py"
│   ├── expected function name: "handle"
│   └── expected parameter count: 2
```

## Data Flow

### Startup reconciliation (changed)

```
__main__.py → bootstrap.run() → plugins_hook
    ├── mkdir .tachikoma/plugins/
    ├── append .tachikoma/plugins/ to .gitignore (idempotent)
    ├── state_repo = PluginStateRepository(db_session_factory)
    ├── reconcile(workspace, settings.plugins, state_repo)
    │   └── For each [plugins.<alias>]:
    │       ├── If install_dir/<alias> exists with valid manifest:
    │       │   ├── If local source AND not symlink: migrate to symlink
    │       │   └── Skip materialization (ensure PluginState row exists)
    │       ├── Else (first install):
    │       │   ├── result = materialize_<kind>(source, staging_dir)
    │       │   ├── _atomic_replace_dir(result.staging_dir, install_dir / alias)
    │       │   └── state_repo.upsert(alias, installed_version=result.version, update_status="unknown")
    │       └── Orphan cleanup (unchanged)
    ├── discover(install_dir)
    │   └── For each plugin: parse manifest → validate config → validate handlers
    ├── PluginManager(settings_manager, bus, workspace_path, loaded, state_repo)
    └── ctx.extras populated

skills_hook(ctx)
    ├── SkillRegistry([builtin_path, workspace_skills_path])
    ├── for (alias, path) in plugin_skill_paths: add_namespaced_source(alias, path)
    └── register_plugin_event_listeners(bus, registry, session_registry)

__main__.py (after bootstrap)
    ├── await run_plugin_init_hooks(manager.list_plugins(), bus)
    ├── register plugin MCP tools server
    └── register daily check scheduler Job (CronTrigger)
```

### Config validation during discovery

```
_discover_one(outcome, install_dir, plugin_sources)
    ├── Existing: parse manifest, validate skill dirs
    ├── Existing: if manifest has config_schema:
    │   ├── user_values = source.config or {}
    │   ├── result = validate_config(manifest.config_schema, user_values)
    │   ├── if not result.is_valid:
    │   │   └── return LoadedPlugin(status="failed",
    │   │           diagnostic=format_diagnostics(result.diagnostics), ...)
    │   └── validated_config = result.values
    ├── else:
    │   └── validated_config = {}
    ├── NEW: if tachikoma-native manifest with hooks/events:
    │   ├── _validate_handlers(manifest, plugin_dir, alias)
    │   │   ├── For each (hook_type, module_name) in manifest.hooks:
    │   │   │   ├── Resolve plugin_dir / "hooks" / {module_name}.py
    │   │   │   ├── Check file exists
    │   │   │   ├── importlib.util.spec_from_file_location → exec_module
    │   │   │   ├── Check module has {hook_type} callable
    │   │   │   ├── _count_positional_params(func) == 1
    │   │   │   └── Store callable in init_hook
    │   │   ├── For each (event_name, module_name) in manifest.events:
    │   │   │   ├── Look up event_name in EVENT_REGISTRY (fail if unknown)
    │   │   │   ├── Resolve plugin_dir / "events" / {module_name}.py
    │   │   │   ├── Check file exists
    │   │   │   ├── importlib.util.spec_from_file_location → exec_module
    │   │   │   ├── Check module has "handle" callable
    │   │   │   ├── _count_positional_params(func) == 2
    │   │   │   └── Store event_type → callable in event_handlers
    │   │   └── Return (init_hook, event_handlers) or raise ValueError
    │   └── On failure: return LoadedPlugin(status="failed", diagnostic=...)
    ├── NEW: if manifest has context_providers:
    │   ├── session_providers, msg_providers = _validate_providers(
    │   │       manifest, plugin_dir, alias, validated_config)
    │   └── On failure: return LoadedPlugin(status="failed", diagnostic=...)
    └── return LoadedPlugin(..., config=validated_config, init_hook=..., event_handlers=...,
                               context_providers=session_providers,
                               message_context_providers=msg_providers)
```

### Provider registration on plugin install

```
PluginInstalled event dispatched
    └── provider_listeners.on_plugin_installed(event)
            ├── for provider in event.plugin.context_providers:
            │   └── pre_pipeline.register(provider)
            └── for provider in event.plugin.message_context_providers:
                └── msg_pre_pipeline.register(provider)
```

### Provider unregistration on plugin remove

```
PluginRemoving event dispatched
    └── provider_listeners.on_plugin_removing(event)
            ├── plugin = plugin_manager lookup by event.alias
            ├── for provider in plugin.context_providers:
            │   └── pre_pipeline.unregister(provider)
            └── for provider in plugin.message_context_providers:
                └── msg_pre_pipeline.unregister(provider)
```

### Provider re-registration on plugin update

```
_reregister_plugin(old_plugin, new_plugin)
    ├── Dispatch PluginRemoving → provider listener unregisters old providers
    ├── ... existing re-registration flow ...
    └── Dispatch PluginInstalled → provider listener registers new providers
```

### Init hooks and event subscription (post-bootstrap)

```
__main__.py (after bootstrap.run(), before channel construction)
    └── await run_plugin_init_hooks(manager.list_plugins(), bus)
            ├── Sort loaded plugins by alias (alphabetical)
            ├── For each plugin with status="loaded":
            │   ├── Create PluginContext(config, bus, alias, install_path)
            │   ├── If init_hook:
            │   │   ├── try: await asyncio.timeout(30): _invoke_handler(init_hook, ctx)
            │   │   ├── except TimeoutError: log WARNING, skip subscriptions
            │   │   ├── except Exception: log ERROR, skip subscriptions
            │   │   └── On success: subscribe_plugin_events(plugin, bus, ctx)
            │   └── Elif event_handlers:
            │       └── subscribe_plugin_events(plugin, bus, ctx)
            └── Log summary: initialized N plugins, M failures
```

### Startup notification consolidation

```
__main__.py (after bootstrap.run(), before channel.run())
    ├── restart_notification = read_restart_notification()
    ├── rollback_was_dispatched = ...
    ├── plugin_manager = bootstrap.extras["plugin_manager"]
    └── handle_restart_notification(bus, restart_notification,
            rollback_was_dispatched, plugin_manager)
            ├── clear_restart_notification()  # DES-011 consume-once
            ├── Build restart content (if marker exists + no rollback)
            ├── Collect failed_plugins from manager
            ├── Build plugin failure summary (if any failures)
            ├── Merge into single content string
            └── dispatch_notification(bus, ...)  # single dispatch
```

### Daily update check (new)

```
scheduler tick (CronTrigger, once daily)
    └── plugin_update_check_tick(manager, state_repo, bus)
        ├── git_plugins = loaded plugins with GitPluginSource
        ├── For each git_plugin:
        │   ├── remote_sha = run_git(["ls-remote", url, ref_spec])
        │   │   └── ref_spec = "refs/heads/<ref>" or "refs/tags/<ref>^{}"
        │   ├── Compare remote_sha with state.installed_version
        │   └── Update PluginState accordingly
        └── If any update-available:
            └── dispatch_notification(bus, "Plugin updates available: ...")
```

### Single plugin update (new)

```
update_plugin(alias) MCP tool
    └── manager.update(alias)
        ├── Lookup plugin + state
        ├── Validate (exists, not local, not locked)
        ├── Acquire per-plugin lock (non-blocking)
        ├── try:
        │   ├── result = materialize_<kind>(source, staging_dir)
        │   ├── parse_manifest + validate_handlers
        │   ├── _atomic_replace_dir(result.staging_dir, install_dir)
        │   ├── state_repo.upsert(alias, installed_version=result.version, update_status="up-to-date")
        │   └── Re-register:
        │       ├── Dispatch PluginRemoving → unsubscribe old wrappers
        │       ├── Re-discover plugin at install_dir
        │       ├── Dispatch PluginInstalled → add_namespaced_source
        │       └── Subscribe new event handlers
        ├── except: existing plugin intact, error returned
        └── finally: Release lock
```

### Bulk plugin update (new)

```
update_all_plugins() MCP tool
    └── manager.update_all()
        ├── Query plugins with update_status = "update-available"
        ├── For each (sequential):
        │   ├── If local: record as skipped
        │   ├── Else: apply_update(alias) → record result
        │   └── Continue on failure
        └── Return UpdateSummary
```

### list_plugins with update info (extended)

```
list_plugins() MCP tool
    └── manager.list()
        └── For each LoadedPlugin:
            ├── Existing: alias, source, status, skills, config
            └── NEW: lookup PluginState → append update_status, installed_version, available_version
```

### Pip-style atomic-swap for directory replacement

**Choice**: `_atomic_replace_dir(new, dst)` performs `os.rename(dst, dst.old)` → `os.rename(new, dst)` → `shutil.rmtree(dst.old)` with rollback on failure.
**Why**: `os.replace` does NOT work for non-empty target directories. The triple-step rename preserves atomicity at each step and avoids the window where dst is missing entirely.
**Alternatives Considered**: `os.replace` (fails for non-empty dirs), `rmtree` + `rename` (leaves gap where dst doesn't exist), Linux-only `renameat2` (non-portable)
**Consequences**: Pro: correct and portable. Con: requires same filesystem (ensured by staging under `.tachikoma/plugins/`).

### Pydantic discriminated-union for source variants

**Choice**: Three models discriminated by field-presence, not a tagged `kind` field.
**Why**: Keeps TOML ergonomic — users write `[plugins.foo]\ngit = "..."` without a `kind` discriminator.
**Consequences**: Pro: ergonomic TOML, type-narrowing at use sites. Con: root-validator boilerplate for exactly-one enforcement.

### Unified `_skills` dict with namespaced keys

**Choice**: Plugin and default-namespace skills share the same `_skills` dict keyed by qualified name.
**Why**: A separate dict would force every consumer (resolve_chain, classifier, agent derivation) to iterate two structures.
**Consequences**: Pro: minimum touchpoints in consumers. Con: `_load_skill` gains an optional namespace parameter.

### `Skill.namespace` field separate from `name`

**Choice**: `namespace: str | None = None` on Skill with a `qualified_name` property.
**Why**: Plugin authors need the bare name for metadata and logs; lookup needs the qualified form. A unified name would force every consumer to split on `:`.
**Consequences**: Pro: round-trippable bare name. Con: one optional field (backwards-compatible).

### Explicit-prefix dep resolution with bare-default-ns fallback

**Choice**: For plugin skills: bare `dep` → default namespace; `:dep` → own plugin sibling; `<other>:dep` → named plugin namespace.
**Why**: Plugin authors don't know the user's chosen alias, so bare-name sibling refs would be implicit. Explicit prefixes make intent visible.
**Consequences**: Pro: visible intent at declaration site. Pro: built-in deps stay ergonomic (bare name). Con: plugin authors must learn the `:` sentinel.

### Event-based decoupling between plugins and skills

**Choice**: `PluginManager` dispatches events; skills module subscribes via `listeners.py` and owns all registry mutation. Manager has no `SkillRegistry` or `session_registry` dependency.
**Why**: Cleanest separation — manager reports facts, skills module translates into registry mutations. Future deltas attach to plugin events without touching PluginManager.
**Consequences**: Pro: manager testable in isolation. Pro: no late-binding hazard. Con: relies on `bus.dispatch` being awaited (bubus property).

### Soft removal with deletion-marker notice

**Choice**: On `remove_plugin`, active session's plugin skill entries get a removal notice prepended and `metadata["deleted"] = True`. Skill body remains in prompt for rest of session.
**Why**: AC-MCP-REM-1 says entries continue to function. The notice informs the agent that on-disk paths may be invalid.
**Consequences**: Pro: spec-compliant, agent informed. Con: `metadata["deleted"]` is a new key (additive on the existing nullable JSON column).

### tomlkit super-table for `[plugins.<alias>]` sub-tables

**Choice**: `tomlkit.table(is_super_table=True)` for the `[plugins]` parent, plain tables for each `[plugins.<alias>]`. Two new `SettingsManager` methods.
**Why**: tomlkit is already a dependency. Super-table flag makes programmatically-constructed tables render as dotted-key headers. Preserves comments and formatting across install/remove.
**Consequences**: Pro: comments preserved. Pro: readable TOML output. Con: two new methods on SettingsManager (tight scope).

### Stdlib urllib + tarfile/zipfile for URL-source materialization

**Choice**: Stream URL archives via `urllib.request.urlopen`, extract via `tarfile`/`zipfile`.
**Why**: All three are in Python's standard library. No new dependencies for one-shot startup downloads.
**Consequences**: Pro: zero new deps. Pro: HTTPS verification on by default. Con: blocking I/O wrapped in `run_in_executor`.

### GitHub shorthand expansion in field validator

**Choice**: Pydantic `field_validator` on `GitPluginSource.git` detects `gh:owner/repo` and `github:owner/repo` and rewrites to `https://github.com/owner/repo.git` in-memory.
**Why**: Adding a separate source variant would proliferate types. Syntactic sugar at validation time gives uniform downstream code.
**Consequences**: Pro: ergonomic TOML. Pro: uniform materializer logic. Con: minor typo surface.

### Config values on PluginSource models

**Choice**: Add `config: dict[str, Any] | None = None` to each source model variant, rather than a separate top-level `plugin_user_config` field on `Settings`.
**Why**: Keeps user config values co-located with their plugin source declaration. The entire plugin definition (source + user values) travels together through the pipeline — from config parsing through discovery to the `LoadedPlugin`. Avoids threading a parallel dict through hooks and into the loader.
**Alternatives Considered**: Separate `plugin_user_config` dict on Settings (requires parallel threading); lazy extraction by the loader (breaks clean data flow).
**Consequences**: Pro: Single dict per alias carries everything. Con: Source models gain a field unrelated to their source-variant responsibility (mitigated: optional, nullable, defaults to None).

### New `config_schema.py` module

**Choice**: Create `plugins/config_schema.py` with the `ConfigFieldSchema` model, `validate_config()` function, and supporting types, rather than embedding in `manifest.py` or `loader.py`.
**Why**: The config schema concern is self-contained — it has its own types, validation logic, and is consumed from two places (manifest parsing produces schemas, loader validates values against them). A dedicated module avoids bloating existing focused modules.
**Alternatives Considered**: Embed in `manifest.py` (couples parsing with validation); embed in `loader.py` (harder to test in isolation).
**Consequences**: Pro: Testable in isolation. Pro: Clean imports. Con: One more file (mitigated: small focused module).

### No config schema support for CC plugins

**Choice**: Only `tachikoma-plugin.toml` manifests support `[config]` sections. CC plugins have `config_schema = {}`.
**Why**: CC plugins use JSON manifests with their own schema. Adding config schema support would require inventing a JSON equivalent that doesn't exist in the CC spec. Consistent with how CC plugins handle other Tachikoma-specific features (non-skill contributions silently ignored).
**Consequences**: Pro: No CC schema invention needed; clean boundary. Con: CC plugin authors who want config must provide a `tachikoma-plugin.toml` (which takes precedence per manifest precedence rules).

### Float-to-integer coercion for zero-fraction values

**Choice**: When a field declares `type = "integer"` and the user provides a TOML float with zero fraction (e.g., `30.0`), coerce to integer `30`. Reject non-zero-fraction floats (e.g., `30.5`).
**Why**: Some TOML generators or copy-paste scenarios produce `30.0` for what the user intends as an integer. Rejecting `30.0` would be pedantic. Rejecting `30.5` catches genuine mistakes.
**Alternatives Considered**: No coercion (strict matching — surprising for `30.0`); coerce all numeric types (less justified for `int` → `float`).
**Consequences**: Pro: Forgiving for the common zero-fraction case. Con: Asymmetric — only float→integer, not integer→float.

### Explicit bool rejection for numeric types

**Choice**: Reject `bool` values for integer and float fields, and reject `int` values for boolean fields.
**Why**: `bool` is a subclass of `int` in Python (`True == 1`, `False == 0`). Accepting `bool` silently would cause surprising behavior (user passes `debug = true`, gets `debug = 1`). Explicit rejection forces correct type usage.
**Consequences**: Pro: Type confusion caught early. Con: Slightly more verbose validation logic.

### Consolidated startup notification via handle_restart_notification extension

**Choice**: Extend `handle_restart_notification()` in `rollback.py` to accept an optional `PluginManager`, collect plugin failures, and merge them into the same notification dispatch as the restart announcement.
**Why**: The restart notification handler already owns the DES-011 consume-once logic and conditional dispatch. Adding plugin failure collection keeps all startup notification logic in one place. A new function would duplicate the conditional logic or result in multiple dispatches.
**Alternatives Considered**: New `dispatch_startup_notifications()` in `__main__.py` (duplicate logic or two dispatches); Plugin manager dispatches its own notification (no consolidation possible).
**Consequences**: Pro: Single function owns all startup notification composition. Pro: DES-011 consume-once semantics preserved (follows DES-011 — marker cleared before side effects). Con: `rollback.py` gains optional `PluginManager` dependency (mitigated: `TYPE_CHECKING` import, optional parameter).

### Auto-discovery event type registry

**Choice**: Build the event type registry by importing all known event modules, then walking `BaseEvent.__subclasses__()` recursively and deriving snake_case names from class names.
**Why**: Avoids maintaining a manual mapping of string names to classes. Adding a new event type to any imported module automatically makes it available to plugins. The module import list is the only thing that needs updating when event modules are added.
**Alternatives Considered**: Static dict with explicit name → class mapping (simpler but every new event requires updating the dict); Fully automatic discovery without module imports (fragile — depends on import ordering).
**Consequences**: Pro: New event types automatically available to plugins. Pro: snake_case convention matches TOML style. Con: Adding a new event module requires updating the registry's import list. Con: Event class names must follow CamelCase for correct snake_case derivation.

### Post-bootstrap init hook execution

**Choice**: Init hooks run after `bootstrap.run()` completes, before `channel.run()` starts, orchestrated by a `run_plugin_init_hooks()` call in `__main__.py` (not as a DES-003 bootstrap hook).
**Why**: Guarantees all subsystems (sessions, tasks, skills, database) are fully initialized before plugin init hooks run. Plugin init hooks may need to interact with these subsystems. Running inside bootstrap would limit access to only earlier-initialized subsystems.
**Alternatives Considered**: End of `plugins_hook` during bootstrap (simplest, but plugins can't access subsystems that haven't been bootstrapped yet); Separate bootstrap hook registered last (same timing but adds another hook for what is fundamentally a post-bootstrap concern).
**Consequences**: Pro: Plugins have access to all initialized subsystems via the event bus. Pro: Clean separation — bootstrap handles subsystem init, post-bootstrap handles plugin lifecycle. Con: Init hooks are not visible in the bootstrap hook registry (mitigated by explicit call site in `__main__.py`).

### LoadedPlugin field extensions for hooks and events

**Choice**: Add `init_hook`, `event_handlers`, and `event_wrappers` fields directly to `LoadedPlugin`.
**Why**: `LoadedPlugin` already carries manifest-level data and mutable runtime state (`contributed_skills`). Handler callables are resolved at discovery time alongside existing validation. A separate wrapper type would add complexity without clear benefit.
**Alternatives Considered**: Separate `ResolvedPlugin` dataclass wrapping `LoadedPlugin` (cleaner separation but adds another type to thread through the system).
**Consequences**: Pro: Single type flows through the entire plugin pipeline. Pro: Consistent with existing pattern of `contributed_skills` being populated after discovery. Con: `LoadedPlugin` gains three more fields (mitigated: all optional/default-empty).

### Active-flag wrapper for event unsubscription

**Choice**: Wrap each plugin event handler in a closure that checks an `_active` flag before dispatching. On plugin removal, set `_active = False` on all registered wrappers.
**Why**: bubus `EventBus` (ADR-009) has no `off()` / `remove()` / `unsubscribe()` method. The handlers dict is a `defaultdict(list)` keyed by event class name, but directly manipulating it couples to bubus internals. The active-flag wrapper is self-contained.
**Alternatives Considered**: Direct `bus.handlers[event_key].remove(handler)` (fragile across bubus versions); Maintain separate mapping and re-register all non-removed handlers on removal (complex, doesn't actually remove from bus).
**Consequences**: Pro: No coupling to bubus internals. Pro: Simple implementation. Con: Minor memory retention — deactivated wrappers remain in bus handler list (acceptable since plugin removal is rare).

### Handler validation during discovery (not manifest parsing)

**Choice**: Handler entry points (file existence, import, callable check, signature validation) are validated during `discover()` in the loader, not during `parse_manifest()`.
**Why**: Manifest parsing is a pure TOML/JSON concern — it validates syntax and basic structure. Handler validation requires filesystem access, dynamic imports, and event registry lookups. These are loader concerns. Keeping manifest parsing pure also means CC plugin manifests parse successfully without needing the event registry.
**Alternatives Considered**: Validation during manifest parsing (couples manifest parsing to filesystem and importlib; CC manifests with hooks/events would need the registry to detect and ignore them).
**Consequences**: Pro: Clean separation — manifest parsing handles syntax, loader handles semantics. Pro: Event type validation uses the fully-built registry. Pro: CC manifest handling unchanged.

### Support for sync and async handlers

**Choice**: Both init hooks and event handlers may be sync or async. The lifecycle module calls the function and checks `asyncio.iscoroutine(result)`, awaiting if needed.
**Why**: Plugin authors may write simple sync handlers (e.g., `def init(ctx): ...`) or async handlers (e.g., `async def init(ctx): await some_async_setup()`). Supporting both removes a friction point.
**Alternatives Considered**: Async-only (forces `async def` for trivial handlers); Sync-only (prevents async operations in handlers).
**Consequences**: Pro: Plugin authors choose the style that fits. Con: `asyncio.timeout()` only enforces timeouts for async handlers. Sync blocking handlers bypass the timeout — documented as a known limitation.

### Path resolution convention: `hooks/` and `events/` directories

**Choice**: Module names in `[hooks]` and `[events]` are resolved as `{hooks|events}/{module_name}.py` relative to the plugin install directory.
**Why**: Mirrors the convention used by skill directories — plugin authors declare a directory-relative path. Dedicated directories keep plugin organization clean and make the distinction visible at the filesystem level.
**Alternatives Considered**: Single `handlers/` directory for both hooks and events (less organized); Flat layout (risks collision with other plugin files).
**Consequences**: Pro: Clear directory convention consistent with `skills/`. Con: Plugin authors must create these directories (mitigated: discovery fails with clear diagnostic if expected file doesn't exist).

### Dedicated database table for plugin state

**Choice**: New `plugin_state` SQLAlchemy table with a `PluginStateRepository`, following the project's established persistence pattern (ADR-007).
**Why**: Plugin version and update status must survive process restarts. A dedicated table provides structured, queryable storage with type-safe access through the repository pattern already used throughout the project.
**Alternatives Considered**: JSON sidecar file (co-locates transient state with plugin files, risks getting out of sync); Key-value app_state table (ADR-013, mixes plugin-specific tracking with general-purpose state).
**Consequences**: Pro: Structured, queryable, follows established patterns. Con: Requires an inline migration in `database.py`.

### `git ls-remote` for update detection

**Choice**: Use `git ls-remote` to resolve the remote ref SHA without downloading any git objects, then compare with the stored `installed_version`.
**Why**: `git ls-remote` performs a single HTTP request (~1-5 KB transfer) returning only the ref-to-SHA mapping. Significantly lighter than `git fetch` for a detection-only check.
**Alternatives Considered**: `git fetch` in existing clone (downloads objects unnecessarily for detection-only); HTTP HEAD request against GitHub API (GitHub-specific).
**Consequences**: Pro: Minimal network overhead, no local state mutation during detection. Con: Needs `^{}` suffix for annotated tags to dereference to commit SHAs.

### Re-clone for update application

**Choice**: Reuse the existing `materialize_git()` function (fresh shallow clone) for applying git updates, rather than `git fetch` + `git checkout` in the existing clone.
**Why**: Re-cloning reuses the battle-tested materializer and its atomic-swap logic. An in-place fetch+checkout would require new error paths for dirty working trees and shallow-boundary issues.
**Alternatives Considered**: In-place `git fetch --depth=1` + `git checkout` (introduces git state management complexity).
**Consequences**: Pro: Reuses proven materializer code. Pro: Clean state. Con: Downloads objects twice (once for ls-remote detection, once for clone — but clone is user-initiated and expected).

### Per-plugin asyncio.Lock for update concurrency

**Choice**: A lazily-populated `dict[str, asyncio.Lock]` on `PluginManager`. Non-blocking acquisition returns "update already in progress" error if held. Lock released in `finally` block.
**Why**: Per-plugin locks allow different plugins to be updated concurrently. Using the existing global `_lock` would serialize everything unnecessarily.
**Alternatives Considered**: Global PluginManager lock for updates (prevents concurrent updates for different plugins); `set[str]` tracking in-progress aliases (requires careful cleanup on all error paths; `asyncio.Lock` handles this naturally).
**Consequences**: Pro: Precise concurrency control. Con: Lock map needs cleanup when plugins are removed.

### Symlink creation for local sources

**Choice**: Replace `shutil.copytree()` with `os.symlink()` for local-source plugins during materialization. Existing copies are replaced with symlinks on the next reconciliation.
**Why**: Local plugins should always reflect live source. A symlink achieves this trivially — no copy, no update needed.
**Migration path**: During reconciliation, if a local-source plugin's install directory is not a symlink, remove the directory and create a symlink. If the configured source path no longer exists, retain the copy and mark as `stale-fallback`.
**Alternatives Considered**: Keep copy + filesystem watcher (over-engineered); Keep copy + periodic sync (defeats "always reflect live source" requirement).
**Consequences**: Pro: Zero-copy, always current. Con: Symlinks break if the source path is moved or deleted.

### LoadedPlugin unchanged; state looked up at query time

**Choice**: `LoadedPlugin` does not gain update-status fields. Update status comes from `PluginState` lookup when needed (e.g., in `list_plugins`).
**Why**: `LoadedPlugin` is a frozen dataclass representing in-memory registration state. Update status is persistence-layer data that changes asynchronously (daily checks). Coupling them would force updates to `LoadedPlugin` instances on every status change.
**Alternatives Considered**: Add update fields to LoadedPlugin (would require re-creating the frozen dataclass on every status change).
**Consequences**: Pro: Clean separation of concerns, LoadedPlugin stays unchanged. Con: `list_plugins` needs a join between in-memory plugins and database state (a simple dict lookup by alias).

### Event-driven provider registration follows skills listener pattern

**Choice**: Provider pipeline registration/unregistration is handled by event listeners (`PluginInstalled`, `PluginRemoving`), not by `PluginManager` directly.
**Why**: Same decoupling pattern as the skills system — `PluginManager` has no pipeline dependencies, and all registration logic is owned by a listener module. The manager just dispatches lifecycle events; the listener translates them into pipeline mutations.
**Alternatives Considered**: Manager receives pipeline references and registers directly (couples manager to pipeline internals); Provider registration inside `lifecycle.py` (conflates different concerns).
**Consequences**: Pro: `PluginManager` unchanged — zero new dependencies. Pro: Consistent with established pattern. Con: One more listener module to maintain.

### Provider instantiation during discovery, not during registration

**Choice**: Provider instances are created during `_validate_providers()` in the loader, not during the `PluginInstalled` event handler.
**Why**: The loader already has the validated config dict and the plugin directory. Instantiation during discovery allows validation to catch constructor signature errors early. The event handler receives pre-created instances and simply registers them.
**Alternatives Considered**: Lazy instantiation during `PluginInstalled` (defers validation); Instantiation in the listener (adds unnecessary dependencies).
**Consequences**: Pro: Early validation with clear diagnostics. Pro: Listener is a thin routing layer.

### Identity-based pipeline unregistration

**Choice**: Pipelines use `list.remove(provider)` (identity-based) in `unregister()`, not name-based or class-based lookup.
**Why**: Provider instances are unique objects. Identity-based removal ensures the exact instance is removed. `list.remove()` is O(n) but provider lists are small (typically 1-5 entries per pipeline).
**Alternatives Considered**: Name-based removal (requires unique names on the ABC); Dict-based registration (more complex for a rarely-called operation).
**Consequences**: Pro: Simple and correct. Pro: No new provider naming requirements.

### ABC inspection for pipeline routing

**Choice**: Provider classes are inspected for `issubclass(cls, ContextProvider)` and `issubclass(cls, MessageContextProvider)` to determine which pipeline(s) to register in. Plugin authors don't specify the target pipeline in the manifest.
**Why**: The ABC a class implements is the authoritative declaration of its behavior. Requiring a manifest-level routing field would create a second source of truth that could diverge from the actual implementation.
**Alternatives Considered**: Manifest-level `pipeline` field (creates coupling); Separate manifest sections for session/message providers (prevents a single class from implementing both).
**Consequences**: Pro: Single source of truth — the ABC determines routing. Pro: Plugin authors only need to implement the right interface. Con: Plugin authors must understand which ABC to implement.

### Dual-ABC provider instance model

**Choice**: When a class implements both `ContextProvider` and `MessageContextProvider`, a single instance is created and registered in both pipelines.
**Why**: The provider is a single stateful object. Creating two instances would duplicate any internal state. Both `provide()` methods operate on the same instance identity.
**Consequences**: Pro: Single instance, no duplicated state. Pro: `unregister()` from both pipelines removes the exact same object.

## System Behavior

### Invariants

1. **Atomic install state**: `.tachikoma/plugins/<alias>/` either contains content from a prior successful materialization or does not exist — never half-written.
2. **Validate-then-write order**: `install_plugin` never mutates config until source is materialized and manifest parsed.
3. **Per-plugin failure isolation**: Any plugin failing at any stage does not affect other plugins or core startup.
4. **Namespace isolation**: Plugin skills stored under `<alias>:<name>` cannot collide with default-namespace or other plugin skills.
5. **First-time-only materialization**: Startup reconciliation only materializes plugins that don't have an existing install directory. Already-installed plugins are left untouched.
6. **Config values are always validated**: A loaded plugin's `config` dict contains only values that passed type checking. No unvalidated user input reaches plugin code.
7. **Config schema is optional**: Plugins without `[config]` sections load normally with `config = {}`. Users without `[plugins.<alias>.config]` sub-tables get default values (or empty dict if no schema).
8. **Consolidation is additive**: Plugin failures are appended to the restart notification. Each can appear independently (restart only, failures only, both, neither).
9. **Init-before-subscribe**: Event subscriptions are only registered after a successful init hook. Plugins without init hooks have events registered immediately.
10. **Sequential init**: Init hooks run one at a time in alphabetical order by alias. No parallel execution.
11. **Handler validation is complete**: A plugin with invalid handlers (missing file, import error, wrong signature, unknown event type) fails at discovery with a diagnostic — no partial handler registration.
12. **Unsubscription is deactivation**: Removed plugins' event wrappers become no-ops but remain in the bus's handler list (bubus has no `off()` API).
13. **Version hashes captured at materialization**: Every materialization (first install or update) captures the version hash and persists it to `PluginState`.
14. **Detection does not mutate on-disk state**: `git ls-remote` only reads remote refs. It does not fetch, modify the local clone, or change any files.
15. **Update application is atomic**: The pip-style atomic swap applies to updates as well as installs. A failed update never leaves a half-replaced directory.
16. **Re-registration is all-or-nothing**: On successful materialization, old skills/events are fully removed before new ones are registered. On re-registration failure, old skills/events remain active.
17. **Local plugins have no version state**: Symlinks are always current. `installed_version` is null, `update_status` is not queried.
18. **Daily check is stateless**: Each run checks all git-source plugins independently. A partially completed check does not affect the next run.
19. **PluginState survives restarts**: The database table is the source of truth for version and update status across process restarts.
20. **Provider instance identity**: A provider class implementing both `ContextProvider` and `MessageContextProvider` results in a single instance tracked in both `LoadedPlugin.context_providers` and `LoadedPlugin.message_context_providers`. Unregistration from both pipelines removes the same object reference.
21. **Provider validation is complete**: A plugin with invalid context providers (missing file, import error, no valid ABC class, wrong constructor signature) fails at discovery with a diagnostic — no partial provider registration.

### Scenario: Fresh workspace, no plugins configured

**Given**: `.tachikoma/plugins/` does not exist; `[plugins]` is absent or empty.
**When**: `plugins_hook` runs.
**Then**: Directory created; `.gitignore` entry appended; empty reconciliation; empty `PluginManager`.
**Rationale**: Empty configuration is a valid initial state.

### Scenario: Single git plugin, happy path

**Given**: `[plugins.code-review]` declares a git source with `ref = "v1.0.0"`. Network is reachable.
**When**: `plugins_hook` runs.
**Then**: Shallow clone fetches at v1.0.0; atomic-swap into install dir; manifest parses; skills registered under `code-review:*`.
**Rationale**: Happy path validates the full pipeline end-to-end.

### Scenario: Source unreachable, valid stale copy on disk

**Given**: `[plugins.code-review]` declares a git source. Network is unreachable. `.tachikoma/plugins/code-review/` exists from a prior successful run.
**When**: `plugins_hook` runs.
**Then**: Clone fails; reconciler reads on-disk manifest; confirms name matches alias; retains copy with status `stale-fallback`; WARNING logged.
**Rationale**: Resilient reconciliation prevents transient failures from disabling working plugins.

### Scenario: Concurrent install attempts for different aliases

**Given**: Two `install_plugin` calls are dispatched concurrently.
**When**: Both are dispatched to `PluginManager.install`.
**Then**: Manager's `asyncio.Lock` serializes them — first completes fully before second begins.
**Rationale**: Coarse-grained locking prevents torn config writes and concurrent reconciliation.

### Scenario: Removal with active session entries

**Given**: Active session has context entries with `metadata["skill_name"] = "code-review:linter"`.
**When**: `remove_plugin("code-review")` runs.
**Then**: `PluginRemoving` dispatched → listener marks entries (notice prepended, `deleted=True`). Config entry removed, directory deleted, `PluginRemoved` dispatched → listener removes namespaced source from registry. Entries continue to function for rest of session.
**Rationale**: Soft removal preserves session continuity while informing agent of on-disk reality.

### Scenario: remove_plugin succeeds at config write but rmtree fails

**Given**: Config entry is dropped but directory deletion fails (permission error).
**When**: The tool runs.
**Then**: rmtree error recorded in diagnostic; `_loaded[alias]` still deleted; `PluginRemoved` still dispatched. Tool response includes diagnostic. Orphan cleaned by next reconcile.
**Rationale**: Config is source of truth; internal state must follow config.

### Scenario: Skill dependency resolution — plugin skill depends on built-in via bare name

**Given**: Plugin skill `code-review:planner` declares `depends_on: ["workflow-authoring-guide"]` (bare name). `workflow-authoring-guide` is a built-in skill.
**When**: `resolve_chain("code-review:planner")` runs.
**Then**: Bare name resolved in default namespace; chain returns `[workflow-authoring-guide, code-review:planner]`.
**Rationale**: Bare names mean default namespace, supporting the common case.

### Scenario: Skill dependency resolution — plugin skill depends on a sibling via `:dep`

**Given**: Plugin skill `code-review:planner` declares `depends_on: [":linter"]`. Sibling `code-review:linter` exists.
**When**: `resolve_chain("code-review:planner")` runs.
**Then**: Leading colon stripped, resolved within anchor's namespace; chain returns `[code-review:linter, code-review:planner]`.
**Rationale**: `:dep` is the explicit sibling-within-this-plugin syntax.

### Scenario: Plugin with required config field, user provides value

**Given**: Manifest declares `[config.api_key]` with `type = "string"`, `required = true`. User config has `[plugins.weather.config]` with `api_key = "sk-..."`.
**When**: Plugin discovery runs.
**Then**: `validate_config()` finds the required field present with correct type. `LoadedPlugin.config = {"api_key": "sk-..."}`.
**Rationale**: Happy path — user provides all required config.

### Scenario: Plugin with required config field, user omits value

**Given**: Manifest declares `[config.api_key]` with `type = "string"`, `required = true`. No `[plugins.weather.config]` section.
**When**: Plugin discovery runs.
**Then**: Plugin status is `"failed"` with diagnostic: "Required config field 'api_key' is missing".
**Rationale**: Fail-safe — required fields must be provided.

### Scenario: Config type mismatch

**Given**: Manifest declares `[config.timeout]` with `type = "integer"`. User provides `timeout = "thirty"` (string).
**When**: Plugin discovery runs.
**Then**: Plugin status is `"failed"` with diagnostic about expected vs actual type.
**Rationale**: Strict type checking prevents runtime errors.

### Scenario: Unknown user config key

**Given**: Manifest declares `[config.api_key]`. User provides both `api_key` and `base_url` (undeclared).
**When**: Plugin discovery runs.
**Then**: Plugin loads normally. `base_url` triggers a WARNING log. Unknown key is NOT in runtime config.
**Rationale**: Forward-compatible — unknown keys may be from a newer plugin version.

### Scenario: Plugin with all-default config, no user values

**Given**: Manifest declares fields with defaults. No `[plugins.weather.config]` section.
**When**: Plugin discovery runs.
**Then**: `LoadedPlugin.config` populated with default values.
**Rationale**: Defaults apply when no user values are present.

### Scenario: CC plugin with user config values

**Given**: A CC plugin with no `tachikoma-plugin.toml`. User provides config values.
**When**: Plugin discovery runs.
**Then**: CC manifest has no config schema. User values are warned as unknown keys. Plugin loads with `config = {}`.
**Rationale**: User config for a CC plugin without a schema is harmlessly ignored.

### Scenario: Consolidated startup notification — restart + plugin failures

**Given**: Restart notification marker exists. Two plugins failed config validation.
**When**: `handle_restart_notification` runs after bootstrap.
**Then**: Restart marker cleared (DES-011 consume-once). Single notification dispatched with restart content and plugin failure summary.
**Rationale**: Single message is less noisy than separate notifications.

### Scenario: Startup notification — plugin failures only, no restart

**Given**: No restart notification marker. One plugin failed config validation.
**When**: `handle_restart_notification` runs.
**Then**: Plugin failure summary dispatched as standalone notification.
**Rationale**: Plugin failures are worth notifying even without a restart signal.

### Scenario: Startup notification — no restart, no failures

**Given**: No restart notification marker. All plugins loaded successfully.
**When**: `handle_restart_notification` runs.
**Then**: No notification dispatched.
**Rationale**: No news is good news.

### Scenario: Plugin with init hook and event subscriptions (happy path)

**Given**: A plugin with `[hooks] init = "init"` and `[events] coordinator_idle = "on_idle"`. Both handler files exist and are valid.
**When**: Startup completes.
**Then**: Init hook runs with PluginContext. On success, `on_idle` handler subscribed to `CoordinatorIdle`. Subsequent `CoordinatorIdle` events invoke the handler.
**Rationale**: Full lifecycle — init prepares plugin, events keep it reacting.

### Scenario: Init hook raises exception

**Given**: Plugin A's init hook raises `ValueError`. Plugin B has a working init hook.
**When**: Init hooks execute.
**Then**: Plugin A's error logged at ERROR level. Plugin A's event subscriptions NOT registered. Plugin B's hook runs normally. Application startup proceeds.
**Rationale**: Per-plugin isolation — one plugin's failure is contained.

### Scenario: Init hook exceeds 30-second timeout

**Given**: Plugin's init hook is async and takes longer than the timeout threshold.
**When**: `asyncio.timeout()` fires.
**Then**: `TimeoutError` caught, WARNING logged with plugin alias and timeout duration. Plugin's event subscriptions not registered.
**Rationale**: Timeout prevents a stuck plugin from blocking startup indefinitely.

### Scenario: Plugin with no init hook but with event subscriptions

**Given**: A plugin with `[events] notification = "on_notify"` but no `[hooks]` section.
**When**: Startup lifecycle runs.
**Then**: No init hook called. Event handler subscribed immediately.
**Rationale**: Init hooks are optional. Event subscriptions work independently.

### Scenario: Event handler raises exception during dispatch

**Given**: Plugin A's `handle` raises during `CoordinatorIdle` dispatch. Plugin B also subscribes to `CoordinatorIdle`.
**When**: Event is dispatched.
**Then**: Plugin A's error logged at ERROR level with plugin alias and event type. Plugin B's handler still invoked. Dispatch completes normally.
**Rationale**: Event handler errors are isolated per-plugin per-handler.

### Scenario: Plugin removed at runtime

**Given**: Plugin with active event subscriptions.
**When**: `remove_plugin` runs.
**Then**: `PluginRemoving` dispatched. Event wrappers deactivated (`_active = False`). Config removed, directory deleted. `PluginRemoved` dispatched.
**Rationale**: Clean removal — deactivation ensures no further handler invocations.

### Scenario: Plugin installed at runtime with init hook

**Given**: `install_plugin` called for a plugin declaring `[hooks] init = "init"`.
**When**: Installation completes.
**Then**: Materialize + validate succeeds. Init hook runs. On success, event subscriptions registered. `PluginInstalled` dispatched.
**Rationale**: Runtime install mirrors startup lifecycle — init first, then subscribe, then announce.

### Scenario: CC plugin with hooks/events contributions

**Given**: A CC plugin with `"hooks": {"init": "init.py"}` or `"events"` in `plugin.json`.
**When**: Manifest parsed.
**Then**: `"hooks"` and `"events"` detected as ignored CC contribution types. INFO log emitted. Plugin loads normally with no hooks/events support.
**Rationale**: Consistent with existing CC contribution handling.

### Scenario: Unknown event type name in manifest

**Given**: `[events] unknown_event = "handler"` in a Tachikoma-native manifest.
**When**: Handler validation runs during discovery.
**Then**: Plugin fails with diagnostic: "Unknown event type 'unknown_event'. Valid types: coordinator_idle, notification, ..."
**Rationale**: Fail-fast — invalid event references caught early with a helpful error.

### Scenario: Handler module with wrong signature

**Given**: `events/on_idle.py` with `def handle(event, ctx, extra):` (3 params instead of 2).
**When**: Handler validation runs during discovery.
**Then**: Plugin fails with diagnostic about expected signature.
**Rationale**: Strict signature validation prevents runtime errors from mismatched handler signatures.

### Scenario: Fresh install of a git plugin (version captured)

**Given**: `[plugins.code-review]` declares a git source with `ref = "v1.0.0"`. No existing install directory.
**When**: Startup reconciliation runs.
**Then**: `materialize_git()` performs shallow clone, captures commit SHA as version. `_atomic_replace_dir()` installs. `PluginState` created with `installed_version = <sha>`, `update_status = "unknown"`.
**Rationale**: First install captures version for future comparison.

### Scenario: Startup with existing git plugin (no re-materialize)

**Given**: `[plugins.code-review]` declares a git source. `.tachikoma/plugins/code-review/` exists with valid manifest.
**When**: Startup reconciliation runs.
**Then**: Reconciler detects existing directory, skips materialization. `PluginState` retains previous values.
**Rationale**: No unnecessary re-cloning on every restart.

### Scenario: Daily check finds update

**Given**: Git plugin `code-review` tracking branch `main`. Stored `installed_version = abc123`. Remote `refs/heads/main` now resolves to `def456`.
**When**: Daily update check runs.
**Then**: `git ls-remote` returns `def456`. Compared with stored `abc123` — differs. `PluginState` updated: `update_status = "update-available"`, `available_version = "def456"`. Notification dispatched listing `code-review`.
**Rationale**: User is informed that an update exists and can choose to apply it.

### Scenario: Daily check finds no update

**Given**: Git plugin `code-review`. Remote SHA matches stored `installed_version`.
**When**: Daily update check runs.
**Then**: `PluginState` updated: `update_status = "up-to-date"`. No notification.
**Rationale**: No news is good news.

### Scenario: Daily check encounters network error

**Given**: Git plugin `code-review`. Remote is unreachable.
**When**: Daily update check runs.
**Then**: `git ls-remote` fails. Previous `update_status` retained. `last_checked_at` updated. Error logged at WARNING level.
**Rationale**: Transient network issues should not flip plugin status.

### Scenario: update_plugin for git plugin

**Given**: Git plugin `code-review` with `update_status = "update-available"`.
**When**: `update_plugin("code-review")` invoked.
**Then**: Acquire per-plugin lock. `materialize_git()` re-clones at the configured ref. Atomic swap replaces install directory. `PluginState` updated: `installed_version = <new sha>`, `update_status = "up-to-date"`. `PluginRemoving` dispatched (old skills removed), `PluginInstalled` dispatched (new skills registered). Lock released.
**Rationale**: Full lifecycle — materialize, persist, re-register.

### Scenario: update_plugin while update in progress

**Given**: `update_plugin("code-review")` is running.
**When**: Another `update_plugin("code-review")` is invoked.
**Then**: Per-plugin lock is held. Non-blocking acquisition fails. Tool returns error: "Update already in progress for plugin 'code-review'".
**Rationale**: Prevent concurrent modifications to the same plugin.

### Scenario: update_plugin fails during materialization

**Given**: Git plugin `code-review`. Remote is unreachable during re-clone.
**When**: `update_plugin("code-review")` invoked.
**Then**: `materialize_git()` raises. Staging directory cleaned up. Existing install directory untouched. `PluginState` unchanged. Lock released in `finally`. Error returned.
**Rationale**: Failed updates must not corrupt existing state.

### Scenario: update_plugin succeeds but re-registration fails

**Given**: Plugin update materializes successfully, but new manifest declares a skill with a name that collides with an existing plugin.
**When**: Re-registration runs.
**Then**: Old skills remain registered. New materialized version stays on disk. `PluginState` gains a diagnostic about the re-registration failure. Error returned.
**Rationale**: Spec requires old skills stay active on re-registration failure.

### Scenario: update_plugin for local plugin

**Given**: Local plugin `dev-tools` (symlink-based).
**When**: `update_plugin("dev-tools")` invoked.
**Then**: Tool returns informational message: "Local plugins are always current (symlink-based). No update needed."
**Rationale**: Symlinks reflect live source at all times.

### Scenario: update_all_plugins with mixed results

**Given**: Three plugins: `code-review` (update-available), `weather` (up-to-date), `dev-tools` (local).
**When**: `update_all_plugins()` invoked.
**Then**: `code-review` updated (success). `weather` skipped (already current). `dev-tools` skipped (local). Summary returned: `{total: 3, updated: 1, skipped: 2, failed: 0, results: [...]}`.
**Rationale**: Bulk operation handles each plugin independently; failures don't block others.

### Scenario: Local plugin migration from copy to symlink

**Given**: Local plugin `dev-tools` was installed before this feature as a directory copy. Source path still exists.
**When**: Startup reconciliation runs.
**Then**: Reconciler detects install directory is not a symlink and source is local. Replaces the directory with a symlink pointing to the configured path. `PluginState` updated: `installed_version = null`.
**Rationale**: Existing installs migrate seamlessly.

### Scenario: Local plugin migration — source path gone

**Given**: Local plugin `dev-tools` installed as copy. Configured source path no longer exists.
**When**: Startup reconciliation runs.
**Then**: Migration skipped. Existing copy retained. `PluginState`: `update_status = "stale-fallback"` with diagnostic about missing source path.
**Rationale**: Don't break existing functionality when source disappears.

### Scenario: list_plugins with update info

**Given**: `code-review` has `update_status = "update-available"`, `installed_version = "abc123"`, `available_version = "def456"`.
**When**: `list_plugins()` invoked.
**Then**: Output for `code-review` includes `update_status: "update-available"`, `installed_version: "abc123"`, `available_version: "def456"`.
**Rationale**: User can see at a glance which plugins need attention.

### Scenario: Plugin with session-gated context provider

**Given**: A `tachikoma-plugin.toml` with `[context_providers] calendar = "calendar"`. Module `context_providers/calendar.py` contains a class subclassing `ContextProvider` with valid `__init__(self, *, config)` and implemented `provide()` and `status_message()`.
**When**: Plugin loads.
**Then**: Provider discovered, instantiated with `config={validated_config}`, stored on `LoadedPlugin.context_providers`. On `PluginInstalled`, registered into `PreProcessingPipeline`. Next `pipeline.run()` includes the provider.
**Rationale**: Full happy path for session-gated provider pipeline.

### Scenario: Provider implementing both ABCs

**Given**: A provider class that subclasses both `ContextProvider` and `MessageContextProvider`.
**When**: Plugin loads.
**Then**: Single instance created, registered in both `PreProcessingPipeline` and `MessagePreProcessingPipeline`. Both provider lists on `LoadedPlugin` reference it.
**Rationale**: Dual-ABC support per design decision.

### Scenario: Provider module with no valid class

**Given**: Module `context_providers/calendar.py` containing only utility functions, no class implementing either ABC.
**When**: Discovery runs.
**Then**: Plugin fails with diagnostic: "No concrete class implementing ContextProvider or MessageContextProvider found in module 'calendar'".
**Rationale**: Fail-fast catches configuration errors early.

### Scenario: Plugin removed with active context providers

**Given**: Plugin with registered providers in both pipelines.
**When**: `remove_plugin` runs.
**Then**: `PluginRemoving` dispatched → listener unregisters providers from both pipelines. Event wrappers deactivated. Config removed. Directory deleted. `PluginRemoved` dispatched.
**Rationale**: Clean removal of providers alongside other plugin contributions.

### Scenario: Plugin updated with new context providers

**Given**: Plugin with registered providers.
**When**: `update_plugin` runs with a new version.
**Then**: `PluginRemoving` dispatched → old providers unregistered. New plugin discovered with new provider instances. `PluginInstalled` dispatched → new providers registered.
**Rationale**: Full lifecycle re-registration for context providers.

### Scenario: Plugin update fails provider validation

**Given**: Updated plugin whose new provider fails validation (e.g., missing file).
**When**: Re-registration runs.
**Then**: Old providers remain active in pipelines. New materialized version stays on disk. Diagnostic stored in `PluginState`.
**Rationale**: All-or-nothing re-registration — existing providers stay active on failure.

### Scenario: CC plugin with context_providers contribution

**Given**: CC plugin with `"context_providers": {"calendar": "calendar"}` in `plugin.json`.
**When**: Manifest parsed.
**Then**: `"context_providers"` detected as an ignored CC contribution type. INFO log emitted. Plugin loads normally with no provider support.
**Rationale**: Consistent with existing CC contribution handling.

## Notes

- The plugin system reuses `tachikoma.git.sync.run_git()` for git materialization — no GitPython or dulwich dependency added.
- CC plugins declaring non-skill contributions install successfully but those contributions are silently ignored (INFO log per type per plugin), enabling forward compatibility with future plugin contribution hooks (custom context providers, post-processors, bundled skills, secondary channels).
- The `_namespaced_source_paths` tracking dict on `SkillRegistry` keeps install/remove symmetric — preventing monotonic growth of source lists across many install/remove cycles.
- Plugin-specific configuration is managed through the existing TOML config under `[plugins]`, not through per-plugin config files.
- Config values are NOT reloaded at runtime — changes to `config.toml` require a restart.
- Config values appear in `list_plugins` output and may be logged. Sensitive field marking and redaction are deferred to a future delta.
- The startup notification consolidation follows DES-011 consume-once semantics — the restart marker is cleared unconditionally before any side effects.
- Handler import uses `importlib.util.spec_from_file_location` with unique module keys (`tachikoma_plugin.{alias}.{hooks|events}.{module_name}`) to avoid `sys.modules` collisions.
- The `asyncio.timeout()` approach (Python 3.11+) only enforces timeouts for async handlers. Sync blocking handlers are not timeout-protected — documented as a known limitation.
- bubus uses event class `__name__` as the handler key (per ADR-009). The registry's snake_case derivation matches this convention.
- Plugin event wrappers use an active-flag pattern (`_deactivate()`) for unsubscription since bubus has no `off()` API. Deactivated wrappers become no-ops but remain in the bus's handler list.
- Init hooks run post-bootstrap (not as a DES-003 bootstrap hook) to guarantee all subsystems are initialized before plugin hooks execute.
- The daily update check uses `CronTrigger` on the central scheduler (DES-010), NOT a `TaskDefinition`. It is a scheduler Job (a zero-arg async tick function), not a task definition with instance generation and status tracking. The job is registered in `__main__.py` alongside other scheduler jobs.
- Annotated tag dereferencing (`refs/tags/<tag>^{}`) is critical for correct SHA comparison — without it, annotated tags return the tag object SHA, not the commit SHA.
- The per-plugin lock map is lazily populated and cleaned up on plugin removal. This avoids pre-allocating locks for plugins that never get updated.
- `hashlib.file_digest()` is a Python 3.11+ addition that handles streaming internally — no manual chunked reads needed.
- Existing databases get the `plugin_state` table via inline migration in `database.py`'s `_run_migrations()`. Pre-existing plugins get `PluginState` rows created during their first reconciliation (status "unknown").
- The update detector reuses `run_git()` from `git/sync.py` for `git ls-remote` — same subprocess wrapper, same error handling.
- Provider modules live in `context_providers/<module_name>.py` relative to the plugin install directory — consistent with the `hooks/` and `events/` directory conventions. The `importlib.util.spec_from_file_location` module key uses `tachikoma_plugin.{alias}.context_providers.{module_name}` to avoid `sys.modules` collisions (same pattern as hooks and events).
- Provider instances are created once during discovery and reused across the plugin's lifetime. No per-message instantiation. The `unregister()` method on pipelines is a safe no-op if the provider is not found in the list — handles edge cases where the same provider might be unregistered twice (e.g., during failed re-registration rollback).
- Provider classes that are abstract (have unimplemented abstract methods) are skipped during discovery — only concrete classes are registered. This allows plugin authors to define base classes in the same module.
- The `__main__.py` wiring point for provider listeners is immediately after pipeline creation and before coordinator construction, ensuring providers are registered before any message processing begins.
- Adding `context_providers` and `message_context_providers` fields to `LoadedPlugin` is backward compatible — both use `field(default_factory=list)`, so existing code constructing `LoadedPlugin` without these fields continues to work.
