## Project Overview

Tachikoma is a proactive personal assistant built on the pi coding-agent SDK (`@earendil-works/pi-coding-agent`). It maintains persistent memory across conversations, extracts learnings automatically, and runs background and scheduled tasks. Accessible via Telegram or a local REPL.

This is the TypeScript implementation. It runs on Node ≥22, which executes the `.ts` sources directly via native type-stripping — intra-repo imports therefore use explicit `.ts` extensions.

## Commands

All commands are available as `just` recipes (thin wrappers over the `pnpm` scripts). **Always use them to validate changes** — run `just check` (or the relevant subset) before considering any task complete.

```bash
just install                 # pnpm install
just run                     # run the agent (REPL by default)

just test                    # vitest run (all tests)
pnpm vitest run path/to.test.ts   # single file
pnpm vitest run -t "name"         # single test by name

just lint                    # biome check .
just fmt                     # biome check --write .
just typecheck               # tsc --noEmit
just build                   # tsc -p tsconfig.build.json
just check                   # lint + typecheck + test (all quality gates)
```

`biome check .` covers the whole repo; generated `drizzle/` artifacts are excluded in `biome.json`.

## Architecture

### Message Flow

```
User message → Channel (Telegram/REPL) → Coordinator.submit()
  → main loop (Coordinator.run): inbound middleware chain
    → Boundary detection (topic shift? resume a recent session? "/new" forces a fresh one)
    → ensure/resume the active pi session (AgentManager.open)
    → context providers gather blocks, injected via the host extension's before_agent_start hook
    → pi AgentSession streams the exchange; messages arriving mid-exchange steer the live run
    → Adapter (streamPrompt) maps pi events → AgentEvent domain types
  → Channel renders the response stream
  → Exchange processors run after each exchange (e.g. rolling summary)
  → On session close: post-processing pipeline by phase (main → preFinalize → finalize):
       memory extraction, core-context update, transcript archive, git commit
```

### Key Abstractions

- **Coordinator** (`coordinator.ts`): the central loop. Owns the inbox, the single active pi session, context injection, delivery gating, and the post-processing pipeline. Mid-exchange messages steer the live run; a `/queue` prefix opts out (waits for the next exchange) and `/new` forces a fresh session.
- **Channels** (`channels/`, `extensions/repl/`, `extensions/telegram/`): consume the `AgentEvent` stream and render to the user. Channels are thin — orchestration lives in the coordinator.
- **Adapter** (`agent/adapter.ts`): the boundary to pi. `streamPrompt` maps a pi session's events to domain `AgentEvent` types (`text`, `thinking`, `tool-start`/`tool-end`, `status`, `result`, `error`).
- **AgentManager / SideRunner** (`agent/manager.ts`, `agent/side-run.ts`): `AgentManager` opens primary pi sessions; `SideRunner` runs headless/forked LLM work off the main conversation (boundary classification, rolling summaries, memory extraction, commit messages) via `complete` and `run`.
- **Extension host** (`extensions/host.ts`, `extensions/api.ts`): builds the `AppContext` and runs each extension's `setup(app)`. First-party extensions are listed in `extensions/index.ts`; third-party ones load through the external-extension manager with startup isolation.
- **Sessions** (`sessions/registry.ts`): drizzle-backed session records (pi session file, summary, last exchange, `closedAt`, `postProcessingState`). Supports close/reopen for topic resumption, with bridging context injected on resume.
- **Scheduler** (`scheduler.ts`): croner-based `cron`/`every` registration for maintenance ticks, retention, and task firing.

### Extension Pattern

Every feature is an extension: `defineExtension({ name, configSchema, setup(app) })` (see `extensions/api.ts`). In `setup`, an extension wires into the host through the `app` API, using only what it needs:

- `app.bootstrap(name, hook)` — ordered, idempotent startup hooks
- `app.onShutdown(name, hook)` — runs once on shutdown, before the coordinator's final delivery flush (error-isolated)
- `app.agent.provideContext(provider)` — per-message context injection (pre-processing)
- `app.sessions.onExchange(processor)` — runs after each exchange (e.g. rolling summary)
- `app.sessions.registerProcessor(processor)` — session-close post-processing (phased: `main` → `preFinalize` → `finalize`)
- `app.agent.use(factory, { sessionScopes? })` — register agent tools via `pi.registerTool` (there is no MCP layer); `sessionScopes` selects which session contexts bind the factory (`["main"]` default, add `"background"` for autonomous task runs)
- `app.inbound.use(middleware)` — inbound message middleware (e.g. boundary detection)
- `app.scheduler.cron(...)` / `app.scheduler.every(...)` — scheduled work

Not every extension uses every hook. Use existing extensions as templates: `memory/` and `git/` register processors and bootstrap hooks; `tasks/` registers tools and scheduled work; `boundary/` is inbound middleware plus an exchange processor; `detached-processes/` registers tools and a watcher tick.

### Configuration

TOML config at the workspace config path. It is parsed with `smol-toml` and validated against TypeBox schemas in `src/config/`. Import TypeBox from `typebox` (not `@sinclair/typebox`). Never use TypeScript `enum`s — derive types from a `const` map.

### Database

drizzle-orm over `better-sqlite3` (synchronous). The core schema lives in `src/db/core-schema.ts`; each extension owns its tables in its own `schema.ts`. Migrations are generated by `drizzle-kit` into `drizzle/` and applied on startup. First-run import from a legacy Python install lives in `src/migration/`.

## Documentation

- `docs/planning/VISION.md` — full project vision and scope
- `docs/planning/DELTAS.md` — feature/work-item inventory with status tracking
- `docs/feature-specs/` — specifications per feature area
- `docs/feature-designs/` — design rationale per feature area
- `docs/delta-specs/` / `docs/delta-designs/` — per-delta specs and designs
- `docs/design/DES-*` — cross-cutting design patterns
- `docs/architecture/ADR-*` — architecture decision records

Use `/katachi:` commands to work with the planning framework.
