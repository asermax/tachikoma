# Design: Agent Integration

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/agent-integration.md](../feature-specs/agent-integration.md)
**Status**: Current

## Purpose

Explain how Tachikoma embeds pi — session construction, credentials, model tiers, event adaptation, and side-channel LLM work — and why each seam sits where it does.

## Problem Context

pi is an embeddable coding agent; Tachikoma hosts it as a personal assistant. That means: an isolated agent dir so Tachikoma never collides with the user's own pi install, several model roles with different cost profiles, headless LLM work that must not touch the conversational session, and a hard rule that pi types never leak past this layer (channels and extensions see only domain types).

**Constraints:**
- pi is pre-1.0 and fast-moving; the verified surface lives in `docs/reference/pi-sdk-notes.md` and must be re-checked on upgrade
- pi has no built-in structured-output API for one-shot calls — classification has to be built on `completeSimple`
- Tests cannot hit live models; the adapter and side-runner consumers are faked via `Pick<>` types ([DES-002](../design/DES-002-extension-authoring.md))

**Interactions:**
- The [conversation loop](conversation-loop.md) calls `AgentManager.open()` for main/resumed sessions and `streamPrompt` per exchange
- Extensions get this layer as `app.agent` (`src/extensions/host.ts` builds a per-extension `SideRunner`)
- [Boundary detection](../feature-specs/boundary-detection.md) is the heaviest `side.classify`/`side.complete` consumer
- [Core shell](../feature-specs/core-shell.md) defines the workspace `piDir` layout the manager builds on

## Design Overview

`AgentManager` is the single construction path for every pi session. It owns the process-wide `AuthStorage`/`ModelRegistry`/`ModelTiers`, and `open(options)` varies along three axes: persistence (`SessionManager.create` / `.open(sessionFile)` / `.inMemory`), binding (all registered factories and prompt builders, the curated background-factory subset, or `bare`), and model (`tier`, or an explicit `model` reference that pins the model over the tier). A fresh `DefaultResourceLoader` is built per open, which is what makes session replacement in the coordinator a plain re-open.

`streamPrompt` is the only bridge from pi's push events to the domain: it subscribes, runs `prompt()`, queues mapped events, and yields them as an async iterable whose termination doubles as the exchange-end signal. `SideRunner` sits beside the session machinery for non-conversational LLM work, going straight to pi-ai's `completeSimple` for text and JSON, and to a bare in-memory session when tool use is needed.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/agent/manager.ts` | Auth/model-registry ownership; `open()` builds sessions (loader, session manager mode, settings, model) | Auth fallback to machine-level pi login; fresh loader per open; `bare` axis for headless work; an explicit `model` option pins the model over the `tier` chain (used by skill-agent delegation); an opt-in `isolatePrompt` spreads `isolatedLoaderOptions()` (`appendSystemPromptOverride: () => []`, `noContextFiles`, `noSkills`) into the loader so a delegated subagent never inherits pi's append / project context files / skills catalog ([DES-005](../design/DES-005-base-prompt-ownership.md)); `selectExtensionFactories` resolves which factories bind — the background subset (`bindBackgroundFactories`), none (`bare`), or all — so background task runs get a curated capability slice |
| `src/agent/models.ts` | `MODEL_TIERS` const map, `parseModelRef`, `ModelTiers.configuredRef/resolve/resolveRef` | `provider/model-id` strings resolved against pi's registry; `configuredRef` returns the configured reference for a tier after applying the fallback chain; `resolveRef` resolves an explicit (non-tier) reference for callers that pin a model directly; fail fast at resolve time |
| `src/agent/adapter.ts` | `streamPrompt`: pi `session.subscribe()` events → `AgentEvent` async iterable | Pull-based queue with promise wake; terminal `result`/`error` event; per-exchange cost/usage from `agent_end`; surrogate sanitization on emitted content; unsubscribe in `finally` |
| `src/agent/errors.ts` | `classifyErrorKind`: failure message → `ErrorKind`; `classifyError`: failure message → `{ errorKind, recoverable }` | Pattern-matched kinds (auth/billing/encoding/provider/unknown); `classifyError` wraps `classifyErrorKind` and adds the recoverable flag; only auth and billing are non-recoverable; both are exported for callers that need just the kind |
| `src/agent/sanitize.ts` | `sanitizeText`: strip lone UTF-8 surrogate code points | Pure helper; valid surrogate pairs preserved, only unpaired halves removed |
| `src/agent/side-run.ts` | `SideRunner.complete/classify/run` | `completeSimple` for one-shots; prompt-engineered JSON + validation + single retry for classify; disposable bare session for `run`, which forwards `isolatePrompt` to `open()` for fully isolated delegated prompts, and `backgroundExtensions` to bind the background-factory subset while dropping the hard tool allowlist (so the bound tools stay active) |
| `src/extensions/host.ts` | Exposes the layer as `app.agent` | One `SideRunner` per extension, bound to that extension's logger |

## Key Decisions

### Fall back to the machine-level pi login

**Choice**: Use `{workspace}/.tachikoma/pi/auth.json` only when it exists with real content (size > 2 bytes, i.e. more than `{}`); otherwise share `~/.pi/agent/auth.json` and env vars via `AuthStorage.create()`.
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
- `session.getSessionStats()` for cost: returns cumulative session totals, not the per-exchange delta the result should report
- A single recoverable boolean with no kind: loses the auth-vs-billing-vs-transient distinction that drives both UX copy and diagnosis
- Sanitizing only at the channel boundary: every consumer would need to remember to do it; doing it once at the adapter is the single choke point all content already flows through

**Consequences**:
- Pro: channels log real cost/token numbers and correlate by session id; the REPL and Telegram surface a non-recoverable failure as needing attention
- Pro: error classification and sanitization are pure, independently tested modules (`tests/agent/errors.test.ts`, `tests/agent/sanitize.test.ts`)
- Con: classification is heuristic on message text — an unrecognized non-recoverable failure defaults to recoverable, so the next message simply retries
- Con: `result.usage`/`result.costUsd` are absent when a run produces no final `agent_end` (e.g. immediate abort); consumers must treat them as optional

### Prompt-engineered JSON classification with one retry

**Choice**: `classify()` appends the JSON Schema to the system prompt, extracts JSON from the reply (fenced block, then outermost braces), validates with the same TypeBox machinery as config parsing (`parseWithSchema`), and retries once with a format reminder.
**Why**: Classification calls (boundary detection, task evaluation) are high-frequency and latency-sensitive; `completeSimple` is one HTTP call with no session scaffolding. The schema doubles as the validator, so malformed output is caught, and a single retry empirically covers the common failure mode.
**Alternatives Considered**:
- pi's `terminate: true` tool pattern in an in-memory session: guaranteed structure, but spins up a full agent session per classification
- No validation: silent drift when models add prose

**Consequences**:
- Pro: typed results (`Static<S>`) with two model calls worst-case
- Con: still probabilistic — a second malformed reply propagates as an error to the caller

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

### Scenario: Headless extraction run

**Given**: A post-processor calls `app.agent.side.run({ prompt, tools: ["read"], tier: "processor" })`
**When**: The run executes
**Then**: A bare in-memory session opens (no Tachikoma factories, no persisted transcript), tools are limited to `read`, the final assistant text is returned, and the session is disposed in `finally` even when the prompt fails.

## Notes

- `SettingsManager.create(workspace.root, workspace.piDir)` keeps pi settings inside the data dir alongside auth and `models.json` — nothing under `~/.pi` is written unless the machine-level auth store is the active one.
- No tier has a built-in default: all four `[agent]` fields are `Type.Optional(Type.String())` with no schema default (`src/config/schema.ts`). The `provider/model-id` strings shown in the generated config template (`main`/`searcher` → `anthropic/claude-opus-4-5`, `processor`/`classifier` → `anthropic/claude-haiku-4-5`) are commented-out example values, not active defaults. An unset chain defers to pi's own resolution (settings `defaultProvider`/`defaultModel`, else the first credentialed model).
- `run()` returns only final text; structured output from tool-using runs would need pi's `terminate: true` pattern, deliberately not built yet.
