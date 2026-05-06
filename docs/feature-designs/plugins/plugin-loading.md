# Design: Plugin Loading

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/plugins/plugin-loading.md](../../feature-specs/plugins/plugin-loading.md)
**Status**: Current

## Purpose

This document explains the design rationale for the plugin loading system: how plugin sources are modeled and materialized, how manifests are parsed, how skills are registered under namespaced names, how lifecycle hooks and event subscriptions extend the plugin system, how event-based decoupling keeps the plugin manager and skills subsystem cleanly separated, and how MCP tools enable runtime plugin management.

## Problem Context

Tachikoma's capabilities live in two layers — a core agent runtime and a per-workspace `skills/` directory. To grow the ecosystem without forcing every contribution through core, the assistant needs an extension surface that lets third-party plugins ship reusable skills declared in config, materialized at startup from a configured source, and registered into the existing `SkillRegistry` without colliding with built-in or workspace skills.

**Constraints:**
- **Reuse, don't parallel.** Plugin skills must flow into the same `SkillRegistry` consumed by the per-message classifier and the coordinator's agent derivation.
- **Skills only.** Plugins contributing MCP tool servers, slash commands, context providers, post-processors, secondary channels, and boundary hooks are deferred to future deltas. CC plugins that declare those surfaces install successfully, but their non-skill contributions are silently ignored. Plugins can declare init hooks and event subscriptions.
- **Fail-safe.** A broken plugin must never block startup or affect other plugins. Per-plugin failure isolation applies in materialization, discovery, manifest parsing, and registry registration.
- **CC compatibility.** Plugins shaped as Claude Code plugins are recognized so Tachikoma can benefit from the existing CC plugin community.
- **Atomic on-disk state.** Mid-write failures during reconciliation must not leave a half-replaced plugin folder.
- **No new long-lived dependencies.** The codebase reuses existing `git` subprocess and `tomlkit` rather than adding GitPython, dulwich, or similar.

**Interactions:**
- **`SkillRegistry`** — gains `add_namespaced_source(alias, path)` and `remove_namespaced_source(alias)`; `Skill` gains `namespace` and `qualified_name`.
- **Bootstrap** — a new `plugins_hook` registers between `git_hook` and `skills_hook`.
- **Configuration** — adds `plugins: dict[str, PluginSource]` with `update_plugin_entry` / `remove_plugin_entry` helpers.
- **Event bus** (ADR-009) — three new events: `PluginInstalled`, `PluginRemoving`, `PluginRemoved`. Plugin event handlers subscribe via `bus.on(EventType, wrapped_handler)`.
- **Sessions** — on `PluginRemoving`, active session's already-injected plugin skill entries get a removal notice and `metadata["deleted"] = True`.
- **Lifecycle hooks** — plugins can declare init hooks (run post-bootstrap, before channel start) and event subscriptions (routed through the event bus with per-plugin isolation).
- **Handler validation** — hook and event handler entry points are validated at discovery time (file exists, imports, callable present, correct signature, known event types).

## Design Overview

The plugin system is a self-contained `tachikoma/plugins/` package whose components map onto the existing subsystem pattern (bootstrap hook, manager class, event types, MCP tool factory). It plugs into the `SkillRegistry` at exactly one point — `add_namespaced_source(alias, path)` — and otherwise communicates outward via three typed events on the project's event bus.

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
├──────────────────────────────────────────────────────────────┤
│ plugins/ package                                             │
│  reconciler.py   walks config, dispatches per source kind    │
│  materializer.py git / url / local + atomic-swap             │
│  loader.py       scans install dir, parses manifests         │
│                 + handler validation (hooks/events)          │
│  manager.py      state + install/remove/list                 │
│                 + init+subscribe on install, unsubscribe      │
│  tools.py        MCP tools factory                           │
│  events.py       PluginInstalled / Removing / Removed        │
│  context.py      PluginContext frozen dataclass               │
│  registry.py     auto-discovered event type registry         │
│  lifecycle.py    init executor + subscribe/unsubscribe       │
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
| `src/tachikoma/plugins/materializer.py` | `materialize_git()`, `materialize_url()`, `materialize_local()` async functions; `_atomic_replace_dir()` helper | Stdlib only — `urllib`, `tarfile`, `zipfile`, `shutil`, `os`. Atomic-swap follows pip pattern |
| `src/tachikoma/plugins/reconciler.py` | `reconcile()` walks config; dispatches to materializer; handles stale-fallback and orphan removal | Always-on re-materialization; per-plugin try/except |
| `src/tachikoma/plugins/loader.py` | `discover()` scans install dir; parses manifests; validates skill directories; validates config against schema; validates hook/event handler entry points | Runs after reconciler; failure of one plugin's manifest or config does not affect others; handler validation imports modules via `importlib.util` and checks signatures via `inspect` |
| `src/tachikoma/plugins/manager.py` | `PluginManager` class with `list()`, `install()`, `remove()`, `failed_plugins()`; owns `asyncio.Lock` | Single class matching tasks subsystem pattern; install runs init hook + subscribes events before `PluginInstalled`; remove unsubscribes during `PluginRemoving` |
| `src/tachikoma/plugins/context.py` | `PluginContext` frozen dataclass | Frozen to prevent mutation by handlers; carries config + bus + alias + install_path |
| `src/tachikoma/plugins/registry.py` | `build_event_registry()` auto-discovers `BaseEvent` subclasses; `get_event_type(name)` lookup | Imports all known event modules explicitly, walks `__subclasses__()` recursively; snake_case derived via regex |
| `src/tachikoma/plugins/lifecycle.py` | `run_plugin_init_hooks()`, `init_plugin()`, `subscribe_plugin_events()`, `unsubscribe_plugin_events()` | Init hooks run with 30-second timeout; sync and async handlers both supported; active-flag wrapper for unsubscribe (bubus has no `off()`) |
| `src/tachikoma/plugins/events.py` | `PluginInstalled`, `PluginRemoving`, `PluginRemoved` event types | Follows ADR-009; `PluginRemoving` fires before directory deletion |
| `src/tachikoma/plugins/tools.py` | `create_plugin_tools_server()` factory; three closure-captured tool functions | Follows DES-006; extracted handlers for testability; `list_plugins` includes config schema/values/diagnostics |
| `src/tachikoma/plugins/hooks.py` | `plugins_hook()` bootstrap callback | Follows DES-003; registered between git and skills hooks |
| `src/tachikoma/skills/listeners.py` | `register_plugin_event_listeners()` subscribes to plugin events | Lives in skills module per decoupling decision; owns all registry mutation triggered by plugin events |
| `src/tachikoma/skills/registry.py` (extension) | `add_namespaced_source()`, `remove_namespaced_source()`; `Skill.namespace` + `qualified_name`; `_namespaced_source_paths` tracking | Minimal-change extension — core `_discover` / `_load_skill` flow unchanged for default-namespace skills |
| `src/tachikoma/config.py` (extension) | `plugins: dict[str, PluginSource]` field; `update_plugin_entry()` / `remove_plugin_entry()` methods | tomlkit super-table for sub-table write-back |
| `src/tachikoma/__main__.py` (extension) | Calls `run_plugin_init_hooks()` after `bootstrap.run()`, before channel construction | Post-bootstrap timing ensures all subsystems initialized before plugin hooks run |

### Cross-Layer Contracts

**Bootstrap → PluginManager → SkillRegistry → MCP tools contract:**

```
plugins_hook(ctx)
    ├── 1. Create .tachikoma/plugins/ (mkdir)
    ├── 2. Append .tachikoma/plugins/ to .gitignore (idempotent)
    ├── 3. report = reconcile(workspace_path, settings.plugins)
    ├── 4. plugins = discover(install_dir, plugin_sources)
    │       └── For each plugin: parse manifest → validate config schema
    │           → validate hook/event handlers → LoadedPlugin(init_hook, event_handlers)
    ├── 5. manager = PluginManager(settings_manager, bus, workspace_path, loaded)
    ├── 6. ctx.extras["plugin_manager"] = manager
    └── 7. ctx.extras["plugin_skill_paths"] = [(alias, dir) for each loaded plugin skill dir]

skills_hook(ctx)
    ├── … existing default-source registry construction …
    ├── For (alias, path) in ctx.extras["plugin_skill_paths"]:
    │   └── registry.add_namespaced_source(alias, path)
    └── register_plugin_event_listeners(bus, registry, session_registry)

__main__.py (after bootstrap.run())
    ├── manager = bootstrap.extras["plugin_manager"]
    ├── bus = bootstrap.extras["event_bus"]
    ├── await run_plugin_init_hooks(manager.list_plugins(), bus)
    │       ├── Sort loaded plugins by alias (alphabetical)
    │       ├── For each plugin with status="loaded":
    │       │   ├── If init_hook: create PluginContext, await with 30s timeout
    │       │   │   ├── On success → subscribe_plugin_events(plugin, bus)
    │       │   │   └── On failure → log ERROR, skip subscriptions
    │       │   └── Elif event_handlers: subscribe_plugin_events(plugin, bus)
    │       └── Log summary: initialized N, M failures
    └── plugin_tools_server = create_plugin_tools_server(manager)
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

### Shared Logic

- **`_atomic_replace_dir(new, dst)` helper**: shared between three materializer paths. Owns the pip-style triple-step swap with rollback.
- **`run_git()` import**: reused from `git/sync.py` for the git materializer path.
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

### Manifest models

```
TachikomaManifest (Pydantic)             [tachikoma-plugin.toml]
├── name: str
├── version: str | None = None
├── description: str
├── skills: list[str] = []                (relative paths to skill dirs)
├── config: dict[str, ConfigFieldSchema] = {}  (declared settings from [config.<field_name>])
├── hooks: dict[str, str] = {}            (hook_type → module_name, e.g. "init" → "init")
└── events: dict[str, str] = {}           (event_type_name → module_name, e.g. "coordinator_idle" → "on_idle")

    @field_validator("hooks", "events", mode="after")
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
└── events: dict[str, str] = field(default_factory=dict)
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
└── event_wrappers: list = field(default_factory=list)   (wrapper objects with _deactivate() for unsubscribe)
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

### Startup reconciliation

```
__main__.py → bootstrap.run() → plugins_hook
    ├── mkdir .tachikoma/plugins/
    ├── append .tachikoma/plugins/ to .gitignore (idempotent)
    ├── reconcile(workspace, settings.plugins)
    │   └── For each [plugins.<alias>]:
    │       ├── materialize_<kind>() → temp staging dir
    │       ├── _atomic_replace_dir(staging, install_dir / alias)
    │       └── Record per-alias status (loaded / stale-fallback / failed)
    ├── discover(install_dir)
    │   └── For each plugin: parse manifest → validate config → validate handlers
    ├── PluginManager(settings_manager, bus, workspace_path, loaded)
    └── ctx.extras populated

skills_hook(ctx)
    ├── SkillRegistry([builtin_path, workspace_skills_path])
    ├── for (alias, path) in plugin_skill_paths: add_namespaced_source(alias, path)
    └── register_plugin_event_listeners(bus, registry, session_registry)

__main__.py (after bootstrap)
    ├── await run_plugin_init_hooks(manager.list_plugins(), bus)
    └── register plugin MCP tools server
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
    └── return LoadedPlugin(..., config=validated_config, init_hook=..., event_handlers=...)
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

## Key Decisions

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

## System Behavior

### Invariants

1. **Atomic install state**: `.tachikoma/plugins/<alias>/` either contains content from a prior successful materialization or does not exist — never half-written.
2. **Validate-then-write order**: `install_plugin` never mutates config until source is materialized and manifest parsed.
3. **Per-plugin failure isolation**: Any plugin failing at any stage does not affect other plugins or core startup.
4. **Namespace isolation**: Plugin skills stored under `<alias>:<name>` cannot collide with default-namespace or other plugin skills.
5. **Always-on re-materialization**: Every reconciliation pass re-materializes every declared plugin.
6. **Config values are always validated**: A loaded plugin's `config` dict contains only values that passed type checking. No unvalidated user input reaches plugin code.
7. **Config schema is optional**: Plugins without `[config]` sections load normally with `config = {}`. Users without `[plugins.<alias>.config]` sub-tables get default values (or empty dict if no schema).
8. **Consolidation is additive**: Plugin failures are appended to the restart notification. Each can appear independently (restart only, failures only, both, neither).
9. **Init-before-subscribe**: Event subscriptions are only registered after a successful init hook. Plugins without init hooks have events registered immediately.
10. **Sequential init**: Init hooks run one at a time in alphabetical order by alias. No parallel execution.
11. **Handler validation is complete**: A plugin with invalid handlers (missing file, import error, wrong signature, unknown event type) fails at discovery with a diagnostic — no partial handler registration.
12. **Unsubscription is deactivation**: Removed plugins' event wrappers become no-ops but remain in the bus's handler list (bubus has no `off()` API).

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
