# Background Task Execution

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Background tasks execute in isolated parallel sessions without interrupting the user. A background task runner picks up pending instances, creates fresh SDK sessions with an adapted pipeline, and runs an evaluator loop that monitors completion. Background task agents can send notifications to the user during execution via a `send_notification` MCP tool; failure notifications are dispatched automatically by the executor.

## User Stories

- As a user, I want Tachikoma to work on complex tasks in the background so that it can process information and complete work without blocking our conversation
- As a user, I want to be notified when a background task finishes or fails so that I stay informed about the results

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Execute pending background task instances in fresh isolated SDK sessions |
| R1 | Adapted pipeline: full pre-processing (memory, projects, skills) and selective post-processing — episodic memory extraction, project submodule commit/push, and git commit (no facts, preferences, or core context extraction) |
| R2 | Evaluator loop that assesses each agent response for completion using a lightweight model |
| R3 | Max iterations limit (configurable, default 10) — forces completion assessment and marks task as failed if not done |
| R4 | Agent-driven success notifications via `send_notification` MCP tool during background task execution; automatic failure notifications dispatched by the executor |
| R5 | Concurrency gating via configurable limit (default 3); excess instances remain pending until a slot opens |
| R6 | Stuck/looping agent detection — evaluator detects unproductive iterations and marks the task as failed |

## Behaviors

### Isolated Execution (R0, R1)

Background tasks run in fresh SDK sessions separate from the main conversation, with full pre-processing (same context providers as the main conversation) and selective post-processing.

**Acceptance Criteria**:
- Given a pending background task instance, when the runner picks it up, then a fresh SDK session is created (not forked from the main session) with an adapted base prompt explaining the background task context
- Given a background task instance is being executed, then the adapted system prompt includes the current date and time in the configured timezone so the agent has temporal awareness during execution
- Given a background task session starts, then the pre-processing pipeline runs with all context providers (memory, projects, skills) — MCP servers and agent definitions from providers are passed to the SDK client options
- Given a background task session completes, then the adapted post-processing pipeline runs with phased execution: episodic extraction (main phase), project submodule commit/push (pre_finalize phase), and git commit (finalize phase) — no facts, preferences, or core context extraction

### Evaluator Loop (R2, R3, R6)

After each agent response, a lightweight model assesses whether the task is complete, should continue, or is stuck.

**Acceptance Criteria**:
- Given a background task agent produces a response, then the evaluator assesses whether the task is complete based on the task definition
- Given the evaluator determines the task is not complete, then the agent receives feedback and continues working (next iteration)
- Given the evaluator detects the agent is stuck or looping, then the task instance is marked as `failed` and a notification is dispatched
- Given the background task reaches the maximum iteration limit, then the evaluator forces completion assessment and marks the task as failed if not done

### Notification (R4)

Background task agents can send notifications to the user during execution via the `send_notification` MCP tool, which dispatches a generic `Notification` event (from `tachikoma.notifications`). The tool is only available during background task execution — the executor registers a notification MCP server per-execution using the DES-006 factory pattern. Failure notifications are dispatched automatically by the executor using the same shared `dispatch_notification()` function, ensuring consistent prompt formatting for both agent-driven and automatic notifications.

**Acceptance Criteria**:
- Given a background task instance is being executed, then the `send_notification` MCP tool server is registered in the SDK client's MCP servers
- Given a background task agent calls `send_notification` with a message, then a `Notification` event with the wrapped prompt and severity "info" is dispatched
- Given a background task agent calls `send_notification` with an empty or whitespace-only message, then the tool returns an error response without dispatching a notification
- Given a background task agent executing a long-running task, when it calls `send_notification` multiple times, then each call dispatches a separate `Notification` event independently
- Given a background task completes successfully and the agent did not call `send_notification`, then no notification is dispatched
- Given a background task fails (stuck, error, or max iterations), then a `Notification` event with severity "error" is dispatched automatically by the executor
- Given a background task agent sent a notification during execution and the task subsequently fails, then both the agent's notification and the automatic failure notification are delivered independently
- Given the `send_notification` MCP tool, then it is only available within a background task agent session — it is not exposed to the main conversation
- Given a `Notification` event is received by a channel, then the notification prompt is enqueued into the coordinator for pipeline-routed delivery (same path as session tasks)

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
