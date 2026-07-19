# DES-002: Extension Authoring Conventions

## Status

Accepted

## Context

Every Tachikoma feature is an extension (DES-001). This document fixes the mechanical conventions so all extensions look alike and pass the quality gates.

## Layout

```
src/extensions/<name>/
  index.ts        # default export: defineExtension({ name, configSchema?, setup })
  schema.ts       # drizzle tables (only if the extension owns tables)
  <topic>.ts      # one concern per module (loader, processor, tools, …)
tests/<name>/
  <topic>.test.ts # vitest, colocated fakes; no live LLM/network calls
```

- `index.ts` should mostly *wire*: read config, register pieces on `app`. Logic lives in sibling modules that take their dependencies as narrow parameters (e.g. `Pick<SideRunner, "classify">`) so tests can fake them.
- **No direct imports between extension directories** (`src/extensions/<a>/` ↔ `src/extensions/<b>/`): each extension owns and registers its own pieces (processors, tools, crons), even when another extension's wiring is a natural-looking host. Shared helpers belong in a neutral module outside both extensions (e.g. `src/util/`, `src/agent/`); cross-extension signaling at runtime goes through `app.events.on/emit`. This keeps extensions independently loadable and avoids import cycles.
- Tables: export drizzle tables from `schema.ts`; they are aggregated centrally in `src/db/schema.ts` (one line: `export * from "../extensions/<name>/schema.ts"`). When you change `schema.ts`, generate the migration immediately with `pnpm exec drizzle-kit generate --name <descriptive_snake_case>` and commit it alongside the schema change — migrations are part of the implementation, never deferred to a later step. Follow the `NNNN_descriptive.sql` naming convention (e.g. `0005_tasks_pi_session_file`).
- Registration: the extension is added to `src/extensions/index.ts` load order centrally — do not edit that file from a feature branch of work.

## The AppContext (see `src/extensions/api.ts` for the source of truth)

| Need | Use |
|---|---|
| feature config | `configSchema` (TypeBox) + `app.extensionConfig` — section `[extensions.<name>]` |
| startup init | `app.bootstrap("hook-name", fn)` — idempotent, fail = abort startup |
| inject a context section as a hidden message | `app.agent.use(provideContext(provide, "custom-type"), { sessionScopes })` — `provide` is a string or `(ctx) => string \| Promise<string>` (empty → no injection) |
| append a context section to the system prompt | `app.agent.use(provideContext(provide), { sessionScopes })` — same `provide`, no `customType` → appended to the turn's system prompt |
| add agent tools / pi hooks | `app.agent.use((pi) => { pi.registerTool({...}); pi.on(...); })` |
| cheap LLM side-calls | `app.agent.side.complete/classify` (typed via TypeBox schema) |
| headless agent run | `app.agent.side.run({ prompt, system, tools, tier })` |
| react after each exchange | `app.sessions.onExchange({ name, process })` |
| work at session close | `app.sessions.registerProcessor({ name, phase?, process })` — phases main → preFinalize → finalize |
| intercept inbound messages | `app.inbound.use(middleware)` |
| periodic work | `app.scheduler.cron(name, pattern, fn)` / `.every(name, seconds, fn)` |
| background output (queued, surfaced as an agent turn) | `app.channels.deliver({ text, tier: "urgent" \| "normal" \| "low" })` |
| small persistent state | `app.state.get/set/delete` (namespaced KV) |
| structured persistence | drizzle tables via `app.db` |
| cross-extension signals | `app.events.on/emit` |
| progress lines | `app.status("…")` |

## Style rules (enforced by the gates)

- TypeScript with **`.ts` extensions in relative imports** (Node runs sources directly).
- **No constructor parameter properties** (`constructor(private x …)`) — `erasableSyntaxOnly` forbids them; declare fields explicitly.
- Schemas: **`typebox`** (the 1.x package) — never `@sinclair/typebox`. String unions via `StringEnum` from `@earendil-works/pi-ai`.
- Null checks with `value == null` / `value != null`. No TS `enum` — const maps + derived types.
- Named exports; arrow functions assigned to consts; airy grouping with blank lines around control structures.
- Comments only for non-obvious WHY; never narrate the next line.
- Tool registration: tools the LLM calls go through `pi.registerTool` with TypeBox `parameters`, `promptSnippet` for the system-prompt line, guidelines naming the tool explicitly. Throw from `execute` to signal errors. Truncate large outputs with pi's `truncateHead/truncateTail`.
- Long-form prompts live as module-level template constants near their use.
- Workspace paths via `app.workspace.resolve(...)`; internal state under `app.workspace.dataDir`.
- External processes: `node:child_process` (`execFile` promisified) — no shell strings unless unavoidable.

## Validation

`just check` (Biome lint, `tsc --noEmit`, vitest) must pass. Tests must not hit the network or a real model — fake `SideRunner`/`SessionsApi` with `Pick<>` types like `tests/boundary/` does.
