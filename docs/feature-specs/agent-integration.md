# Agent Integration

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Agent integration is the layer that embeds the pi agent SDK: `AgentManager` (`src/agent/manager.ts`) constructs `AgentSession`s with shared auth, model registry, and workspace wiring; `ModelTiers` (`src/agent/models.ts`) maps configured model references to pi registry entries by role; the adapter (`src/agent/adapter.ts`) converts pi session events into SDK-free domain `AgentEvent`s; and `SideRunner` (`src/agent/side-run.ts`) provides side-channel LLM work — one-shot completions, structured classification, and headless agent runs.

Extensions reach this layer only through `app.agent` (`use`, `models`, `side` — see [DES-001](../design/DES-001-unified-extension-api.md)); the [conversation loop](conversation-loop.md) consumes it for the main session.

## User Stories

- As the coordinator, I want one factory for pi sessions so that main, resumed, and headless sessions share auth, models, and workspace configuration
- As an extension developer, I want cheap side-channel LLM calls so that extraction and classification never go through the main conversation
- As a user, I want Tachikoma to reuse my existing machine-level pi login so that I do not authenticate twice
- As a channel, I want SDK-free agent events so that rendering does not depend on pi internals

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | `AgentManager.open()` builds pi sessions against the workspace: `cwd` is the workspace root, `agentDir` is `{workspace}/.tachikoma/pi`, with shared `AuthStorage`, `ModelRegistry`, and `SettingsManager` |
| R1 | Main sessions bind all registered pi extension factories and override the system prompt with the core base prompt (`buildMainSystemPrompt({ workspaceRoot })` — identity + guidance + workspace root), unless an explicit `systemPrompt` option is given |
| R1a | The core base prompt documents only the conversation substrate the core owns (issue-445): mid-exchange steering and the `/queue` opt-out, command routing, system-origin turns, and delivery timing, each stated compactly inline with a pointer line to a core reference file (`src/agent/references/conversation.md`, `config.md` — read on demand via `referencePointer`). It never names an extension's tools or turn formats — extension-specific guidance lives in the owning extension's usage section/reference ([DES-014](../design/DES-014-two-tier-agent-facing-documentation.md); see the placement matrix in [foundational-context](foundational-context.md) and [DES-005](../design/DES-005-base-prompt-ownership.md)) |
| R2 | Session persistence modes: new persisted session under the workspace sessions dir, resume from an existing pi session file, fork a fresh session seeded with another session file's full history (`forkFromFile`, via `SessionManager.forkFrom` — read-only on the source), or ephemeral in-memory |
| R3 | `bare` sessions skip registered factories and the core base prompt (headless side work); explicit `systemPrompt`, `tools`, and `tier` options override defaults; an opt-in `isolatePrompt` additionally suppresses pi's `APPEND_SYSTEM.md` append, project context files (AGENTS.md/CLAUDE.md), and the skills catalog so the session sees exactly its own `systemPrompt` (plus pi's date/cwd footer); an opt-in `bindBackgroundFactories` binds the registered background factories (those scoped via `app.agent.use(f, { sessionScopes: [..., "background"] })`) instead of no factories. An opt-in `skillPaths` adds extra skill-source directories to the loader (`additionalSkillPaths` — composes with `isolatePrompt`: the added paths are still discovered even under `noSkills`, and the catalog then contains exactly their skills; the skills-catalog *section* reaches the system prompt only when a built-in `read` tool is active, so catalog-listing is on the same terms as any other skill), and an opt-in `forceLoadSkills` binds an injection factory that copies the named skills' bodies from pi's catalog into one hidden `skill-content` message at run start (fail-soft per skill; once per session per skill) — the session's *system prompt* stays exactly its own `systemPrompt`, while its *input* additionally carries the injected message when the option is set (single-prompt runs only) |
| R4 | Auth resolution: a workspace-local `{piDir}/auth.json` is used when it has actual content; otherwise the machine-level pi login (`~/.pi/agent/auth.json` plus env vars) is shared; `apiKeyFor(provider)` exposes key lookup |
| R5 | Four model roles — `main`, `searcher`, `processor`, `classifier` — configured as optional `provider/model-id[:thinkingLevel]` strings; unset roles fall back along classifier → processor → main (searcher → main), then to pi's resolution (settings default, else first credentialed model); malformed references and models missing from pi's registry fail with errors naming the reference and tier |
| R6 | Session model fallback is logged as a warning; the thinking level comes from the role's `:level` suffix when present, otherwise pi's `defaultThinkingLevel` |
| R7 | `streamPrompt(session, text)` exposes one prompt run as an `AsyncIterable<AgentEvent>` ending in a terminal `result` or `error` event after pi's `prompt()` settles. The terminal `result` carries the `sessionId` and, when the run reports its totals, the summed token `usage` and USD `cost` for the exchange. The terminal `error` carries a `recoverable` flag and an `errorKind` (`auth`/`billing`/`encoding`/`provider`/`unknown`). Emitted text, thinking, and error content is sanitized of lone UTF-8 surrogate code points |
| R8 | `SideRunner.complete()` performs a one-shot completion on a tier model (default `processor`), throwing on `error`/`aborted` stop reasons |
| R9 | `SideRunner.classify()` returns schema-validated structured output (default tier `classifier`): JSON-instructed prompt, tolerant JSON extraction, TypeBox validation, one retry on failure |
| R10 | `SideRunner.run()` executes a headless agent run in an ephemeral bare session with an explicit pi tool allowlist (default none), returning the final assistant text and disposing the session; an `isolatePrompt` flag forwards to `open()` to fully isolate the system prompt (used by delegated subagents with self-contained prompts); a `backgroundExtensions` flag binds the background-factory subset and drops the hard tool allowlist (so the bound factory tools stay active), used by autonomous background task runs; `skillPaths` and `forceLoadSkills` forward to `open()` unchanged (R3 — used by skill-evolution's proposal agent to ground its run in the authoring guides) |
| R10a | `AgentManager.forkAndContinue(sourceSessionFile, prompt, tier, tools?)` (exposed to extensions as `app.agent.forkAndContinue`) forks the source session on the **non-bare** path — so the fork keeps the composed persona and the agent's own tools, with the source conversation live in its history — then sends `prompt` as one follow-up user turn, runs to completion, and disposes; the optional `tools` allowlist hard-limits the fork's toolset (the SDK `tools` option is independent of the system prompt, so persona survives while tools are restricted). Used by memory extraction and the core-context processor |
| R11a | `app.sessions.runPostProcessors(context)` runs the registered post-processors once in phase order (error-isolated), letting headless/background runs reuse the live-session post-processing pipeline. Context injection needs no equivalent collection call: extension context sections (`app.agent.use(provideContext(provide, customType?), { sessionScopes })`) reach background runs directly through their background-scoped pi factories' `before_agent_start` |
| R12 | The agent layer exposes typed wrappers over pi's **append-only** session-tree primitives (`src/agent/session-tree.ts`, per [ADR-014](../architecture/ADR-014-session-source-of-truth.md)): re-seat the leaf to an earlier entry (`branch(id)`), append a branch summary and re-seat (`branchWithSummary`), append out-of-context state (`appendCustomEntry`) and hidden in-context messages (`appendCustomMessageEntry`), and walk a branch's entries. The trunk model stores all conversational state through these; boundary collapse, checkpoint tangents, and `/rollback`'s rewind (a `branch(id)` re-seat — no deletion primitive exists) build on them, requiring no SDK capability beyond them |

## Behaviors

### Session Construction (R0, R1, R2, R3)

One `open()` path serves the main conversational session, boundary resumption, and headless side runs.

**Acceptance Criteria**:
- Given registered factories, when `open()` is called with no options, then the session binds every factory and uses the core base prompt (`buildMainSystemPrompt({ workspaceRoot })`) as `systemPromptOverride`
- Given `open({ sessionFile })`, when the session is created, then pi resumes from that JSONL transcript (`SessionManager.open`)
- Given `open({ inMemory: true })`, when the session is created, then nothing is persisted to disk (`SessionManager.inMemory`)
- Given `open({ forkFromFile })`, when the session is created, then `SessionManager.forkFrom` copies that file's full history into a fresh session (source untouched) and the new session continues from it; opened non-bare, the fork keeps the composed persona and the agent's tools
- Given `forkAndContinue(file, prompt, tier, tools)`, when invoked, then a non-bare fork of `file` is opened with the `tools` allowlist applied, `prompt` is sent as one turn, and the session is disposed in `finally`; the persona and history survive while the toolset is restricted to `tools`
- Given `open({ bare: true })`, when the session is created, then no factories are bound and no core base prompt is applied; an explicit `systemPrompt` option takes precedence over the core base prompt
- Given `open({ isolatePrompt: true })`, when the session is created, then pi's `APPEND_SYSTEM.md` append, project context files, and skills catalog are suppressed (`appendSystemPromptOverride: () => []`, `noContextFiles`, `noSkills`) alongside the explicit `systemPrompt`; without the flag none of these suppressions apply
- Given `open({ tools, tier })`, when the session is created, then pi's tool set is restricted to the named built-ins and the model resolves from that tier's chain; with the whole chain unset, no model is passed so pi's own resolution (session restore → settings → first available) applies
- Given `open({ bare: true, skillPaths, forceLoadSkills })`, when the session is built, then the loader gains `additionalSkillPaths` and the force-load injection factory binds alongside the bare-path selection; without either option neither appears (no `additionalSkillPaths`, no extra factory)

### Authentication and Models (R4, R5, R6)

**Acceptance Criteria**:
- Given `{piDir}/auth.json` exists with more than empty-object content, when the manager initializes, then it is the auth source; otherwise the machine-level pi auth store is used
- Given a tier configured as `"anthropic/claude-haiku-4-5"`, when resolved, then the registry entry for that provider/id is returned
- Given a reference without a `/` separator, when parsed, then an error names the invalid reference
- Given a reference naming a model absent from pi's registry, when resolved, then an error names the model and tier
- Given pi reports a model fallback on session open, when the session is returned, then a warning is logged with the fallback message

### Event Adaptation (R7)

The adapter bridges pi's push-based `session.subscribe()` into a pull-based domain stream (covered by `tests/adapter.test.ts`).

**Acceptance Criteria**:
- Given `message_update` events with `text_delta`/`thinking_delta`, when streamed, then they map to `text`/`thinking` events with the delta text
- Given `tool_execution_start`/`tool_execution_end`, when streamed, then they map to `tool-start` (with call id, name, args) and `tool-end` (with `isError`)
- Given `compaction_start` or `auto_retry_start`, when streamed, then a `status` event describes the activity; all other pi event types are dropped
- Given the prompt resolves successfully, when the stream drains, then the final event is a `result` with `stopReason: "done"` and the session id; if the run emitted a final `agent_end` (not a retry boundary), the `result.usage` and `result.costUsd` totals are summed across the run's assistant turns
- Given the prompt rejects, when the stream drains, then the final event is `{ kind: "error", message, recoverable, errorKind }` and no `result` event is emitted; auth and billing failures are non-recoverable, while encoding, transient provider, and unrecognized failures are recoverable
- Given any streamed text, thinking, or error content, when it is emitted, then lone UTF-8 surrogate code points are stripped (valid surrogate pairs are preserved) so downstream UTF-8 re-encoding never throws
- Given iteration ends, when the generator finalizes, then the session subscription is removed

### Side-Channel LLM Work (R8, R9, R10)

**Acceptance Criteria**:
- Given a `complete()` call, when the model responds, then the concatenated text blocks are returned; an `error`/`aborted` stop reason throws with the provider message
- Given a `classify()` call, when the model answers with JSON (raw, fenced, or embedded in prose), then the extracted object is validated against the TypeBox schema and returned typed
- Given a `classify()` response that fails extraction or validation, when the first attempt errors, then exactly one retry is made with an output-format reminder appended
- Given a `run()` call with `tools: ["read", "grep"]`, when the run completes, then the final assistant message's text is returned and the ephemeral session is disposed even on failure
