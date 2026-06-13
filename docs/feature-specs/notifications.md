# Notifications

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The notifications extension routes user-facing notifications from any producer to the active channel. Producers emit a payload on the `notify` app event; the extension routes it by severity — urgent notices are delivered immediately, everything else accumulates over a short flush window and goes out idle-gated, batched into a single digest when several pile up. A short-lived dedup guard drops a repeated `(source + text)` notice so re-emit/retry loops do not flood the user. Delivery gating itself (idle hold, max-hold force flush) is owned by the conversation loop (see [conversation-loop.md](conversation-loop.md)).

A `notify_user` tool lets the agent itself emit notifications through the same path.

## User Stories

- As a user, I want background-originated updates to reach me at natural pauses so that routine notices don't interrupt what I'm doing
- As a user, I want urgent notices to reach me immediately while routine ones are batched
- As a developer, I want any extension to emit a notification with one event call so that producers don't deal with channels or gating

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Subscribe to the `notify` app event; any extension (or core) can emit a notification payload: required `text`, optional `title`, `severity`, `source` |
| R1 | Payload parsing is best-effort: payloads without a non-empty `text` string are quietly skipped (the event also carries non-notification signals, e.g. task status objects); missing or unknown severity downgrades to `info`; missing source becomes `unknown` |
| R2 | Three severities: `info`, `warning`, `urgent` |
| R3 | Urgent notifications bypass accumulation and are delivered with `gate: "immediate"` |
| R4 | Non-urgent notifications accumulate over a flush window (`flushWindowSeconds`, default 30); the flush delivers one message with `gate: "idle"` and `maxHoldSeconds` (default 900) |
| R5 | A flush with a single item is formatted as one notification with a source/time header; multiple items become one digest enumerating each item with its severity and source |
| R6 | The `notify_user` tool lets the agent emit a notification (text, optional title and severity, default `info`); the payload is emitted on the `notify` event with source `agent`, and empty text is rejected |
| R7 | A TTL dedup guard suppresses a notification whose `(source + text)` key was already seen within `dedupTtlSeconds` (default 60); suppression applies before severity routing (urgent included), is logged at info, and expired keys are pruned opportunistically |
| R8 | Every delivery carries a `priority` derived from severity (`urgent` highest, then `warning`, then `info`) so the conversation loop orders notices ahead of lower-severity ones — and ahead of unprioritized deliveries (default 0) — when several flush together; a digest takes the highest priority among its items |

## Behaviors

### Severity Routing (R2, R3)

Urgent notices skip the accumulation window entirely.

**Acceptance Criteria**:
- Given a payload with `severity: "urgent"`, when the router handles it, then it is delivered immediately with `gate: "immediate"` and the source/time header, regardless of notices already pending in the window
- Given pending non-urgent notices when an urgent one arrives, then the pending ones stay in the window and flush later on their own timer

### Accumulation and Digest (R4, R5)

Non-urgent notices are held briefly so bursts collapse into one message.

**Acceptance Criteria**:
- Given one `info` notification arrives, when the flush window elapses, then exactly one idle-gated delivery goes out containing that notification with its source/time header
- Given several non-urgent notifications arrive within the window, when it elapses, then one idle-gated digest delivery enumerates each item with its severity and source
- Given nothing is pending, when `flush()` runs, then nothing is delivered

### Duplicate Suppression (R7)

A producer (or the agent) re-emitting the same notice within the TTL window must not deliver it twice.

**Acceptance Criteria**:
- Given a notification, when an identical `(source + text)` notice arrives within `dedupTtlSeconds`, then the repeat is suppressed (not delivered) and logged at info
- Given an identical notice arrives after the window has elapsed, then it is delivered normally
- Given a notice with the same source but different text (or vice versa), then it is delivered normally
- Given suppression applies to all severities — an `urgent` repeat within the window is also dropped

### Priority Ordering (R8)

Severity determines the delivery `priority` so the conversation loop's flush surfaces important notices first.

**Acceptance Criteria**:
- Given an `urgent` notice and an `info` notice held in the same flush, when the conversation loop flushes, then the urgent one is delivered ahead of the info one
- Given a digest of mixed-severity items, when it is delivered, then its `priority` is the highest severity among its items
- Given two notices of the same severity, when they flush together, then they keep their arrival order (the loop's sort is stable)

### Payload Tolerance (R0, R1)

The `notify` event is a shared, loosely-typed channel; the router only acts on payloads that look like notifications.

**Acceptance Criteria**:
- Given a payload without a usable `text` field (status objects, non-objects), when the router handles it, then it is skipped with a debug log and nothing is delivered
- Given a payload with `text` but an unrecognized severity, then it is treated as `info`; a missing source is reported as `unknown`

### Agent Tool (R6)

The `notify_user` tool is registered into conversational agent sessions via `app.agent.use` (DES-001).

**Acceptance Criteria**:
- Given the agent calls `notify_user` with text and no severity, then an `info` payload with source `agent` is emitted on the `notify` event and follows the normal accumulation path
- Given the agent calls `notify_user` with empty or whitespace-only text, then the tool errors and nothing is emitted
