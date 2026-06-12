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
- Tables: export drizzle tables from `schema.ts`; they are aggregated centrally in `src/db/schema.ts` (one line: `export * from "../extensions/<name>/schema.ts"`). NEVER run `drizzle-kit generate` yourself — migrations are generated centrally after integration.
- Registration: the extension is added to `src/extensions/index.ts` load order centrally — do not edit that file from a feature branch of work.

## The AppContext (see `src/extensions/api.ts` for the source of truth)

| Need | Use |
|---|---|
| feature config | `configSchema` (TypeBox) + `app.extensionConfig` — section `[extensions.<name>]` |
| startup init | `app.bootstrap("hook-name", fn)` — idempotent, fail = abort startup |
| inject context before prompts | `app.agent.provideContext({ name, provide })` — return `{ tag, content }` or `null` |
| add agent tools / pi hooks | `app.agent.use((pi) => { pi.registerTool({...}); pi.on(...); })` |
| system prompt section | `app.agent.systemPrompt(() => "...")` |
| cheap LLM side-calls | `app.agent.side.complete/classify` (typed via TypeBox schema) |
| headless agent run | `app.agent.side.run({ prompt, system, tools, tier })` |
| react after each exchange | `app.sessions.onExchange({ name, process })` |
| work at session close | `app.sessions.registerProcessor({ name, phase?, process })` — phases main → preFinalize → finalize |
| intercept inbound messages | `app.inbound.use(middleware)` |
| periodic work | `app.scheduler.cron(name, pattern, fn)` / `.every(name, seconds, fn)` |
| user-facing background output | `app.channels.deliver({ text, gate: "idle" \| "immediate", maxHoldSeconds })` |
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
