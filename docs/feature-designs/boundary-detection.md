# Design: Boundary Detection

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/boundary-detection.md](../feature-specs/boundary-detection.md)
**Status**: Current

## Purpose

Explains how conversation segmentation is built on the extension API's inbound middleware and exchange-processor hooks, and why the rolling summary and classification are shaped the way they are.

## Problem Context

pi keeps one long-lived `AgentSession` per conversation and provides no cross-session orchestration (see `docs/reference/pi-sdk-notes.md`, "What pi does NOT provide"). Deciding when a conversation ends — and whether a message belongs to a closed one — is host territory. The detector needs a cheap, current representation of "what this session is about" available before every message, without replaying transcripts.

**Constraints:**
- Classification must add little latency and must never block a message (fail-open)
- The detector cannot trust LLM-produced session ids — they must be validated host-side
- Everything must live in the extension, not the coordinator (DES-001: core is only the main loops)

**Interactions:**
- The coordinator owns the middleware chain, session close/reopen, and post-processing (see [conversation-loop](./conversation-loop.md))
- Side-channel LLM calls go through `SideRunner` on the `classifier`/`processor` tiers (see [agent-integration](./agent-integration.md))
- Closing a session triggers the memory and context post-processors (see [memory](./memory.md), [foundational-context](./foundational-context.md))

## Design Overview

Two cooperating pieces. An exchange processor keeps a rolling summary and the verbatim last exchange on the session row after every prompt cycle. An inbound middleware runs before each message is handled: it gathers resume candidates, asks a classifier for a single `continue`/`new`/`resume` decision, and — only for the non-continue cases — drives the transition through the `InboundContext` (`closeSession()`, `resumeSession()`) before passing the message along with `next()`.

```
exchange ends ──> rolling-summary processor ──> sessions.update(summary, lastExchange)
                                                        │
message arrives ──> boundary middleware ── classify ────┘ (reads summary + candidates)
                        │ continue: next()
                        │ new:      closeSession() → next()  (fresh session created downstream)
                        └ resume:   resumeSession(target) → next()
```

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/boundary/index.ts` | Wiring: `enabled` toggle, registers the summary processor via `app.sessions.onExchange` (only when enabled) and the middleware via `app.inbound.use` (always); builds candidates from `app.sessions.listResumable()`; metadata fast-paths | The middleware is registered unconditionally; the `enabled` flag only gates the summary processor and topic classification, not the middleware itself. Three metadata fast-paths run before classification: `boundary === "skip"` (system-originated injections — bypass everything), `forceNew` (the `/new` command — close the active session and proceed, honored even when detection is off), and a numeric `resumeSessionId` (Telegram reply-to — force-route to that session, only when detection is enabled). Filters candidates to sessions with a summary, excluding the active one and any with failed post-processing (`listResumable` already drops null-`piSessionFile` rows); skips detection when there is nothing to compare; emits `app.status` lines |
| `src/extensions/boundary/detector.ts` | `detectBoundary`: prompt rendering, structured classification, validation, fail-open | `BoundaryDecisionSchema` uses `StringEnum` + optional numeric `resumeSessionId`; unknown ids downgrade to `continue`; all errors caught and logged |
| `src/extensions/boundary/summary.ts` | `createSummaryProcessor`: rolling summary + last exchange persistence | Clips exchange to 2000 chars and summary to 600; on completion failure still writes `lastExchange` |

## Key Decisions

### Detection as inbound middleware, not coordinator logic

**Choice**: Implement boundary detection as an `app.inbound.use` middleware inside the `boundary` extension.
**Why**: DES-001 makes inbound middleware the designated place where "boundary detection decides to continue, close + open, or resume a session". The `InboundContext` contract (`session`, `closeSession`, `resumeSession`) is exactly the leverage needed; the coordinator stays free of topic semantics.
**Alternatives Considered**:
- Coordinator-owned detection: couples topic policy into core; conflicts with the everything-is-an-extension bet
- pi's `input` extension event: session-scoped, so it cannot orchestrate across sessions or survive session replacement

**Consequences**:
- Pro: deleting `src/extensions/boundary/` removes the feature cleanly; disabling it is a config flag
- Pro: testable with a faked classifier (`Pick<SideRunner, "classify">`) and no coordinator
- Con: the middleware depends on the coordinator honoring the `InboundContext` contract for transitions it cannot observe directly

### Synchronous summary updates per exchange

**Choice**: Run the rolling-summary processor as a registered exchange processor that the coordinator awaits at the end of each `handle()` cycle.
**Why**: The summary is guaranteed current before the next message is dequeued, with no "await pending per-message post-processing" machinery — the single-consumer inbox already serializes exchanges.
**Alternatives Considered**:
- Fire-and-forget background task with an await-before-detection barrier: more moving parts for the same guarantee
- Summarize lazily at detection time: puts the summarization latency on the message-handling critical path

**Consequences**:
- Pro: no pending-task tracking, no race between summary writes and boundary reads
- Pro: failure isolation comes free from the coordinator's `Promise.allSettled` over exchange processors
- Con: the exchange (and the idle timer reset) completes only after summarization — latency lands after the user already received the response, but it does delay queued messages slightly

### One classification call covering shift and resume matching

**Choice**: A single `classify` call returns the decision and, for `resume`, the candidate id; candidates are inlined into the prompt as `id: summary` lines.
**Why**: Topic-shift detection and resume matching need the same inputs; two calls would double latency and cost on every message for no extra signal.
**Alternatives Considered**:
- Classify shift first, then match candidates only on `new`: an extra round-trip exactly on the latency-sensitive path

**Consequences**:
- Pro: constant one-call overhead per classified message
- Pro: `resume` validation is trivial — the id must be one of the candidates that were offered
- Con: large candidate lists inflate the prompt; bounded in practice by the one-day resume window

## System Behavior

### Scenario: Topic shift mid-conversation

**Given**: An active session about debugging with a stored summary
**When**: The user asks an unrelated question and the classifier returns `new`
**Then**: The middleware closes the active session (its post-processing pipeline runs to completion), `next()` proceeds, and the coordinator's `ensureSession` opens a fresh pi session for the message.

### Scenario: Resuming a recent conversation

**Given**: A closed session within the resume window whose summary matches the incoming message
**When**: The classifier returns `resume` with that session's id
**Then**: `detectBoundary` confirms the id is among the offered candidates, the middleware calls `resumeSession`, and the coordinator — after verifying the target's `piSessionFile` exists on disk (skipping the resume and keeping the active session if it is null or missing) — reopens the record and its pi session file, then injects a `bridging-context` block summarizing any sessions that closed while the resumed one was dormant (`listClosedBetween`).

### Scenario: Classifier outage

**Given**: The classifier-tier model is unreachable
**When**: A message arrives during an active session
**Then**: `detectBoundary` catches the error, logs it, and returns `continue` — the message is handled normally.

### Scenario: First message after startup

**Given**: No active session and no closed sessions with summaries inside the resume window
**When**: A message arrives
**Then**: The middleware skips classification entirely and the message proceeds straight to session creation.

## Notes

- The classification prompt biases toward `continue` ("default when in doubt, and always when the message is a short reaction, follow-up, or answer to the assistant") — the acknowledgment rule is encoded in the prompt rather than in code.
- `lastExchange` is written even when summarization fails so the detector always has at least a recency signal on the next message.
- Cold-start resumption (matching the first message after a restart against recent sessions) falls out of the same middleware: candidates are offered even when `active == null`.
