# Workflow State Machine

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A workflow construct within skills that maps multi-step processes to directory trees the agent navigates natively. Workflows are defined as subdirectories within a skill's `workflows/` folder, each containing ordered steps with instructions, references, and scripts. MCP tools manage the full lifecycle — starting a workflow creates tracked state in the database, updating state validates transitions and returns step-specific instructions, and ending a workflow cleans up. State persists across context compaction because it lives in the database, not in conversation memory. The agent uses the SDK's built-in Task tools to track step progress, guided by instructions returned from the workflow tools. Workflows can compose other workflows by referencing them in step frontmatter, enabling reusable sub-sequences that execute as one continuous nested run from the agent's perspective. Steps can also declare conditional predicates that halt the cascade and prompt the agent to evaluate the condition (start or skip), or declare a loop reference that runs a target workflow once per item in an agent-supplied list.

## User Stories

- As a skill developer, I want to define multi-step workflows within my skill so that the agent can reliably execute ordered sequences without skipping steps or losing its place
- As the agent, I want workflow tools that enforce state transitions so that I can progress through steps with guarantees about ordering and completion
- As the agent, I want workflow state to survive context compaction so that I can resume long-running workflows after interruptions
- As the agent, I want a recovery mechanism when I lose my workflow ID so that I can find and resume active workflows
- As the system, I want stale workflow cleanup so that abandoned workflows don't accumulate indefinitely
- As a skill developer, I want a workflow step to inline-reference another workflow so reusable sub-sequences can be shared across multiple parents instead of being duplicated
- As a skill developer, I want steps to be conditionally skipped based on natural-language predicates so workflows can branch around unnecessary work without hardcoded conditionals
- As a skill developer, I want a step to iterate a referenced workflow once per item in an agent-supplied list so that batch processes (process every inbox note, send every reminder) can be expressed without unrolling steps by hand or relying on agent self-looping

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Skills can optionally contain a `workflows/` folder with multiple workflow definitions, each as a subdirectory of ordered steps |
| R1 | MCP tools manage the full lifecycle (start, update state, query state, end) — the tools are the state machine boundary |
| R2 | Workflow state persisted in a database table |
| R3 | `start_workflow` creates state with unique ID, returns overview (step list, guidance to create tasks, how to update state) |
| R4 | `update_workflow_state` validates transitions, returns step instructions. Completing or skipping a step auto-starts the next pending step. When all steps are done, the workflow is auto-finalized (state cleaned up automatically) |
| R5 | Step instructions.md has frontmatter with title and extensible properties (e.g., skippable) enforced by transition validation |
| R6 | `end_workflow` aborts a workflow in progress, cleans up state. Normal completion is handled automatically via auto-finalize |
| R7 | Agent decides which workflow to start based on SKILL.md content and user intent |
| R8 | Agent uses SDK Task tools (TodoWrite) to track step progress, prompted by start_workflow output (voluntary — agent follows guidance, not enforced) |
| R9 | Agent maintains scratchpad notes during workflow (prompting-based, not MCP tool) |
| R10 | Multiple concurrent workflows supported (each has own ID and DB record) |
| R11 | Steps are mini-skills with optional `references/` and `scripts/` subdirectories |
| R12 | Instructions frontmatter with step title and extensible fields |
| R13 | System preamble updated with workflow awareness |
| R14 | Built-in workflow-authoring-guide skill (like skill-authoring-guide) |
| R15 | Stale workflow state cleanup mechanism (default 24 hours, based on `updated_at`) |
| R16 | `get_workflow_state` MCP tool for read-only state lookup (resuming after context loss) |
| R17 | Step ordering uses alphabetical sort of directory names (convention: prefix with `01-`, `02-`, etc.) |
| R18 | `list_active_workflows` MCP tool returns all active workflows for recovery after context loss |
| R19 | Step instructions frontmatter may declare a `required_skills` list; at step activation the workflow tool resolves each declared skill's transitive chain via the skill registry and appends the resolved skill content to the tool response, bypassing classification |
| R20 | Steps can declare a `condition` (natural-language predicate) that gates whether the step starts; when the cascade auto-advances to a condition step, it halts and surfaces the condition prompt to the agent, who evaluates it and calls `action="start"` to proceed or `action="skip"` to skip |
| R21 | A step can declare a `composes` reference (`<workflow>` same-skill or `<skill>/<workflow>` cross-skill); activating the step pauses the parent, runs the child to completion, then auto-resumes the parent |
| R21.1 | The child workflow's definition is snapshotted at child-spawn time (not at parent-start time) — children always run against the latest registered definition |
| R22 | Composition references are validated at registry load time: cycles, missing targets, and zero-step targets reject the workflow with a warning identifying the parent and the underlying cause; cascading rejection propagates if a target is itself rejected |
| R23 | The agent drives nested runs with a single workflow ID (the parent's); tool calls route to the deepest active layer; mismatching step IDs return errors naming the deepest layer's workflow and listing its valid step IDs |
| R24 | Tool responses include a breadcrumb (`<parent>/<step> > <child>/<step>`, separator ` > `) showing the active layer's path |
| R25 | Composed children inherit the parent's scratchpad path; the file is created and removed once at the top-level lifecycle; concurrent parents composing the same child remain isolated |
| R26 | Composed children are exempt from the `(skill, workflow)` uniqueness check that applies to top-level instances |
| R27 | `list_active_workflows` surfaces only top-level workflows; nested children are not listed separately |
| R28 | `get_workflow_state` on a top-level workflow inlines the active child path; child IDs return a standalone view with a note pointing to the parent; an active composition step whose target is no longer registered surfaces a corruption warning directing abort |
| R29 | `end_workflow` abort on a top-level tears down all transitive descendants atomically; calls on a child ID return an error directing to the top-level |
| R30 | Stale cleanup considers the freshest `updated_at` across the active stack — an active child keeps the parent alive |
| R31 | The composition step itself can declare `required: false` or `condition`; either causes the sub-workflow to be skipped without spawning a child record |
| R32 | Composition graphs may nest to arbitrary depth (3+ levels supported); load-time cycle detection guarantees finite runtime depth |
| R33 | A step's frontmatter may declare a `loop` reference to another workflow (`<workflow>` same-skill or `<skill>/<workflow>` cross-skill); activating the step runs the target once per item supplied by the agent at start |
| R34 | `update_workflow_state` accepts an optional `items` parameter on the `start` action — a JSON-encoded string of opaque references (e.g. `'["a.md", "b.md"]'`). Required for loop steps and rejected for non-loop steps. The JSON-string encoding works around the SDK MCP transport's inability to pass array-typed arguments (same pattern as the `scrub_paths` parameter on the `push` tool). The tool wrapper decodes the string internally before passing to the handler |
| R35 | Each iteration runs the loop target as a full composition child, inheriting all step-level semantics (per-iteration snapshot, condition, `required`, `required_skills`, nested composition / nested loops) |
| R36 | Items are opaque strings encoded in a JSON array; invalid JSON, non-array JSON, non-string items, or missing items return validation errors before any state change is made |
| R37 | The current item is exposed in every iteration tool response (breadcrumb suffix `(item: <value>)`) and in `get_workflow_state` (a `### Loop step` block with items list, current 1-indexed iteration, and current item) |
| R38 | An empty items list (`items=[]`) auto-completes the loop step with zero iterations; the cascade advances to the parent's next step (or auto-finalizes the top-level if the loop step was last) |
| R39 | Iterations run sequentially in the order the agent supplied them; iteration N+1 spawns in the same tool call that finalizes iteration N's child |
| R40 | `loop` and `composes` are mutually exclusive on a single step — registry validation rejects the parent workflow with a malformed-step warning |
| R41 | `loop` and `composes` edges share the same cycle/reference graph; cross-edge cycles (e.g., `A` loops `B`, `B` composes `A`) are rejected at registry load with cycle warnings |
| R42 | The loop step's items list and current iteration index are persisted on the parent workflow state record (new `loop_state` JSON column shaped `{<step_id>: {items, index}}`); the immutable definition snapshot is not mutated |
| R43 | When the cascade auto-advances and the next pending step is a loop step, auto-start halts: the loop step stays `pending` and the response prompts the agent to call `update_workflow_state(..., action="start", items=[...])` |
| R44 | Mid-iteration errors roll back the in-flight `MutationBatch` atomically; iteration-child IDs are rejected by `update_workflow_state` and `end_workflow` with the standard child-ID rejection error |
| R45 | `end_workflow` abort on a top-level cascades through iteration children atomically (existing `abort_cascade` infrastructure); `loop_state` is preserved on the soft-deleted parent row as audit trail |

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
- Given the registry loads workflows whose composition graph contains a cycle that mixes `loop` and `composes` edges (e.g., `A` loops `B`, `B` composes `A`; or `A` loops `B`, `B` composes `C`, `C` loops `A`), then every workflow in the cycle is removed from the registry and a warning is logged identifying the cycle's members — `loop` and `composes` edges share the same cycle graph
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
- Given a skill name and workflow name, when `start_workflow` is called, then a new workflow state record is created in the database with a unique ID
- Given `start_workflow` succeeds, when the tool returns, then the result contains: workflow ID, ordered step list with titles, instructions to create tasks via TodoWrite, guidance on how to use update_workflow_state to progress
- Given a workflow already active for the same skill+workflow, when `start_workflow` is called again, then the tool returns an error indicating the workflow is already running (returns the existing ID)
- Given an invalid skill or workflow name, when `start_workflow` is called, then the tool returns an error
- Given a valid skill name but a workflow name that does not exist within that skill, when `start_workflow` is called, then the tool returns an error indicating the workflow was not found

### MCP Tools — update_workflow_state (R1, R4, R5)

**Acceptance Criteria**:
- Given a workflow ID and step identifier with action "start", when `update_workflow_state` is called, then the step is marked as started and the step's instructions (plus references/scripts paths) are returned as the tool result
- Given a workflow ID and step identifier with action "complete", when `update_workflow_state` is called, then the step is marked completed; if a next pending step exists, it is **auto-started** (transitioned to `started`) and its instructions are returned; if it was the last step, the workflow is **auto-finalized** (soft-deleted, scratchpad removed) and the result confirms completion
- Given a workflow ID and step identifier with action "skip", when `update_workflow_state` is called and the step is marked skippable, then the step is marked skipped; if a next pending step exists, it is auto-started and its instructions are returned
- Given a workflow ID and step identifier with action "skip", when `update_workflow_state` is called and the step is NOT marked skippable, then the transition is rejected with an explanation
- Given a workflow where the current step is not yet started, when "complete" is called on it, then the tool returns an error (must start before completing)
- Given an invalid workflow ID, when `update_workflow_state` is called, then the tool returns an error indicating the workflow is not active
- Given a valid workflow ID but an invalid step identifier, when `update_workflow_state` is called, then the tool returns an error listing the valid step identifiers
- Given a completed workflow, when `update_workflow_state` is called, then the tool returns an error indicating the workflow is no longer active
- Given a step whose definition snapshot includes `required_skills`, when the step is activated (either via explicit `action="start"` or via auto-start after `complete`/`skip`), then the tool response includes the transitive skill chain for each declared skill, deps-first with each anchor last, with shared transitive deps emitted exactly once across anchors
- Given a step with no `required_skills` (or a snapshot that predates the field), when the step is activated, then the tool response matches the pre-existing format with no empty skills section or extra whitespace
- Given a step whose `required_skills` includes an unknown skill name, when the step is activated, then the unknown name is silently skipped at resolution and known skills are still included
- Given a step has a `condition`, when the cascade auto-advances to it, then the cascade halts, the step stays `pending`, and the response shows the condition prompt with instructions to start or skip; the agent evaluates the condition and calls `action="start"` to proceed or `action="skip"` to skip
- Given an explicit `action="start"` on a condition step, when the agent has decided to start, then the step starts without condition re-evaluation (trusts the agent's decision)
- Given `action="skip"` on a required step that has a `condition`, when the agent decides to skip, then the step is skipped and the cascade auto-advances (the agent is the condition evaluator)
- Given a parent workflow has spawned an active child, when the agent calls `update_workflow_state(parent_id, <child_step>, <action>)`, then the tool routes the action to the active child layer and applies it
- Given the agent calls `update_workflow_state(parent_id, <step>, <action>)` where `<step>` does not match the deepest active layer's expected step (typo, parent step while a child is active, stale reference), then the tool returns an error naming the deepest layer's workflow and listing its valid step IDs
- Given any activation response while a child is active, when the response is constructed, then it is prefixed with a breadcrumb header showing the active path with `>` as the separator (e.g. `weekly-review/02-handle-inbox > process-inbox-note/01-check`)
- Given a composition step is activated and its condition (if any) passes, then the engine spawns the referenced child workflow and the response describes the child's first activated step (not the composition step's own `instructions.md` body)
- Given a composition step has a `condition` that the agent evaluates as non-passing, then the agent calls `action="skip"` on the required+condition step, no child workflow record is created, and the parent advances to its next step
- Given a composition step has `required: false` and the agent calls `action="skip"`, then the step is skipped, no child workflow record is created, and the parent advances
- Given a composition step is `required: true` (default) and the agent calls `action="skip"`, then the tool returns the standard "step is required" error and no child workflow record is created (no spawn side effect)
- Given a composition step declares `required_skills` (in addition to `composes`), when the step activates and the child spawns, then the declared skills are emitted alongside the child's first step's `required_skills`, deduplicated across both layers
- Given a child is at its last step and the agent completes it via `update_workflow_state(parent_id, <last_child_step>, "complete")`, when the tool processes the call, then in a single tool response the child is auto-finalized (soft-deleted), the parent's composition step is auto-completed, and the parent's next pending step auto-starts — the response is the parent's next step's instructions
- Given a child's last pending step has a `condition`, when the cascade evaluates, then the cascade halts and the agent evaluates the condition; if the agent skips, the step is condition-skipped, the child auto-finalizes, the parent's composition step auto-completes, and the parent's next step auto-starts — identical to the completion path
- Given the cascade reaches the top-level workflow's last step, when the tool processes the call, then the top-level workflow auto-finalizes (soft-deleted, scratchpad removed) and the response confirms top-level completion
- Given a child auto-finalizes and the parent's next step is itself a composition step, when the tool processes the call, then the next child is spawned in the same tool call and the response is its first step's instructions
- Given a cascade is in flight and an error is raised mid-flight (e.g. the composition target is missing), when the error propagates, then no state is written — the child remains active, the parent's composition step is not auto-completed, the parent's `current_step` is unchanged, and the agent can retry
- Given the agent calls `update_workflow_state(<child_id>, ...)` with a child workflow ID, then the tool returns an error directing the agent to operate on the top-level workflow
- Given a loop step is being started, when `update_workflow_state(workflow_id, <loop_step>, "start", items=["a", "b"])` is called, then the items list is persisted on the parent's `loop_state` and iteration 0's child is spawned with the first item as the current item
- Given a loop step is being started without items, when `update_workflow_state(..., "start")` is called, then the tool returns an error indicating items are required for loop steps; the loop step's state remains `pending` and no iteration child is created
- Given a non-loop step is being started with items provided, when `update_workflow_state(..., "start", items=[...])` is called, then the tool returns an error indicating items are not allowed for non-loop steps — the structural step-kind/items consistency check runs before condition handling, so this error surfaces even when the step has a condition
- Given items contains invalid JSON, a non-array JSON value, or non-string array elements, when `update_workflow_state` is called, then a validation error is returned before the handler runs (atomic by construction — the decode layer rejects malformed input before the handler is called)
- Given items contains duplicate strings, when start is called, then duplicates are accepted as-is (the agent decides whether duplication is meaningful)
- Given items validation fails on a start call, when the tool returns the validation error, then no `loop_state` row is written and no iteration child record is created (atomic — no partial state)
- Given a loop step is started with items=["a", "b", "c"], when iteration 0 spawns, then a child workflow record is created with parent_workflow_id pointing at the parent and parent_step_id pointing at the loop step, sharing the parent's scratchpad path; the child runs against the latest registered loop target (per-spawn snapshot)
- Given iteration N's child finalizes, when the cascade pops, then iteration N+1 spawns in the same tool call (single response returns iteration N+1's first step instructions) — the parent's `loop_state[<step>].index` is advanced atomically with the soft-delete of N and the create of N+1
- Given the last iteration's child finalizes, when the cascade pops, then the loop step is marked `completed`, the parent's next pending step auto-starts (or the top-level auto-finalizes if the loop step was the parent's last)
- Given iterations run, when they execute, then they run sequentially in the order the agent supplied (item 0 → item 1 → item 2, no parallelism, no skipping); insertion order is preserved across context loss and recovery
- Given an iteration body's step has `condition`, `required: false`, or `required_skills`, when the iteration runs, then those features behave identically to the same step inside a non-iterated composition child (no special loop-context behavior)
- Given an iteration body contains nested composition (`composes` step) or further loop steps, when the iteration runs, then those features behave identically to top-level composition (no special loop-context behavior)
- Given an iteration child's last step is condition-skipped or `required: false`-skipped, when the cascade pops, then the iteration is treated as finalized and the next iteration spawns (or the loop step completes if the just-finished iteration was the last item)
- Given a loop step is started with items=[], when the engine processes the start, then no child is spawned, the loop step is marked `completed` directly (no STARTED intermediate), `loop_state[<step>] = {items: [], index: 0}` is persisted, and the cascade advances to the parent's next pending step (or auto-finalizes the top-level if the loop was last)
- Given a loop step has `condition` and the condition fails, when start is called (with or without items), then the step is condition-skipped without spawning any iteration; items (if provided) are silently discarded — items are not consulted after a non-passing condition
- Given a loop step has `required: false`, when the agent calls `action="skip"` (no items), then the loop step is skipped, no iterations are spawned, and the cascade advances
- Given a loop step has `required_skills`, when start is called with items, then those skills are resolved and emitted alongside the first iteration's first step's `required_skills` (deduplicated across both layers)
- Given any tool call inside an iteration (start / complete / skip / get_workflow_state), when the response is returned, then the breadcrumb's deepest segment is suffixed with `(item: <value>)` until the iteration's child finalizes
- Given the engine processes an iteration, when each iteration spawns, then the engine does not modify the scratchpad with the current item (scratchpad remains agent-managed)
- Given the previous step completes (or is skipped) and the next pending step is a loop step, when the cascade auto-advances, then auto-start halts at the loop step (the loop step is NOT auto-started); the response indicates the next step is a loop step and instructs the agent to call `update_workflow_state(..., action='start', items=[...])` to begin iterating, or `items=[]` to skip with zero iterations
- Given the halt point is reached, when the response is returned, then the loop step's state remains `pending` (not partially activated)
- Given a workflow whose first step is a loop step, when `start_workflow` is called and the agent attempts to advance, then the first step is not auto-started; the response halts and prompts for items
- Given an iteration's child is active, when the agent calls `update_workflow_state(top_level_id, <iteration_step>, <action>)`, then the action routes to the deepest active iteration child (existing deepest-active routing behavior)
- Given the agent calls `update_workflow_state(<iteration_child_id>, ...)` directly with the child's ID, then the standard child-ID rejection error fires directing the agent to the top-level workflow
- Given a cascade is in flight (e.g., spawning iteration N+1 after N's auto-finalize, or constructing a halt-prompt response) and an error is raised mid-flight, when the error propagates, then the in-flight `MutationBatch` is rolled back, no state is written, and the agent's retry reproduces the same path deterministically

### MCP Tools — get_workflow_state (R16)

**Acceptance Criteria**:
- Given a workflow ID, when `get_workflow_state` is called, then the tool returns: workflow ID, skill name, workflow name, current step, all step states (pending/started/completed/skipped), and created/updated timestamps
- Given an invalid or completed workflow ID, when `get_workflow_state` is called, then the tool returns an error indicating the workflow is not active
- Given the agent loses conversation context (context compaction, session transition), when it calls `get_workflow_state` with a known ID, then the full state is returned enabling resumption from the correct step
- Given an active parent with an active child, when `get_workflow_state(parent_id)` is called, then the response includes the parent's step states (with the composition step shown as `started`), an `### Active Child` section listing the child's workflow name, current step, and step states, and a breadcrumb header showing the active path
- Given the parent has previously completed a composition step whose child has been finalized, when `get_workflow_state(parent_id)` is called, then the completed composition step appears as `completed` with no nested child data inlined
- Given the agent has lost context including the breadcrumb, when it calls `get_workflow_state(parent_id)`, then the response includes the active child path and step states sufficient to re-orient and resume — no separate child ID is required
- Given the agent calls `get_workflow_state(<child_id>)` with a composed child's ID (legacy or persisted), then the response is the child's standalone view with a note recommending access via the parent and the parent's ID
- Given an active composition step's target is no longer registered (skill rename or cycle introduced by hot reload), when `get_workflow_state(parent_id)` is called, then the response prepends a corruption warning identifying the affected step and target, and directing the agent to abort the workflow
- Given a parent has an active loop step, when `get_workflow_state(parent_id)` is called, then the response includes a `### Loop step` block on the parent's loop step listing the items, the current 1-indexed iteration (e.g., `4 / 5`), and the current item; the active iteration child renders identically to a regular composition child (no parallel rendering path)
- Given context loss between iteration N completing and iteration N+1 starting, when the agent calls `get_workflow_state(top_level_id)`, then the response shows the items list, current iteration index, current item, and the active iteration's nested view sufficient to resume

### MCP Tools — end_workflow (R6)

**Acceptance Criteria**:
- Given a workflow ID, when `end_workflow` is called with action "abort", then the workflow state is soft-deleted from the database and the scratchpad file is removed
- Given a workflow ID, when `end_workflow` is called with action "complete", then the workflow state is soft-deleted from the database (also valid, but normal completion is handled via auto-finalize)
- Given an invalid workflow ID, when `end_workflow` is called, then the tool returns an error
- Given a completed or already-ended workflow, when any workflow tool is called with its ID, then the tool returns an error indicating the workflow no longer exists
- Given an active parent with an active child, when `end_workflow(parent_id, "abort")` is called, then both records are soft-deleted atomically and the shared scratchpad file is removed once
- Given an active 3-level stack (parent → child → grandchild), when `end_workflow(parent_id, "abort")` is called, then all three records are soft-deleted in a single atomic operation and the shared scratchpad is removed once
- Given the agent calls `end_workflow(<child_id>, ...)` with a composed child's ID, then the tool returns an error directing the agent to operate on the top-level workflow
- Given a top-level workflow with an active loop step (mid-iteration), when `end_workflow(top_level_id, "abort")` is called, then the cascade soft-deletes the parent record and all in-flight iteration child records atomically (existing `abort_cascade` walks them via `parent_workflow_id`); the shared scratchpad is removed once
- Given the agent calls `end_workflow(<iteration_child_id>, ...)` directly with an iteration child's ID, when the tool runs, then the standard child-ID rejection error fires directing the agent to operate on the top-level workflow
- Given a parent record is soft-deleted as part of an abort, when the record is inspected post-abort, then the `loop_state` column remains intact on the soft-deleted row as part of the audit trail (no separate clear-on-abort step)

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
- Given the child workflow's definition has been modified between parent-start and composition-step activation, when the composition step activates, then the child runs against the latest registered definition (snapshot is per-spawn, not per-parent-start)
- Given a child is spawned, when its `scratchpad_path` is set, then it is the parent's scratchpad path — no new file is created
- Given the agent writes notes during a child's execution, when the parent resumes after the child auto-finalizes, then those notes remain in the parent's scratchpad
- Given the top-level workflow auto-finalizes or is aborted, when cleanup runs, then exactly one scratchpad file is deleted (the top-level's)
- Given a loop step is started with items, when the workflow state is persisted, then the items list and the current iteration index are stored on the parent's `loop_state` JSON column, shaped `{<step_id>: {"items": [...], "index": <i>}}` — the immutable definition snapshot is not mutated
- Given a record's snapshot defines multiple loop steps that have been started, when the column is inspected, then `loop_state` carries one key per started loop step (history retained on completed loop steps; the index reaches `len(items)` when a loop is exhausted)
- Given a nested loop (an iteration child's snapshot itself defines a loop step), when the inner loop runs, then the inner loop's `loop_state` lives on the iteration child's record (the record whose snapshot owns the loop step); the top-level parent's `loop_state` only carries the outer loop's progress
- Given an iteration's child fails or is aborted via `end_workflow`, when the cascade resolves, then the items list and current index remain intact on the parent record — recovery is still possible after the cascade abort scope is resolved

### Agent Task Tracking (R8)

**Acceptance Criteria**:
- Given `start_workflow` returns a step list, when the output is processed by the agent, then it is instructed to create TodoWrite tasks matching each step
- Given a step is started or completed via update_workflow_state, when the agent processes the tool result, then it is instructed to update the corresponding TodoWrite task
- Given the agent does not create TodoWrite tasks, when the workflow tools are called, then the workflow still functions correctly — TodoWrite is advisory guidance, not enforced

### Agent Scratchpad (R9)

**Acceptance Criteria**:
- Given a workflow is active, when the start_workflow output includes scratchpad guidance, then the agent is instructed to Read the scratchpad file first, then use Edit to update it with progress notes
- Given the scratchpad instructions, when the agent transitions between steps, then it is prompted to review and update its scratchpad
- Given the scratchpad is a file in the workspace, when context compaction occurs, then the agent can read the scratchpad file to recover its notes

### Concurrent Workflows (R10)

**Acceptance Criteria**:
- Given two different skills with workflows, when both are started, then each has its own ID and independent state
- Given the same workflow from the same skill already active, when `start_workflow` is called again, then the tool returns an error indicating the workflow is already running (returns the existing ID) — one active instance per skill+workflow name
- Given multiple active workflows, when update_workflow_state is called for one, then only that workflow's state changes
- Given workflow `X` is currently composed inside an active parent, when the agent calls `start_workflow` to start `X` standalone, then the call succeeds — the duplicate-prevention check applies only across other top-level instances
- Given workflow `X` is currently composed inside one active parent, when another parent reaches its own composition step targeting `X`, then a second composed instance is created successfully (composed instances are exempt from the `(skill, workflow)` uniqueness check)
- Given two top-level parents are each composing their own instance of the same child workflow, when each child writes to its scratchpad, then each writes to its own parent's scratchpad — the two parent scratchpads remain isolated and no cross-write occurs

### System Preamble (R13)

**Acceptance Criteria**:
- Given the system preamble is assembled, when a Workflows section is included, then it explains the workflow concept, available MCP tools (start, update, get state, end, list active), and how workflows relate to skills
- Given the system preamble's Workflows section is read, when the agent encounters a nested run, then the preamble documents single-ID driving (always pass the top-level workflow ID), the breadcrumb format with `>` separator, that `list_active_workflows` is top-level-only, that `get_workflow_state` inlines the active child path, and that condition-skipped steps are listed alongside the next activated step
- Given the system preamble's Workflows section is rendered, when the Loops sub-section is read, then it documents the `loop` frontmatter field, the `items` parameter on `start`, current-item exposure in tool responses, auto-completion when items are exhausted, mutual exclusion with `composes`, that items are required for loop steps, and the auto-start halt behavior

### Built-in Workflow Authoring Guide (R14)

**Acceptance Criteria**:
- Given a built-in skill `workflow-authoring-guide` exists, when the registry loads built-in skills, then it is available like the skill-authoring-guide
- Given the workflow-authoring-guide, when the agent reads it, then it provides guidance on creating workflow definitions (directory structure, naming convention for ordering, frontmatter fields including `loop` with at least one paired producer + loop step example, step design patterns, relationship to parent skill resources)

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
