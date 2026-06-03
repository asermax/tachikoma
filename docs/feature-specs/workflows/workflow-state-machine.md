# Workflow State Machine

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A workflow construct within skills that maps multi-step processes to directory trees. Workflows are defined as subdirectories within a skill's `workflows/` folder, each containing ordered steps with instructions, references, and scripts. Workflows run autonomously as background tasks — the main session retains only `start_workflow` for initiation; once started, each step fires as a separate background task instance with full step context injected into its prompt. Step-driving tools (`complete_step`, `skip_step`, `abort_workflow`, `request_input`) are available only inside the workflow step task session. Steps communicate through hand-off messages (relayed to the next step's prompt) and the shared scratchpad (for long-term context). State persists across context compaction because it lives in the database, not in conversation memory. Workflows can compose other workflows by referencing them in step frontmatter, enabling reusable sub-sequences. Steps can also declare conditional predicates that are injected into the step task's prompt for inline evaluation, or declare a loop reference that runs a target workflow once per item in an agent-supplied list.

## User Stories

- As a skill developer, I want to define multi-step workflows within my skill so that the agent can reliably execute ordered sequences without skipping steps or losing its place
- As the agent, I want workflow tools that enforce state transitions so that I can progress through steps with guarantees about ordering and completion
- As the agent, I want workflow state to survive context compaction so that I can resume long-running workflows after interruptions
- As the agent, I want a recovery mechanism when I lose my workflow ID so that I can find and resume active workflows
- As the system, I want stale workflow cleanup so that abandoned workflows don't accumulate indefinitely
- As a skill developer, I want a workflow step to inline-reference another workflow so reusable sub-sequences can be shared across multiple parents instead of being duplicated
- As a skill developer, I want steps to be conditionally skipped based on natural-language predicates so workflows can branch around unnecessary work without hardcoded conditionals
- As a skill developer, I want a step to iterate a referenced workflow once per item in an agent-supplied list so that batch processes can be expressed without unrolling steps by hand
- As a skill developer, I want workflows to run autonomously as background tasks so that the main conversation stays free while multi-step processes execute independently

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Skills can optionally contain a `workflows/` folder with multiple workflow definitions, each as a subdirectory of ordered steps |
| R1 | MCP tools manage start, query state, and list active — the main session has `start_workflow`, `get_workflow_state`, and `list_active_workflows`. Step-driving tools (`complete_step`, `skip_step`, `abort_workflow`, `request_input`) are available only inside workflow step task sessions |
| R2 | Workflow state persisted in a database table |
| R3 | `start_workflow` creates state with unique ID, scratchpad, and enqueues the first step as a pending background task instance. Returns the workflow ID and confirmation that the workflow is running in the background |
| R4 | Step-driving tools (`complete_step`, `skip_step`) available only inside workflow step task sessions. Each step runs as a separate background task instance with full context injected. `complete_step` and `skip_step` tool handlers execute cascade logic and enqueue the next step — no additional agent action needed |
| R5 | Step instructions.md has frontmatter with title and extensible properties (e.g., skippable) enforced by transition validation |
| R6 | `abort_workflow` available inside workflow step task sessions aborts the workflow — cascade abort propagates through the parent and all transitive descendants. Automatic abort on step failure (see R45) |
| R7 | Agent decides which workflow to start based on SKILL.md content and user intent |
| R8 | Agent maintains scratchpad notes during workflow (each step reads scratchpad at start and writes findings during execution) |
| R9 | Scratchpad is a persistent file shared across all steps for accumulated context. Steps can read it at start and write to it during execution. Hand-off messages (max 4000 chars) relay concise context to the next step's prompt |
| R10 | Multiple concurrent workflows supported (each has own ID and DB record) |
| R11 | Steps are mini-skills with optional `references/` and `scripts/` subdirectories |
| R12 | Instructions frontmatter with step title and extensible fields |
| R13 | System preamble updated with workflow awareness |
| R14 | Built-in workflow-authoring-guide skill |
| R15 | Stale workflow state cleanup mechanism (default 24 hours, based on `updated_at`) |
| R16 | `get_workflow_state` MCP tool for read-only state lookup (resuming after context loss) |
| R17 | Step ordering uses alphabetical sort of directory names (convention: prefix with `01-`, `02-`, etc.) |
| R18 | `list_active_workflows` MCP tool returns all active workflows for recovery after context loss |
| R19 | Step instructions frontmatter may declare a `required_skills` list; at step activation the context provider resolves each declared skill's transitive chain via the skill registry and injects the resolved skill content into the step prompt |
| R20 | Steps can declare a `condition` (natural-language predicate); when the cascade reaches a condition step, the condition prompt is injected into the step task's initial prompt and the step agent evaluates it inline by calling `complete_step` (condition passes) or `skip_step` (condition fails) |
| R21 | A step can declare a `composes` reference (`<workflow>` same-skill or `<skill>/<workflow>` cross-skill); activating the step pauses the parent, runs the child to completion, then auto-resumes the parent |
| R21.1 | The child workflow's definition is snapshotted at child-spawn time (not at parent-start time) — children always run against the latest registered definition |
| R22 | Composition references are validated at registry load time: cycles, missing targets, and zero-step targets reject the workflow with a warning identifying the parent and the underlying cause; cascading rejection propagates if a target is itself rejected |
| R23 | Workflow step tasks share the top-level workflow ID; cascade routing uses the deepest active layer; step IDs that don't match the deepest layer return errors |
| R24 | Tool responses include a breadcrumb (`<parent>/<step> > <child>/<step>`, separator ` > `) showing the active layer's path |
| R25 | Composed children inherit the parent's scratchpad path; the file is created and removed once at the top-level lifecycle; concurrent parents composing the same child remain isolated |
| R26 | Composed children are exempt from the `(skill, workflow)` uniqueness check that applies to top-level instances |
| R27 | `list_active_workflows` surfaces only top-level workflows; nested children are not listed separately |
| R28 | `get_workflow_state` on a top-level workflow inlines the active child path; child IDs return a standalone view with a note pointing to the parent; an active composition step whose target is no longer registered surfaces a corruption warning directing abort |
| R29 | Abort cascade on a top-level tears down all transitive descendants atomically |
| R30 | Stale cleanup considers the freshest `updated_at` across the active stack — an active child keeps the parent alive |
| R31 | The composition step itself can declare `required: false` or `condition`; either causes the sub-workflow to be skipped without spawning a child record |
| R32 | Composition graphs may nest to arbitrary depth (3+ levels supported); load-time cycle detection guarantees finite runtime depth |
| R33 | A step's frontmatter may declare a `loop` reference to another workflow (`<workflow>` same-skill or `<skill>/<workflow>` cross-skill); activating the step runs the target once per item supplied by the agent at start |
| R34 | Each iteration runs the loop target as a full composition child, inheriting all step-level semantics (per-iteration snapshot, condition, `required`, `required_skills`, nested composition / nested loops) |
| R35 | Items are opaque strings encoded in a JSON array; invalid JSON, non-array JSON, non-string items, or missing items return validation errors before any state change is made |
| R36 | The current item is exposed in every iteration tool response (breadcrumb suffix `(item: <value>)`) and in `get_workflow_state` (a `### Loop step` block with items list, current 1-indexed iteration, and current item) |
| R37 | An empty items list (`items=[]`) auto-completes the loop step with zero iterations; the cascade advances to the parent's next step (or auto-finalizes the top-level if the loop step was last) |
| R38 | Iterations run sequentially in the order the agent supplied them; iteration N+1 spawns in the same tool call that finalizes iteration N's child |
| R39 | `loop` and `composes` are mutually exclusive on a single step — registry validation rejects the parent workflow with a malformed-step warning |
| R40 | `loop` and `composes` edges share the same cycle/reference graph; cross-edge cycles are rejected at registry load with cycle warnings |
| R41 | The loop step's items list and current iteration index are persisted on the parent workflow state record (new `loop_state` JSON column shaped `{<step_id>: {items, index}}`); the immutable definition snapshot is not mutated |
| R42 | Workflow step TaskInstances carry a `workflow_id` field on the TaskInstance, enabling workflow-specific timeout, tool registration, and cascade coordination |
| R43 | Workflow background tasks use a separate `workflow_wait_timeout` config (default 7 days) for steps awaiting user input via `request_input` |
| R44 | `request_input` tool available inside workflow step task sessions requests user input by dispatching a respondable urgent notification and transitioning the task to `waiting`; the task resumes in the same SDK session when the user responds |
| R45 | Step task failures (error, stuck, max iterations) fail the entire workflow — cascade abort propagates through the parent and all transitive descendants, a failure notification is dispatched, and the workflow state is soft-deleted |
| R46 | When a step task agent completes its work without calling any workflow tool (`complete_step`, `skip_step`, or `abort_workflow`), the evaluator loop reaches max iterations, the step is treated as failed, and the workflow abort cascade triggers |

## Behaviors

### Workflow Definition (R0, R11, R12, R17)

Workflows are optional subdirectories within skills (`workflows/<name>/`), each containing ordered step directories with instructions, references, and scripts.

**Acceptance Criteria**:
- Given a skill directory, when it contains a `workflows/` subdirectory, then each subdirectory within `workflows/` is recognized as a workflow definition
- Given a workflow directory, when it contains step subdirectories, then steps are ordered by alphabetical sort of their directory names
- Given step directories named `01-plan`, `02-execute`, `03-review`, when the workflow is loaded, then steps are ordered as plan -> execute -> review
- Given a step directory, when it contains an `instructions.md` file with YAML frontmatter, then the frontmatter is parsed for step properties (title, skippable, and extensible fields)
- Given a step directory, when it contains a `references/` subdirectory, then those files are available as supporting data for the step
- Given a step directory, when it contains a `scripts/` subdirectory, then those scripts are available as tools for the step
- Given a step's frontmatter contains `required_skills: [skill-a, skill-b]`, when the workflow definition is loaded, then the step's `required_skills` is preserved as a tuple of declared skill names
- Given a step's frontmatter contains a `required_skills` value that is not a list of strings, when the workflow definition is loaded, then a warning is logged and the step's `required_skills` is empty (the step still loads)
- Given a step declares a `required_skills` name that is not registered in the skill registry, when the registry finishes loading the workflow, then a single warning is logged naming the skill, workflow, step, and missing name
- Given a step without `references/` or `scripts/`, when the workflow runs, then the step functions normally (both are optional)
- Given a step directory without an `instructions.md`, when the workflow definition is loaded, then the step is rejected and a warning is logged
- Given a step with invalid YAML frontmatter, when the workflow definition is loaded, then a warning is logged identifying the step and the invalid field
- Given a workflow directory with no step subdirectories, when `start_workflow` is called for it, then the tool returns an error indicating the workflow has no steps
- Given a step's frontmatter contains `condition: "<predicate>"`, when the workflow definition is loaded, then `condition` is preserved as a string on the step definition
- Given a step's frontmatter contains a `condition` value that is not a string, when the workflow definition is loaded, then a warning is logged and the step's `condition` is `None` (the step still loads)
- Given a step's frontmatter contains `composes: <value>`, when the workflow definition is loaded, then `composes` is preserved as a string on the step definition
- Given a step's frontmatter contains a `composes` value that is not a string, when the workflow definition is loaded, then a warning is logged and the step's `composes` is `None` (the step still loads)
- Given a composition step (one with `composes`), when the engine activates it at runtime, then only its frontmatter (title, required, condition, required_skills) is honoured — the step's `instructions.md` body is not read
- Given the registry loads workflows containing a composition cycle (`A` composes `B`, `B` composes `A`, or `A` composes itself), then every workflow in the cycle is removed from the registry and a warning is logged identifying the cycle's members
- Given a parent workflow's composition reference targets a workflow that does not exist, has zero steps, or has a malformed `composes` value, when the registry loads, then the parent is removed from the registry and a warning is logged identifying the parent and the underlying cause
- Given a parent's composition target is itself rejected (cycle or missing dependency), when the registry loads, then the parent is also rejected via cascading propagation; the warning identifies both the parent and the underlying cause
- Given a step's frontmatter contains `loop: <value>`, when the workflow definition is loaded, then `loop` is preserved as a string on the step definition
- Given a step's frontmatter contains a `loop` value that is not a string, when the workflow definition is loaded, then a warning is logged and the step's `loop` is `None` (the step still loads)
- Given a `loop` value is empty, contains multiple slashes, points at a missing target, or points at a zero-step target, when registry validation runs, then the parent workflow is rejected with a warning identifying the parent and the underlying cause
- Given a step declares both `loop` and `composes`, when registry validation runs, then the parent workflow is rejected before edge collection (mutex pre-pass) and removed from the registry
- Given the registry loads workflows whose composition graph contains a cycle that mixes `loop` and `composes` edges, then every workflow in the cycle is removed from the registry and a warning is logged identifying the cycle's members — `loop` and `composes` edges share the same cycle graph
- Given a non-cyclic loop graph (e.g., `A` loops `B`, `B` does not reference `A`), when registry validation runs, then both workflows load successfully
- Given a loop step is activated at runtime, when the engine processes it, then only its frontmatter (title, required, condition, required_skills, loop) is honoured — the step's `instructions.md` body is not read (consistent with composition)

### Workflow Discovery (R7)

The agent discovers workflows by reading a skill's SKILL.md content, which describes available workflows and when to use them.

**Acceptance Criteria**:
- Given a skill with workflows, when SKILL.md is read by the agent, then it describes the available workflows and when to use them
- Given a loaded skill with multiple workflows, when the agent evaluates user intent, then it selects the appropriate workflow to start (or none if no match)
- Given a skill without workflows, when the skill is loaded, then workflow tools are not offered — no overhead

### MCP Tools — start_workflow (R1, R3)

**Acceptance Criteria**:
- Given a skill name and workflow name, when `start_workflow` is called, then a new workflow state record is created in the database with a unique ID, a scratchpad file is created, and the first step is enqueued as a pending background task instance (TaskInstance with `workflow_id` set)
- Given `start_workflow` succeeds, when the tool returns, then the result contains the workflow ID and confirms the workflow is running in the background
- Given a workflow already active for the same skill+workflow, when `start_workflow` is called again, then the tool returns an error indicating the workflow is already running (returns the existing ID)
- Given an invalid skill or workflow name, when `start_workflow` is called, then the tool returns an error
- Given a valid skill name but a workflow name that does not exist within that skill, when `start_workflow` is called, then the tool returns an error indicating the workflow was not found

### MCP Tools — Workflow Step Tools (R1, R4, R44)

Workflow step tools (`complete_step`, `skip_step`, `abort_workflow`, `request_input`) are available only inside workflow step background task sessions. They are registered via a separate MCP server factory (`create_workflow_step_tools_server()`) distinct from the main-session workflow tools. Regular background tasks do not have these tools.

**Acceptance Criteria**:
- Given a workflow background task session (identified by `workflow_id` on the TaskInstance), when the executor registers MCP servers, then a workflow step tools MCP server is registered containing `complete_step`, `skip_step`, `abort_workflow`, and `request_input` — this server is NOT registered for regular background tasks
- Given a regular background task (no `workflow_id`), when the executor registers MCP servers, then the workflow step tools server is NOT present — regular tasks get the standard tool set only
- Given a step task agent calls `complete_step(handoff="summary of work done")`, when the tool handler processes the call, then: (1) the step's workflow state is transitioned to completed, (2) cascade logic runs to determine the next step, (3) if a next step exists, a new TaskInstance is enqueued with the next step's context — all within the tool handler
- Given a step completes via `complete_step`, when no next pending step exists, then the workflow auto-finalizes (soft-delete, scratchpad removed)
- Given a step is skipped via `skip_step`, when the step is marked skippable (or has `required: false`), then the next step is enqueued as a new pending background task instance — cascade preserved
- Given a step is skipped via `skip_step`, when the step is NOT marked skippable and is required, then the tool returns an error and the step state is unchanged
- Given a step task agent calls `abort_workflow()`, when the tool processes the call, then the current step task instance is marked failed and the workflow abort cascade triggers — identical to automatic failure propagation
- Given a step task agent calls `request_input(question="...")`, when the call succeeds, then a respondable urgent notification is dispatched via `send_notification(await_response=true)` and the task transitions to `waiting` — the task resumes in the same SDK session when the user responds
- Given a step task calls `request_input`, when the question is empty or whitespace-only, then the tool returns an error without dispatching a notification
- Given a step task agent completes its work without calling any workflow tool, when the evaluator loop reaches max iterations, then the step is treated as failed and the workflow abort cascade triggers (R46)

### Hand-Off and Scratchpad Communication (R9)

Steps communicate through two mechanisms: hand-off messages (concise context relayed to the next step's prompt) and the shared scratchpad (persistent file for long-term context).

**Acceptance Criteria**:
- Given a step completes via `complete_step(handoff="summary")`, when the tool handler processes the call, then the hand-off message is captured and included in the next step's initial prompt
- Given a step completes via `complete_step()` without a hand-off parameter, when the next step is enqueued, then the next step's prompt has no hand-off section
- Given a step completes via `complete_step(handoff="")`, when the tool handler processes the call, then the empty string is treated identically to no hand-off
- Given a step completes via `complete_step(handoff=<message exceeding 4000 chars>)`, when the tool handler processes the call, then the tool returns a validation error and the step is NOT completed
- Given a step's initial prompt, when the scratchpad exists and has content, then the prompt includes the scratchpad path and guidance to read it for accumulated context
- Given the hand-off message, when the next step's agent reads it, then the hand-off can reference scratchpad entries for detailed context

### Step Task Failure Propagation (R45, R46)

When a workflow step task fails, the entire workflow is aborted — cascade abort propagates through the parent and all transitive descendants.

**Acceptance Criteria**:
- Given a step task fails (evaluator detects stuck, max iterations reached, or unhandled error), when the executor marks the instance as failed, then the entire workflow is aborted — cascade abort propagates through the parent and all transitive descendants (composition children, loop iterations) atomically
- Given a step task fails, when the workflow abort cascade triggers, then a failure notification is dispatched describing which step failed and why
- Given `abort_workflow` is called from within a step task, when the tool processes the call, then the current step task instance is marked failed and the workflow abort cascade triggers — identical to automatic failure propagation
- Given a step task agent completes its work without calling any workflow tool (`complete_step`, `skip_step`, or `abort_workflow`), when the evaluator loop reaches max iterations, then the step is treated as failed and the workflow abort cascade triggers

### Workflow Wait Timeout (R43)

Workflow step tasks use a separate `workflow_wait_timeout` config (default 7 days) for steps awaiting user input.

**Acceptance Criteria**:
- Given a configuration field `workflow_wait_timeout` with default 7 days, when a workflow task enters `waiting` state, then the expired waiter sweep uses the workflow-specific timeout instead of the default background task timeout
- Given a non-workflow background task in `waiting` state, when the expired waiter sweep runs, then it uses the standard background task wait timeout — workflow timeout does not affect regular tasks
- Given a workflow task that has been waiting longer than `workflow_wait_timeout`, when the expired waiter sweep runs, then the task is marked failed and a non-respondable urgent notification is dispatched, triggering the workflow abort cascade

### MCP Tools — get_workflow_state (R16)

**Acceptance Criteria**:
- Given a workflow ID, when `get_workflow_state` is called, then the tool returns: workflow ID, skill name, workflow name, current step, all step states (pending/started/completed/skipped), and created/updated timestamps
- Given an invalid or completed workflow ID, when `get_workflow_state` is called, then the tool returns an error indicating the workflow is not active
- Given the agent loses conversation context (context compaction, session transition), when it calls `get_workflow_state` with a known ID, then the full state is returned enabling resumption from the correct step
- Given an active parent with an active child, when `get_workflow_state(parent_id)` is called, then the response includes the parent's step states (with the composition step shown as `started`), an `### Active Child` section listing the child's workflow name, current step, and step states, and a breadcrumb header showing the active path
- Given the parent has previously completed a composition step whose child has been finalized, when `get_workflow_state(parent_id)` is called, then the completed composition step appears as `completed` with no nested child data inlined
- Given the agent calls `get_workflow_state(<child_id>)` with a composed child's ID (legacy or persisted), then the response is the child's standalone view with a note recommending access via the parent and the parent's ID
- Given an active composition step's target is no longer registered (skill rename or cycle introduced by hot reload), when `get_workflow_state(parent_id)` is called, then the response prepends a corruption warning identifying the affected step and target, and directing the agent to abort the workflow
- Given a parent has an active loop step, when `get_workflow_state(parent_id)` is called, then the response includes a `### Loop step` block on the parent's loop step listing the items, the current 1-indexed iteration (e.g., `4 / 5`), and the current item; the active iteration child renders identically to a regular composition child (no parallel rendering path)
- Given context loss between iteration N completing and iteration N+1 starting, when the agent calls `get_workflow_state(top_level_id)`, then the response shows the items list, current iteration index, current item, and the active iteration's nested view sufficient to resume

### MCP Tools — list_active_workflows (R18)

**Acceptance Criteria**:
- Given `list_active_workflows` is called, then the tool returns all active workflows with id, skill name, workflow name, current step, and started timestamps
- Given no active workflows exist, when `list_active_workflows` is called, then the tool returns an empty list
- Given the agent loses all context (scratchpad and workflow ID), when it calls `list_active_workflows`, then all active workflow IDs are returned for recovery
- Given an active parent with an active composed child, when `list_active_workflows` is called, then only the parent surfaces — the child is not listed separately
- Given an active parent with an in-flight loop iteration child, when `list_active_workflows` is called, then only the parent surfaces (consistent with composition's top-level-only listing)

### State Persistence (R2)

**Acceptance Criteria**:
- Given a workflow is started, when state is written to the database, then the record includes: workflow ID, skill name, workflow name, current step, step states JSON (per-step: pending/started/completed/skipped), definition snapshot JSON, scratchpad path, created timestamp, updated timestamp
- Given a workflow state record, when the agent queries it via `get_workflow_state`, then the full state is available
- Given the process restarts, when active workflows exist in the database, then they remain available (the agent can resume by calling `get_workflow_state` with the stored ID)
- Given a workflow definition is changed while a workflow is active, when the agent calls any workflow tool, then the workflow uses the definition snapshot from when it was started (not the modified definition)
- Given a child workflow is spawned, when the records are inspected, then the child has `parent_workflow_id` set to the parent's ID and `parent_step_id` set to the parent's composition step ID; top-level records have both fields `NULL`
- Given a child is created at composition-step activation, when the child's snapshot is captured, then it reflects the *current* registered child workflow definition at that moment (not a snapshot taken at parent-start time)
- Given a child is spawned, when its `scratchpad_path` is set, then it is the parent's scratchpad path — no new file is created
- Given the top-level workflow auto-finalizes or is aborted, when cleanup runs, then exactly one scratchpad file is deleted (the top-level's)
- Given a loop step is started with items, when the workflow state is persisted, then the items list and the current iteration index are stored on the parent's `loop_state` JSON column, shaped `{<step_id>: {"items": [...], "index": <i>}}`
- Given a step completes via `complete_step(handoff="...")`, when the hand-off is stored, then it is persisted on the workflow state's `pending_handoff` field — the next step's context provider reads and clears it

### Agent Scratchpad (R9)

**Acceptance Criteria**:
- Given a workflow is active, when the step task's initial prompt is constructed, then it includes the scratchpad path and guidance to read it for accumulated context
- Given the scratchpad instructions, when the step agent starts, then it is prompted to read the scratchpad file for context from previous steps
- Given the scratchpad is a file in the workspace, when context compaction occurs, then the next step can read the scratchpad file to recover accumulated context

### Concurrent Workflows (R10)

**Acceptance Criteria**:
- Given two different skills with workflows, when both are started, then each has its own ID and independent state
- Given the same workflow from the same skill already active, when `start_workflow` is called again, then the tool returns an error indicating the workflow is already running (returns the existing ID) — one active instance per skill+workflow name
- Given multiple active workflows, when step tasks run for each, then only that workflow's state changes
- Given workflow `X` is currently composed inside an active parent, when the agent calls `start_workflow` to start `X` standalone, then the call succeeds — the duplicate-prevention check applies only across other top-level instances
- Given two top-level parents are each composing their own instance of the same child workflow, when each child writes to its scratchpad, then each writes to its own parent's scratchpad — the two parent scratchpads remain isolated and no cross-write occurs

### System Preamble (R13)

**Acceptance Criteria**:
- Given the system preamble is assembled, when a Workflows section is included, then it explains the workflow concept, available MCP tools in the main session (`start_workflow`, `get_workflow_state`, `list_active_workflows`), and how workflows run autonomously as background tasks
- Given the system preamble's Workflows section is read, when the agent encounters a nested run, then the preamble documents single-ID driving (always pass the top-level workflow ID), the breadcrumb format with `>` separator, that `list_active_workflows` is top-level-only, that `get_workflow_state` inlines the active child path, and that condition steps are evaluated inline by the step agent
- Given the system preamble's Workflows section is rendered, when the Loops sub-section is read, then it documents the `loop` frontmatter field, current-item exposure in tool responses, auto-completion when items are exhausted, mutual exclusion with `composes`, and that loop steps are driven by the cascade engine

### Built-in Workflow Authoring Guide (R14)

**Acceptance Criteria**:
- Given a built-in skill `workflow-authoring-guide` exists, when the registry loads built-in skills, then it is available like the skill-authoring-guide
- Given the workflow-authoring-guide, when the agent reads it, then it provides guidance on creating workflow definitions (directory structure, naming convention for ordering, frontmatter fields including `loop` with at least one paired producer + loop step example, step design patterns, relationship to parent skill resources, hand-off communication pattern, scratchpad usage pattern)

### Stale State Cleanup (R15)

**Acceptance Criteria**:
- Given active workflow records in the database, when a cleanup mechanism runs, then workflows whose `updated_at` is older than a configurable threshold (default 24 hours) are soft-deleted
- Given a stale workflow is cleaned up, when the agent references its ID, then the tool returns an error indicating the workflow no longer exists
- Given a workflow actively being updated within the threshold, when cleanup runs, then it is not removed
- Given a parent and active child stack where the freshest `updated_at` across the stack is within the threshold, when cleanup runs, then neither record is removed — an active child keeps the entire stack alive
- Given every record in a parent-child stack has `updated_at` exceeding the threshold, when cleanup runs, then the entire stack is soft-deleted atomically and the shared scratchpad is removed once

## Requires

Dependencies:
- Skill system (skills.md) — workflow definitions live within skills
- Core architecture (core-architecture.md) — MCP tools registration
- Post-processing pipeline (post-processing-pipeline.md) — stale cleanup processor
- Background task execution (background-task-execution.md) — workflow step tasks run as background task instances
