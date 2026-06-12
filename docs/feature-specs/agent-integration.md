# Agent Integration

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Agent integration is the layer that embeds the pi agent SDK: `AgentManager` (`src/agent/manager.ts`) constructs `AgentSession`s with shared auth, model registry, and workspace wiring; `ModelTiers` (`src/agent/models.ts`) maps configured model references to pi registry entries by role; the adapter (`src/agent/adapter.ts`) converts pi session events into SDK-free domain `AgentEvent`s; and `SideRunner` (`src/agent/side-run.ts`) provides side-channel LLM work — one-shot completions, structured classification, and headless agent runs.

Extensions reach this layer only through `app.agent` (`use`, `systemPrompt`, `provideContext`, `models`, `side` — see [DES-001](../design/DES-001-unified-extension-api.md)); the [conversation loop](conversation-loop.md) consumes it for the main session.

## User Stories

- As the coordinator, I want one factory for pi sessions so that main, resumed, and headless sessions share auth, models, and workspace configuration
- As an extension developer, I want cheap side-channel LLM calls so that extraction and classification never go through the main conversation
- As a user, I want Tachikoma to reuse my existing machine-level pi login so that I do not authenticate twice
- As a channel, I want SDK-free agent events so that rendering does not depend on pi internals

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | `AgentManager.open()` builds pi sessions against the workspace: `cwd` is the workspace root, `agentDir` is `{workspace}/.tachikoma/pi`, with shared `AuthStorage`, `ModelRegistry`, and `SettingsManager` |
| R1 | Main sessions bind all registered pi extension factories and override the system prompt with the registered builders' output, joined in registration order |
| R2 | Session persistence modes: new persisted session under the workspace sessions dir, resume from an existing pi session file, or ephemeral in-memory |
| R3 | `bare` sessions skip registered factories and system prompt builders (headless side work); explicit `systemPrompt`, `tools`, and `tier` options override defaults |
| R4 | Auth resolution: a workspace-local `{piDir}/auth.json` is used when it has actual content; otherwise the machine-level pi login (`~/.pi/agent/auth.json` plus env vars) is shared; `apiKeyFor(provider)` exposes key lookup |
| R5 | Four model tiers — `agent`, `searcher`, `processor`, `classifier` — configured as `provider/model-id` strings; malformed references and models missing from pi's registry fail with errors naming the reference and tier |
| R6 | Session model fallback is logged as a warning; the thinking level comes from `agent.thinkingLevel` |
| R7 | `streamPrompt(session, text)` exposes one prompt run as an `AsyncIterable<AgentEvent>` ending in a terminal `result` or `error` event after pi's `prompt()` settles |
| R8 | `SideRunner.complete()` performs a one-shot completion on a tier model (default `processor`), throwing on `error`/`aborted` stop reasons |
| R9 | `SideRunner.classify()` returns schema-validated structured output (default tier `classifier`): JSON-instructed prompt, tolerant JSON extraction, TypeBox validation, one retry on failure |
| R10 | `SideRunner.run()` executes a headless agent run in an ephemeral bare session with an explicit pi tool allowlist (default none), returning the final assistant text and disposing the session |

## Behaviors

### Session Construction (R0, R1, R2, R3)

One `open()` path serves the main conversational session, boundary resumption, and headless side runs.

**Acceptance Criteria**:
- Given registered factories and system prompt builders, when `open()` is called with no options, then the session binds every factory and uses the builders' sections joined with blank lines as `systemPromptOverride`
- Given `open({ sessionFile })`, when the session is created, then pi resumes from that JSONL transcript (`SessionManager.open`)
- Given `open({ inMemory: true })`, when the session is created, then nothing is persisted to disk (`SessionManager.inMemory`)
- Given `open({ bare: true })`, when the session is created, then no factories or prompt builders are bound; an explicit `systemPrompt` option takes precedence over composed builders
- Given `open({ tools, tier })`, when the session is created, then pi's tool set is restricted to the named built-ins and the model resolves from that tier

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
- Given the prompt resolves successfully, when the stream drains, then the final event is `{ kind: "result", stopReason: "done" }`
- Given the prompt rejects, when the stream drains, then the final event is `{ kind: "error", message }` and no `result` event is emitted
- Given iteration ends, when the generator finalizes, then the session subscription is removed

### Side-Channel LLM Work (R8, R9, R10)

**Acceptance Criteria**:
- Given a `complete()` call, when the model responds, then the concatenated text blocks are returned; an `error`/`aborted` stop reason throws with the provider message
- Given a `classify()` call, when the model answers with JSON (raw, fenced, or embedded in prose), then the extracted object is validated against the TypeBox schema and returned typed
- Given a `classify()` response that fails extraction or validation, when the first attempt errors, then exactly one retry is made with an output-format reminder appended
- Given a `run()` call with `tools: ["read", "grep"]`, when the run completes, then the final assistant message's text is returned and the ephemeral session is disposed even on failure
