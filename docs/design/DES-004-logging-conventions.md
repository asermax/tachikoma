# DES-004: Logging Conventions

## Status

Accepted

## Context

All components — core and extensions — log through a single pino root logger created in `src/log.ts`. Channels own stdout (the REPL renders conversation output there), so diagnostics must never land on stdout. Separately, users watching a conversation need progress feedback that is *not* a log line; that is `app.status()`.

## Pattern

### 1. One root, child per component

`createRootLogger(config.logging)` is called once in `src/app.ts`; everything else receives a child via `componentLogger(root, name)`, which binds `{ component: name }`:

- Core components are bound in `src/app.ts` (`events`, `scheduler`, `agent`, `coordinator`, one per channel).
- Each extension's `app.log` is bound to the extension's name by the host (`src/extensions/host.ts`) — extensions never create loggers.

### 2. stderr always, file optionally

stderr is always written to file descriptor 2 (`pino.destination(2)`, or pino-pretty `destination: 2` when `pretty`). Rationale: the REPL channel renders agent output on stdout; interleaving logs would corrupt the conversation stream, and keeping stdout clean makes the process pipeable.

When `logging.toFile` is set (default), the root logger fans out via `pino.multistream` to **both** stderr and a JSON file at `{workspace}/.tachikoma/logs/tachikoma.log` (`pino.destination({ dest, mkdir: true })`). The file always receives raw JSON regardless of `pretty`, so a daemon run leaves a durable, shippable record while an operator still gets pretty stderr. pino-pretty is wired as a *stream* (not a transport) here so it composes with multistream — transports run in a worker thread and can't be combined.

File logs are pruned by a self-contained startup rotation (`rotateLogs` in `src/log.ts`, called from `src/app.ts` after `workspace.ensure()`): on each start the current `tachikoma.log` is renamed to `tachikoma.<YYYY-MM-DD_HH-MM-SS>.log`, then archives older than `logging.retentionDays` (default 7) are deleted. No external rotation dependency — logrotate/journald are not assumed present.

Logs live under `.tachikoma/logs` (internal data, never committed) rather than the workspace root, which is a git repo for memories and context.

### 3. Output modes

`[logging]` config (`src/config/schema.ts`): `level` (default `"info"`), `pretty` (default `true`), `toFile` (default `true`), `retentionDays` (default `7`). `pretty = true` gives colorized pino-pretty output for development; `pretty = false` emits structured JSON lines for service mode. `toFile` controls the persisted file sink described above.

### 4. Structured calls

Bindings object first, message second. Never interpolate data into the message string:

```ts
log.warn({ err: error, skill, agent: file }, "failed to load skill agent — skipped");
```

- Errors always go under the `err` key (pino serializes stack traces from it).
- Identifiers (session id, task id, job name) go in the bindings object so they are filterable.
- Never log secrets: bot tokens, API keys, auth material.

### 5. Level discipline

| Level | Use |
|---|---|
| `debug` | internal detail invisible to operators (pipeline status echoes, queue movements) |
| `info` | normal lifecycle: startup, session open/close, job runs, deliveries |
| `warn` | unexpected but handled — the error-isolation paths (a failing context provider, processor, or event handler) log here and continue |
| `error` | an operation failed in a way that affects behavior (exchange failed, delivery failed) |

The dominant pattern is `warn`: pipelines isolate per-item failures (DES-001), and isolation without a `warn` line is a silent failure.

### 6. `app.status()` vs logs

`app.status(text)` is **user-facing progress**, not logging: the coordinator emits a `status` event on the app bus for channels to consume (e.g. `Gathering context: memory-index…`, `Post-processing: episodic…` in `src/coordinator.ts`; no channel renders these yet — see DLT-043). Use it for steps a user is actively waiting on; keep it short, present tense, one line. Every status is also echoed at `debug`, so logs remain the complete record — never use `status()` as a substitute for a log call, and never use `log.info` to talk to the user. (The `status`-kind `AgentEvent`s from `src/agent/adapter.ts` — compaction, provider retries — are a separate, in-exchange surface that the REPL does render.)

## Rationale

1. **Child loggers**: every line is attributable to a component without repeating context at call sites; filtering a subsystem is one `jq` clause.
2. **stderr-only**: hard separation between conversation (stdout) and diagnostics (stderr) — no protocol needed, just file descriptors.
3. **Two consumers, two surfaces**: operators read stderr, users read the channel; conflating them either spams the user or hides progress.

## Exceptions

- Failures before the root logger exists (config parse errors in `src/main.ts`) may write directly to `process.stderr`.
- Tests fake loggers structurally (`{ warn: vi.fn() } as unknown as Logger`, see DES-003) — assertions on log output are reserved for behavior where the warning *is* the contract (error isolation).

## Related Patterns

- DES-001: Unified extension API (`app.log`, `app.status` services)
- DES-003: Testing conventions (fake loggers)
