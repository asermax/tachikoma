# Conversation Boundary Detection

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Detects whether an incoming message continues the current conversation or starts a new topic. When a topic shift is detected, the system closes the current session (triggering post-processing asynchronously), clears the SDK session ID (so the next message starts a fresh SDK session without prior context), and starts a new session — all before the coordinator processes the message. A per-message post-processing pipeline maintains a rolling conversation summary after each agent response, keeping it ready for the next boundary check.

## User Stories

- As a user, I want the assistant to detect when I've changed topics so that each conversation is processed separately and prior context doesn't bleed into unrelated conversations

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Detect whether an incoming message continues the current conversation or starts a new one |
| R1 | On topic shift, close the current session and open a new one before the coordinator processes the message |
| R2 | On session close via boundary detection, trigger session post-processing asynchronously (not blocking the new session) |
| R3 | Per-message post-processing pipeline (`MessagePostProcessingPipeline`) runs asynchronously after each agent response, using a dedicated processor interface distinct from the session-level pipeline |
| R3.1 | Conversation summary processor: generates or updates a rolling summary after each agent response |
| R4 | Store a rolling conversation summary on the session record, updated by the per-message pipeline |
| R5 | Boundary detector uses the current session summary for comparison against the incoming message |
| R6 | Boundary detection adds no more than 1-2 seconds to message processing latency (excludes await-pending time from R11) |
| R7 | Handle edge cases: first message with no prior session, detector errors, ambiguous shifts, messages arriving during transition |
| R8 | Boundary detection failures must not block message processing (fail-open to continuation) |
| R9 | After topic shift, new conversation starts without prior SDK session context |
| R10 | Inject previous conversation summary into new session's system prompt for brief context |
| R11 | When a new message arrives, await any pending per-message post-processing before proceeding to boundary detection |
| R12 | Boundary detection operates independently of the coordinator's SDK session |
| R13 | Rolling conversation summary remains concise and bounded in size regardless of conversation length |
| R14 | On graceful shutdown, await running background post-processing tasks before exiting |

## Behaviors

### Boundary Detection (R0, R5, R6, R12)

The boundary detector classifies each incoming message as either a continuation of the current conversation or a new topic, using the rolling conversation summary for comparison.

**Acceptance Criteria**:
- Given an active session with a conversation summary, when a new message arrives that continues the same topic, then the boundary detector classifies it as a continuation and the message proceeds to the coordinator normally
- Given an active session with a conversation summary, when a new message arrives on a clearly different topic, then the boundary detector classifies it as a topic shift
- Given an active session with a conversation summary, when a new message arrives that could be interpreted as either a continuation or a new topic, then the boundary detector biases toward continuation — only clear, unambiguous topic shifts are classified as such
- Given a boundary detection call, when the detector runs, then it operates independently of the active SDK conversation session
- Given a boundary detection call, when the detector completes, then the detection itself adds no more than 1-2 seconds to message processing latency (excluding any await-pending time)

### Session Transition on Topic Shift (R1, R9, R10)

When a topic shift is detected, the system orchestrates a full session transition: closing the current session, resetting the SDK conversation context, and starting a fresh session.

**Acceptance Criteria**:
- Given the boundary detector identifies a topic shift, when the transition begins, then the current session is closed in the session registry before the new session is created
- Given a topic shift is detected, when the new conversation starts, then the SDK conversation context is reset so no prior context bleeds into the new session
- Given a topic shift is detected, when the new session starts, then the previous conversation's summary is injected into the system prompt for the new SDK session
- Given the session transition completes, when the coordinator processes the incoming message, then it does so within the new session context

### Async Session Post-Processing (R2, R14)

Session post-processing fires asynchronously when a session closes via boundary detection, running as a background task that doesn't block the new session.

**Acceptance Criteria**:
- Given the current session is closed due to a topic shift, when the session has a valid SDK session ID, then the session-level post-processing pipeline is triggered asynchronously
- Given the current session is closed due to a topic shift, when the session has no SDK session ID (e.g., no agent response yet), then session post-processing is skipped
- Given async session post-processing is running from a previous topic shift, when the system operates normally, then the background task completes without affecting active conversations
- Given async session post-processing fails, when the error occurs, then it is logged but does not affect the active conversation
- Given multiple topic shifts occur in sequence, when background post-processing tasks accumulate, then all tasks are tracked and complete independently
- Given the system is shutting down gracefully, when background post-processing tasks are still running, then shutdown awaits their completion before exiting

### Per-Message Post-Processing (R3, R3.1, R4, R13)

After each agent response, a per-message pipeline runs asynchronously to update the rolling conversation summary.

**Acceptance Criteria**:
- Given the agent completes a response, when the response stream ends, then the per-message post-processing pipeline is triggered asynchronously
- Given the per-message pipeline runs, when the summary processor executes, then it generates or updates a rolling conversation summary and stores it on the session record
- Given the per-message pipeline uses the `MessagePostProcessor` interface, when processors are registered, then they follow the same error isolation patterns as session-level processors
- Given a per-message processor fails, when the error occurs, then it is logged and the conversation continues uninterrupted
- Given a per-message processor failed on a previous exchange, when the next agent response completes, then the per-message pipeline runs again (each invocation is independent — no permanent failure state)
- Given a long conversation (many exchanges), when the summary processor runs, then the resulting summary remains concise and bounded in size

### Await Pending Post-Processing (R11)

Before boundary detection, the coordinator ensures any pending per-message post-processing has completed, guaranteeing the summary is up-to-date.

**Acceptance Criteria**:
- Given per-message post-processing from a previous response is still running, when a new message arrives, then the system awaits its completion before proceeding to boundary detection
- Given no per-message post-processing is pending, when a new message arrives, then boundary detection proceeds immediately with no delay
- Given the pending per-message post-processing fails while being awaited, when the error occurs, then it is logged and message processing continues (boundary detection runs with whatever summary is available)

### Edge Cases and Graceful Degradation (R7, R8)

Boundary detection is a best-effort enhancement that never blocks normal message processing.

**Acceptance Criteria**:
- Given no active session exists (first message ever, or after startup), when a message arrives, then boundary detection is skipped and a new session is created normally
- Given an active session with no summary yet (first message in session, per-message pipeline hasn't run), when the next message arrives, then boundary detection is skipped and the message proceeds normally
- Given the coordinator has no workspace directory configured, when a message arrives, then boundary detection is skipped
- Given the boundary detector encounters an error (SDK failure, timeout, malformed response), when the error occurs, then it is logged and the message proceeds as a continuation (fail-open)
- Given a topic shift triggers a session transition, when the SDK session ID is cleared, then the next message creates a fresh SDK session with no prior conversation context
