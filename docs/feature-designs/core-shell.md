# Design: Core Shell

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/core-shell.md](../feature-specs/core-shell.md)
**Status**: Current

## Purpose

This document explains how the core shell is composed: the service construction order, the seam between extension setup and core runtime, and the choices behind the config, database, logging, scheduling, and workspace primitives that every extension consumes.

## Problem Context

Per [DES-001](../design/DES-001-unified-extension-api.md), the core is only the main loops — every feature ships as an extension. The shell must therefore provide a small, stable set of process-scoped services (`AppContext`) that features can rely on, and a deterministic startup that loads features in a known order without core knowing what they are.

**Constraints:**
- Single-process, single-user app; no need for connection pooling or worker isolation
- pi's API is never wrapped (DES-001) — the shell composes around pi, it does not abstract it
- The workspace must stay compatible with legacy layouts, and pi state must not leak into the user's own `~/.pi` install
- Extensions load before the channel starts, so anything they register must be in place before the first message

**Interactions:**
- The `AppContext` service table and extension lifetimes are defined in [DES-001](../design/DES-001-unified-extension-api.md); authoring conventions in [DES-002](../design/DES-002-extension-authoring.md)
- The `Registrations` object filled here is consumed by the coordinator (middleware, processors, channels) and the `AgentManager` (pi factories — including extension context sections — and system prompt builders) — both documented in their own areas
- Every feature extension depends on this shell; the `external` extension extends it at runtime via `app.registerExtension`

## Design Overview

`runApp` (`src/app.ts`) builds everything in one pass, each step depending only on earlier ones:

1. `loadConfig` — parse TOML (smol-toml), generate the commented default on first run, validate with TypeBox
2. `createRootLogger` — pino to stderr, pretty or JSON per `logging.pretty`
3. `Workspace.ensure()` — create the `{workspace}/.tachikoma/pi/sessions` chain
4. `adaptConfig` (legacy config translation — its result is `Object.assign`-ed into `config` in place so translated values flow through the rest of the wiring), then `adaptWorkspace` (legacy workspace migration) — both from `src/migration/`
5. `createDatabase` + `runMigrations` — better-sqlite3 with WAL/foreign-keys pragmas, drizzle migrations from `drizzle/`
6. `adaptWorkspaceData` — legacy data migration, after the schema exists (`src/migration/`)
7. `EventBus`, `Scheduler`, `createRegistrations()` — passive services
8. `AgentManager`, `SessionRegistry`, `Coordinator` — runtime consumers of the registrations
9. `host.load(firstPartyExtensions)` — extension setups in list order, then `host.bootstrap()` runs all hooks
10. `coordinator.recoverDanglingSessions()` — close and post-process sessions a previous run left open
11. Channel resolution (`--channel` flag or `channels.default`), `channel.start`, then `coordinator.run(signal)` until SIGINT/SIGTERM aborts

The three `adapt*` calls are best-effort legacy migrations (see [migration](../feature-designs/migration.md)); they sit between workspace creation and the runtime services so translated config/workspace/data is in place before anything reads it.

Shutdown is the reverse tail: the abort signal wakes the coordinator loop, whose `finally` closes the active session (running post-processing); `runApp`'s `finally` then stops the channel and the scheduler.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/main.ts` | CLI entry: `parseArgs` (`--channel`/`-c`, `--config`, `--help`), delegates to `runApp`, exits 1 on error | No CLI framework — `node:util` `parseArgs` covers three flags |
| `src/app.ts` | `runApp`: fixed composition sequence, channel resolution, signal handling, shutdown ordering | One function owns the whole wiring; no DI container |
| `src/config/schema.ts` | `ConfigSchema` (TypeBox) with nested-object defaults | `Type.Object(..., { default: {} })` per section so missing sections still get field defaults; model refs are `provider/model-id` strings |
| `src/config/parse.ts` | `parseWithSchema`: `Clone` → `Default` → `Convert` → `Assert`, aggregating `Errors()` into one `ConfigError` | Shared by app config and per-extension config — one validation behavior everywhere |
| `src/config/load.ts` | Resolve path (`XDG_CONFIG_HOME` aware), write `DEFAULT_CONFIG_TEMPLATE` on ENOENT, parse | The template itself is parsed on first run, guaranteeing template values equal built-in defaults |
| `src/log.ts` | `createRootLogger` (pino), `componentLogger` child binding `component` | Always stderr (fd 2) so REPL output on stdout stays clean |
| `src/db/index.ts` | `createDatabase` (better-sqlite3 + drizzle, WAL, foreign keys), `runMigrations` | Migrations folder resolved relative to the source tree (`import.meta.dirname`) |
| `src/db/core-schema.ts` | Core tables: `sessions`, `app_state` | `post_processing_state` is a JSON column mapping processor name → completed/failed |
| `src/db/schema.ts` | Aggregate schema: core plus every first-party extension's `schema.ts` | Single module read by drizzle-kit (`drizzle.config.ts`) → one `drizzle/` migrations dir for the whole app |
| `src/db/state.ts` | `KeyValueState`: namespaced JSON KV over `app_state` with upsert | Synchronous API; composite primary key (namespace, key) |
| `src/events.ts` | `EventBus`: string-named events, unsubscribe closures, fire-and-forget emit | Handlers wrapped in `Promise.resolve().then()` for isolation; failures logged with event name |
| `src/scheduler.ts` | `Scheduler`: named `cron`/`every` jobs returning `ScheduledJob` handles | croner with `timezone`, `protect` (overlap skip), `catch`; `guarded()` logs failures; name reuse stops the previous job |
| `src/workspace.ts` | `Workspace`: root resolution (`expandHome`), `dataDir`, `piDir`, `sessionsDir`, `databaseFile`, `ensure()` | All derived paths are getters off the root; `ensure()` creates only the deepest chain |
| `src/extensions/api.ts` | `AppContext`, pipeline contracts (`ExchangeProcessor`, `PostProcessor`, `InboundMiddleware`), `defineExtension` | `defineExtension` is identity — purely a typing aid for the `C` config parameter |
| `src/extensions/registrations.ts` | `Registrations`: mutable arrays/maps filled during setup, read by core at runtime | Plain data object — the seam between host and coordinator/agent |
| `src/extensions/host.ts` | `ExtensionHost`: queue-based `load()`, per-extension `buildContext()`, `bootstrap()` | Context built fresh per extension: child logger, validated config section, namespaced KV state |
| `src/extensions/index.ts` | `firstPartyExtensions` load order | `commands, context, memory, projects, git, boundary, skills, workflows, tasks, detached-processes, notifications, self-update, repl, telegram, external` (15 extensions) |

## Key Decisions

### Synchronous SQLite via better-sqlite3

**Choice**: better-sqlite3 under drizzle, with synchronous `KeyValueState` and registry call sites
**Why**: A single-process personal agent has no concurrency that justifies async DB plumbing; synchronous calls keep pipeline code linear and drizzle's better-sqlite3 migrator runs inline before extensions load.
**Alternatives Considered**:
- `node:sqlite` (stdlib): newer, but weaker drizzle integration
- libsql/async drivers: async ceremony with no payoff at this scale

**Consequences**:
- Pro: no `await` noise in state/registry accessors; migrations are a plain startup call
- Pro: WAL mode keeps reads cheap alongside writes
- Con: large queries would block the event loop (acceptable: the DB is tiny)

### Mutable Registrations object as the setup/runtime seam

**Choice**: A plain `Registrations` data object is created before the host, handed to the `AgentManager` and `Coordinator` at construction, and filled by extensions during `setup()`
**Why**: The coordinator and agent manager must exist before extensions load (extensions register against them via `AppContext`), but they only read the registries at runtime — after loading finished. Sharing a mutable object breaks the construction-order cycle without events or late binding.
**Alternatives Considered**:
- Constructing the coordinator after `host.load()`: forces every `AppContext` closure to defer through indirection anyway
- Registration via the event bus: loses typing and ordering guarantees

**Consequences**:
- Pro: `AppContext` methods are one-line array pushes; trivially testable
- Pro: load order directly determines pipeline order (providers, middleware, processors run in registration order)
- Con: nothing prevents late registration after startup — by convention only `setup()` writes to it

### Isolated pi agent dir with shared-credential fallback

**Choice**: All pi state (sessions, settings, models.json) lives under `{workspace}/.tachikoma/pi`, but `AuthStorage` falls back to the user's `~/.pi/agent/auth.json` unless a workspace-local `auth.json` has actual content
**Why**: Tachikoma must never collide with a user's own pi install (session trees, settings), yet credentials are machine-level — forcing a second login for the same machine would be hostile.
**Alternatives Considered**:
- Fully isolated auth: duplicate logins, confusing token refresh
- Sharing `~/.pi` entirely: Tachikoma's sessions and settings would pollute the user's pi

**Consequences**:
- Pro: deleting `.tachikoma/` removes all agent state; the user's pi is untouched
- Pro: existing pi logins (including OAuth) just work
- Con: the "has content" check is heuristic (file exists and size > 2 bytes)

### Single-pass growable extension queue

**Choice**: `host.load()` iterates the queue by index while extensions may append to it via `app.registerExtension()`
**Why**: External (third-party) extension loading is itself an extension (DES-001): the `external` extension imports configured modules during its own setup and enqueues them, so they load in the same pass with the same contract — no second loader, no special plugin phase.
**Alternatives Considered**:
- A separate plugin-loading phase after first-party load: duplicates the context-building logic and creates two extension kinds

Setup failures are isolated by provenance. A first-party extension's `setup()` failure propagates and aborts startup (a core bug must surface). An external (third-party) extension is wrapped: its setup runs under `withTimeout` (default `DEFAULT_EXTERNAL_SETUP_TIMEOUT_MS = 30_000` ms, overridable per queued extension via `setupTimeoutMs`), and any throw or timeout is caught, logged as a warning ("external extension setup failed or timed out — skipping"), and that one extension is skipped while the rest of the pass continues. The same first-party-fails-hard / external-is-isolated rule governs bootstrap hooks (`bootstrap()`).

**Consequences**:
- Pro: one loading mechanism; external extensions get the exact same `AppContext` (minus migrations — they must use `app.state` per DES-001)
- Pro: a broken or hanging third-party extension cannot abort startup or wedge the load loop
- Con: load order of external extensions depends on where `external` sits in the first-party list (last, by design)

### Fire-and-forget event bus emission

**Choice**: `emit()` schedules each handler through `Promise.resolve().then()` and returns immediately; rejections are logged, never propagated
**Why**: The bus carries cross-extension signals (`session:closed`, `status`, …) where the emitter must not care who listens or whether a listener is broken — a failing subscriber in one extension must never affect the emitting code path or sibling subscribers.

**Consequences**:
- Pro: emitters never await or try/catch; handler errors are uniformly logged with the event name
- Con: no backpressure or completion signal — work that must complete before continuing belongs in the explicit pipelines (processors, hooks), not on the bus

## System Behavior

### Scenario: First run on a clean machine

**Given**: No config file and no workspace exist
**When**: `tachikoma` starts
**Then**: The default commented config is written and logged, the workspace chain `{workspace}/.tachikoma/pi/sessions` is created, migrations build the schema in a fresh `tachikoma.db`, all extensions load and their bootstrap hooks initialize the workspace content (memories layout, skills dir, context files, git repo — each owned by its extension), and the REPL channel starts.

### Scenario: External extension enqueued during load

**Given**: `[extensions.external]` lists a source path exporting a `defineExtension` module
**When**: `host.load()` reaches the `external` extension (last in the first-party list)
**Then**: Its setup imports the module and calls `app.registerExtension`; the loop picks the nested extension up in the same pass, builds it a fresh `AppContext` (own logger, config section, KV namespace), and runs its setup before `host.bootstrap()`.

### Scenario: Bootstrap hook failure

**Given**: A first-party extension's bootstrap hook throws
**When**: `host.bootstrap()` runs the hooks sequentially
**Then**: The error propagates out of `runApp`; `main.ts` prints the stack and exits with code 1. No channel ever starts. On the next run the hook executes again (hooks are idempotent by convention, DES-002). An *external* extension's hook is instead isolated — caught, logged as a warning, and skipped — so a third-party hook failure never aborts startup.

### Scenario: SIGINT during an idle conversation

**Given**: The app is running with an open session and scheduled jobs
**When**: The user presses Ctrl+C
**Then**: The signal handler aborts the coordinator loop; its `finally` force-flushes held deliveries, disposes the active pi session, marks the session closed, and runs post-processing phases. `runApp`'s `finally` then stops the channel and calls `scheduler.stopAll()` (the boundary extension's idle timer is `unref`'d, so it never blocks shutdown).

## Notes

- `logging.pretty` is an explicit config flag (default `true`), not environment detection; service deployments set `pretty = false` for JSON lines
- Overlap protection (`protect`) applies to `cron()` jobs via croner; `every()` intervals have no overlap guard, only error guarding, and are `unref`'d so they never keep the process alive
- One-shot scheduling is not a core `Scheduler` capability; the tasks extension parses ISO datetimes into one-shot task schedules with croner directly (`src/extensions/tasks/schedule.ts`)
- `app.sessions` exposes `close()`/`listResumable()` but no direct resume — resuming happens through inbound middleware (`InboundContext.resumeSession`), keeping session replacement under coordinator control
- The host-owned pi factory (`coordinator.hostFactory()`) is pushed into `regs.piFactories` before extensions load, so context-block injection via `before_agent_start` is always bound first
