# Background Task Execution

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Background tasks execute in isolated parallel sessions without interrupting the user. A background task runner picks up pending instances, creates fresh SDK sessions with an adapted pipeline, and runs an evaluator loop that monitors completion. On completion or failure, the user can be notified via transient session task instances delivered during the next idle period.

## User Stories

- As a user, I want Tachikoma to work on complex tasks in the background so that it can process information and complete work without blocking our conversation
- As a user, I want to be notified when a background task finishes or fails so that I stay informed about the results

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Execute pending background task instances in fresh isolated SDK sessions |
| R1 | Adapted post-processing pipeline: episodic memory extraction and git commit only (no facts, preferences, or core context extraction) |
| R2 | Evaluator loop that assesses each agent response for completion using a lightweight model |
| R3 | Max iterations limit (configurable, default 10) — forces completion assessment and marks task as failed if not done |
| R4 | Notification via transient session task instances on completion (when `notify` is set) or failure |
| R5 | Concurrency gating via configurable limit (default 3); excess instances remain pending until a slot opens |
| R6 | Stuck/looping agent detection — evaluator detects unproductive iterations and marks the task as failed |

## Behaviors

### Isolated Execution (R0, R1)

Background tasks run in fresh SDK sessions separate from the main conversation, with an adapted pipeline that only extracts episodic memories and commits to git.

**Acceptance Criteria**:
- Given a pending background task instance, when the runner picks it up, then a fresh SDK session is created (not forked from the main session) with an adapted base prompt explaining the background task context
- Given a background task session starts, then the pre-processing pipeline runs (memory/context injection active)
- Given a background task session completes, then the adapted post-processing pipeline runs with phased execution: episodic extraction (main phase) followed by git commit (finalize phase) — no facts, preferences, or core context extraction

### Evaluator Loop (R2, R3, R6)

After each agent response, a lightweight model assesses whether the task is complete, should continue, or is stuck.

**Acceptance Criteria**:
- Given a background task agent produces a response, then the evaluator assesses whether the task is complete based on the task definition
- Given the evaluator determines the task is not complete, then the agent receives feedback and continues working (next iteration)
- Given the evaluator detects the agent is stuck or looping, then the task instance is marked as `failed` and a notification is dispatched
- Given the background task reaches the maximum iteration limit, then the evaluator forces completion assessment and marks the task as failed if not done

### Notification (R4)

On completion (with `notify` set) or failure, a `TaskNotification` event is dispatched on the bus. Channels deliver notifications directly to the user.

**Acceptance Criteria**:
- Given the evaluator determines the task is complete and the definition has a non-null `notify` field, then a `TaskNotification` event is dispatched with severity "info"
- Given the evaluator determines the task is complete and `notify` is null, then no notification is generated
- Given a background task fails (stuck, error, or max iterations), then a `TaskNotification` event is dispatched with severity "error"
- Given a `TaskNotification` event is received by a channel, then the notification message is sent directly to the user with appropriate severity formatting

### Concurrency (R5)

Background tasks execute concurrently up to a configurable limit.

**Acceptance Criteria**:
- Given multiple background task instances are pending, then they execute concurrently up to the configured limit (default 3)
- Given the concurrency limit is reached, then excess instances remain pending until a slot opens

## Requires

Dependencies:
- None

Assumes existing:
- Task management with persistent instances (task-management)
- Post-processing pipeline with phased execution (post-processing-pipeline)
- Event bus for typed event dispatch (ADR-009)
- SDK session management pattern (core-architecture)
- Channel notification subscriptions (telegram, terminal-repl)
