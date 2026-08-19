# Core Shell

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The core shell is everything that runs before and around conversations: configuration loading, structured logging, the SQLite database with migrations, the namespaced key-value state store, the app event bus, the cron scheduler, the workspace layout, and the extension host that loads every feature. Per [DES-001](../design/DES-001-unified-extension-api.md), the core is only the main loops — all features ship as extensions consuming these services through the `AppContext`.

`runApp` (`src/app.ts`) composes the shell in a fixed, fail-fast sequence and hands control to the coordinator's main loop until a shutdown signal arrives.

## User Stories

- As a user, I want all tunable parameters in a single commented TOML file so that I can customize behavior without touching code
- As a developer, I want every feature to receive the same set of core services so that extensions stay uniform and testable
- As the system, I want startup to be a fixed, fail-fast sequence so that misconfiguration is reported clearly instead of producing partial state
- As the system, I want a single-instance guard on each workspace so that scheduled actions fire once and shared state is never corrupted by a second process

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Load configuration from a TOML file at `~/.config/tachikoma/config.toml` (respecting `XDG_CONFIG_HOME`), overridable via the `--config` CLI flag |
| R1 | Auto-generate a fully commented default config file on first run; every value in the template matches the built-in default |
| R2 | Validate config against a TypeBox schema at startup: apply defaults, coerce compatible primitives, and fail with a per-field `ConfigError` on invalid values |
| R3 | Per-extension config lives under `[extensions.<name>]` and is validated against that extension's own `configSchema` when it declares one |
| R4 | Structured logging via pino, always to stderr; components log through child loggers bound with `component: <name>`; pretty-printed when `logging.pretty` is true (default), JSON lines otherwise |
| R5 | SQLite database (better-sqlite3 + drizzle) at `{workspace}/.tachikoma/tachikoma.db` with WAL journal mode and foreign keys enabled |
| R6 | Drizzle migrations from the repo `drizzle/` directory run automatically at startup before extensions load; one migrations directory covers core tables plus all first-party extension tables (aggregated in `src/db/schema.ts`) |
| R7 | Namespaced key-value state store (`app_state` table); each extension receives a `KeyValueState` scoped to its name |
| R8 | App event bus with string-named events: `on()` returns an unsubscribe function; `emit()` is fire-and-forget with per-handler error isolation |
| R9 | Croner-backed scheduler with named jobs: `cron(name, pattern, fn)` (timezone-aware, overlap-protected) and `every(name, seconds, fn)`; jobs are error-guarded and manually triggerable; re-registering a name replaces the previous job |
| R10 | Workspace rooted at `workspace.path` (default `~/tachikoma`; `~` expanded, relative paths absolutized); internal data lives under `{workspace}/.tachikoma` |
| R11 | pi runs against a dedicated agent dir at `{workspace}/.tachikoma/pi` so Tachikoma never collides with a user's own pi install; pi session transcripts live under `{workspace}/.tachikoma/pi/sessions` |
| R12 | First-party extensions load in the order listed in `src/extensions/index.ts`; each extension's `setup(app)` runs once, in order |
| R13 | Extensions may enqueue further extensions during setup via `app.registerExtension()` (external extension support); enqueued extensions load in the same pass, after the current list |
| R14 | Bootstrap hooks registered via `app.bootstrap(name, hook)` run after all extension setups complete, sequentially in registration order, named `<extension>:<name>`; a failing first-party hook aborts startup (propagates), while a failing external (third-party) hook is isolated — caught, logged as a warning, and skipped so startup continues |
| R15 | The channel is selected by the `--channel` flag or `channels.default`; an unknown channel name fails startup with a message listing available channels |
| R16 | On `SIGINT`/`SIGTERM`, or an uncaught exception/unhandled rejection, the main loop drains its held queue to the channel (the trunk is left open — the memory pipeline runs at the nightly close or next-startup recovery, not during teardown), the channel stops, and the scheduler stops all jobs; a crash-initiated drain is bounded by a force-exit timeout and exits non-zero so a supervisor restarts the process |
| R17 | The `[env]` config section (a `string → string` map) is applied to `process.env` at startup, before any runtime service or pi session is constructed, so the variables are visible app-wide and to anything that inherits the process environment (sessions, spawned tools, detached processes); config-defined values overwrite existing same-named variables |
| R18 | Single-instance enforcement: startup acquires the workspace instance lock (`{workspace}/.tachikoma/instance.json`) after `workspace.ensure()` and before any other service is constructed; a live foreign holder fails startup with an error naming the holder's PID, start time, and command line; stale locks (dead pid, pid reuse, unreaped zombie, corrupt content, own pid after a re-exec) are taken over automatically, and the lock is released on every exit path and before a deferred re-exec |
| R19 | The CLI takes no subcommands: zero positionals and the legacy `run` are accepted; any other positional fails with an error naming it plus a usage hint and exit code 1, before the app starts |

## Behaviors

### Configuration Loading (R0, R1, R2, R3)

`loadConfig` reads the TOML file (smol-toml), creating a commented default first when none exists, then validates with `parseWithSchema`.

**Acceptance Criteria**:
- Given no config file exists at the resolved path, when the app starts, then the directory is created, the commented default template is written, the parsed config equals the built-in defaults, and the generation is logged
- Given a config file that only sets `agent.main`, when loaded, then that value applies, other roles stay unset (falling back along the tier chain at resolution time), and every other value falls back to its default (e.g. `channels.default = "repl"`)
- Given sections are missing entirely, when loaded, then defaults apply: `workspace.path = "~/tachikoma"`, all `agent` roles unset (model selection defers to pi: settings `defaultProvider`/`defaultModel`, else the first credentialed model), `logging.level = "info"`, `logging.pretty = true`, `channels.default = "repl"`, `sessions.resumeWindowSeconds = 86400`, `scheduler.timezone` unset, `env = {}`, `extensions = {}`
- Given a value of the wrong type (e.g. `resumeWindowSeconds = "soon"`), when loaded, then a `ConfigError` is thrown listing each offending path with its validation message
- Given `XDG_CONFIG_HOME` is set, when the default path resolves, then it is `$XDG_CONFIG_HOME/tachikoma/config.toml`; otherwise `~/.config/tachikoma/config.toml`
- Given an extension declaring a `configSchema`, when the host builds its context, then `app.extensionConfig` is the `[extensions.<name>]` section parsed against that schema with defaults applied (errors labeled `extensions.<name>`); without a schema, the raw section (or `{}` when absent) passes through

### Logging (R4)

**Acceptance Criteria**:
- Given `logging.pretty = true`, when the root logger is created, then output is colorized via pino-pretty on stderr; given `false`, then JSON lines are written to stderr
- Given `componentLogger(root, name)`, when a component logs, then every entry carries `component: <name>`

### Database and Migrations (R5, R6)

**Acceptance Criteria**:
- Given the app starts, when the database opens, then `journal_mode = WAL` and `foreign_keys = ON` are set, and pending migrations from `drizzle/` apply before any extension loads
- Given a fresh database file, when migrations run, then the core `sessions` and `app_state` tables exist alongside first-party extension tables
- Given a first-party extension owning tables, when its `schema.ts` is re-exported from `src/db/schema.ts`, then drizzle-kit (`drizzle.config.ts`) generates its migrations into the single shared `drizzle/` directory

### Key-Value State (R7)

**Acceptance Criteria**:
- Given two extensions storing the same key, when each reads it back, then each sees only its own namespaced value
- Given a key set twice, when read, then the latest value is returned (upsert) and `updatedAt` reflects the last write
- Given a missing key, when read, then `null` is returned; `delete` removes the row

### Event Bus (R8)

**Acceptance Criteria**:
- Given multiple subscribers to an event, when one handler throws, then the error is logged with the event name and the remaining handlers still run
- Given the unsubscribe function returned by `on()` is called, when the event is emitted, then the handler is not invoked
- Given `emit()` is called, when handlers are async, then emission does not block the caller (handlers are scheduled fire-and-forget with error logging)

### Scheduler (R9)

**Acceptance Criteria**:
- Given a cron job whose previous run is still executing, when the next tick arrives, then the run is skipped (croner `protect`)
- Given `scheduler.timezone` is configured, when cron patterns are evaluated, then they use that IANA timezone; when unset, the system timezone applies
- Given a job function throws, when the job runs, then the error is logged with the job name and the schedule keeps firing
- Given a job is registered under a name already in use, when registered, then the previous job with that name is stopped and replaced
- Given a `ScheduledJob` handle, when `trigger()` is called, then the guarded function runs immediately
- Given `stopAll()` is called (shutdown), when jobs exist, then every job is stopped and the registry cleared; `every()` intervals are `unref`'d so they never keep the process alive

### Workspace Layout (R10, R11)

**Acceptance Criteria**:
- Given `workspace.path = "~/tachikoma"`, when the workspace initializes, then `~` is expanded and relative paths resolve to absolute
- Given the app starts, when `workspace.ensure()` runs, then `{workspace}/.tachikoma/pi/sessions` exists (creating the full data-dir chain)
- Given an agent session opens, when the `AgentManager` configures pi, then `agentDir` is `{workspace}/.tachikoma/pi` for the resource loader, session creation, and settings, and `models.json` is workspace-local
- Given `{workspace}/.tachikoma/pi/auth.json` has content, when credentials resolve, then it is used; otherwise the user's existing pi login (`~/.pi/agent/auth.json` and env vars) is shared

### Extension Loading and Bootstrap (R12, R13, R14)

**Acceptance Criteria**:
- Given the first-party list (`commands`, `context`, `memory`, `projects`, `git`, `boundary`, `skills`, `workflows`, `tasks`, `detached-processes`, `notifications`, `self-update`, `repl`, `telegram`, `external`), when the host loads, then setups run in that order and each receives an `AppContext` with a component-bound logger, validated `extensionConfig`, and a namespaced `KeyValueState`
- Given an extension calls `app.registerExtension(nested)` during setup, when loading continues, then the nested extension's setup runs after the remaining queued extensions in the same pass
- Given extensions registered bootstrap hooks, when `host.bootstrap()` runs after all setups, then hooks execute sequentially in registration order — an extension may rely on services registered by earlier extensions but not later ones
- Given a first-party bootstrap hook throws, when startup runs, then the error propagates and the process exits non-zero
- Given an external (third-party) bootstrap hook throws, when `host.bootstrap()` runs, then the error is caught and logged as a warning, that hook is skipped, and startup continues

### Startup and Shutdown (R15, R16, R17)

**Acceptance Criteria**:
- Given `tachikoma --channel telegram`, when the app starts, then the `telegram` channel is used regardless of `channels.default`
- Given an unknown channel name, when startup resolves the channel, then it fails with an error listing the available channel names
- Given startup completes, when the order is observed, then it is: config → workspace.ensure → instance lock → logging → `adaptConfig` (legacy config translation, applied in place) → apply `[env]` to `process.env` → `adaptWorkspace` (legacy workspace migration) → database + migrations → `adaptWorkspaceData` (legacy data migration) → extension host load → bootstrap hooks → dangling-session recovery → channel start → coordinator main loop. The three `adapt*` steps are best-effort legacy migrations from `src/migration/` (see [migration](migration.md))
- Given a non-empty `[env]` section, when the app starts, then each entry is written to `process.env` (overwriting any existing same-named variable) before runtime services or pi sessions are built, and the applied keys are logged (values are not); an empty section applies nothing
- Given sessions were left open by a previous run, when the app starts, then they are closed and their post-processing runs before the channel starts
- Given SIGINT or SIGTERM, when received, then the coordinator loop exits, the held queue drains to the channel (the trunk persists — no close pipeline runs), the channel stops, the scheduler stops all jobs, and the instance lock is released

### Single-Instance Guard (R18, R19)

`acquireInstanceLock` creates the lock exclusively (`wx`) and verifies its own content survived; takeover decisions use pid liveness plus `/proc` start-time identity when available (see [core-shell design](../feature-designs/core-shell.md)).

**Acceptance Criteria**:
- Given a lock file whose holder is alive and is not this process, when the app starts against the same workspace, then startup fails before the database is opened or migrations run, and the error names the holder's PID, start time, and command line and points at the lock file
- Given a lock whose holder pid is dead, whose pid was reused by another process, whose holder is an unreaped zombie, whose content is corrupt (malformed JSON, empty, or mistyped fields), or that records this same pid (a re-exec), when the app starts, then the stale lock is replaced (logged to stderr) and startup proceeds
- Given the app exits after acquiring the lock — a graceful signal drain or any later startup failure — when the process ends, then the lock file is removed (a force-exited crash leaves a stale file, taken over by the next boot)
- Given a deferred restart (`restart_self`/`upgrade_self`), when the restarter runs, then the lock is released before the re-exec so the replacement process acquires it cleanly
- Given two processes racing to take over the same stale lock, when both attempt acquisition, then at most one wins (exclusive create plus verify); the loser refuses, and release never removes a lock file owned by another process
- Given an invocation with an unknown positional (e.g. `tachikoma workflow`), when the CLI parses arguments, then it prints an error naming the argument plus a usage hint, exits 1, and starts nothing
