# Session Task Execution

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Session tasks are proactive messages that Tachikoma injects into the main conversation during idle periods. When the user hasn't interacted recently, pending session task instances are delivered through the active channel (Telegram or REPL) as if the agent initiated the conversation. The message goes through the full processing pipeline, including pre-processing and boundary detection.

## User Stories

- As a user, I want Tachikoma to proactively message me during idle periods so that I receive timely reminders and follow-ups without having to ask
- As a user, I want proactive messages to feel like natural conversation turns so that the experience is seamless

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Deliver pending session task messages through the active channel during idle periods |
| R1 | Idle gating: only deliver when time since last coordinator message exchange exceeds the configurable idle window (default 5 min) |
| R2 | Session task messages go through the full pre-processing pipeline and trigger boundary detection if topic changed |
| R3 | Periodic check loop (~5 min interval) for pending session task instances |
| R4 | Event bus delivery: scheduler dispatches typed events, channels subscribe and deliver via coordinator |
| R5 | Channels handle concurrent user messages during session task processing according to their concurrency model (steering in Telegram, queuing in REPL) |

## Behaviors

### Idle-Gated Delivery (R0, R1, R3)

A periodic scheduler checks for pending session task instances. When the session is idle (last message exchange exceeds the configured idle window), instances are dispatched for channel delivery.

**Acceptance Criteria**:
- Given a pending session task instance exists and the time since the last coordinator message exchange exceeds the idle window, then the task message is dispatched for delivery via the event bus
- Given a pending session task instance exists but the last message exchange was less than the idle window ago, then the instance is skipped for this check cycle and retried at the next tick
- Given no pending session task instances exist, then the periodic check completes without side effects
- Given `last_message_time` tracking, then it is updated on both user messages and agent responses at the coordinator level

### Pipeline Integration (R2)

Session task messages flow through the coordinator's full processing pipeline, including pre-processing and boundary detection.

**Acceptance Criteria**:
- Given a session task message is injected via the coordinator, then it goes through the full pre-processing pipeline (memory context injection, etc.)
- Given a session task message is injected and the boundary detector classifies it as a topic change, then a new session is created following normal boundary detection behavior
- Given the coordinator processes a session task message, then the agent responds via the active channel as if it were a normal conversation turn

### Event Bus Delivery (R4)

The scheduler dispatches `SessionTaskReady` events on the bus. Channels subscribe to these events and deliver task messages through the coordinator.

**Acceptance Criteria**:
- Given a session task is ready for delivery, when the scheduler dispatches a `SessionTaskReady` event, then the subscribed channel receives the event and sends the task prompt through the coordinator
- Given the channel successfully processes the session task, then the completion callback is invoked, marking the instance as completed

### Concurrent User Messages (R5)

When a user sends a message while a session task is being processed, each channel handles it according to its concurrency model.

**Acceptance Criteria**:
- Given a session task is being processed in Telegram and the user sends a message, then the user's message is steered into the current stream via `coordinator.steer()`
- Given a session task is being processed in the REPL, then user input is handled at the next input cycle (single-threaded input loop)

## Requires

Dependencies:
- None

Assumes existing:
- Task management with persistent instances (task-management)
- Coordinator `send_message()` and `steer()` APIs (core-architecture)
- Event bus for typed event dispatch (ADR-009)
- Channel event subscriptions (telegram, terminal-repl)
- Pre-processing and boundary detection pipelines (core-architecture)
