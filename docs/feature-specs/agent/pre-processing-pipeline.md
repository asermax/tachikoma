# Pre-Processing Pipeline

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A reusable, pluggable pipeline that runs registered context providers in parallel when invoked. Each provider returns a named, XML-tagged context block. The pipeline assembles all successful results and prepends them to the message. The pipeline is domain-agnostic — it knows nothing about what providers do. It is stateless and has no serialization lock (unlike the post-processing pipeline, which serializes concurrent invocations).

## User Stories

- As a developer, I want a reusable pre-processing pipeline so that any context provider can register without coupling to other providers or the coordinator
- As a user, I want my assistant to automatically enrich messages with relevant context so that responses are informed without me repeating information

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Reusable pipeline that runs registered context providers in parallel and collects their results |
| R1 | Context provider interface (ABC) that is domain-agnostic — the pipeline knows nothing about what providers do |
| R2 | Error isolation — individual provider failures are logged but don't prevent the message from being processed or other providers from completing |
| R3 | Each provider returns a named, XML-tagged context block; the pipeline assembles all results and prepends them to the message text |
| R4 | Context injection uses XML tags consistent with the existing `<soul>`, `<user>`, `<agents>` convention, generalized for easy addition of new context sources |
| R5 | Pipeline extensible — adding a new context provider requires only implementing the ABC and registering it; no changes to pipeline or coordinator code |
| R6 | Context results can optionally carry agent definitions alongside text context, enabling providers to return agents that the coordinator loads for the session. Backward compatible for providers that don't set it |

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

Successful results are assembled into XML-tagged blocks and prepended to the original message.

**Acceptance Criteria**:
- Given providers return context results, when the pipeline assembles them, then each result is wrapped in XML tags using the provider's declared tag name (e.g., `<memories>...</memories>`)
- Given the XML tag convention, when context is injected, then it is consistent with the existing foundational context tags (`<soul>`, `<user>`, `<agents>`)
- Given a context result tag name, when it is validated, then it must conform to valid XML tag name format (starts with letter/underscore, contains only alphanumeric, hyphens, underscores)
- Given no providers return results (all returned None or all failed), when the pipeline assembles context, then the original message is returned unmodified

### Agent Definitions Support (R6)

Context results can optionally carry agent definitions that the coordinator extracts and loads for the session.

**Acceptance Criteria**:
- Given a provider sets the agents field on its `ContextResult`, when the pipeline collects results, then the agents are available on the result object for the coordinator to extract
- Given a provider does not set agents (defaults to None), when the pipeline runs, then it continues to work unchanged — backward compatible
- Given multiple providers return results, when the coordinator processes them, then it extracts and merges agent definitions from all results
