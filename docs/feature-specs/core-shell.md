# Core Shell

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The core shell is everything that runs before and around conversations: configuration loading, logging, the SQLite database with migrations, the namespaced key-value state store, the app event bus, the cron scheduler, the workspace layout, and the extension host that loads every feature. Per DES-001, the core is only the main loops — all features ship as extensions on top of these services.

## User Stories

- As a user, I want all tunable parameters in a single commented config file so that I can customize behavior without touching code
- As a developer, I want every feature to receive the same set of core services so that extensions are uniform and testable

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Load configuration from a TOML file at `~/.config/tachikoma/config.toml` (respecting `XDG_CONFIG_HOME`), overridable via `--config` |
| R1 | Auto-generate a fully commented default config file on first run; every value in the template matches the built-in default |
| R2 | Validate config against a TypeBox schema at startup: apply defaults, coerce compatible primitives, and fail with a readable per-field error (`ConfigError`) on invalid values |
| R3 | Per-extension config lives under `[extensions.<name>]` and is validated against that extension's own `configSchema` when it declares one |
| R4 | Structured logging via pino; all log output goes to stderr; components log through child loggers bound with `component: <name>` (see DES-004) |
| R5 | SQLite database (better-sqlite3 + drizzle) at `{workspace}/.tachikoma/tachikoma.db` with WAL journal mode and foreign keys enabled |
| R6 | Drizzle migrations from the repo `drizzle/` directory run automatically at startup; one migrations directory covers core tables plus all first-party extension tables (aggregated in `src/db/schema.ts`) |
| R7 | Namespaced key-value state store (`app_state` table) for extension state that does not warrant its own tables; each extension gets a `KeyValueState` scoped to its name |
| R8 | App event bus with string-named events: `on()` returns an unsubscribe function; `emit()` isolates handler failures so one failing subscriber never affects the others |
| R9 | Croner-backed scheduler with named jobs: `cron(name, pattern, fn)` and `every(name, seconds, fn)`; jobs are overlap-protected, error-guarded, manually triggerable, and respect the configured IANA timezone |
| R10 | Workspace rooted at `workspace.path` (default `~/tachikoma`, `~` expanded); internal data lives under `{workspace}/.tachikoma` (data dir), never part of user-visible workspace content |
| R11 | pi runs against a dedicated agent dir at `{workspace}/.tachikoma/pi` so Tachikoma never collides with a user's own pi install (`~/.pi`); pi session transcripts live under `{workspace}/.tachikoma/pi/sessions` |
| R12 | First-party extensions load in the order listed in `src/extensions/index.ts`; extension `setup(app)` runs once per extension, in order |
| R13 | Extensions may enqueue further extensions during setup via `app.registerExtension()` (third-party plugin support); enqueued extensions load in the same pass, after the current list |
| R14 | Bootstrap hooks registered via `app.bootstrap(name, hook)` run after all extension setups complete, in registration order, named `<extension>:<name>`; a failing hook aborts startup |
| R15 | On shutdown (SIGINT/SIGTERM), the channel stops, the scheduler stops all jobs, and the coordinator closes the active session |

## Behaviors

### Configuration Loading (R0, R1, R2, R3)

**Acceptance Criteria**:
- Given no config file exists, when the app starts, then the directory is created and a commented default file is written, and the app proceeds with defaults
- Given a config file missing entire sections, when loaded, then defaults apply: `agent.model = "anthropic/claude-opus-4-5"`, `agent.thinkingLevel = "medium"`, `agent.searcherModel = "anthropic/claude-opus-4-5"`, `agent.processorModel = "anthropic/claude-haiku-4-5"`, `agent.classifierModel = "anthropic/claude-haiku-4-5"`, `logging.level = "info"`, `logging.pretty = true`, `channels.default = "repl"`, `sessions.idleCloseSeconds = 900`, `sessions.resumeWindowSeconds = 86400`, `workspace.path = "~/tachikoma"`, `extensions = {}`
- Given a config value of the wrong type (e.g. `idleCloseSeconds = "soon"`), when loaded, then a `ConfigError` is thrown listing the offending path and message
- Given an extension with a `configSchema`, when the host builds its context, then `app.extensionConfig` is the `[extensions.<name>]` section validated with defaults applied; without a schema, the raw section is passed through
- Given `scheduler.timezone` is unset, when cron jobs are scheduled, then the system timezone applies

### Database and Migrations (R5, R6)

**Acceptance Criteria**:
- Given the app starts, when the database opens, then `journal_mode = WAL` and `foreign_keys = ON` are set and pending migrations run before any extension loads
- Given a fresh database file, when migrations run, then the `sessions` and `app_state` core tables exist
- Given a first-party extension owning tables, when its `schema.ts` is re-exported from `src/db/schema.ts`, then drizzle-kit generates migrations for it in the single shared `drizzle/` directory

### Key-Value State (R7)

**Acceptance Criteria**:
- Given two extensions storing the same key, when each reads it back, then each sees only its own namespaced value
- Given a key is set twice, when read, then the latest value is returned (upsert) and `updatedAt` reflects the last write
- Given a missing key, when read, then `null` is returned; `delete` removes the row

### Event Bus (R8)

**Acceptance Criteria**:
- Given multiple subscribers to an event, when one handler throws, then the error is logged and the remaining handlers still run
- Given a subscription's returned unsubscribe function is called, when the event is emitted, then the handler is not invoked
- Given `emit()` is called, when handlers are async, then emission does not block the caller (fire-and-forget with error logging)

### Scheduler (R9)

**Acceptance Criteria**:
- Given a cron job whose previous run is still executing, when the next tick arrives, then the run is skipped (overlap protection)
- Given a job function throws, when the job runs, then the error is logged with the job name and the schedule keeps firing
- Given a job is registered with a name already in use, when registered, then the previous job with that name is stopped and replaced
- Given `every()` intervals and croner timers, when only they remain pending, then they do not keep the process alive (`unref`)

### Workspace Layout (R10, R11)

**Acceptance Criteria**:
- Given `workspace.path = "~/tachikoma"`, when the workspace initializes, then `~` is expanded and relative paths are resolved to absolute
- Given the app starts, when `workspace.ensure()` runs, then `{workspace}/.tachikoma/pi/sessions` exists (creating the full data-dir chain)
- Given pi sessions are created, when the agent manager opens them, then `agentDir` is `{workspace}/.tachikoma/pi` — pi's auth, models, settings, and sessions stay inside the data dir by default

### Extension Host Loading and Bootstrap (R12, R13, R14)

**Acceptance Criteria**:
- Given the first-party list `[context, boundary, repl]`, when the host loads, then setups run in that order and each receives an `AppContext` with a component-bound logger, validated `extensionConfig`, and a namespaced `KeyValueState`
- Given an extension calls `app.registerExtension(nested)` during setup, when loading continues, then the nested extension's setup runs after the remaining queued extensions
- Given extensions registered bootstrap hooks, when `host.bootstrap()` runs after all setups, then hooks execute sequentially in registration order — an extension may rely on services registered by earlier extensions but not later ones
- Given a bootstrap hook throws, when startup runs, then the error propagates and the app fails to start
