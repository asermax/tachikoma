# Pre-Processing Pipeline

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A reusable, pluggable pipeline that runs registered context providers in parallel when invoked. Each provider returns a named, XML-tagged context block. The coordinator persists successful results as session context entries and assembles them into the system prompt from the database. A standalone `assemble_context()` function assembles results into XML-tagged blocks prepended to a message, used by the background task executor which does not use database persistence. The pipeline is domain-agnostic — it knows nothing about what providers do. It is stateless and has no serialization lock (unlike the post-processing pipeline, which serializes concurrent invocations).

In addition to the session-gated pipeline (which runs on the first message of a new session), a per-message pipeline (`MessagePreProcessingPipeline`) runs on every message. Per-message providers receive the session's existing context entries and return results that are appended as new entries. This enables providers like the skills context provider to re-evaluate relevance as the conversation topic evolves, classifying only skills not already in context. Both pipelines use the same parallel execution with error isolation pattern.

## User Stories

- As a developer, I want a reusable pre-processing pipeline so that any context provider can register without coupling to other providers or the coordinator
- As a user, I want my assistant to automatically enrich messages with relevant context so that responses are informed without me repeating information

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Reusable pipeline that runs registered context providers in parallel and collects their results |
| R1 | Context provider interface (ABC) that is domain-agnostic — the pipeline knows nothing about what providers do |
| R2 | Error isolation — individual provider failures are logged but don't prevent the message from being processed or other providers from completing |
| R3 | Each provider returns a named, XML-tagged context block; the pipeline collects results for the caller to handle (the coordinator persists them as session context entries for system prompt assembly; the task executor prepends them to the message text via `assemble_context()`) |
| R4 | Context injection uses XML tags consistent with the existing `<soul>`, `<user>`, `<agents>` convention, generalized for easy addition of new context sources |
| R5 | Pipeline extensible — adding a new context provider requires only implementing the ABC and registering it; no changes to pipeline or coordinator code |
| R6 | Context providers can return tool capabilities (MCP servers) alongside text context, enabling the pipeline to pass capability requirements to the coordinator for session configuration |
| R7 | Context results can optionally carry agent definitions alongside text context, enabling providers to return agents that the coordinator loads for the session. Backward compatible for providers that don't set it |
| R8 | Per-message pipeline runs registered MessageContextProvider instances on every message (not just first message of session), receiving the `MessageEnvelope` (typically a `TextMessage`) and the session's existing context entries. The coordinator gates both the session-gated and per-message pipelines on `envelope.runs_pre_processing`, skipping providers entirely for envelopes that opt out (e.g., `ButtonTapMessage` taps, `ReactionMessage` reactions — both carry low-semantic-content payloads that providers cannot meaningfully classify against) |
| R9 | Context results can optionally carry structured metadata (JSON dict) for provider-specific data that the coordinator persists alongside the entry without interpretation |
| R10 | Per-message pipeline passes SDK session ID to providers, enabling session forking for providers that need conversation context |
| R11 | Both pipelines accept an optional async status callback on `run()`; for each provider, the callback is invoked twice — once with the provider's start message before `provide()` runs, and once with the completion message after `provide()` returns — while preserving parallel execution of providers |
| R12 | Both `ContextProvider` and `MessageContextProvider` ABCs expose abstract `status_message(result=None) -> str` — called with no args at start (returns start message) and with the `provide()` result at completion (returns completion message). Every concrete provider must implement it. When `provide()` returns `None`, the completion call passes `None` as `result`; the provider cannot distinguish this from the start call (both have `result=None`), so the completion message will match the start message |
| R13 | Provider status messages include both start (before `provide()`) and completion (after `provide()`) emissions. The completion message reflects the result (e.g., "Found N relevant memories" vs "No relevant memories found"). If `provide()` raises, no completion message is emitted — the exception propagates past the completion code |
| R14 | Both pipelines support dynamic provider registration and unregistration via `register()` and `unregister()` methods, enabling plugins to contribute providers at runtime alongside built-in providers |

## Behaviors

### Provider Registration (R0, R1, R5)

Providers register with the pipeline. The pipeline accepts any class implementing the `ContextProvider` ABC.

**Acceptance Criteria**:
- Given a class implements `ContextProvider`, when it defines `provide(message)`, then it can register with the pipeline
- Given the `ContextProvider` ABC, then it has no dependency on the Claude Agent SDK
- Given a new provider is implemented and registered, when the pipeline runs, then it is included in the parallel execution without changes to the pipeline or coordinator

### Parallel Execution (R0, R2)

The pipeline runs all registered providers in parallel. Failures in one provider do not affect others.

**Acceptance Criteria**:
- Given multiple providers are registered, when the pipeline runs, then all providers execute concurrently (not sequentially)
- Given multiple providers are registered, when they run in parallel, then each provider operates independently with no access to other providers' results
- Given a provider raises an exception, when other providers are running, then they complete normally
- Given a provider raises an exception, when the pipeline collects results, then the failure is logged and the message proceeds with results from successful providers only
- Given all providers fail, when the pipeline completes, then it returns an empty result list
- Given no providers are registered, when the pipeline runs, then it returns an empty result immediately

### Context Assembly (R3, R4)

Successful results are persisted as session context entries by the coordinator and assembled into the system prompt from the database. The `assemble_context()` function assembles results into XML-tagged blocks prepended to a message, used by the background task executor which does not use database persistence.

**Acceptance Criteria**:
- Given providers return context results, when the coordinator processes them, then each result is persisted as a context entry with the result's tag as owner and content as the text content
- Given the XML tag convention, when context appears in the system prompt, then it is consistent with the existing foundational context tags (`<soul>`, `<user>`, `<agents>`)
- Given a context result tag name, when it is validated, then it must conform to valid XML tag name format (starts with letter/underscore, contains only alphanumeric, hyphens, underscores)
- Given no providers return results (all returned None or all failed), when the coordinator processes results, then no entries are persisted and the system prompt contains only foundational entries
- Given the background task executor runs pre-processing, then it uses `assemble_context()` to prepend results to the task prompt (not database persistence)

### Capability Injection (R6)

Context providers can return structured capabilities (e.g., MCP servers) alongside text context, enabling the coordinator to configure session-specific tools.

**Acceptance Criteria**:
- Given a context provider returns a `ContextResult` with `mcp_servers`, when the coordinator processes pipeline results, then it extracts and merges `mcp_servers` from all results and passes them to `ClaudeAgentOptions` for the session
- Given multiple providers return `mcp_servers`, when the coordinator merges them, then all server configurations are combined into a single dictionary
- Given a session transition occurs, when the coordinator handles the transition, then MCP servers from the previous session are cleared and re-extracted from pipeline results in the new session

### Agent Definitions Support (R7)

Context results can optionally carry agent definitions that the coordinator extracts and loads for the session.

**Acceptance Criteria**:
- Given a provider sets the agents field on its `ContextResult`, when the pipeline collects results, then the agents are available on the result object for the coordinator to extract
- Given a provider does not set agents (defaults to None), when the pipeline runs, then it continues to work unchanged — backward compatible
- Given multiple providers return results, when the coordinator processes them, then it extracts and merges agent definitions from all results

### Per-Message Pipeline (R8, R10)

A second pipeline variant runs on every message (not just the first message of a new session). Providers receive the `MessageEnvelope` (carrying typed metadata like pinned skills and the rendered SDK input) and the session's existing context entries, enabling them to determine what's already loaded and avoid redundant work. The pipeline also passes the SDK session ID, enabling providers to fork the session for conversation context. The coordinator passes the envelope directly to the pipeline; the background task executor constructs a `TextMessage` envelope from the task's prompt and pinned skills. The coordinator gates the pipeline on `envelope.runs_pre_processing`, skipping it for envelopes that opt out (e.g., a `ButtonTapMessage` carries no semantic intent the providers can act on).

**Acceptance Criteria**:
- Given per-message providers are registered, when the pipeline runs, then all providers execute concurrently with error isolation (same pattern as session-gated pipeline)
- Given a per-message provider receives existing context entries, when it determines no new context is needed, then it returns None and no new entries are created
- Given the per-message pipeline runs after the session-gated pipeline on the first message, when both have results, then results from both are persisted
- Given the per-message pipeline runs on a subsequent message, when new context is detected, then results are appended as additional context entries alongside existing ones
- Given the per-message pipeline runs with an SDK session ID, when it calls providers, then the sdk_session_id is passed through to each provider

### Structured Metadata (R9)

Context results can carry structured metadata that the coordinator persists alongside the entry without interpretation.

**Acceptance Criteria**:
- Given a provider sets metadata on its ContextResult, when the coordinator persists the result, then the metadata is stored with the context entry
- Given a provider does not set metadata (defaults to None), when the result is persisted, then the entry has no metadata — backward compatible
- Given the skills provider returns one result per detected skill, when each result has metadata identifying the skill name, then the coordinator persists it without interpreting the content

### Status Emission (R11, R12, R13)

Both pipelines emit granular, provider-driven status messages so the coordinator can forward them to the active channel as `Status` AgentEvents while pre-processing runs. Each provider emits two messages: a start message before `provide()` runs, and a completion message after it returns.

**Acceptance Criteria**:
- Given N providers are registered and a pipeline is run with `on_status`, when the pipeline executes, then the callback is invoked twice per provider: once with `status_message()` (start) and once with `status_message(result)` (completion)
- Given a provider's `provide()` returns a non-None result, when the completion message is emitted, then `status_message(result)` returns a message reflecting the result (e.g., "Found 3 relevant memories" for a list of 3, or "No relevant memories found" for an empty list)
- Given a provider's `provide()` returns `None`, when the completion message is emitted, then `status_message(None)` is called; since this is indistinguishable from the start call (both have `result=None`), the provider returns the same message as the start
- Given a provider raises inside `provide()`, when the pipeline runs, then only the start message was emitted (completion is unreachable because the exception propagates past the completion code), the exception is logged per DES-002, and sibling providers complete normally
- Given a pipeline has zero registered providers, when `run()` is called with or without `on_status`, then no status callback invocations occur
- Given a pipeline is run without `on_status`, when it executes, then no callback invocations occur — the parameter is optional

### Dynamic Provider Management (R14)

Providers can be registered and unregistered dynamically, enabling plugins to contribute providers at runtime. Dynamically registered providers participate identically to built-in providers.

**Acceptance Criteria**:
- Given a provider is registered via `register()`, when the pipeline runs, then it participates in parallel execution alongside existing providers
- Given a provider is unregistered via `unregister()`, when the pipeline next runs, then it is no longer included in execution
- Given `unregister()` is called with a provider not in the pipeline, when the call completes, then no exception is raised (safe no-op)
- Given a dynamically registered provider returns a `ContextResult`, when the pipeline collects results, then its results are treated identically to built-in provider results
