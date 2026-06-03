# Background Task Execution

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Background tasks execute in isolated parallel sessions without interrupting the user. A background task runner picks up pending instances, creates fresh SDK sessions with an adapted pipeline, and runs an evaluator loop that monitors completion. Background task agents can send notifications to the user during execution via a `send_notification` MCP tool; failure notifications are dispatched automatically by the executor.

## User Stories

- As a user, I want Tachikoma to work on complex tasks in the background so that it can process information and complete work without blocking our conversation
- As a user, I want to be notified when a background task finishes or fails so that I stay informed about the results
- As a user, I want workflow steps to run as background tasks so that multi-step processes execute autonomously without blocking the main conversation

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Execute pending background task instances in fresh isolated SDK sessions |
| R1 | Adapted pipeline: full pre-processing (memory, projects, skills) and selective post-processing — episodic memory extraction, project submodule commit/push, and git commit (no facts, preferences, or core context extraction). Pinned skills from the task definition are loaded unconditionally before LLM-based classification |
| R2 | Evaluator loop that assesses each agent response for completion using a lightweight model (three statuses: `complete`, `stuck`, `continue` — no `needs_input` classification) |
| R3 | Max iterations limit (configurable, default 10) — forces completion assessment and marks task as failed if not done |
| R4 | Agent-driven notifications via `send_notification` MCP tool during background task execution; automatic failure notifications dispatched by the executor. `send_notification` accepts an `await_response` flag (default `false`) — when `true`, it dispatches a respondable urgent notification and transitions the task to `waiting` (the only way to enter the waiting state). Notifications carry a priority field (Urgent/Normal/Low); `send_notification` accepts an optional priority (default Normal) and automatic failure notifications use Urgent. When `await_response=true`, priority is forced to Urgent regardless of the agent-specified value. Delivery to the user is handled by the priority buffer (see [delivery/priority-buffer](../delivery/priority-buffer.md)) |
| R5 | Concurrency gating via configurable limit (default 3); excess instances remain pending until a slot opens |
| R6 | Stuck/looping agent detection — evaluator detects unproductive iterations and marks the task as failed |
| R7 | Background task agents share the main agent's restricted git surface: a destructive-git deny hook blocks `git push`, `git reset`, `git checkout .`, `git restore .`, `git clean`, and mutating `git remote` subcommands; `push` and `sync` MCP tools are available for on-demand push/sync operations |
| R8 | Background task agents have access to the task management MCP tools (`create_task`, `list_tasks`, `get_task`, `update_task`, `delete_task`) so they can schedule, inspect, update, and remove task definitions during autonomous execution |
| R9 | Background task agents have access to the workflow management MCP tools (`start_workflow`, `get_workflow_state`, `list_active_workflows`) so they can start and monitor multi-step workflows during autonomous execution. Workflow step-driving tools (`complete_step`, `skip_step`, `abort_workflow`, `request_input`) are available only inside workflow step task sessions (see R10) |
| R10 | Workflow step tasks are a subtype of background tasks identified by a `workflow_id` field on the TaskInstance. They use a tailored system prompt (`WORKFLOW_STEP_SYSTEM_PROMPT`), workflow-specific MCP tools (`complete_step`, `skip_step`, `abort_workflow`, `request_input`), a workflow-specific pre-processing context provider (`WorkflowStepContextProvider`), and a workflow-specific post-processing processor (`WorkflowFailureProcessor`). Regular background tasks are unaffected by the workflow subtype |
| R11 | Workflow step tasks use a separate `workflow_wait_timeout` config (default 7 days) for the expired waiter sweep, distinct from the standard `wait_timeout` used by regular background tasks |

## Behaviors

### Isolated Execution (R0, R1)

Background tasks run in fresh SDK sessions separate from the main conversation, with full pre-processing (same context providers as the main conversation) and selective post-processing.

**Acceptance Criteria**:
- Given a pending background task instance, when the runner picks it up, then a fresh SDK session is created (not forked from the main session) with an adapted base prompt explaining the background task context
- Given a background task instance is being executed, then the adapted system prompt includes the current date and time in the configured timezone so the agent has temporal awareness during execution
- Given a background task session starts, then the pre-processing pipeline runs with all context providers (memory, projects, skills) — MCP servers and agent definitions from providers are passed to the SDK client options
- Given a background task definition declares `pinned_skills`, when the executor runs pre-processing, then the pinned skills are resolved (including transitive dependencies) and injected into context unconditionally before LLM-based classification — the per-message pipeline receives the pinned skills via a `TextMessage` envelope
- Given a background task session completes, then the adapted post-processing pipeline runs with phased execution: episodic extraction (main phase), project submodule commit/push (pre_finalize phase), and git commit (finalize phase) — no facts, preferences, or core context extraction

### Evaluator Loop (R2, R3, R6)

After each agent response, a lightweight model assesses the agent's workflow state using an ordered structured checklist. The evaluator judges whether the agent finished its workflow — not whether the output content is correct or high-quality. Three statuses are possible: `complete` (workflow finished), `stuck` (blocking error), and `continue` (mid-workflow). The evaluator does NOT classify user input needs — that is handled exclusively by `send_notification` with `await_response=true` (see Notification behavior below).

**Acceptance Criteria**:
- Given a background task agent produces a response, then the evaluator assesses workflow completion using an ordered checklist: blocking error → complete → continue
- Given the evaluator determines the agent completed its workflow (announced completion, summarized results, or called `send_notification`), then the task instance is marked as `completed` — regardless of output quality
- Given the evaluator detects the agent is stuck or looping, then the task instance is marked as `failed` and a notification is dispatched
- Given the evaluator determines the agent is mid-workflow, then the agent receives feedback and continues working (next iteration)
- Given the background task reaches the maximum iteration limit, then the task is marked as failed if not done

### Notification (R4)

Background task agents can send notifications to the user during execution via the `send_notification` MCP tool, which dispatches a generic `Notification` event (from `tachikoma.notifications`). The tool is only available during background task execution — the executor registers a notification MCP server per-execution using the DES-006 factory pattern. Failure notifications are dispatched automatically by the executor using the same shared `dispatch_notification()` function, ensuring consistent prompt formatting for both agent-driven and automatic notifications. Notifications carry a `priority` field (Urgent/Normal/Low); agents can pass a priority to `send_notification`, and automatic failure notifications default to Urgent. Channels do not handle `Notification` directly — the priority buffer subscribes to the event, enqueues each notification, and delivers it to the user as a new message turn when the conversation is idle (see [delivery/priority-buffer](../delivery/priority-buffer.md)).

`send_notification` accepts an `await_response` flag (default `false`). When `true`, it is the **only** way to enter the `waiting` state — the evaluator does NOT classify input needs. The executor uses a `NotificationCycleState` (mutable shared state between the notification handler and executor) to detect that `await_response` was requested during the agent's response, then transitions the task to `waiting` without calling the evaluator. When `await_response=True`: priority is forced to Urgent (regardless of any agent-specified priority), `response_instance_id` is set to the task instance ID, execution pauses, and the user's response will resume the task in the same SDK session on the next runner tick.

**Acceptance Criteria**:
- Given a background task instance is being executed, then the `send_notification` MCP tool server is registered in the SDK client's MCP servers
- Given a background task agent calls `send_notification` with a message, then a `Notification` event with the wrapped prompt, severity "info", and the specified priority (default Normal) is dispatched
- Given a background task agent calls `send_notification` with an empty or whitespace-only message, then the tool returns an error response without dispatching a notification
- Given a background task agent executing a long-running task, when it calls `send_notification` multiple times, then each call dispatches a separate `Notification` event independently
- Given a background task completes successfully and the agent did not call `send_notification`, then no notification is dispatched
- Given a background task fails (stuck, error, or max iterations), then a `Notification` event with severity "error" and priority Urgent is dispatched automatically by the executor
- Given a background task agent sent a notification during execution and the task subsequently fails, then both the agent's notification and the automatic failure notification are delivered independently
- Given the `send_notification` MCP tool, then it is only available within a background task agent session — it is not exposed to the main conversation
- Given the background task system prompt, then it documents the available priority levels (Urgent for time-sensitive results, Normal for standard completion results, Low for informational updates) and the default (Normal)
- Given a `Notification` event is dispatched on the bus, then the priority buffer enqueues it for idle-gated delivery — no channel subscribes to `Notification` directly
- Given a background task agent calls `send_notification(await_response=true, message="...")`, then a respondable urgent `Notification` is dispatched (with `response_instance_id=instance_id`, priority forced to Urgent), the task transitions to `waiting` with the current `sdk_session_id` preserved, and the evaluator is NOT called
- Given `await_response=False` (default), then the notification is dispatched as a fire-and-forget info notification with the agent-specified priority (default Normal) — behavior unchanged
- Given a `waiting` task created via `await_response=True`, when the `wait_timeout` expires, then the expired waiter sweep marks it failed with a non-respondable urgent notification (existing timeout behavior applies)

### Task Scheduling (R8)

Background task agents can schedule follow-up work via the same task management MCP tools available to the main agent. The system prompt explains when to use them — splitting a run into a separate scheduled pass, cleaning up existing schedules, or setting up recurring checks — and clarifies that newly scheduled tasks produce fresh isolated runs rather than nesting inside the current execution.

**Acceptance Criteria**:
- Given a background task instance is being executed, then the task-tools MCP server is registered in the SDK client's MCP servers alongside the notification and git-tools servers
- Given the background task system prompt, then it documents the task management tools (`create_task`, `list_tasks`, `get_task`, `update_task`, `delete_task`) and states that scheduled tasks run in fresh isolated sessions when their schedule fires
- Given a background task agent calls `create_task` during execution, then the new task definition is persisted identically to definitions created from the main conversation (same validation, same instance-generation behavior)

### Workflow Tools (R9)

Background task agents can start and monitor multi-step workflows via the same workflow management MCP tools available to the main agent. The system prompt explains when to use them — structured multi-step processes, ordered step execution, and automatic skill content loading. Background task agents do not have `update_workflow_state` or `end_workflow` — workflow steps drive themselves via step-specific tools (see Workflow Task Subtype below).

**Acceptance Criteria**:
- Given a background task instance is being executed, then the workflow-tools MCP server is registered in the SDK client's MCP servers alongside the git-tools and task-tools servers
- Given the background task system prompt, then it documents the workflow management tools (`start_workflow`, `get_workflow_state`, `list_active_workflows`) and when to use them
- Given a background task agent calls `start_workflow` during execution, then the workflow state is created identically to workflows started from the main conversation (same state persistence, same step resolution)

### Workflow Task Subtype (R10, R11)

When a TaskInstance has a `workflow_id` field set, it is a workflow step task — a subtype of background tasks with specialized behavior. The executor discriminates based on `workflow_id` to conditionally include workflow-specific pipeline participants, MCP tools, and system prompt.

**Acceptance Criteria**:
- Given a TaskInstance with `workflow_id` set, when the executor loads it, then a tailored system prompt (`WORKFLOW_STEP_SYSTEM_PROMPT`) is used instead of the generic `BACKGROUND_TASK_SYSTEM_PROMPT`
- Given a TaskInstance with `workflow_id` set, when the executor registers MCP servers, then a workflow step tools server (`complete_step`, `skip_step`, `abort_workflow`, `request_input`) is registered in addition to the standard notification server — regular background tasks do not get this server
- Given a TaskInstance with `workflow_id` set, when the executor registers MCP servers, then the workflow-tools server (containing `start_workflow`, `get_workflow_state`, `list_active_workflows`) is NOT registered — workflow step tasks should not start nested workflows
- Given a TaskInstance with `workflow_id` set, when pre-processing runs, then a `WorkflowStepContextProvider` is included that reads the workflow state from the database, resolves the step definition, resolves required skills, reads the scratchpad, and constructs the full step prompt
- Given a TaskInstance with `workflow_id` set, when post-processing runs, then a `WorkflowFailureProcessor` is included that detects failed workflow tasks and runs the abort cascade with a failure notification
- Given a regular background TaskInstance (no `workflow_id`), when the executor runs, then no workflow-specific tools, context provider, failure processor, or system prompt are applied — the executor behaves identically to pre-workflow-subtype behavior
- Given a workflow step task enters `waiting` state, when the expired waiter sweep runs, then it uses `workflow_wait_timeout` (default 7 days) instead of the standard `wait_timeout`
- Given a regular background task in `waiting` state, when the expired waiter sweep runs, then it uses the standard `wait_timeout` — workflow timeout does not affect regular tasks
- Given a workflow step task that has been waiting longer than `workflow_wait_timeout`, when the expired waiter sweep runs, then the task is marked failed and a non-respondable urgent notification is dispatched

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
