# Session Task Execution

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Session tasks are proactive messages that Tachikoma injects into the main conversation during idle periods. When the user hasn't interacted recently, pending session task instances are handed to the priority buffer, which delivers them as new message turns once the conversation is idle (see [delivery/priority-buffer](../delivery/priority-buffer.md)). Delivery flows through the coordinator's full processing pipeline, including pre-processing and boundary detection.

## User Stories

- As a user, I want Tachikoma to proactively message me during idle periods so that I receive timely reminders and follow-ups without having to ask
- As a user, I want proactive messages to feel like natural conversation turns so that the experience is seamless

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Detect ready session task instances and hand them to the priority buffer for idle-gated delivery |
| R2 | Session task messages go through the full pre-processing pipeline and trigger boundary detection if topic changed |
| R3 | Periodic check loop (~5 min interval) for pending session task instances |
| R4 | Scheduler enqueues session tasks into the priority buffer at Normal priority via `buffer.enqueue()`; the buffer handles idle gating, ordering, force-delivery, and new-turn dispatch (see [delivery/priority-buffer](../delivery/priority-buffer.md)) |
| R5 | Channels handle concurrent user messages during session task processing according to their concurrency model (buffering in Telegram, queuing in REPL) |

## Behaviors

### Scheduler Detection (R0, R3)

A periodic scheduler checks for pending session task instances and hands ready instances to the priority buffer for delivery.

**Acceptance Criteria**:
- Given a pending session task instance is ready for delivery, when the scheduler's check tick fires, then the instance is enqueued into the priority buffer at Normal priority and the buffer handles the idle-gated delivery (see [delivery/priority-buffer](../delivery/priority-buffer.md))
- Given no pending session task instances exist, then the periodic check completes without side effects

### Pipeline Integration (R2)

Session task messages flow through the coordinator's full processing pipeline, including pre-processing and boundary detection.

**Acceptance Criteria**:
- Given the buffer delivers a session task through the active channel, then it is sent via `coordinator.send_message()` (or `enqueue()` when the coordinator is mid-exchange during a shutdown flush) and goes through the full pre-processing pipeline (memory context injection, etc.)
- Given a session task message is injected and the boundary detector classifies it as a topic change, then a new session is created following normal boundary detection behavior
- Given the coordinator processes a session task message, then the agent responds via the active channel as if it were a normal conversation turn

### Buffer Delivery (R4)

The scheduler calls `buffer.enqueue()` directly with a `BufferedItem` carrying the session task prompt and completion callback. The buffer emits a `BufferedDelivery` event when the item is ready; the active channel routes it through the coordinator and fires the per-item `on_delivered` callback.

**Acceptance Criteria**:
- Given a session task instance is ready, when the scheduler enqueues it into the buffer, then the item carries the task prompt, Normal priority, and an `on_delivered` callback that marks the instance as completed
- Given the buffer delivers a session task via `BufferedDelivery`, when the channel routes the prompt through the coordinator and the exchange completes, then the `on_delivered` callback fires and the instance is marked completed

### Concurrent User Messages (R5)

When a user sends a message while a session task is being processed, each channel handles it according to its concurrency model.

**Acceptance Criteria**:
- Given a session task is being processed in Telegram and the user sends a message, then the user's message is buffered via `coordinator.enqueue()` and processed within the same session or as a new session after completion
- Given a session task is being processed in the REPL, then user input is handled at the next input cycle (single-threaded input loop)

## Requires

Dependencies:
- None

Assumes existing:
- Task management with persistent instances (task-management)
- Coordinator `send_message()` and `enqueue()` APIs (core-architecture)
- Priority buffer with `enqueue()` API and `BufferedDelivery` event (delivery/priority-buffer)
- Event bus for typed event dispatch (ADR-009)
- Channel subscriptions to `BufferedDelivery` (telegram, terminal-repl)
- Pre-processing and boundary detection pipelines (core-architecture)
