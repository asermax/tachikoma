# DES-003: Testing Conventions

## Status

Accepted

## Context

The Python repo established testing conventions (pytest, mirrored structure, AC traceability). This document adapts them to this repo's actual stack: vitest running TypeScript sources directly, structural fakes instead of patch-based mocking, and real SQLite/git/process integration in temp directories. Tests are part of the quality gates — `just check` runs Biome, `tsc --noEmit`, and `vitest run`.

## Pattern

### 1. Directory structure

Tests mirror the source layout: extension tests live in `tests/<extension>/<topic>.test.ts`, matching `src/extensions/<extension>/<topic>.ts`; core tests sit at the `tests/` root (`config.test.ts`, `db.test.ts`, `events.test.ts`, `adapter.test.ts`, `smoke.test.ts`). Shared per-area fixtures go in `tests/<extension>/setup.ts` or `helpers.ts` — plain modules imported explicitly, never auto-loaded.

Naming: `describe("<function or unit>")` blocks with `it("<behavior in plain prose>")` cases. Import test APIs by name from `vitest` (`describe`, `it`, `expect`, `vi`) — no globals.

### 2. Narrow structural fakes over module mocking

Modules under test take their collaborators as **narrow `Pick<>` seams**, and tests construct plain objects with `vi.fn()`:

```ts
// src/extensions/boundary/detector.ts
export type Classifier = Pick<SideRunner, "classify">;

// tests/boundary/detector.test.ts
const classifierReturning = (value: unknown): Classifier => ({
  classify: vi.fn().mockResolvedValue(value),
});
```

The same pattern covers `Completer` (`Pick<SideRunner, "complete">`), `Runner` (`Pick<SideRunner, "run">`), and dependency bags like `SpawnDeps`/`ReconcileDeps`. Loggers are faked structurally: `{ warn: vi.fn(), error: vi.fn() } as unknown as Logger`.

Do not reach for `vi.mock()` module replacement — if a dependency is hard to fake, narrow the seam in the module under test instead.

### 3. Database tests: real SQLite, temp dirs, DDL mirrors

Database-touching tests use a **real SQLite file in a temp directory** — never an abstraction over the DB:

```ts
const dir = await mkdtemp(join(tmpdir(), "tachi-tasks-"));
const db = createDatabase(join(dir, "test.db"));
runMigrations(db);
```

Extension tables not yet covered by the central `drizzle/` migrations are created from a **DDL mirror** in the area's `setup.ts` (see `tests/tasks/setup.ts`, `tests/detached-processes/setup.ts`), always marked with the comment `// DDL mirror of schema.ts — remove once central migrations include these tables`. Keep the mirror byte-faithful to what drizzle-kit would generate; delete it when the central migration lands.

### 4. Real integration in temp dirs

Where the unit's job *is* the external system, test against the real thing, sandboxed in `mkdtemp` directories:

- **git** (`tests/git/`, `tests/projects/`): run the real `git` binary via `runGit`. Helpers in `tests/git/helpers.ts` provide `initRepo` (repo-local identity, `commit.gpgsign false` so commits work anywhere), `commitFile`, and `setupRemotePair` (bare origin + two clones for ahead/behind/diverged topologies).
- **processes** (`tests/detached-processes/`): spawn real OS processes and observe exit/output through the repository.

Clean up temp dirs in `afterEach` with `rm(dir, { recursive: true, force: true })`.

### 5. No live LLM or network

Tests never call a model or the network. `SideRunner` is always faked through its `Pick<>` seams; Telegram tests fake the Bot API surface; pi sessions are never created in tests. If a behavior seems to require a live model, the unit is cut wrong — split the prompt-assembly/parsing logic from the call.

### 6. Async and time

- Poll conditions with a `waitFor(condition, timeoutMs)` helper (see `tests/detached-processes/setup.ts`) instead of fixed sleeps.
- Use `vi.useFakeTimers()` for hold-window/digest timing logic (see `tests/notifications/router.test.ts`); restore real timers in `afterEach`.
- Never leave a test dependent on wall-clock races.

## Rationale

1. **Structural fakes**: `Pick<>` seams document exactly what a module needs, keep fakes one object literal long, and break loudly at compile time when the real interface changes — unlike patch-by-path mocking.
2. **Real SQLite/git/processes**: the behaviors under test (migrations, rebase topologies, PID lifecycles) live in those systems; faking them tests the fake.
3. **DDL mirrors**: let extension tests run before central migration generation (DES-002 forbids running `drizzle-kit generate` from feature work) without committing throwaway migrations.
4. **Mirrored layout**: finding the tests for any module is mechanical.

## Exceptions

- `tests/smoke.test.ts` wires more of the app together than a unit test would — acceptable as the single end-to-end guard.
- Pure helpers with no collaborators need no fakes or temp dirs; test them directly.

## Related Patterns

- DES-001: Unified extension API (the seams extensions expose)
- DES-002: Extension authoring conventions (test placement, validation gates)
- DES-004: Logging conventions (fake loggers in tests)
