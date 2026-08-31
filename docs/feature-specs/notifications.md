# Notifications

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The notifications extension routes notifications from any producer into the conversation as agent turns. Producers emit a payload on the `notify` app event; the extension routes it by severity → delivery tier — urgent notices are queued immediately at the Urgent tier, everything else accumulates over a short flush window and goes out at the tier of its highest-severity item, batched into a single digest when several pile up. A short-lived dedup guard drops a repeated `(source + text)` notice so re-emit/retry loops do not flood the user. The priority queue itself — tier ordering, per-tier idle/max-hold timing, and agent-turn delivery — is owned by the conversation loop (see [conversation-loop.md](conversation-loop.md)); this extension only decides *what* gets delivered and *at which tier*. The extension also contributes a usage context section (scope: main only) telling the agent that background updates arrive as batched digests while it works, with the emission-side guidance (`notify_user`) living in the background prompt; digest detail is in its reference file (`references/notifications.md`, per [DES-014](../design/DES-014-two-tier-agent-facing-documentation.md)).

A `notify_user` tool lets a background-task agent emit notifications through the same path (it is bound only into background runs, not the main conversation). Notifications are one-way; when a flow needs to await a user response, that is handled by the tasks await/respond mechanism (`ask_user` / `respond_to_task`, see [tasks.md](tasks.md)), not by this extension.

## User Stories

- As a user, I want background-originated updates to reach me at natural pauses so that routine notices don't interrupt what I'm doing
- As a user, I want urgent notices to reach me immediately while routine ones are batched
- As a developer, I want any extension to emit a notification with one event call so that producers don't deal with channels or gating

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Subscribe to the `notify` app event; any extension (or core) can emit a notification payload: required `text`, optional `title`, `severity`, `source` |
| R1 | Payload parsing is best-effort: payloads without a non-empty `text` string are quietly skipped (the event may carry non-notification signals from other producers); missing or unknown severity downgrades to `info`; missing source becomes `unknown` |
| R2 | Three severities: `info`, `warning`, `urgent` |
| R3 | Urgent notifications bypass the flush window and are queued immediately at the Urgent tier |
| R4 | Non-urgent notifications accumulate over a flush window (`flushWindowSeconds`, default 30); the flush delivers one message at the tier of its highest-severity item (warning → Normal, info → Low) |
| R5 | A flush with a single item is formatted as one notification with a source/time header; multiple items become one digest enumerating each item with its severity and source |
| R6 | The `notify_user` tool lets a background-task agent emit a notification (text, optional title and severity, default `info`); the payload is emitted on the `notify` event with source `agent`, and empty text is rejected. It is registered with `sessionScopes: ["background"]`, so it is bound **only** into autonomous background task runs — not the main conversation, where the agent replies to the user directly and out-of-band notification is meaningless |
| R7 | A TTL dedup guard suppresses a notification whose `(source + text)` key was already seen within `dedupTtlSeconds` (default 60); suppression applies before severity routing (urgent included), is logged at info, and expired keys are pruned opportunistically |
| R8 | Every delivery carries a `tier` derived from severity (`urgent` → Urgent, `warning` → Normal, `info` → Low) so the conversation loop orders notices ahead of lower-tier ones when several drain together; a digest takes the highest tier among its items |
| R9 | At shutdown the extension drains any notices still pending in the flush window via an `onShutdown` hook (`router.flushNow()`), so they are pushed into the conversation loop's final awaited delivery drain instead of dying with the process |

## Behaviors

### Severity Routing (R2, R3)

Urgent notices skip the accumulation window entirely.

**Acceptance Criteria**:
- Given a payload with `severity: "urgent"`, when the router handles it, then it is delivered immediately at the Urgent tier with the source/time header, regardless of notices already pending in the window
- Given pending non-urgent notices when an urgent one arrives, then the pending ones stay in the window and flush later on their own timer

### Accumulation and Digest (R4, R5)

Non-urgent notices are held briefly so bursts collapse into one message.

**Acceptance Criteria**:
- Given one `info` notification arrives, when the flush window elapses, then exactly one Low-tier delivery goes out containing that notification with its source/time header
- Given several non-urgent notifications arrive within the window, when it elapses, then one digest delivery (at the highest tier among its items) enumerates each item with its severity and source
- Given nothing is pending, when `flush()` runs, then nothing is delivered

### Duplicate Suppression (R7)

A producer (or the agent) re-emitting the same notice within the TTL window must not deliver it twice.

**Acceptance Criteria**:
- Given a notification, when an identical `(source + text)` notice arrives within `dedupTtlSeconds`, then the repeat is suppressed (not delivered) and logged at info
- Given an identical notice arrives after the window has elapsed, then it is delivered normally
- Given a notice with the same source but different text (or vice versa), then it is delivered normally
- Given suppression applies to all severities — an `urgent` repeat within the window is also dropped

### Tier Ordering (R8)

Severity determines the delivery `tier` so the conversation loop's queue surfaces important notices first.

**Acceptance Criteria**:
- Given an `urgent` notice and an `info` notice queued together, when the conversation loop drains, then the urgent (Urgent tier) one leads the info (Low tier) one
- Given a digest of mixed-severity items, when it is delivered, then its `tier` is the highest among its items
- Given two notices of the same tier, when they drain together, then they keep their arrival order (the loop's sort is FIFO within a tier)

### Shutdown Drain (R9)

Notices accumulating in the flush window must not be lost when the process winds down.

**Acceptance Criteria**:
- Given non-urgent notices pending in the flush window, when shutdown runs, then the registered `onShutdown` hook calls `router.flushNow()`, which clears the window timer and emits the pending notices (one notification, or a digest when several are pending) into the conversation loop's final drain
- Given the shutdown drain happens, then the emitted delivery is rendered to the channel as part of the loop's final awaited digest rather than dropped — see [conversation-loop.md](conversation-loop.md)

### Payload Tolerance (R0, R1)

The `notify` event is a shared, loosely-typed channel; the router only acts on payloads that look like notifications.

**Acceptance Criteria**:
- Given a payload without a usable `text` field (status objects, non-objects), when the router handles it, then it is skipped with a debug log and nothing is delivered
- Given a payload with `text` but an unrecognized severity, then it is treated as `info`; a missing source is reported as `unknown`

### Agent Tool (R6)

The `notify_user` tool is registered via `app.agent.use(..., { sessionScopes: ["background"] })` (DES-001), so it is bound **only** into autonomous background task runs — a background task can notify the user directly through the tool; the main conversational session does not have it.

**Acceptance Criteria**:
- Given a background-task agent calls `notify_user` with text and no severity, then an `info` payload with source `agent` is emitted on the `notify` event and follows the normal accumulation path
- Given the agent calls `notify_user` with empty or whitespace-only text, then the tool errors and nothing is emitted
- Given a main conversational session, then `notify_user` is not bound (the agent replies to the user directly)
