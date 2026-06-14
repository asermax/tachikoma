# DES-001: Unified Extension API

## Status

Accepted

## Context

Tachikoma's defining architectural bet on this stack: the core is only the main loops — config, logging, database, event bus, scheduler, channel registry, session registry, coordinator, and the extension host. Every feature (memory, boundary detection, skills, tasks, projects, git, channels, …) ships as an extension.

pi already has an extension system (`ExtensionAPI`), but it is *session-scoped*: extensions are instantiated per agent session and rebound when sessions are replaced. Tachikoma features also need *process-scoped* capabilities pi deliberately does not provide: cron scheduling, database access, channel delivery, cross-session lifecycle (open/close/resume), and post-session processing pipelines.

The unified extension API gives one extension format that spans both lifetimes, instead of forcing features to split into "a pi extension plus a separately-wired app module".

## Decision

A Tachikoma extension is a module whose default export is created with `defineExtension`:

```ts
import { Type } from "typebox";
import { defineExtension } from "../api.ts";

export default defineExtension({
  name: "memory",
  configSchema: Type.Object({
    enabled: Type.Boolean({ default: true }),
  }),

  async setup(app) {
    // ---- process-scoped (runs once at startup) ----
    app.bootstrap("memory", async () => { /* idempotent init */ });
    app.scheduler.cron("0 3 * * *", () => maintenanceTick(app));
    app.sessions.registerProcessor({ name: "episodic", phase: "main", process: extractEpisodic });
    app.agent.use({ name: "memory-index", contextProvider: injectIndex, sessionScopes: ["main", "background"] });

    // ---- session-scoped (factory invoked for every agent session) ----
    app.agent.use((pi) => {
      pi.registerTool({ /* ... */ });
      pi.on("tool_call", async (event) => { /* ... */ });
    });
  },
});
```

### Lifetimes

| Scope | Mechanism | Lives |
|---|---|---|
| Process | `setup(app)` + everything registered on `app` | once, from startup to shutdown |
| Agent session | factories passed to `app.agent.use(factory)` | per pi `AgentSession`; re-instantiated when the coordinator replaces the session (topic boundary, resume) |

Session factories receive pi's native `ExtensionAPI` — no wrapping, no renaming. Everything pi documents in its extensions guide applies verbatim. The host passes collected factories to `DefaultResourceLoader({ extensionFactories })` whenever it creates a session.

### AppContext services

| Service | Purpose |
|---|---|
| `app.config` | full validated app config (read-only) |
| `app.extensionConfig` | this extension's `[extensions.<name>]` section, validated against `configSchema`, defaults applied |
| `app.workspace` | workspace root path + helpers (`resolve`, `ensureDir`) |
| `app.log` | pino child logger bound to `component: <name>` (DES-002) |
| `app.db` | shared drizzle database handle |
| `app.state` | namespaced key-value store (per-extension, db-backed) for state that does not warrant tables |
| `app.events` | typed app event bus (publish/subscribe across extensions and core) |
| `app.scheduler` | croner-backed jobs: `cron(expr, fn, opts)`, `every(seconds, fn)`; all jobs named and owned by the extension |
| `app.sessions` | session registry access + lifecycle hooks: `onOpen`, `onExchange`, `registerProcessor` (post-processing), `current()`, `close()`, `openNew()`, `resume(id)` |
| `app.channels` | `register(channel)` for channel extensions; `deliver(item, { gate: "idle" \| "immediate", maxHoldSeconds })` for background-originated output |
| `app.agent` | `use(factory, { sessionScopes })` (pi extension factories) or `use({ contextProvider, sessionScopes, name })` (a persisted context section), `systemPrompt(builder)` (system-prompt section), `models` (tier lookup: agent/searcher/processor/classifier), `side` (headless side sessions for extraction/background work) |
| `app.bootstrap(name, hook)` | ordered, idempotent startup hooks |
| `app.onShutdown(name, hook)` | hook run once during shutdown, before the coordinator's final delivery flush (so it can push held output into that flush); error-isolated |
| `app.status(text)` | progress line surfaced through the active channel during processing |

### Pipelines

- **Context sections** (`app.agent.use({ contextProvider, sessionScopes, name })`): each contributes a `<context owner="…">` block injected once per session as a persisted hidden message via pi's `before_agent_start` (prepared on `session_start`, so `contextProvider` may read the session ctx and be async, and may be a static string). Sessions bind a section by `sessionScopes` (main and/or background), so context reaches the background agent exactly when scoped to it. Empty content contributes nothing. This is the single mechanism for extension-contributed context — there is no separate provider-collection layer.
- **Exchange processors** (`app.sessions.onExchange`): run after every completed prompt cycle (rolling summary, last-exchange tracking). Parallel, error-isolated.
- **Post-processing** (`app.sessions.registerProcessor`): run when a session closes, in phases `main` → `preFinalize` → `finalize`; parallel within a phase, error-isolated, completion recorded per processor on the session row.
- **Inbound middleware** (`app.inbound.use`): runs on every inbound message before the agent sees it; this is where boundary detection decides to continue, close + open, or resume a session.

### Loading

First-party extensions live in-tree under `src/extensions/<name>/` and are listed in `src/extensions/index.ts` in load order. Out-of-tree loading is itself an extension (`external`): it imports third-party `defineExtension` modules from configured sources or git-installed aliases — there is no separate plugin system; the extension API is the extensibility surface. Extension setup runs in list order; bootstrap hooks run afterwards in registration order, so an extension may rely on earlier extensions' services but not on later ones.

### Database contributions

First-party extensions define drizzle tables in `src/extensions/<name>/schema.ts`, re-exported by `src/db/schema.ts`; one drizzle-kit migrations directory covers the whole app. Third-party extensions must use `app.state` (no migration access).

## Consequences

- Features are uniform: reading one extension teaches the layout of all of them; deleting a directory removes a feature.
- pi's API is never wrapped or aliased — pi docs remain directly applicable, and pi upgrades surface as compile errors in extensions, not in a translation layer.
- Two lifetimes are explicit in the file layout: anything inside `app.agent.use(...)` may be torn down and re-created with the session; everything else must tolerate living for the whole process.
- The host must rebind session factories on every session replacement; the coordinator owns that, not the extensions (mirrors pi's own `session_shutdown`/`session_start` rebind contract).
- Core stays honest: if a feature can't be expressed with these services, the fix is widening AppContext deliberately — not implementing the feature inside core.
