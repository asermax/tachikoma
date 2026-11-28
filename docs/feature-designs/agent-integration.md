# Design: Agent Integration

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/agent-integration.md](../feature-specs/agent-integration.md)
**Status**: Current

## Purpose

Explain how Tachikoma embeds pi — session construction, credentials, model tiers, event adaptation, and side-channel LLM work — and why each seam sits where it does.

## Problem Context

pi is an embeddable coding agent; Tachikoma hosts it as a personal assistant. That means: an isolated agent dir so Tachikoma never collides with the user's own pi install, several model roles with different cost profiles, headless LLM work that must not touch the conversational session, and a hard rule that pi types never leak past this layer (channels and extensions see only domain types).

**Constraints:**

- pi is on a fast-moving 1.x line, pinned exact; the verified surface lives in `docs/reference/pi-sdk-notes.md` and must be re-checked on upgrade
- pi has no built-in structured-output API for one-shot calls — classification has to be built on `completeSimple`
- Tests cannot hit live models; the adapter and side-runner consumers are faked via `Pick<>` types ([DES-002](../design/DES-002-extension-authoring.md))

**Interactions:**

- The [conversation loop](conversation-loop.md) calls `AgentManager.open()` for main/resumed sessions and `streamPrompt` per exchange
- Extensions get this layer as `app.agent` (`src/extensions/host.ts` builds a per-extension `SideRunner`)
- [Boundary detection](../feature-specs/boundary-detection.md) is the heaviest `side.classify`/`side.complete` consumer
- [Core shell](../feature-specs/core-shell.md) defines the workspace `piDir` layout the manager builds on

## Design Overview

`AgentManager` is the single construction path for every pi session. It owns the process-wide `ModelRuntime`/`ModelRegistry`/`ModelTiers`, and `open(options)` varies along three axes: persistence (`SessionManager.create` / `.open(sessionFile)` / `.forkFrom(forkFromFile)` / `.inMemory`), binding (all registered factories, the curated background-factory subset, or `bare`), and model (`tier`, or an explicit `model` reference that pins the model over the tier). The core base prompt is `AgentManager`'s own concern: for any non-bare session that brings no explicit `systemPrompt`, it installs `buildMainSystemPrompt({ workspaceRoot })` (identity + guidance + workspace root) as the `systemPromptOverride`, replacing pi's coding prompt; SOUL/USER and per-extension guidance are layered on by extensions via `provideContext` (see [DES-005](../design/DES-005-base-prompt-ownership.md)). `provideContext`'s hidden-message mode (`customType`) injects once per session by default; an optional `options.debounceMs` (or the `provideDebouncedContext` helper, default 30 min) turns that latch into a leading-edge refresh-rate limiter — the first turn injects, further turns within the window are suppressed, and the next turn at/after the boundary re-injects with content re-resolved. This is distinct from [DES-007](../design/DES-007-debounced-background-task.md)'s trailing-edge coalescing debounce (`createDebouncedTask`, which fires once after a burst settles): there is no trailing action to coalesce and no timer to arm, only a per-turn timestamp comparison, so the gate is a pure function (`dueForInjection`) and the window state lives in the per-session factory closure. A fresh `DefaultResourceLoader` is built per open, which is what makes session replacement in the coordinator a plain re-open. `open()` also carries a fourth, opt-in axis — skill loading: `skillPaths` feeds the loader's `additionalSkillPaths` and `forceLoadSkills` binds the force-load injection factory (`src/agent/force-load-skills.ts`), so a headless run can ground itself in specific skills without widening its tool surface or system prompt. On top of `open`, `forkAndContinue(file, prompt, tier, tools?)` is the post-processing fork primitive: a non-bare fork of `file` (persona + history live), tool-restricted by `tools`, handed one follow-up user turn and disposed — used by memory extraction and core-context. Because a non-bare fork binds every registered factory, those forks also fire each factory's `before_agent_start` work; `AgentManager` tracks a `forkDepth` counter around each `forkAndContinue` run and exposes `isForking()` (surfaced as `app.agent.isForking()`) so a factory can scope per-turn work to genuine top-level turns and skip it inside the post-processing forks.

`streamPrompt` is the only bridge from pi's push events to the domain: it subscribes, runs `prompt()`, queues mapped events, and yields them as an async iterable whose termination doubles as the exchange-end signal. `SideRunner` sits beside the session machinery for non-conversational LLM work, going straight to pi-ai's `completeSimple` — from its `/compat` entry, which pi documents as temporary — for text and JSON, and to a bare in-memory session when tool use is needed.

## Components

### Implementation Structure

| Component                                | Responsibility                                                                                                                                                                                                                        | Key Decisions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/agent/manager.ts`                   | Model-runtime and model-registry ownership; `open()` builds sessions (loader, session manager mode, settings, model)                                                                                                                  | One process-wide `ModelRuntime` (pi's model/auth runtime, from `ModelRuntime.create`) backs the `ModelRegistry` and every `createAgentSession` call; auth fallback to machine-level pi login; fresh loader per open; `bare` axis for headless work; an explicit `model` option pins the model over the `tier` chain (used by skill-agent delegation); an opt-in `isolatePrompt` spreads `isolatedLoaderOptions()` (`appendSystemPromptOverride: () => []`, `noContextFiles`, `noSkills`) into the loader so a delegated subagent never inherits pi's append / project context files / skills catalog ([DES-005](../design/DES-005-base-prompt-ownership.md)); an opt-in `skillPaths` adds loader `additionalSkillPaths` (composes with `noSkills` — the added paths are still discovered) and an opt-in `forceLoadSkills` appends `forceLoadSkillsFactory` after `selectExtensionFactories` (binds even for `bare`, which selects none) — the skill-loading axis, in the curated-options style of `bindBackgroundFactories`; `selectExtensionFactories` resolves which factories bind — the background subset (`bindBackgroundFactories`), none (`bare`), or all — so background task runs get a curated capability slice |
| `src/agent/models.ts`                    | `MODEL_TIERS` const map, `parseModelRef`, `ModelTiers.configuredRef/resolve/resolveRef`                                                                                                                                               | `provider/model-id` strings resolved against pi's registry; `configuredRef` returns the configured reference for a tier after applying the fallback chain; `resolveRef` resolves an explicit (non-tier) reference for callers that pin a model directly; fail fast at resolve time                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `src/agent/adapter.ts`                   | `streamPrompt`: pi `session.subscribe()` events → `AgentEvent` async iterable                                                                                                                                                         | Pull-based queue with promise wake; terminal `result`/`error` event; per-exchange cost/usage from `agent_end`; surrogate sanitization on emitted content; unsubscribe in `finally`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `src/agent/errors.ts`                    | `classifyErrorKind`: failure message → `ErrorKind`; `classifyError`: failure message → `{ errorKind, recoverable }`                                                                                                                   | Pattern-matched kinds (auth/billing/encoding/provider/unknown); `classifyError` wraps `classifyErrorKind` and adds the recoverable flag; only auth and billing are non-recoverable; both are exported for callers that need just the kind                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `src/agent/sanitize.ts`                  | `sanitizeText`: strip lone UTF-8 surrogate code points                                                                                                                                                                                | Pure helper; valid surrogate pairs preserved, only unpaired halves removed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `src/agent/side-run.ts`                  | `SideRunner.complete/classify/run`                                                                                                                                                                                                    | `completeSimple` from `@earendil-works/pi-ai/compat`, the temporary entry that keeps pi-ai's old global API, for one-shots (forwarding `maxTokens`/`temperature`/`signal`); prompt-engineered JSON (string-aware brace extraction) + validation + single retry for classify, which caps output and samples at temperature 0 and forwards an abort signal so a deadline cancels the call; disposable bare session for `run`, which forwards `isolatePrompt` to `open()` for fully isolated delegated prompts, `backgroundExtensions` to bind the background-factory subset while dropping the hard tool allowlist (so the bound tools stay active), and `skillPaths`/`forceLoadSkills` unchanged (see `force-load-skills.ts`) — the skill-loading pair for grounded headless runs                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `src/agent/force-load-skills.ts`         | `forceLoadSkillsFactory({names, log, readSkill?})`: a pi extension factory whose `before_agent_start` handler copies the named skills' bodies from pi's catalog (`stripFrontmatter`-stripped) into one hidden `skill-content` message | Caller-directed skill loading for grounded headless runs (skill-evolution's proposal agent force-loads the authoring guides) — not the classifier-picked proactive injection of the skills extension's `suggest.ts` (the structural precedent; its workflow-start wording is deliberately not carried over); once-per-session latch per name; per-skill fail-soft (absent from catalog or unreadable → warn and skip, empty → debug skip, the run proceeds ungrounded); single-prompt runs only — no `session_compact` re-evaluation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `src/agent/skill-body.ts`                | `readSkillFile` (default synchronous SKILL.md reader) and `readSkillBody(skill, readSkill, log, label)`: a skill's frontmatter-stripped, trimmed body, or null                                                                        | The one shared body-load mechanic for both hidden-`skill-content` injectors (`suggest.ts`'s proactive loader and `force-load-skills.ts`), so a pi strip/`filePath` shape change is edited once; each injector keeps its own framing and render tokens; fail-soft per skill (unreadable → warn, empty → debug, caller skips that skill)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `AgentManager.forkDepth` / `isForking()` | Tracks whether a `forkAndContinue` run is in flight                                                                                                                                                                                   | Counter incremented as the first statement of `forkAndContinue` and released in `finally` (covers `open` failures and nested/parallel forks); `app.agent.isForking()` lets a factory skip per-turn work inside post-processing forks. `SideRunner` runs (`complete`/`run`) do not touch it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `src/extensions/host.ts`                 | Exposes the layer as `app.agent`                                                                                                                                                                                                      | One `SideRunner` per extension, bound to that extension's logger                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

## Key Decisions

### Fall back to the machine-level pi login

**Choice**: Use `{workspace}/.tachikoma/pi/auth.json` only when it exists with real content (size > 2 bytes, i.e. more than `{}`); otherwise `ModelRuntime.create()` is given no `authPath`, so pi reads `~/.pi/agent/auth.json` and the provider environment variables.
**Why**: Developers running Tachikoma on a machine where they already use pi should not authenticate twice, while a dedicated deployment (VPS) can hold its own credentials inside the workspace data dir. The size guard prevents a present-but-empty file from shadowing a working login.
**Alternatives Considered**:

- Always workspace-local: isolation, but forces a second OAuth/key setup on dev machines
- Always machine-level: no way to give a deployment scoped credentials
- An explicit config switch: another knob for what file presence already expresses

**Consequences**:

- Pro: zero-config on dev machines, self-contained on servers
- Con: which store is active is implicit — diagnosable only by checking the local file

### Model tiers resolved through pi's `ModelRegistry`

**Choice**: A four-tier split (`main`/`searcher`/`processor`/`classifier`) as config strings `provider/model-id`, resolved via `registry.find()` with a workspace `models.json` overlay; resolution failures throw.
**Why**: Tiers let every consumer ask for a role, not a model, so cost tuning is one config edit. Going through the registry (instead of pi-ai's static `getModel`) keeps custom providers and `models.json` entries usable, and validates that auth knows the provider.
**Alternatives Considered**:

- Hardcoded models per call site: the exact sprawl the role-based config exists to eliminate
- Single model everywhere: makes extraction/classification pay conversational-model prices

**Consequences**:

- Pro: `app.agent.models` gives extensions tier lookup without config coupling
- Con: a typoed model id surfaces only when first resolved, not at config load

### Pull-based adapter with terminal events instead of thrown errors

**Choice**: `streamPrompt` buffers mapped events in a queue drained by an async generator; prompt failure becomes a final `{ kind: "error" }` event rather than a rejection, and success appends `{ kind: "result" }`.
**Why**: Channels consume a single `for await` loop; making termination an in-band event means a channel renders provider failures with the same code path as text, and the coordinator can treat "iterator done" as "exchange done" without racing pi's promise.
**Alternatives Considered**:

- Handing channels the raw `session.subscribe()`: leaks pi types and has no notion of per-exchange scope
- Throwing from the iterator: every channel would need try/catch rendering duplicating the error path

**Consequences**:

- Pro: one ordering guarantee — events, then exactly one terminal event
- Pro: trivially testable with a fake session (`tests/adapter.test.ts`)
- Con: unmapped pi events (queue updates, turn boundaries) are silently dropped until a mapping is added

### Terminal events carry result accounting and error classification

**Choice**: The terminal `result` event carries the session id plus the exchange's token `usage` and USD `cost`; the terminal `error` event carries a `recoverable` flag and an `errorKind`. Cost/usage are summed from the assistant turns in the run's final `agent_end` event (skipping retry-boundary `agent_end`s, which `willRetry` marks). Errors are classified by pattern-matching the failure message in `src/agent/errors.ts`. Emitted text, thinking, and error strings are run through `sanitizeText` first.
**Why**: Channels need accounting (cost/usage logging, a session id to correlate) and a recoverability signal to decide whether a failure warrants user attention or will self-heal on the next message — that fidelity is what makes a terminal event more than a bare stop reason. pi exposes per-turn `usage` (with `cost`) on each `AssistantMessage` and the run's messages on `agent_end`, so summing them is the faithful per-exchange total. pi's own retry classifier already encodes which provider conditions are transient; mirroring those patterns keeps the recoverable/non-recoverable split aligned with pi's behavior. Surrogate sanitization prevents lone UTF-16 surrogates in streamed deltas from throwing when the Telegram API or transcript writers re-encode as UTF-8.
**Alternatives Considered**:

- `session.getSessionStats()` for cost: returns cumulative session totals, cache-warm refreshes included, not the per-exchange delta the result should report
- A single recoverable boolean with no kind: loses the auth-vs-billing-vs-transient distinction that drives both UX copy and diagnosis
- Sanitizing only at the channel boundary: every consumer would need to remember to do it; doing it once at the adapter is the single choke point all content already flows through

**Consequences**:

- Pro: channels log real cost/token numbers and correlate by session id; the REPL and Telegram surface a non-recoverable failure as needing attention
- Pro: error classification and sanitization are pure, independently tested modules (`tests/agent/errors.test.ts`, `tests/agent/sanitize.test.ts`)
- Con: classification is heuristic on message text — an unrecognized non-recoverable failure defaults to recoverable, so the next message simply retries
- Con: `result.usage`/`result.costUsd` are absent when a run produces no final `agent_end` (e.g. immediate abort); consumers must treat them as optional

### Prompt-engineered JSON classification with one retry

**Choice**: `classify()` appends the JSON Schema to the system prompt, extracts JSON from the reply (fenced block, then a string-aware brace scan to the first balanced object — ignoring trailing prose), validates with the same TypeBox machinery as config parsing (`parseWithSchema`), and retries once with a format reminder (skipping the retry when an abort signal has already fired). Output is capped (`maxTokens`, default 256) and sampled at `temperature` 0, and a caller-supplied `AbortSignal` is forwarded so a deadline cancels the underlying request rather than leaving it racing.
**Why**: Classification calls (boundary detection, task evaluation) are high-frequency and latency-sensitive; `completeSimple` is one HTTP call with no session scaffolding. The schema doubles as the validator, so malformed output is caught, and a single retry empirically covers the common failure mode.
**Alternatives Considered**:

- pi's `terminate: true` tool pattern in an in-memory session: guaranteed structure, but spins up a full agent session per classification
- No validation: silent drift when models add prose

**Consequences**:

- Pro: typed results (`Static<S>`) with two model calls worst-case
- Con: still probabilistic — a second malformed reply propagates as an error to the caller

### Prompt-cache warming between runs is left on pi's default

**Choice**: Tachikoma sets no `cacheWarming` override and registers no `cache_warming_decision` handler. pi's `idle` default applies: after an exchange settles, pi may keep the trunk's prompt cache warm for up to 30 minutes.
**Why**: pi sends a refresh only when the model declares a prompt-cache lifetime and expects the refresh to save at least $0.05, and a refresh is the last request re-sent with a single output token. The warmer's lifecycle bounds the cost: a new run replaces the previous warming, warming moves to the idle phase when the run settles, and `session.dispose()` cancels it. `forkAndContinue` and `SideRunner.run` dispose their sessions in a `finally`, so only the long-lived trunk idles warm. A refresh is persisted as a `usage` entry of kind `cache_warm` and emitted as `entry_appended`; it counts toward pi's session totals and never enters the model context. `streamPrompt` sums an exchange's usage from that exchange's own assistant messages, so the reported usage and cost are unaffected.
**Alternatives Considered**:

- `"streaming"` or `"off"` in `{workspace}/.tachikoma/pi/settings.json`: considered and not chosen — the pi default is adopted as it is. The setting is pi's and global; an operator who wants either sets it there
- An extension handling `cache_warming_decision`, to approve or stop each refresh per session: out of scope; nothing overrides pi's decision

**Consequences**:

- Pro: a follow-up inside the window can reuse the trunk's warm prompt cache, with no wiring of Tachikoma's own
- Con: possible provider cost while the trunk sits idle after an exchange, for a model that declares a cache lifetime. The maintainer checks the logs a few minutes after an exchange for cache-warming errors

### The summary chain descends from the session's system message

**Choice**: The session-tree seam appends a branch summary that has no summary above it under the session's system message — the root-most declaration on the live branch's path — rather than as a parentless root. Topic summaries extend the one before them and a tangent summary hangs at its checkpoint, so every summary descends from the day's declaration.
**Why**: pi builds each request from the session file's leaf path, tools and prompt sections included, and replays them from the system messages on that path. A summary chain that starts at the day's system message carries them on every path the trunk can continue from — the chain, a rollback rewind, a reopen's re-seat — and a collapse adds no declaration of its own; a leaf moved onto such a path without the seam still sends the full tool set. The append-only trunk model this rides on is [ADR-014](../architecture/ADR-014-session-source-of-truth.md)'s, and the verified pi mechanism is tracked in `docs/reference/pi-sdk-notes.md`.
**Alternatives Considered**:

- A parentless first summary, with the re-sync alone: every collapse and every same-day restart persists a full copy of the prompt sections and the tool declarations, and the trunk's context depends on the re-sync rather than on the tree's shape
- Hanging the summary under the latest system message on the path: a declaration pi wrote mid-topic sits below the topic's own turns, so the summary would carry them onto the trunk
- Resolving the first summary's parent at each caller: `null` already means "no summary above" to the coordinator, the reopen and rollback alike, so the seam is the one place that has to know which entry it names
- Rewriting the summaries of trunks already on disk into the rooted shape: it touches markers keyed by summary id, rollback targets and the channel ledger, for a file that is closed at midnight; the re-sync covers those trunks instead

**Consequences**:

- Pro: every request on such a trunk carries the tool set and the prompt sections from its own path, whatever path it is built from, and a collapse or a restart persists no copy of them
- Pro: the property holds by construction — a collapse, a rollback rewind or a reopen lands on a path that already declares the tools
- Con: a first-topic summary's `details.baseId` is `null`, which says that no summary is above it but not that its parent is the system message

### A leaf move re-syncs the agent's in-memory transcript

**Choice**: The session-tree seam's two leaf-moving wrappers (`branchWithSummary`, `reseatLeaf`) end by calling pi's `session.refreshContext()`, which rebuilds the agent's in-memory transcript from the session file's projection at the new leaf. They do not write the prompt sections or tool declarations themselves: pi persists those at the next request, on any path that lacks them.
**Why**: pi builds each request from the session file's leaf path, tools and prompt sections included, but decides what to declare by diffing against the in-memory transcript, and it re-syncs that transcript only after changes it commits through its own session layer. A leaf move made through `SessionManager` directly leaves the transcript on the old path, and a request built from a path that declares less than the transcript reports goes out with the difference missing: every tool on a trunk whose chain starts at a parentless summary, or one tool switched on inside an abandoned topic. The seam is the one place that wraps leaf moves, so the rule holds for every caller: the topic collapse, the same-day reopen, a rollback rewind and the tangent fold.
**Alternatives Considered**:

- Assigning the projection to `session.agent.state.messages` by hand: the same transcript, without the bookkeeping pi keeps beside it, which `refreshContext()` also refills
- Writing a copy of the current system message at collapse time: duplicates pi's declaration logic and couples the seam to that entry's shape
- Ending the re-sync once the chain is rooted: a trunk whose chain starts at a parentless summary sends requests with no tools after its next collapse or reopen, and a tool switched on inside an abandoned topic is missing from every later topic
- Routing collapses through pi's public `navigateTree()`: it navigates to an existing entry, so it cannot express a collapse onto a fresh root summary, and it moves summarization into an extension event, which breaks the collapse's no-partial-writes contract

**Consequences**:

- Pro: every request on the trunk carries the tool set and the prompt sections, whatever path it is built from, including on a trunk whose chain starts at a parentless summary
- Pro: on a chain that descends from the system message the re-sync writes nothing extra, and a tool switched on inside an abandoned topic comes back as a small delta
- Con: on a trunk whose chain starts at a parentless summary, one system-message entry is persisted per collapse and per same-day restart, carrying a full copy of the prompt sections and the tool declarations
- Con: the rule assumes leaf moves happen between exchanges, when the in-memory transcript is fully persisted; the seam documents the precondition and does not enforce it

## System Behavior

### Scenario: Provider failure mid-exchange

**Given**: A streaming exchange in progress
**When**: pi's `prompt()` rejects (provider error)
**Then**: Already-queued events are drained, a single sanitized `error` event is yielded — classified `recoverable` with `errorKind: "provider"` for a transient failure, or non-recoverable (`auth`/`billing`) when the message names an auth or billing problem — the subscription is removed, and the iterator completes. The coordinator's exchange ends normally; channels render a recoverable failure as a plain notice and a non-recoverable one with a "needs your attention" hint.

### Scenario: Successful exchange accounting

**Given**: A streaming exchange that completes normally
**When**: pi emits its final `agent_end` and `prompt()` resolves
**Then**: The terminal `result` carries the session id and the run's summed token `usage` and USD `cost` (totalled across the run's assistant turns). Channels log these (Telegram structured log, REPL dim line); a run that ends without a final `agent_end` yields a `result` with the session id but no usage.

### Scenario: Boundary classification with a sloppy model reply

**Given**: `classify()` called with the boundary decision schema
**When**: The model wraps its JSON in prose
**Then**: Brace extraction recovers the object and validation passes; if extraction or validation fails, one retry runs with "output ONLY the JSON object" appended, and a second failure throws to the caller (boundary middleware error-isolates it).

### Scenario: After an exchange on the trunk

**Given**: A model that declares a prompt-cache lifetime
**When**: An exchange ends and the trunk stays open
**Then**: pi may keep the prompt cache warm for up to 30 minutes, sending a refresh only while it expects the refresh to pay for itself. Each refresh is persisted as a `cache_warm` usage entry and left out of the exchange's reported usage. Forks and headless runs do not warm, because they are disposed when they finish.

### Scenario: The first request after a topic collapse

**Given**: A trunk whose leaf sits, after a topic collapse, on the day's summary chain
**When**: The next message is prompted
**Then**: The request carries the session's full tool set and prompt sections, replayed from the system message the chain descends from, and pi writes no new declaration. On a trunk whose chain starts at a parentless summary, the leaf move re-syncs the agent's in-memory transcript onto the new path and pi finds nothing declared there, so it persists one system message as the first entry under the new base, ahead of the user message. A rollback rewind onto a path that already declares the tools adds no new declaration.

### Scenario: Headless extraction run

**Given**: A post-processor calls `app.agent.side.run({ prompt, tools: ["read"], tier: "processor" })`
**When**: The run executes
**Then**: A bare in-memory session opens (no Tachikoma factories, no persisted transcript), tools are limited to `read`, the final assistant text is returned, and the session is disposed in `finally` even when the prompt fails.

## Notes

- `SettingsManager.create(workspace.root, workspace.piDir)` keeps pi settings inside the data dir alongside auth and `models.json` — nothing under `~/.pi` is written unless the machine-level auth store is the active one. pi's `cacheWarming` setting is read from this directory's `settings.json` (`{workspace}/.tachikoma/pi/settings.json`) and nowhere else in the tree: it is a global setting, and Tachikoma sets none.
- No tier has a built-in default: all four `[agent]` fields are `Type.Optional(Type.String())` with no schema default (`src/config/schema.ts`). The `provider/model-id` strings shown in the generated config template (`main`/`searcher` → `anthropic/claude-opus-4-5`, `processor`/`classifier` → `anthropic/claude-haiku-4-5`) are commented-out example values, not active defaults. An unset chain defers to pi's own resolution (settings `defaultProvider`/`defaultModel`, else the first credentialed model).
- `run()` returns only final text; structured output from tool-using runs would need pi's `terminate: true` pattern, deliberately not built yet.
- The session-tree seam (`src/agent/session-tree.ts`) wraps the append-only `SessionManager` primitives [ADR-014](../architecture/ADR-014-session-source-of-truth.md) adopted for the trunk model: `branchWithSummary` (collapse + re-seat), `reseatLeaf` (`branch(id)` — move the leaf to an earlier entry), and `appendState`/`appendInContextEntry` (`appendCustomEntry`/`appendCustomMessageEntry`). There is no delete primitive; `/rollback`'s rewind is a `branch(id)` re-seat that leaves the wrong-framing exchange as an inert off-path branch. The verified surface is tracked in `docs/reference/pi-sdk-notes.md` and re-checked on upgrade. A summary with no summary above it is appended under the session's system message (`branchWithSummary`) — or, when the live path carries no system message, left a parentless root, as pi writes it — and both leaf-moving wrappers end by re-syncing the agent's in-memory transcript from the file's projection (see the key decisions above); `tests/agent/session-tree-projection.test.ts` runs them against a real, offline pi session.
