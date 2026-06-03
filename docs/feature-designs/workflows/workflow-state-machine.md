# Design: Workflow State Machine

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/workflows/workflow-state-machine.md](../../feature-specs/workflows/workflow-state-machine.md)
**Relevant ADRs**: ADR-007 (persistence), ADR-008 (system prompt composition)
**Relevant DES**: DES-003 (bootstrap hooks), DES-004 (prompt-driven processors), DES-006 (MCP tool server factory)
**Status**: Current

## Purpose

This document explains the design rationale for the workflow state machine: how workflows are defined within skills, how state is tracked, how the MCP tools enforce the state machine boundary, and how the system integrates with existing Tachikoma infrastructure.

## Problem Context

Skills that define multi-step workflows (morning routines, deployment processes, reading list processing) rely entirely on the LLM to remember which steps are done and what comes next. Without explicit state, the agent skips steps, repeats completed ones, or loses its place after context compaction. The solution uses a file-tree-based approach where workflows are directory structures the agent navigates natively, with MCP tools enforcing state transitions and a database table persisting state across context boundaries.

**Constraints:**
- Workflow definitions must be directory-based, consistent with skill folder patterns
- State transitions must be enforced by MCP tools (not left to LLM discipline)
- State must survive context compaction and process restarts
- Workflows are independent of sessions — they outlive session transitions
- Invalid workflows or steps should not crash the system
- The background task executor already handles SDK session lifecycle, evaluator loops, pause/resume, and concurrency gating
- Workflow-specific behavior should not pollute the generic background task pipeline
- Existing workflow definitions, state persistence, and recovery tools remain unchanged

**Interactions:**
- Skill registry (`skills`): discovers and exposes workflow definitions for lookup by MCP tools
- Coordinator (`core-architecture`): receives workflow MCP tools via `mcp_servers` parameter
- Post-processing pipeline (`post-processing-pipeline`): runs stale cleanup processor in `pre_finalize` phase
- System preamble (`context/loading`): static Workflows section in `SYSTEM_PREAMBLE_TEMPLATE`
- Bootstrap (`__main__.py`): `workflows_hook` initializes the repository
- Task model (`tasks/model.py`): `workflow_id` field on TaskInstance enables subtype discrimination
- Background task executor (`tasks/executor.py`): subtype discrimination for MCP tools and pipeline composition
- Pre-processing pipeline: workflow step context provider injects step prompt
- Post-processing pipeline: workflow failure processor detects failed workflow tasks

## Design Overview

Workflows are optional subdirectories within skills (`workflows/<name>/`), each containing ordered step directories with instructions, references, and scripts. The main session retains three MCP tools (`start_workflow`, `get_workflow_state`, `list_active_workflows`) for initiation and monitoring. Each workflow step runs as a separate background task instance with a fresh SDK session and full step context injected via a workflow-specific pre-processing context provider. Four workflow-specific MCP tools (`complete_step`, `skip_step`, `abort_workflow`, `request_input`) are available only inside workflow step task sessions via a separate MCP server factory. The cascade logic runs inside the step tool handlers — when a step completes, the handler determines the next step and enqueues it as a new background task instance.

Steps communicate via two mechanisms: hand-off messages (captured by `complete_step`, stored on the workflow state, and relayed to the next step's prompt) and the shared scratchpad (for long-term context across all steps). Integration with the background task system is pipeline-based: a workflow step context provider (pre-processing) constructs the step prompt, and a workflow failure processor (post-processing) handles abort cascades. The executor itself only adds subtype discrimination — checking `workflow_id` to conditionally register the workflow step MCP tools and include workflow-specific pipeline processors.

Steps may declare a `condition` (natural-language predicate) that gates activation; when the cascade advances to a condition step, the condition prompt is injected into the step task's initial prompt and the step agent evaluates it inline via `complete_step` (condition passes) or `skip_step` (condition fails). Steps may also declare a `composes` reference to another workflow; activating such a step pauses the parent, runs the referenced child to completion (with full step-level semantics — `condition`, `required`, `required_skills`), then auto-resumes the parent. A third edge type, `loop`, runs the referenced workflow once per item in an agent-supplied list — each iteration is a full composition child, and the engine reuses every existing composition primitive (cycle detection, spawn, snapshot, scratchpad inheritance, abort cascade, atomic mutations) plus a small set of loop-specific extensions (an `items` parameter on `complete_step`, a `loop_state` JSON column on the parent record, an auto-start halt at pending loop steps, and an iteration-advance branch on cascade pop). `loop` and `composes` are mutually exclusive on a single step; their edges share the same cycle/reference graph. From the agent's perspective the run is one continuous workflow: a single ID drives everything, tool responses route to the deepest active layer, and a breadcrumb (`<parent>/<step> > <child>/<step>`, with `(item: <value>)` suffixed on the deepest segment when an iteration is active) identifies the current location in the stack.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Main Session Workflow MCP Tools                           │
│  ┌────────────┐ ┌────────────────┐ ┌──────────────────┐                     │
│  │ start_     │ │ get_           │ │ list_active_     │                     │
│  │ workflow   │ │ workflow_state │ │ workflows        │                     │
│  └─────┬──────┘ └──────┬─────────┘ └────────┬─────────┘                     │
│        │               │                     │                               │
│        ▼               ▼                     ▼                               │
│  ┌──────────────────────────────────────────────────────┐                    │
│  │              WorkflowStateRepository                 │                    │
│  │              (SQLAlchemy async)                      │                    │
│  └──────────────────────────────────────────────────────┘                    │
│                                                                              │
│  start_workflow ──► creates TaskInstance ──► Background Task Runner          │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                    Step Session Workflow MCP Tools                           │
│  ┌──────────────┐ ┌────────────┐ ┌────────────────┐ ┌───────────────┐       │
│  │ complete_    │ │ skip_step  │ │ abort_workflow │ │ request_input │       │
│  │ step         │ │            │ │                │ │               │       │
│  └──────┬───────┘ └─────┬──────┘ └───────┬────────┘ └──────┬────────┘       │
│         │               │                │                  │                │
│         ▼               ▼                ▼                  ▼                │
│  ┌──────────────────────────────────────────────────────────────┐            │
│  │              Cascade Logic (cascade.py)                      │            │
│  │              + WorkflowStateRepository                       │            │
│  └──────────────────────────────────────────────────────────────┘            │
│         │               │                                                   │
│         ▼               ▼                                                   │
│  ┌──────────────┐  ┌───────────────────┐                                    │
│  │ Enqueue next │  │ WorkflowFailure   │                                    │
│  │ TaskInstance │  │ Processor         │                                    │
│  └──────────────┘  └───────────────────┘                                    │
├──────────────────────────────────────────────────────────────────────────────┤
│                    Pre-Processing Pipeline (per step task)                    │
│  ┌──────────────────────────────────────────────────────┐                    │
│  │ WorkflowStepContextProvider                          │                    │
│  │ reads workflow state → resolves step → resolves     │                    │
│  │ skills → reads scratchpad → reads pending_handoff   │                    │
│  │ → constructs step prompt via build_step_prompt()    │                    │
│  └──────────────────────────────────────────────────────┘                    │
├──────────────────────────────────────────────────────────────────────────────┤
│                    Skill Registry & Sources                                  │
│  ┌──────────────────────────────────────────────────────┐                    │
│  │ get_workflow(skill_name, workflow_name)              │                    │
│  │ workflows property → dict[(skill, name), Definition] │                    │
│  └──────────────────────────────────────────────────────┘                    │
│  ┌───────────────────────────────────┐                                       │
│  │ my-skill/                         │                                       │
│  │ ├── SKILL.md                      │                                       │
│  │ └── workflows/                    │                                       │
│  │     └── my-workflow/             │                                       │
│  │         ├── 01-first-step/        │                                       │
│  │         │   ├── instructions.md   │                                       │
│  │         │   ├── references/       │                                       │
│  │         │   └── scripts/          │                                       │
│  │         ├── 02-next-step/         │                                       │
│  │         │   └── instructions.md   │                                       │
│  │         └── 03-final-step/        │                                       │
│  │             └── instructions.md   │                                       │
│  └───────────────────────────────────┘                                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/workflows/definition.py` | `StepDefinition` and `WorkflowDefinition` frozen dataclasses for filesystem-parsed workflow model; includes optional `condition: str \| None` (natural-language predicate injected into step prompt for inline evaluation), `composes: str \| None` (sub-workflow reference), and `loop: str \| None` (target workflow run once per agent-supplied item; mutually exclusive with `composes`) | Follows skill dataclass pattern; uses directory name as step ID |
| `src/tachikoma/workflows/loader.py` | `load_workflows()` — discovers `workflows/` subdirectories within skill dirs, reads step subdirectories sorted alphabetically, parses `instructions.md` frontmatter via python-frontmatter; parses `condition`, `composes`, and `loop` (warn-and-fall-back-to-`None` on type mismatch) | Uses same frontmatter library as skill loading; logs warnings for invalid steps |
| `src/tachikoma/workflows/composition.py` | Pure helpers and in-memory dataclasses for composition: `resolve_composes`, `detect_cycles`, `validate_references`, `_composition_edges` (yields composes-or-loop value per step — single point that unifies the two edge types), `MutationBatch`, `UpdateState` (with optional `loop_state: dict \| None` field), `CreateChild`, `SoftDelete`, `CascadeOutcome` (with optional `halted_at_loop_step` and `condition_prompt` for condition halts) | No SDK / DB / async dependencies — keeps composition logic testable in isolation; shared between registry validation and cascade engine; `_composition_edges` keeps cycle detection and reference validation single-codepath across both edge types |
| `src/tachikoma/workflows/model.py` | `StepState` type alias, `WorkflowState` frozen dataclass (with nullable `loop_state: dict \| None` and nullable `pending_handoff: str \| None`), `WorkflowStateRecord` ORM model on `workflow_states` table (with nullable `pending_handoff` column); `to_domain()` method (decodes `loop_state` JSON when present); JSON serialization helpers; `parent_workflow_id`, `parent_step_id`, `loop_state`, and `pending_handoff` columns; composite `Index("ix_workflow_states_parent", "parent_workflow_id", "deleted_at")` | Follows ADR-007 (SQLAlchemy async + aiosqlite); JSON columns for step_states, definition_snapshot, and loop_state; soft delete via `deleted_at`; non-partial composite parent index serves both top-level filter and active-child lookup |
| `src/tachikoma/workflows/repository.py` | `WorkflowStateRepository` — async CRUD: create, get (non-deleted), get_active (top-level only), update, soft_delete, list_active (top-level only), list_stale (subtree-aware), get_active_chain, get_active_child, abort_cascade, apply_mutation_batch (writes `loop_state` to the parent record when `UpdateState.loop_state is not None`), `update_pending_handoff` (sets/clears the hand-off field on the workflow state) | Application-level duplicate prevention (only enforced for top-level instances per R26); always bumps `updated_at`; cascade methods own one session each via `async with db.begin()` for atomicity (ADR-007); `loop_state` write is conditional on the field being non-None (None means "don't touch the column") |
| `src/tachikoma/workflows/tools.py` | `create_workflow_tools_server()` — MCP server factory with 3 main-session tools (`start_workflow`, `get_workflow_state`, `list_active_workflows`); `start_workflow` creates the workflow state, scratchpad, and enqueues the first step as a pending `TaskInstance` with `workflow_id` set; cascade response formatting for `get_workflow_state` and `list_active_workflows`; `delete_scratchpad` helper shared with failure processor | Follows DES-006 (MCP tool server factory); handlers testable without SDK; cascade computation extracted to `cascade.py` — tools.py handles only response formatting for the main-session tools |
| `src/tachikoma/workflows/cascade.py` | Extracted cascade computation from `tools.py` into reusable pure functions: `run_cascade()` returns a `CascadeResult` (next step info + mutations) instead of formatting tool responses; shared helper functions (`_find_next_step_and_condition`, `_try_spawn_child`, `_render_breadcrumb`, etc.); response formatting stays in `tools.py` for main-session tools | Separation of concerns: cascade computation is independent of response format; shared between old response formatting (tools.py) and new step tool handlers (step_tools.py); pure function is independently testable without SDK/MCP/DB dependencies |
| `src/tachikoma/workflows/step_tools.py` | `create_workflow_step_tools_server()` — MCP server factory producing 4 step-session tools: `complete_step(handoff)`, `skip_step()`, `abort_workflow()`, `request_input(question)`; handlers run cascade logic internally and enqueue next step as `TaskInstance` | Follows DES-006; separate from `tools.py` to keep main-session and step-session tool surfaces distinct; each handler validates input, calls `run_cascade()`, enqueues next step via `TaskRepository`, and returns structured response |
| `src/tachikoma/workflows/step_prompt.py` | `build_step_prompt()` pure function + `WORKFLOW_STEP_SYSTEM_PROMPT` constant; constructs the full step prompt with instructions, resolved required skills, scratchpad path, workflow metadata, hand-off from previous step, and available workflow tools guidance | Extracted from cascade response formatting; shared between context provider (pre-processing injection) and cascade enqueuer (next step's prompt field); workflow step system prompt replaces the generic `BACKGROUND_TASK_SYSTEM_PROMPT` for workflow tasks |
| `src/tachikoma/workflows/step_context.py` | `WorkflowStepContextProvider(ContextProvider)` — pre-processing context provider for workflow step tasks; constructor injection of `TaskInstance`, `WorkflowStateRepository`, `SkillRegistry`; reads workflow state, resolves step, resolves required skills, reads scratchpad, reads and clears `pending_handoff`, constructs step prompt via `build_step_prompt()` | Follows existing context provider pattern; constructor injection same as `SkillsContextProvider(agent_defaults, skill_registry)`; provider replaces placeholder prompt with full step context during `provide()` |
| `src/tachikoma/workflows/failure_processor.py` | `WorkflowFailureProcessor(PostProcessor)` — post-processing processor that detects failed workflow tasks; checks `instance.workflow_id`; calls `abort_cascade` + deletes scratchpad + dispatches failure notification; errors logged and swallowed (post-processing error isolation) | Extends PostProcessor directly; runs in the background task's adapted post-processing pipeline; self-selects by checking `workflow_id`; failure to clean up workflow state does not prevent executor from completing error handling |
| `src/tachikoma/workflows/cleanup.py` | `StaleWorkflowCleanupProcessor(PostProcessor)` — calls `repository.abort_cascade` per stale top-level root for atomic subtree teardown, then deletes the scratchpad file once; runs in `pre_finalize` phase | Extends PostProcessor directly (not PromptDrivenProcessor — no SDK fork needed); subtree-aware staleness via repository |
| `src/tachikoma/workflows/hooks.py` | `workflows_hook` — bootstrap hook: creates repository from shared Database, stores in extras | Follows DES-003 (subsystem-owned bootstrap hooks) |
| `src/tachikoma/skills/registry.py` | Extended with `_workflows` dict, `get_workflow()`, `workflows` property; workflow definitions discovered during `_load_skill()`; `_validate_deps` runs (a) a `loop`/`composes` mutex pre-pass that removes any offending parent workflow before edge collection, then (b) composition cycle detection and reference validation over the unified edge set, then (c) existing depends_on / required_skills checks; rejected workflows are removed from `_workflows` | Extends existing registry without creating a new one; composition validation co-located with skill dependency validation; mutex runs before edge collection so a rejected workflow cannot contribute partial edges to the cycle graph |
| `src/tachikoma/database.py` | Pragma migration adds `parent_workflow_id` and `parent_step_id` columns to `workflow_states` (nullable, no DEFAULT); follow-up migration adds nullable `loop_state TEXT` column for loop iteration progress; follow-up migration adds nullable `pending_handoff TEXT` column for hand-off relay between steps; the composite index is auto-created via `Base.metadata.create_all` | Same `pragma_table_info` + `ALTER TABLE` pattern across all migrations; nullable for backward compatibility |
| `src/tachikoma/context/loading.py` | Static `# Workflows` section in `SYSTEM_PREAMBLE_TEMPLATE` documents single-ID driving, breadcrumb format, top-level filter on `list_active_workflows`, nested view in `get_workflow_state`, condition inline evaluation, and a `## Loops` sub-section covering the `loop` field, `items` parameter, current-item exposure, auto-completion when items are exhausted, mutex with `composes`, items required for loop steps, and the auto-start halt | Follows ADR-008 (system prompt composition via append); the Loops sub-section slots in alongside the existing `## Workflow Composition` sub-section |
| `src/tachikoma/skills/builtin/workflow-authoring-guide/` | Built-in skill with SKILL.md and `references/step-design.md`; documents `composes`, `condition`, `loop`, hand-off, and scratchpad frontmatter fields with worked examples | Follows existing built-in skill pattern |
| `src/tachikoma/tasks/model.py` | `TaskInstance` dataclass extended with `workflow_id: str \| None` field; `TaskInstanceRecord` ORM model with nullable `workflow_id` column | Nullable column; soft reference to `workflow_states.id` (no FK constraint — workflow state may be soft-deleted); NULL = regular background task; non-NULL = workflow step task |
| `src/tachikoma/tasks/repository.py` | `list_expired_waiting_instances` gains optional `workflow_id` filter; sweep calls it twice (regular timeout for non-workflow instances, `workflow_wait_timeout` for workflow instances) | Two calls is simpler than parameterizing the timeout per-row; keeps the query unchanged |
| `src/tachikoma/config.py` | `TaskSettings` extended with `workflow_wait_timeout: int = 604800` (7 days) | Separate timeout from `wait_timeout` because workflow steps waiting for input represent a fundamentally different interaction pattern (long-lived, user-driven cadence) |
| `src/tachikoma/tasks/executor.py` | Subtype wiring: conditionally registers workflow step tools MCP server and includes workflow pipeline providers/processors when `instance.workflow_id` is set; selects `WORKFLOW_STEP_SYSTEM_PROMPT` instead of `BACKGROUND_TASK_SYSTEM_PROMPT` for workflow tasks | Minimal executor changes — subtype check is a single `if` branching on the `workflow_id` field |

### Cross-Layer Contracts

**MCP Tool Schemas (main session):**

```
start_workflow(skill_name: str, workflow_name: str)
  → { workflow_id: str, message: str }
  | { error: str, existing_workflow_id: str }

get_workflow_state(workflow_id: str)
  → { workflow_id, skill_name, workflow_name, current_step, steps: [{id, title, status}], created_at, updated_at }
  | { error: str }

list_active_workflows()
  → { workflows: [{workflow_id, skill_name, workflow_name, current_step, started_at}] }
```

**MCP Tool Schemas (workflow step tools — background session only):**

```
complete_step(handoff: str | None = None)
  → { step_completed: true, workflow_finalized?: bool, message: str }
  | { error: str }

skip_step()
  → { step_skipped: true, workflow_finalized?: bool, message: str }
  | { error: str }

abort_workflow()
  → { workflow_aborted: true, message: str }

request_input(question: str)
  → { notification_sent: true, message: str }
  | { error: str }
```

**Step prompt structure (injected by WorkflowStepContextProvider):**

```
## Workflow Step

You are executing step "{step_title}" ({step_id}) of workflow "{workflow_name}"
from skill "{skill_name}". Step {position} of {total_steps}.

Workflow ID: {workflow_id}
Scratchpad: {scratchpad_path}

### Instructions
{instructions_content}

### Required Skills
{resolved_skills_xml}

### Hand-Off from Previous Step
{handoff_message}

### Available Workflow Tools
- complete_step(handoff="summary"): Complete this step and advance to the next.
  The hand-off message (max 4000 chars) is relayed to the next step's agent.
- skip_step(): Skip this step (only if not required). Advances to next step.
- abort_workflow(): Abort the entire workflow immediately.
- request_input(question): Ask the user a question and wait for response.
  Execution pauses until the user replies.

Read the scratchpad at {scratchpad_path} for accumulated context from previous steps.
```

**Transition validation rules** (enforced by step tool handlers):
- `complete_step`: marks the current step as `completed` in the workflow state; cascade logic runs to determine the next step. If next step exists, a new `TaskInstance` is enqueued with `workflow_id` set and the hand-off is stored as `pending_handoff` on the workflow state. If no next step (workflow complete), the workflow state is soft-deleted and the scratchpad removed. Returns `{ step_completed: true, workflow_finalized?: true }`. Auto-starts next pending step. Hand-off is validated (max 4000 chars, empty string treated as no hand-off).
- `skip_step`: marks the current step as `skipped`; same cascade auto-advance / auto-finalize rules as `complete_step`. For required steps without a `condition`, the skip is rejected.
- `abort_workflow`: triggers the abort cascade (atomic soft-delete of workflow state and all descendants), deletes the scratchpad, returns confirmation.
- `request_input`: validates question is non-empty, calls `send_notification(await_response=true)` using the existing await_response mechanism. The executor transitions the TaskInstance to `waiting`, releases the semaphore. When the user responds via `respond_to_task`, the executor resumes the same SDK session.
- All actions update `updated_at` on the workflow state record.

**Cascade routing rules** (deepest-active):
- The cascade always operates on the top-level workflow ID. The engine reads the active chain via `get_active_chain(top_level_id)` and routes the action to the deepest active layer.
- When the cascade advances to a pending loop step, auto-start halts: the loop step stays `pending` and the step prompt includes guidance to call `complete_step` with items. The cascade computation in `run_cascade()` inspects `next_info["loop"]` after `_find_next_step_and_condition` returns the next pending step; if set, it returns a halt result without queuing a STARTED transition or a spawn. The parent's `current_step` is left at the just-finalized step (deliberate deviation from normal auto-advance).
- Condition steps are evaluated inline: the condition prompt is injected into the step task's initial prompt by the context provider. The step agent evaluates the condition and calls either `complete_step` (condition passes) or `skip_step` (condition fails). There is no cascade halt for conditions — the step agent decides inline.
- Cascade computation is in `cascade.py` (`run_cascade()` returns `CascadeResult`). Response formatting for main-session tools (`get_workflow_state`, `list_active_workflows`) stays in `tools.py`.

**Cascade atomicity**:
- All resulting state changes accumulate in an in-memory `MutationBatch` (`UpdateState`, `CreateChild`, `SoftDelete`).
- Once the cascade loop terminates, `repository.apply_mutation_batch(batch)` applies every queued mutation in a single `async with db.begin():` block. Any raise rolls everything back — partial state never reaches the database.

**Repository surface (composition-aware):**

```
get_active_chain(root_id) -> list[WorkflowState]
  Root-first; empty if root not found / deleted.
  Walks parent_workflow_id back-edges via repeated indexed SELECTs
  (depth bounded by load-time cycle detection).

get_active_child(parent_id) -> WorkflowState | None
  Single SELECT WHERE parent_workflow_id = :parent_id
  AND deleted_at IS NULL.

abort_cascade(root_id) -> list[str]
  Atomic. Inside a single `async with db.begin():` block,
  BFS-walks the descendant set, then issues one bulk
  UPDATE ... WHERE id IN (ids) setting deleted_at.
  Idempotent: returns [] if root is already soft-deleted.
  Orphaned children (root deleted but child still active)
  are NOT walked — that corruption case is handled by stale
  cleanup independently.

apply_mutation_batch(batch: MutationBatch) -> None
  Atomic. Applies UpdateState / CreateChild / SoftDelete in
  the order they were queued, all in one transaction.
  Any raise rolls everything back (R13).

update_pending_handoff(workflow_id: str, handoff: str | None) -> None
  Sets or clears the pending_handoff field on the workflow state.
  Called by complete_step handler to store hand-off for the next step's
  context provider to consume. Cleared after consumption by
  WorkflowStepContextProvider.

# Modified semantics (no signature change):
get_active(skill, workflow)        # adds parent_workflow_id IS NULL
list_active()                      # adds parent_workflow_id IS NULL
list_stale(threshold)              # subtree-aware: returns top-level
                                   # roots whose entire subtree exceeds
                                   # the threshold (early-exit: roots
                                   # with fresh own updated_at skip the
                                   # descendant walk)
```

**Integration Points**:
- `start_workflow` tool handler calls `task_repository.create_instance()` to enqueue the first step as a pending `TaskInstance` with `workflow_id` set and `definition_id=None` (transient)
- Executor's `_run_preprocessing()` includes `WorkflowStepContextProvider` when `workflow_id` is set on the TaskInstance
- Executor's `_run_postprocessing()` includes `WorkflowFailureProcessor` when `workflow_id` is set (registered on failure paths)
- Executor registers `create_workflow_step_tools_server()` in MCP servers when `workflow_id` is set
- `complete_step` handler calls extracted `run_cascade()` + `task_repository.create_instance()` for next step
- `request_input` handler calls `send_notification(await_response=true)` — the existing pause/resume mechanism
- `expired_waiter_sweep` calls `list_expired_waiting_instances` twice: once with regular timeout (filter: `workflow_id IS NULL`), once with `workflow_wait_timeout` (filter: `workflow_id IS NOT NULL`)
- `WorkflowFailureProcessor.process()` calls `workflow_state_repository.abort_cascade()` on failure; errors within the processor are logged and swallowed
- MCP tools call skill registry to resolve skill_name -> workflow definition
- MCP tools call workflow state repository for all DB operations
- Stale cleanup runs as post-processor in `pre_finalize` phase, before GitProcessor in `finalize`
- System preamble static text added to preamble template
- `start_workflow` returns confirmation that workflow is running in background
- Workflow MCP tools registered alongside task MCP tools in coordinator's `mcp_servers` dict
- DB errors surface as MCP tool errors via `{"is_error": true, ...}` (consistent with task tools pattern)

**Mutex pre-pass** (runs first in `SkillRegistry._validate_deps`):

Walks every workflow and every step. If any step declares both `composes` and `loop`, the parent workflow is removed from `_workflows` up-front (logging a malformed-step warning) — *before* edge collection. This guarantees a rejected workflow's other steps cannot contribute partial edges to the cycle graph, mirroring `validate_references`'s "reject the parent on malformed `composes` value" pattern.

**Cycle detection algorithm** (used by `SkillRegistry._validate_deps`):

Three-color DFS over the unified composition graph. Vertices = `(skill_name, workflow_name)` pairs; edges = `composes` edges OR `loop` edges (a step contributes at most one — mutex enforced upstream). Both edge types resolve via the same `resolve_composes` helper and are indistinguishable for cycle purposes — a `loop: A` cycle is rejected by the same DFS that rejects a `composes: A` cycle. A back-edge to a vertex on the current DFS stack identifies a cycle; the slice of the stack from that vertex is recorded as one detected cycle (not a full Tarjan SCC — the algorithm extracts cycles directly from the DFS stack on back-edge encounters). Self-loops are detected on the first DFS step. One warning per detected cycle; every vertex in the cycle is removed from `_workflows`.

**Reference validation algorithm** (after cycle detection):

Fixed-point iteration over `_workflows`. For each non-rejected workflow, walk its composition steps and resolve each `composes` or `loop` value (whichever is set) via `resolve_composes(value, parent_skill)`; the helper does not care which frontmatter key carried the value. Reject the parent if the value is malformed (raises `ValueError`), the target is absent from `_workflows`, the target has zero steps, or the target is itself rejected. The loop repeats while any rejection was added in the previous pass; cascading rejections converge after at most `depth` passes because the graph is acyclic post-cycle-removal. Each rejection logs one warning identifying the parent, the underlying cause, and which edge type (`composes` or `loop`) carried the rejected value.

**Required-skill expansion at activation** (applied in both the step context provider and the step prompt builder):
- `_resolve_required_skills(step_info, skill_registry)` reads `step_info["required_skills"]` from the step's snapshot entry
- Each declared anchor is expanded via `SkillRegistry.resolve_chain(name)` (deps-first, anchor-last, cycle-tolerant, unknown-tolerant, memoized)
- A `KeyError` from `resolve_chain` (anchor name not registered) is swallowed with a debug log — matching the silent-skip posture at resolution time
- A cross-anchor `seen: set[str]` dedups shared transitive deps so each skill's `<skill name="X" directory="...">...</skill>` block is emitted exactly once per activation
- When no skills resolve (no declarations, or all anchors unknown), the helper returns an empty string

### Shared Logic

- **Workflow definition model**: Parsed step structure (id, title, instructions path, references, scripts, frontmatter properties) shared between loader and MCP tools
- **Step state enum**: `pending | started | completed | skipped` — shared between state model and transition validation
- **Transition validation**: Central logic in step tool handlers, enforcing step ordering and frontmatter properties
- **Cascade computation** (`cascade.py`): Pure function `run_cascade()` extracted from `tools.py`. Takes workflow state + step transition, returns `CascadeResult` (next step info + `MutationBatch`). No side effects — callers apply mutations and enqueue tasks.
- **Step prompt construction** (`step_prompt.py`): Pure function `build_step_prompt()`. Takes step definition, resolved skills, scratchpad path, workflow metadata, and optional hand-off. Returns formatted prompt string. Shared between the context provider (pre-processing injection) and the cascade enqueuer (next step's prompt field on the TaskInstance).
- **Hand-off validation**: Shared validation in `step_tools.py` — max 4000 chars, empty string treated as no hand-off.

## Modeling

```
WorkflowDefinition (filesystem):
  skill_name: str
  workflow_name: str
  steps: [StepDefinition]

StepDefinition (filesystem):
  id: str (directory name, e.g., "01-plan")
  title: str (from frontmatter)
  instructions_path: Path
  references_path: Path | None
  scripts_path: Path | None
  required: bool (default true; skippable steps set required: false)
  required_skills: tuple[str, ...] (declared skills resolved via the registry at activation;
                                    list of strings or warn-and-fall-back-to-empty)
  condition: str | None (natural-language predicate injected into the step prompt
                         for inline evaluation by the step agent)
  composes: str | None (raw frontmatter value: "<workflow>" same-skill or
                        "<skill>/<workflow>" cross-skill; resolved at registry
                        validation time and at spawn time)
  loop: str | None (same syntax as composes; mutually exclusive with composes;
                    iterates the target workflow once per agent-supplied item)
  properties: dict (extensible frontmatter fields; declared field names excluded)

WorkflowState (database — table: `workflow_states`):
  id: str (UUID, PK)
  skill_name: str (Indexed)
  workflow_name: str (Indexed)
  parent_workflow_id: str | None (set on composed children; NULL on top-level)
  parent_step_id: str | None (the parent's composition step ID; set together with parent_workflow_id)
  current_step: str | None
  step_states: JSON ({"01-plan": "completed", "02-execute": "started", ...})
  definition_snapshot: JSON (serialized step definitions; for top-level workflows
                             captured at start time; for composed children captured
                             at child-spawn time)
  scratchpad_path: str (absolute path, e.g., ".tachikoma/scratchpads/workflow-abc123.md";
                        children inherit the parent's path)
  loop_state: dict | None (nullable JSON column shaped {<step_id>: {items, index}};
                           set on any record whose own snapshot defines a loop step
                           that has been started; multiple keys when a record has
                           multiple loop steps that have run. For nested loops,
                           the inner loop's state lives on the outer iteration
                           child's record — not on the top-level parent — because
                           loop_state always lives on the record whose snapshot
                           owns the loop step)
  pending_handoff: str | None (nullable TEXT column; set by complete_step handler
                                with the hand-off message; read by
                                WorkflowStepContextProvider when constructing
                                the next step's prompt; cleared after consumption)
  deleted_at: datetime | None (NULL = active, set on soft delete)
  created_at: datetime
  updated_at: datetime

  Indexes:
    ix_workflow_states_skill_name        (skill_name)
    ix_workflow_states_workflow_name     (workflow_name)
    ix_workflow_states_active_lookup     (skill_name, workflow_name)
    ix_workflow_states_parent            (parent_workflow_id, deleted_at)

  Invariants (application-level):
    - At most one active row per (skill_name, workflow_name) where
      parent_workflow_id IS NULL — top-level uniqueness; composed
      children are exempt (R26).
    - At most one active child per parent — enforced by the cascade
      spawn loop. No DB-level partial unique index is added (consistent
      with the codebase's no-partial-index convention). If the invariant
      is violated through corruption, get_active_child returns the first
      match and the situation is treated as recoverable via abort+restart.
    - parent_workflow_id and parent_step_id are set together (both NULL
      or both non-NULL).
    - All records in a connected component share the same scratchpad_path.

  Duplicate prevention: application-level check via get_active() before create,
                        gated on parent_workflow_id IS NULL (top-level only).

CascadeOutcome (in-memory, ephemeral):
  deepest_layer_id: str
  active_step_id: str | None     (None when top-level finalized)
  condition_skips: list[(workflow_name, step_id, reason)]
  finalized_top_level: bool
  halted_at_loop_step: str | None  (step_id of loop step that halted auto-advance)
  halted_at_condition_step: str | None  (step_id of condition step that halted auto-advance)
  condition_prompt: str | None  (condition text surfaced to agent)

MutationBatch (in-memory, ephemeral):
  ordered: list[Mutation]
  Mutation = UpdateState(layer_id, step_states, current_step,
                         loop_state: dict | None = None)
                # loop_state: None means "do not touch the column";
                # non-None dict is written verbatim to the row on apply.
           | CreateChild(child_id, parent_id, parent_step_id,
                         skill_name, workflow_name, step_states,
                         definition_snapshot, scratchpad_path)
           | SoftDelete(layer_id)

CascadeResult (in-memory, ephemeral — from extracted cascade computation):
  outcome: CascadeOutcome          (reuses the existing dataclass from composition.py;
                                     carries deepest_layer_id, active_step_id,
                                     condition_skips, finalized_top_level,
                                     halted_at_loop_step, halted_at_condition_step,
                                     condition_prompt)
  mutations: MutationBatch         (atomic DB mutations — same type used today)
  next_step_id: str | None         (None = workflow finalized or halted at condition/loop)
  next_step_info: dict | None      (step definition snapshot entry for the next step)
  handoff_for_next: str | None     (captured from current step's complete_step;
                                     stored as pending_handoff on WorkflowState)

WorkflowStepContext (in-memory, ephemeral — built by context provider):
  workflow_id: str
  skill_name: str
  workflow_name: str
  step_id: str
  step_title: str
  step_position: int           (1-indexed position within the workflow)
  total_steps: int
  instructions: str            (from instructions.md body)
  resolved_skills: str         (XML-tagged skill content)
  scratchpad_path: str
  handoff: str | None          (from previous step's complete_step)

TaskInstance (extended — new field; defined in tasks/model.py):
  ... (existing fields)
  workflow_id: str | None      (FK-soft → workflow_states.id; nullable;
                                 NULL = regular background task;
                                 non-NULL = workflow step task;
                                 all steps of a workflow — including composition
                                 children and loop iterations — share the
                                 top-level workflow ID)
```

The composition graph is a transient in-memory structure built only during `_validate_deps`:

```
CompositionGraph (in-memory, transient):
  vertices: set[(skill_name, workflow_name)]
  edges: dict[vertex, list[(vertex, edge_type)]]
                # edge_type in {"composes", "loop"}; cycle detection
                # treats them uniformly. Mutex enforced before edge
                # collection so no vertex contributes both kinds.
```

## Data Flow

### Workflow start and first step execution

```
1. Main agent calls start_workflow(skill_name, workflow_name)
2. start_workflow handler:
   a. Validates skill/workflow exist (unchanged)
   b. Checks no active instance (unchanged)
   c. Creates WorkflowState record with definition snapshot (unchanged)
   d. Creates scratchpad file (unchanged)
   e. Determines first step from snapshot
   f. Creates TaskInstance(
        status="pending",
        prompt=<step_id placeholder>,
        workflow_id=<workflow_state.id>,
        definition_id=None,  # transient — no TaskDefinition
        task_type="background"
      )
   g. Returns workflow ID + confirmation that workflow is running in background
3. Background task runner picks up the pending TaskInstance
4. Executor detects workflow_id → includes WorkflowStepContextProvider + WorkflowFailureProcessor
5. WorkflowStepContextProvider reads workflow state, resolves first step,
   resolves required skills, reads scratchpad, constructs step prompt
6. Executor runs SDK session with step prompt as initial query + workflow step tools registered
7. Step agent executes the step, calls complete_step(handoff="summary of work done")
8. complete_step handler runs cascade, enqueues next step as new TaskInstance
9. Repeat until complete_step finalizes the workflow (no next step)
```

### Step cascade and enqueuing

```
1. Step agent calls complete_step(handoff="...")
2. complete_step handler:
   a. Validates handoff length (<= 4000 chars)
   b. Reads workflow state from DB
   c. Marks current step completed in step_states
   d. Calls run_cascade() to determine next step
   e. If next step exists:
      - Creates new TaskInstance(
          status="pending",
          prompt=<next_step_id placeholder>,
          workflow_id=<same workflow_id>,
          definition_id=None,
          task_type="background"
        )
      - Stores handoff on the workflow state for the context provider to pick up
   f. If no next step (workflow complete):
      - Soft-deletes workflow state
      - Deletes scratchpad file
   g. Returns confirmation to the step agent
3. Background task runner picks up the new TaskInstance on its next tick
4. WorkflowStepContextProvider constructs the next step's prompt, including handoff
```

### User input request (request_input)

```
1. Step agent calls request_input(question="Which option?")
2. request_input handler:
   a. Validates question is non-empty
   b. Calls send_notification(await_response=true, message=question)
      — This is the existing await_response mechanism
   c. The notification handler sets cycle_state.await_response_requested
   d. Returns confirmation to the step agent
3. Executor detects await_response_requested:
   a. Marks TaskInstance as waiting with sdk_session_id
   b. Returns (releases semaphore slot)
4. User responds via respond_to_task in main conversation
5. On next runner tick, executor resumes the same SDK session with user's response
   — The step agent continues in the same SDK session
```

### Failure propagation

```
1. Background task executor detects step task failure (stuck/error/max iterations)
2. Executor marks TaskInstance as failed
3. WorkflowFailureProcessor runs in post-processing:
   a. Detects instance has workflow_id AND status == "failed"
   b. Calls workflow_state_repository.abort_cascade(workflow_id)
      — Atomic soft-delete of workflow state and all descendants
   c. Deletes scratchpad file
   d. Dispatches failure notification describing which step failed
4. No further step tasks are enqueued — the workflow is dead
```

### Workflow wait timeout sweep

```
1. expired_waiter_sweep runs on central scheduler tick
2. Two calls to repository:
   a. list_expired_waiting_instances(wait_timeout) — regular instances
      (filter: workflow_id IS NULL)
   b. list_expired_waiting_instances(workflow_wait_timeout) — workflow instances
      (filter: workflow_id IS NOT NULL)
3. Each set of expired instances is failed with appropriate timeout notification
4. Workflow failure processor handles the abort cascade for workflow instances
```

### Load-time validation

```
1. SkillRegistry._discover() loads all skills + their workflow definitions.
2. SkillRegistry._validate_deps() runs:
   2a. Build the composition graph: for each workflow, for each step
       with a `composes` field, resolve the target via resolve_composes
       and add an edge.
   2b. Run cycle detection (three-color DFS); collect detected cycles.
   2c. Remove every cycle member from _workflows. Log one warning per
       detected cycle listing its members.
   2d. Run reference validation (target exists, has >=1 step, not
       already rejected, composes value parses). Iterate to fixed-point
       so cascading rejections converge.
   2e. Remove rejected references' parents from _workflows. Log warnings
       identifying parent + cause.
   2f. Existing depends_on / required_skills validation runs as before.
3. After _validate_deps, _workflows contains only valid composable workflows.
```

### Nested workflow execution

```
Step agent for parent calls complete_step(handoff="...")
  -> cascade: chain = get_active_chain(parent_id) = [parent]
  -> deepest = parent; step "01-plan" matches; transition validates.
  -> Cascade loop:
       mark "01-plan" completed in mutable_ss[parent].
       _find_next_step_and_condition finds next pending: "02-handle-inbox" (composes set).
       mark "02-handle-inbox" started in mutable_ss[parent]; queue
         UpdateState(parent, ..., current_step="02-handle-inbox").
       _try_spawn_child resolves the composes target; allocates child_id;
         queues CreateChild(...) inheriting parent.scratchpad_path.
       descend into child; child's first pending step "01-check"
         (no condition) is marked started.
       (the in-memory CreateChild already includes the started state,
        so no separate UpdateState mutation is needed for the child)
       cascade terminates (non-composition step started in child).
  -> MutationBatch:
      [UpdateState(parent, step_states={..."01-plan":COMPLETED,
                                         "02-handle-inbox":STARTED},
                   current_step="02-handle-inbox"),
       CreateChild(child_id, parent_id, "02-handle-inbox",
                   skill, "process-inbox-note",
                   {"01-check":STARTED, ...}, snapshot,
                   parent.scratchpad_path)]
  -> apply_mutation_batch commits everything atomically.
  -> Enqueue new TaskInstance for child's first step.
  -> Response to step agent: step completed, next step enqueued.
```

### Cascade up to top-level (child auto-finalizes on parent's last step)

```
Step agent for child's last step calls complete_step()
  -> cascade: chain = [parent, child]; deepest = child; step matches.
  -> Cascade loop:
       mark child's <child_last_step> completed.
       _find_next_step_and_condition on child returns None (no pending steps).
       queue UpdateState(child, ...) + SoftDelete(child); pop layer.
       parent's composition step (parent_step_id) auto-completes
         in mutable_ss[parent].
       _find_next_step_and_condition on parent returns None (parent's last step).
       queue UpdateState(parent, ...) + SoftDelete(parent);
         set finalized_top_level=True.
  -> MutationBatch: 4 mutations applied atomically.
  -> After commit: scratchpad file deleted (idempotent unlink).
  -> No new TaskInstance enqueued — workflow is complete.
  -> Response: { step_completed: true, workflow_finalized: true }
```

### Abort cascade

```
Step agent calls abort_workflow()
  -> abort_workflow handler:
       reads workflow state; calls abort_cascade(workflow_id) — atomic.
         BFS-walks descendants inside the transaction (each iteration
         issues a SELECT against the open session); collects all IDs;
         issues one UPDATE WHERE id IN (...) setting deleted_at.
       after commit: deletes the shared scratchpad once.
       dispatches notification describing abort.
  -> Response confirms abort with a count of records cleaned up.
```

### Stale subtree cleanup

```
StaleWorkflowCleanupProcessor.process:
  stale_roots = repository.list_stale(threshold)
    # Top-level roots whose own updated_at exceeds the cutoff are
    # candidates; for each, walk get_active_chain and compute
    # MAX(updated_at) across the subtree. Only return roots whose
    # entire subtree exceeds the cutoff.
  for root in stale_roots:
    abort_cascade(root.id)         # atomic
    delete scratchpad once
```

### Loop iteration (start with items, advance, exhaust)

```
Step agent reaches a pending loop step (cascade halts with loop step info).
The step prompt includes guidance for the loop step.

Step agent calls complete_step(handoff="...", items=["a","b","c"])  # items handled internally by cascade
  -> Actually: loop step is the next step enqueued. When step agent for the
     previous step calls complete_step, cascade finds the loop step and halts.
     The next TaskInstance prompt includes the loop step's instructions.
     The step agent evaluates the loop context and provides items.

In practice, the cascade handles loop items internally:
  -> When cascade encounters a loop step during auto-advance:
     - If items are provided (via the step prompt or context), spawn iteration 0
     - Otherwise, halt and let the step agent decide
  -> Loop step halt: the step prompt includes the loop step's instructions
     and guidance about the loop. The step agent works through the loop items.

When iteration N's last step completes:
  Step agent calls complete_step()
  -> cascade: chain = [parent, child_N]; deepest = child_N; step matches.
  -> Cascade loop:
       mark child's last step COMPLETED.
       _find_next_step_and_condition on child returns None (no pending steps).
       queue UpdateState(child_N, current_step=None) + SoftDelete(child_N); pop.
       parent's just-completed step is "03-process" (a loop step).
       read loop_state["03-process"].index = N; advance to N+1.
       items[N+1] exists ("b"):
         _try_spawn_child(loop_target, current_item="b") -> child_id_N+1.
         queue UpdateState(parent, ..., loop_state={"03-process": {items, index: N+1}}).
         queue CreateChild(child_id_N+1, ..., scratchpad_path=parent.scratchpad_path).
         descend into child_N+1; first step STARTED; cascade terminates.
  -> apply_mutation_batch commits everything atomically (single SQLite tx).
  -> Enqueue new TaskInstance for child_N+1's first step.

When the last item exhausts:
  -> Cascade pop: child_last finalizes. parent's "03-process" exhausts
     (index advances to len(items)); queue
       UpdateState(parent, step_states={..., "03-process": COMPLETED},
                   loop_state={"03-process": {items, index: len(items)}}).
     fall through to existing cascade-up logic; advance parent's next
     pending step OR auto-finalize the top-level if "03-process" was last.
```

### Empty items list (auto-complete with zero iterations)

```
Cascade encounters loop step with empty items:
  -> The "spawn or exhaust" decision is centralised: index 0 vs.
     len(items)=0 -> exhausted on first check.
  -> queue UpdateState(parent,
                        step_states={..., "03-process": COMPLETED},
                        loop_state={"03-process": {items: [], index: 0}}).
  -> _find_next_step_and_condition on parent: next pending step OR finalize.
  -> MutationBatch: 1-3 mutations (no CreateChild).
  -> Cascade advances to parent's next step or finalizes top-level.
```

### Auto-start halt at a pending loop step

```
Step agent for previous step calls complete_step()
  -> Cascade marks previous step COMPLETED.
  -> _find_next_step_and_condition returns next pending step "03-process" (loop set).
  -> Halt branch fires: do NOT mark STARTED, do NOT spawn anything.
       queue UpdateState(parent, step_states={..., "02-prepare": COMPLETED},
                         current_step="02-prepare").  (loop_state untouched)
  -> apply_mutation_batch commits the partial state ("02-prepare" only).
  -> Returns halt result: next step is a loop step.
  -> Next TaskInstance enqueued for the loop step with its instructions.
     The step prompt includes loop guidance.
  -> The loop step's state stays `pending`. Re-evaluation on the next
     cascade run reproduces the halt deterministically.
```

### Recovery flow (context loss)

```
1. Main agent loses workflow ID (context compaction, session corruption)
2. Main agent reads scratchpad file -> recovers workflow ID
   OR (scratchpad also lost):
3. Main agent calls list_active_workflows() -> recovers workflow ID
   (only top-level workflows surface; nested children are hidden —
   iteration children behave identically to composition children).
4. Main agent calls get_workflow_state(id) -> full state for monitoring.
   For a parent with an active child, the response inlines the
   active child path under "### Active Child" with a breadcrumb
   header. For a parent with an active loop step, the response also
   inlines a "### Loop step" block listing the items, current
   1-indexed iteration, and current item.
```

## Key Decisions

### File tree as workflow definition

**Choice**: Workflows mapped to directory structures the agent navigates natively
**Why**: Resilient to model capability changes — if a model can handle multiple steps in one go, steps can be condensed without changing the system
**Alternatives Considered**:
- YAML manifest with step definitions: More structured but creates a parallel format the agent must learn
- Database-only definitions: Loses the filesystem alignment that makes skills intuitive

**Consequences**:
- Pro: Agent already knows how to navigate directories; aligns with skill folder pattern
- Pro: Steps are self-contained with their own references and scripts
- Con: Ordering relies on naming convention (alphabetical sort with numeric prefix)

### MCP tools as state machine boundary

**Choice**: All state transitions go through MCP tools that validate and enforce rules. The main session has initiation/monitoring tools; step sessions have step-driving tools.
**Why**: Prevents the agent from corrupting state; tools enforce transitions at the boundary. Splitting tools between main session and step session keeps each surface focused.

**Consequences**:
- Pro: Strong state guarantees regardless of agent capability
- Pro: Tool results naturally guide the agent to the next action
- Con: Agent must call tools correctly (but tool errors provide clear guidance)

### Definition snapshot at start time

**Choice**: Workflow definition is serialized when started and stored with the state record
**Why**: If a skill's workflow definition changes while a workflow is active, the active workflow uses the original definition — preventing mid-flight inconsistencies

**Consequences**:
- Pro: Active workflows are insulated from definition changes
- Con: Definition snapshot increases DB record size (mitigated by small step definitions)

### Database table for state (not filesystem)

**Choice**: Workflow state persisted in a SQLAlchemy database table
**Why**: Consistent with existing persistence pattern (ADR-007), supports concurrent workflows, queryable, and benefits from existing database infrastructure

**Consequences**:
- Pro: Follows established patterns, no new persistence mechanism
- Pro: Atomic updates, concurrent access safety

### Workflows outlive sessions

**Choice**: Workflow state records have no `session_id` field — workflows are independent of sessions
**Why**: Workflows may span multiple message exchanges within a session and should persist across session transitions. Coupling workflows to sessions would make cleanup harder as session-scoped cleanup could destroy active workflows prematurely.

**Consequences**:
- Pro: Workflows survive session transitions naturally
- Pro: No coordination needed between session management and workflow management
- Con: `list_active_workflows` may return workflows from previous sessions (agent must correlate by context)

### Post-processor for stale cleanup

**Choice**: Stale workflow cleanup runs as a post-processor in the `pre_finalize` phase, before GitProcessor commits
**Why**: Session close already triggers post-processing, so cleanup piggybacks on existing lifecycle without additional infrastructure. Running in `pre_finalize` ensures cleanup is committed atomically with session changes.

**Consequences**:
- Pro: No additional infrastructure — reuses existing post-processing pipeline
- Pro: Cleanup committed atomically with session changes
- Con: Cleanup frequency tied to session close rate (acceptable for 24h threshold)

### Soft delete over hard delete

**Choice**: `abort_workflow`, stale cleanup, and auto-finalization set `deleted_at` on the DB record instead of removing it
**Why**: Soft delete preserves the audit trail — you can inspect past workflow runs for debugging. The unique constraint only applies to non-deleted records, so a new workflow of the same name can be started after the previous one ends.

**Consequences**:
- Pro: Audit trail preserved for debugging and analysis
- Pro: Unique constraint naturally allows re-use after soft delete

### list_active_workflows as recovery safety net

**Choice**: A dedicated tool returns all active workflow IDs and metadata
**Why**: The primary recovery mechanism is the agent persisting the workflow ID in its scratchpad. But if the scratchpad is also lost, the agent needs a way to discover active workflows without knowing any IDs.

**Consequences**:
- Pro: Simple safety net — one tool call recovers all active workflow IDs
- Pro: Keeps system preamble static (ADR-008)

### Step-declared required skills injected via step prompt

**Choice**: Steps declare their required skills via a `required_skills: [names]` frontmatter field. The `WorkflowStepContextProvider` resolves each declared skill's transitive chain through `SkillRegistry.resolve_chain` and includes the resolved skill bodies in the step prompt — bypassing the skill classifier. Skills are NOT persisted as `SessionContextEntry`.
**Why**: The classifier infers relevant skills from the user message and can miss foundations a step silently relies on (git operations, API clients, domain knowledge) when instructions are terse. Declarative activation guarantees the required skills are present at the moment the step begins executing. Reusing `resolve_chain` means deps-first ordering, cycle tolerance, unknown-dep tolerance, and memoization are all inherited rather than reimplemented in the workflow subsystem.

**Consequences**:
- Pro: Reliable foundation loading at step activation — independent of classifier phrasing sensitivity
- Pro: Zero new traversal code in `workflows/` — reuses the existing skill dependency resolver
- Pro: Unknown-dep tolerance and cross-anchor dedup inherited from the resolver
- Pro: Load-time validation lives in `SkillRegistry._validate_deps`, co-located with skill `depends_on` validation
- Con: Skills injected via the step prompt do not become `SessionContextEntry` records (classification still can pick them up on subsequent messages)

### Auto-start and auto-finalize on complete/skip

**Choice**: Completing or skipping a step automatically starts the next pending step. When all steps are done, the workflow is automatically finalized (soft-deleted, scratchpad cleaned up).
**Why**: The agent had to make redundant calls after every step completion. Auto-start and auto-finalize eliminate these friction points, making the workflow progression naturally linear: `complete_step` (auto-enqueues next) -> `complete_step` (auto-enqueues next) -> `complete_step` (auto-finalizes).

**Consequences**:
- Pro: One tool call per step suffices
- Pro: No separate finalization call for normal completion
- Con: Auto-start bypasses `validate_transition` (safe: `_find_next_pending_step` guarantees the step is pending)

### Stage in memory, single atomic commit (cascade atomicity)

**Choice**: State changes accumulate in an in-memory `MutationBatch`. One `async with db.begin():` block applies everything atomically at the end of the cascade.
**Why**: Holds the SQLite write lock only during the actual write phase.

**Consequences**:
- Pro: Lock-hold-time matches existing single-row updates (~ms).
- Pro: Adding new mutation types is uniform — every new transition slots into the same batch+commit pipeline.
- Pro: Cascade rollback on mid-flight error is automatic (the batch is dropped, nothing was committed).
- Con: Slight in-memory bookkeeping overhead per call (~5-10 mutations max); negligible.

### Python-driven chain walk (vs. recursive CTE)

**Choice**: `get_active_chain(root_id)` reads the root via `get`, then iteratively `SELECT WHERE parent_workflow_id = current.id AND deleted_at IS NULL` until no child is found. Stale cleanup uses iterative widening per top-level root.
**Why**: Depth is bounded by load-time cycle detection (real workflows nest 1-3 levels). 2-5 sequential indexed queries cost sub-millisecond each on aiosqlite. The codebase has no existing recursive-CTE usage — introducing one would be a one-off pattern with invisible perf gain against LLM latency.

**Consequences**:
- Pro: Matches the existing repository style (every method is a single `select()` with simple WHERE clauses).
- Pro: Each layer's `WorkflowState` is fully read into the chain — useful for breadcrumb rendering and the nested view without further queries.
- Con: 2-5 round-trips per cascade (acceptable on a local indexed file DB).

### Composite (non-partial) parent index

**Choice**: `Index("ix_workflow_states_parent", "parent_workflow_id", "deleted_at")` — no partial-index predicate.
**Why**: SQLite uses leading-column prefixes, so a single composite index serves both `parent_workflow_id IS NULL AND deleted_at IS NULL` (top-level filter) and `parent_workflow_id = X AND deleted_at IS NULL` (active-child lookup). No other model in the codebase uses partial indexes; consistency wins over micro-optimization.

**Consequences**:
- Pro: One index serves two query patterns.
- Pro: Consistent with existing index conventions in `workflows/model.py`.
- Con: Tiny space overhead from including soft-deleted rows in the index (negligible).

### Repository-owned cascade methods

**Choice**: New `WorkflowStateRepository.abort_cascade` and `apply_mutation_batch` methods own one session each, opening their own `async with db.begin():` blocks. No `AsyncSession` is exposed to handlers.
**Why**: ADR-007 mandates that ORM types do not escape the persistence layer. A transactional context manager that yielded an `AsyncSession` to handlers would leak that type and force handlers to know SQLAlchemy idioms.

**Consequences**:
- Pro: ADR-007 compliance preserved; cascade complexity stays in the repository where it can be unit-tested without the SDK.
- Pro: Handlers receive frozen dataclasses (or typed mutation lists) and never see SQLAlchemy.
- Con: Two new repository methods accept argument bags shaped by the cascade engine (mitigated by the typed `MutationBatch` and the small surface).

### Single-ID driving for the agent

**Choice**: The main agent always passes the top-level workflow ID. Internally the cascade walks to the deepest active layer.
**Why**: Asking the agent to track child IDs adds error-prone bookkeeping that's unnecessary given the structural invariant of one active child per parent at any time.

**Consequences**:
- Pro: Agent's mental model stays simple — one ID per logical workflow, regardless of nesting depth.
- Pro: `get_workflow_state` retains a backwards-compat path for child IDs.

### Shared scratchpad inheritance

**Choice**: When a child is spawned, its `scratchpad_path` is set to the parent's path. The scratchpad file is created and removed once at the top-level lifecycle.
**Why**: A parent and its composed children are conceptually one continuous run; notes the agent takes during the child's execution belong in the same file. Two parents running independent compositions of the same child workflow remain isolated because each has its own parent scratchpad.

**Consequences**:
- Pro: One file, one lifecycle, one cleanup point — covers normal finalization, abort, and stale cleanup uniformly.
- Pro: Two top-level parents composing the same child stay isolated.
- Con: A naive reader might expect the scratchpad to be named after the active child workflow during the child's execution; documented in the authoring guide.

### Create-then-soft-delete in same transaction (empty children)

**Choice**: When a child workflow's only step (or all steps) condition-skip on first activation, the child record is still created with its definition snapshot, then immediately queued for soft-delete in the same `MutationBatch`. Both writes commit atomically.
**Why**: Condition evaluation is dynamic, so we cannot statically predict which child steps will skip without running them. Creating uniformly and letting the cascade loop drive the rest keeps the spawn path single-codepath.

**Consequences**:
- Pro: One spawn path; the cascade loop handles all outcomes uniformly.
- Pro: Snapshot-at-spawn-time semantics preserved even for short-lived children.

### Loop iteration as a second edge type in the existing composition graph

**Choice**: `loop` and `composes` are sibling frontmatter fields on the same step model; cycle detection, reference validation, spawn, snapshot-at-spawn, scratchpad inheritance, deepest-active routing, abort cascade, and atomic mutation are all reused.
**Why**: The composition machinery already encodes "spawn a child, run it, auto-resume parent". An iteration is the same mechanism applied N times with a different `current_item`. Building a parallel pipeline would duplicate cycle detection, snapshot logic, atomicity, routing, and rendering.

**Consequences**:
- Pro: One spawn path, one cycle detector, one cascade engine. Bug fixes on either feature benefit both.
- Pro: Cross-edge cycles (`A` loops `B`, `B` composes `A`) fall out of the unified graph naturally.
- Con: The cascade engine's pop branch grows a new conditional. Mitigated by keeping the loop-specific logic localized.

### `loop_state` as a JSON column on the parent record (not a separate table)

**Choice**: Items list and current iteration index live on the parent's `workflow_states` row in a new nullable `loop_state` JSON column shaped `{<step_id>: {items: list[str], index: int}}`.
**Why**: Loop progress is intrinsic to the parent's loop step, not to the iteration child. The parent's state record is the durable home. One read returns everything needed for the parent's view; no JOIN.

**Consequences**:
- Pro: One read returns everything; no JOIN.
- Pro: Schema migration is a single nullable column add.
- Pro: `loop_state` survives soft-delete intact (audit trail).
- Con: A loop step with a very large items list embeds it in the parent row. Acceptable: realistic batch sizes are dozens, not thousands.

### Auto-start halts at pending loop steps (cascade does not infer items)

**Choice**: When the cascade auto-advance reaches a pending loop step, it returns the loop step as the next pending step but does *not* mark it `STARTED` and does *not* spawn any iteration.
**Why**: The engine cannot fabricate items. Auto-starting with an empty list would silently skip the loop step. The right default is to halt and require explicit input.

**Consequences**:
- Pro: No silent skips; the step agent always sees loop guidance.
- Pro: The loop step's state stays `PENDING` — no half-activated state.
- Pro: Idempotent retries.

### `UpdateState` extended with optional `loop_state` (vs. a new mutation type)

**Choice**: The existing `UpdateState` mutation dataclass gains an optional `loop_state: dict | None` field. `apply_mutation_batch` writes the column when set. No new mutation type is introduced.
**Why**: `UpdateState` already targets a single workflow row; adding another field-on-that-row keeps the mutation count constant and the transaction shape unchanged.

**Consequences**:
- Pro: Same mutation type covers both step transitions and loop-progress updates.
- Pro: One transaction, one atomic write, one rollback boundary.

### Pipeline-based integration over executor modification

**Choice**: Workflow-specific behavior is implemented as pre-processing context providers and post-processing processors that self-select based on `workflow_id`. The executor only adds a subtype check to conditionally include them.
**Why**: Minimizes changes to the generic background task pipeline. The pipeline pattern already supports this — providers and processors self-determine relevance. The executor's total changes are: (1) check `workflow_id` to decide which MCP tools to register, (2) include workflow providers/processors in the pipeline when `workflow_id` is set.

**Alternatives Considered**:
- Executor-side hooks: Add workflow-specific logic directly in the executor after failure detection. Simpler but couples the executor to workflow concerns.
- Separate executor subclass: Create `WorkflowTaskExecutor(BackgroundTaskExecutor)`. Clean separation but duplicates the entire executor for a few conditional branches.

**Consequences**:
- Pro: Executor stays generic — workflow is a pluggable extension
- Pro: Follows the established pipeline/provider/processor pattern (DES-003, DES-004)
- Pro: Other subtypes can follow the same pattern without executor changes
- Con: Two new pipeline participants to maintain (mitigated by their self-selecting nature)

### Cascade computation extraction

**Choice**: Extract the cascade computation from `tools.py._run_cascade()` into a pure function in `cascade.py` that returns a `CascadeResult` (which wraps the existing `CascadeOutcome` + `MutationBatch` + next step info + handoff) without side effects. Both the main-session response formatting (tools.py) and the step tool handlers (step_tools.py) use the extracted function. `CascadeResult` does not replace `CascadeOutcome` — it composes it as a field, preserving the existing cascade engine's internal data structures.
**Why**: The original `_run_cascade()` was tightly coupled to tool response formatting. For the background task model, we need the cascade computation but with different output (enqueue TaskInstance instead of return tool response). Extraction keeps one cascade engine with two presentation layers.

**Alternatives Considered**:
- Duplicate cascade logic in step tools: Would work but creates a maintenance risk — cascade bug fixes must land in two places.
- Keep cascade in tools.py, add a "background mode" flag: Conflates two different execution contexts in one function, making both paths harder to reason about.

**Consequences**:
- Pro: One cascade engine, two consumers — bug fixes benefit both
- Pro: Pure function is independently testable without SDK/MCP/DB dependencies
- Pro: The main-session response formatting becomes a thin wrapper
- Con: Extraction was a non-trivial refactor (mitigated by keeping the same cascade semantics)

### Hand-off stored on workflow state (not on TaskInstance)

**Choice**: The hand-off message from `complete_step` is stored on the `WorkflowState` record via the `pending_handoff` field. The `WorkflowStepContextProvider` reads it when constructing the next step's prompt, then clears it.
**Why**: The hand-off is a transient bridge between two consecutive steps — it belongs to the workflow's coordination state, not to the task instance. Storing it on the workflow state means the context provider (which already reads the workflow state) has access to it naturally.

**Alternatives Considered**:
- Store hand-off on TaskInstance: Would work but requires a new field on the model and a lookup to the previous instance's ID.
- Store hand-off in scratchpad file: The agent can already use the scratchpad for this, but making it automatic via a structured field provides a cleaner contract.

**Consequences**:
- Pro: Context provider already reads workflow state — no additional lookup
- Pro: Automatic cleanup (cleared after consumption)
- Con: One new nullable column on `workflow_states` (minimal migration)

### request_input reuses await_response mechanism

**Choice**: `request_input` internally calls `send_notification(await_response=true)`, which sets `cycle_state.await_response_requested` and dispatches a respondable urgent notification. The executor's existing await_response handling (transition to `waiting`, resume on response) works unchanged.
**Why**: The existing `await_response` mechanism already handles everything: pause/resume, semaphore release, SDK session preservation, crash recovery, timeout sweep. `request_input` is a domain-specific veneer over the same mechanism.

**Alternatives Considered**:
- New pause mechanism specific to workflows: Would duplicate all the await_response infrastructure for no benefit.

**Consequences**:
- Pro: Zero new pause/resume code — fully inherited from background task infrastructure
- Pro: Crash recovery, timeout sweep, and resume semantics work identically
- Pro: The `respond_to_task` tool in the main session handles workflow step responses too

### Step prompt as pre-processing injection via constructor-injected provider

**Choice**: The step prompt (instructions, skills, metadata, hand-off) is constructed by a `WorkflowStepContextProvider` that runs as part of the pre-processing pipeline. The provider receives the `TaskInstance`, `WorkflowStateRepository`, and `SkillRegistry` via constructor injection (the executor creates it with these dependencies when `workflow_id` is set). The TaskInstance's `prompt` field stores a lightweight identifier (the step ID), and the context provider replaces it with the full step prompt.
**Why**: This follows the existing pre-processing pattern where context providers enrich the prompt before the SDK session starts. Constructor injection is the same pattern used by `SkillsContextProvider(agent_defaults, skill_registry)`. The step prompt is constructed from live state (workflow snapshot, scratchpad content) which may differ between steps of the same workflow.

**Alternatives Considered**:
- Store full step prompt on TaskInstance at creation time: Works but means the prompt is frozen at enqueue time. If the scratchpad changes between enqueue and execution, the prompt would be stale.
- Construct step prompt inside the executor: Would couple the executor to workflow concerns, violating the pipeline-based integration approach.

**Consequences**:
- Pro: Step prompt reflects current state at execution time (fresh scratchpad read)
- Pro: Executor remains unaware of workflow prompt construction
- Pro: Provider pattern is well-established in the codebase
- Con: One additional context provider in the pipeline (only instantiated for workflow tasks; zero overhead for non-workflow tasks)

### Workflow step tools as a separate MCP server factory

**Choice**: A new `create_workflow_step_tools_server()` factory in `workflows/step_tools.py`, distinct from the existing `create_workflow_tools_server()` in `workflows/tools.py`. Registered only for instances with `workflow_id`.
**Why**: The main-session factory and the step-session factory serve different surfaces. The main session keeps `start_workflow`, `get_workflow_state`, `list_active_workflows`. The step session gets `complete_step`, `skip_step`, `abort_workflow`, `request_input`. Keeping them as separate factories means each has a focused closure with only the dependencies it needs.

**Alternatives Considered**:
- Single factory with conditional tool registration: Would require the factory to receive both sets of dependencies and decide which tools to include. Less clear separation.

**Consequences**:
- Pro: Each factory has a focused dependency set
- Pro: Main-session and step-session tool surfaces evolve independently
- Pro: Follows the existing pattern (task-tools has two factory calls too)

### Transient TaskInstances for workflow steps (definition_id=None)

**Choice**: Workflow step TaskInstances are created with `definition_id=None` — they are transient, like ad-hoc `run_task_now` instances. Each step is a one-shot execution.
**Why**: Workflow steps don't correspond to recurring task definitions. Each step fires exactly once as part of its workflow's cascade. Reusing the transient instance pattern avoids creating throwaway definitions that would pollute `list_tasks`.

**Consequences**:
- Pro: No definition-table pollution
- Pro: Reuses existing transient instance handling
- Pro: `list_tasks` stays clean — workflow step instances excluded by filtering `workflow_id IS NOT NULL`
- Con: The executor's `_resolve_source` fallback uses `prompt[:100]` for notification source — the step prompt placeholder should be meaningful

### Expired waiter sweep with two calls (not per-row timeout parameterization)

**Choice**: The expired waiter sweep calls `list_expired_waiting_instances` twice — once with the regular `wait_timeout` for non-workflow instances, once with `workflow_wait_timeout` for workflow instances. Each call filters on `workflow_id IS NULL` / `workflow_id IS NOT NULL`.
**Why**: The repository query is a simple `WHERE updated_at < now - timeout`. Parameterizing the timeout per-row (using a CASE expression) would make the query harder to read for negligible performance gain on a local SQLite database.

**Alternatives Considered**:
- Per-row timeout via SQL CASE: Works but obscures the intent.
- Store timeout on the TaskInstance: Over-engineering for two timeout values.

**Consequences**:
- Pro: Each query is simple and testable in isolation
- Pro: Adding a third timeout category is another filter + call, not a CASE expansion
- Con: Two queries instead of one (negligible on local SQLite)

### Workflow step system prompt (not the generic BACKGROUND_TASK_SYSTEM_PROMPT)

**Choice**: Workflow step tasks use a tailored system prompt (`WORKFLOW_STEP_SYSTEM_PROMPT` constant in `workflows/step_prompt.py`) that replaces the generic `BACKGROUND_TASK_SYSTEM_PROMPT`. The executor selects it via a conditional on `workflow_id`.
**Why**: The generic system prompt explains `send_notification` and `run_task_now` — concepts that are confusing in a workflow step context. The workflow step agent needs to know about `complete_step`, `skip_step`, `abort_workflow`, and `request_input`. A tailored prompt reduces agent confusion and improves step completion reliability.

**Consequences**:
- Pro: Step agent gets focused guidance matching its actual tool surface
- Pro: Removes irrelevant guidance (task scheduling, arbitrary notifications)
- Pro: Can be tuned independently of the generic background task prompt
- Pro: Selection is a single `if` in the executor — no new abstraction
- Con: One more system prompt to maintain (mitigated by it being specific and short)

## System Behavior

### Scenario: Start workflow and execute first step

**Given**: A skill with a workflow definition
**When**: The main agent calls `start_workflow(skill_name, workflow_name)`
**Then**: A WorkflowState record is created with definition snapshot, a scratchpad file is created, and a pending TaskInstance is created with `workflow_id` set. The tool returns the workflow ID and confirms the workflow is running in the background. The background task runner picks up the instance, the WorkflowStepContextProvider constructs the first step's prompt, and the step agent executes with workflow step tools available.

### Scenario: Step completes with hand-off to next step

**Given**: A workflow step task agent has completed its work
**When**: The agent calls `complete_step(handoff="Processed 3 items, results in scratchpad")`
**Then**: The hand-off is validated (max 4000 chars), the step is marked completed in the workflow state, cascade logic runs to find the next step, a new pending TaskInstance is created for the next step (same `workflow_id`), and the hand-off is stored on the workflow state for the next step's context provider to inject.

### Scenario: Step completes without hand-off

**Given**: A workflow step task agent calls `complete_step()` with no hand-off
**Then**: The step is marked completed, cascade runs, and the next step's context provider does not include a hand-off section. The next step agent starts without context from the previous step (except what's in the scratchpad).

### Scenario: Step completes last step — workflow finalizes

**Given**: A workflow's last step task agent calls `complete_step()`
**When**: The cascade finds no next pending step
**Then**: The workflow state is soft-deleted, the scratchpad file is removed, and `complete_step` returns `{ step_completed: true, workflow_finalized: true }`.

### Scenario: Step requests user input

**Given**: A workflow step task agent needs user input
**When**: The agent calls `request_input(question="Which database should I use?")`
**Then**: The tool internally calls `send_notification(await_response=true, message=question)`, which dispatches a respondable urgent notification and sets `cycle_state.await_response_requested`. The executor detects this, transitions the TaskInstance to `waiting` with the SDK session ID, and returns (releasing the semaphore). When the user responds via `respond_to_task`, the executor resumes the same SDK session with the response.

### Scenario: Step fails — abort cascade

**Given**: A workflow step task fails (evaluator detects stuck, error, or max iterations)
**When**: The executor marks the instance as failed
**Then**: The `WorkflowFailureProcessor` detects the failed workflow task during post-processing, calls `abort_cascade(workflow_id)` which atomically soft-deletes the workflow state and all descendants, deletes the scratchpad, and dispatches a failure notification describing which step failed and why.

### Scenario: Agent explicitly aborts workflow

**Given**: A workflow step task agent calls `abort_workflow()`
**When**: The tool handler processes the call
**Then**: The current step's TaskInstance is marked failed, the workflow abort cascade triggers (identical to automatic failure propagation), and the tool returns confirmation.

### Scenario: Agent completes without calling any workflow tool

**Given**: A workflow step task agent finishes its work but doesn't call `complete_step`, `skip_step`, or `abort_workflow`
**When**: The evaluator loop reaches max iterations
**Then**: The instance is marked failed, the `WorkflowFailureProcessor` triggers the abort cascade, and a failure notification is dispatched.

### Scenario: Workflow wait timeout expires

**Given**: A workflow step task in `waiting` state whose `updated_at` is older than `workflow_wait_timeout` (default 7 days)
**When**: The expired waiter sweep runs
**Then**: The TaskInstance is marked failed with a timeout reason, a non-respondable urgent notification is dispatched, and the `WorkflowFailureProcessor` triggers the abort cascade.

### Scenario: Process restarts while step is waiting

**Given**: A `waiting` workflow step TaskInstance with a stored `sdk_session_id`
**When**: The process restarts and the runner comes back up
**Then**: Crash recovery does not touch `waiting` rows. The waiting instance persists; the expired waiter sweep catches any that exceeded `workflow_wait_timeout` during downtime, and unresponded ones continue waiting.

### Scenario: Composition step activates child workflow

**Given**: A workflow step with `composes: "child-workflow"` completes via `complete_step`
**When**: The cascade encounters the composition step
**Then**: The child workflow's first step is enqueued as a new TaskInstance with the same `workflow_id` (top-level workflow ID). All steps in the workflow — parent steps, composition children, and loop iterations — share the top-level ID. This is consistent with the existing single-ID driving model where the cascade engine internally walks to the deepest active layer. When the child's last step completes, the cascade pops back to the parent and enqueues the parent's next step. This preserves the existing composition semantics.

### Scenario: Loop step spawns iterations

**Given**: A workflow step with `loop: "item-processor"` is the next pending step
**When**: The step's TaskInstance executes and the cascade encounters the loop step
**Then**: The cascade halts at the loop step. A TaskInstance is enqueued for the loop step with loop guidance in its prompt. The step agent works through the loop, calling `complete_step` after each item. The cascade advances the loop state and spawns the next iteration as a new TaskInstance. When all items are exhausted, the loop step completes and the cascade continues to the parent's next step.

### Scenario: Condition step evaluates inline

**Given**: A workflow step with a `condition` predicate is the next pending step
**When**: The step's context provider injects the condition prompt into the step prompt
**Then**: The step agent evaluates the condition and calls either `complete_step` (condition passes) or `skip_step` (condition fails). The cascade continues accordingly. This replaces the previous halt-and-return-to-main-session pattern with inline evaluation inside the step task.

### Scenario: Duplicate workflow prevention

**Given**: An active workflow for skill "morning-routine" workflow "morning-routine"
**When**: `start_workflow("morning-routine", "morning-routine")` is called again
**Then**: The tool returns an error with the existing workflow ID, indicating the workflow is already running.

### Scenario: Recovery via get_workflow_state and list_active_workflows

**Given**: An active workflow running as background tasks
**When**: The main agent calls `list_active_workflows()` or `get_workflow_state(id)`
**Then**: The response format is identical to the current implementation — step states, composition nesting, loop state. The tools remain available in the main session.

### Scenario: Concurrent workflow step and regular task

**Given**: A workflow step TaskInstance and a regular background TaskInstance are both pending
**When**: The runner picks up both
**Then**: Both compete for semaphore slots equally. The workflow step task has the same concurrency semantics as a regular background task — no priority or special treatment.

### Scenario: Workflow resumption after context compaction

**Given**: An active workflow with ID "abc-123" at step "03-review"
**When**: Context compaction occurs and the main agent loses the workflow context
**Then**: The main agent reads its scratchpad file to recover the workflow ID, calls `get_workflow_state("abc-123")`, receives the full state. The workflow continues autonomously in the background — the main agent can monitor but does not drive steps.
**Rationale**: State lives in the database, not conversation memory. The scratchpad file survives context compaction because it's on disk. Step execution is decoupled from the main session.

### Scenario: Full context loss recovery

**Given**: An active workflow where both the conversation context and scratchpad are lost
**When**: The main agent needs to check on the workflow but has no workflow ID
**Then**: The main agent calls `list_active_workflows()`, identifies the relevant workflow, and calls `get_workflow_state(id)` to check status. The workflow continues running in the background regardless.

### Scenario: Attempting to skip a non-skippable step

**Given**: An active workflow where step "02-execute" has `required: true` in its frontmatter and no condition
**When**: The step agent calls `skip_step()`
**Then**: The tool returns an error explaining the step cannot be skipped

### Scenario: Stale workflow cleanup

**Given**: A workflow state record with `updated_at` 25 hours ago (default threshold: 24h)
**When**: The post-processor runs during session close (pre_finalize phase)
**Then**: The record is soft-deleted (`deleted_at` set) and its scratchpad file is deleted before the git commit

### Scenario: Definition changes during active workflow

**Given**: An active workflow started from a 3-step definition
**When**: The skill author modifies the workflow to have 4 steps
**Then**: The active workflow continues with the original 3-step definition (from snapshot)

### Scenario: Two parents composing the same child concurrently

**Given**: `weekly-review` is active and has just spawned a child instance of `process-inbox-note`. Independently, `daily-review` is started and reaches its own composition step that also references `process-inbox-note`.
**When**: `daily-review`'s composition step activates and tries to spawn its own child.
**Then**: The spawn succeeds. The duplicate-prevention check on `get_active(skill, "process-inbox-note")` filters on `parent_workflow_id IS NULL`, so the existing composed instance (whose `parent_workflow_id` is `weekly-review`'s ID) is invisible. Two child records exist concurrently — one parented by `weekly-review`, one by `daily-review` — and each writes to its own parent's scratchpad.
**Rationale**: Composition is logically scoped — the same child workflow can run multiple times in parallel as long as each instance is contained inside a different parent context. Top-level uniqueness is unchanged.

### Scenario: Child auto-finalizes on a condition-skipped last step

**Given**: A child workflow's last step has `condition: "scratchpad has unprocessed items"`.
**When**: The cascade reaches that point.
**Then**: The condition prompt is injected into the step task's prompt. The step agent evaluates the condition and calls `skip_step` (non-passing) or `complete_step` (passing). If skipped, the cascade sees no more pending steps in the child, queues `SoftDelete(child)`, pops to the parent, auto-completes the parent's composition step, and advances into the parent's next pending step.

### Scenario: Cycle introduced by hot skill reload

**Given**: A skill is hot-reloaded with new workflow definitions that introduce a cycle (`A` composes `B`, `B` composes `A`).
**When**: `SkillRegistry.refresh()` runs.
**Then**: `_validate_deps` rejects both `A` and `B`; both are removed from `_workflows` and a cycle warning is logged. If a workflow with a now-invalid composition reference was already running (its `definition_snapshot` still references the deleted target), `get_workflow_state(parent_id)` prepends a corruption warning identifying the affected step and the missing target, and any subsequent cascade that tries to spawn into the missing target returns a corruption error directing abort.

### Scenario: Definition changes between parent-start and composition-step-activate

**Given**: A skill author modifies `process-inbox-note` (adds a new step) between the moment `weekly-review` started and the moment `weekly-review` reaches its composition step.
**When**: The composition step activates.
**Then**: The child is spawned with the *current* (modified) `process-inbox-note` definition snapshot. The parent's snapshot (captured at parent-start) is not consulted for the child.
**Rationale**: Snapshot-at-spawn captures the latest registered definition naturally. Snapshotting at parent-start would force the parent to embed every transitively-referenced child definition, blowing up record size.

### Scenario: Cascade error mid-flight

**Given**: A cascade is in flight; an unexpected exception is raised while building the `MutationBatch` (e.g. composition target is missing).
**When**: The cascade loop is building the `MutationBatch`.
**Then**: The exception propagates out of the cascade loop before any commit. `apply_mutation_batch` is never called. No state is written. The handler catches the exception and returns an error response. The child remains active, the parent's composition step remains `started`, and the parent's `current_step` is unchanged. The step agent can retry.

### Scenario: Routing error with a typo'd step ID

**Given**: A child of `weekly-review` is active at step `02-categorize`. The cascade processes a step.
**When**: The step ID doesn't match the expected step in the active layer.
**Then**: The cascade identifies the deepest layer and finds no match. The tool returns an error naming the active workflow and listing the valid step IDs — the step agent doesn't need to know which workflow is active.

### Scenario: Deep nesting (3 levels)

**Given**: A non-cyclic graph `A -> B -> C` composes to depth 3 at runtime. A step in `C` is active.
**When**: Steps advance through every level.
**Then**: `get_active_chain(parent_id)` returns `[A, B, C]`. The cascade always targets `C` until `C` finalizes. When `C`'s last step completes, the cascade pops to `B`, auto-completes `B`'s composition step, and advances `B`. If `B`'s next step is also a composition (spawning a sibling `D`), the cascade descends into `D` in the same call. The breadcrumb reflects the full active path.
**Rationale**: Load-time cycle detection bounds depth statically; runtime never encounters infinite recursion.

### Scenario: Stale subtree cleanup

**Given**: A parent's `updated_at` is 30 hours old (past the 24h threshold). Its active child's `updated_at` is 10 minutes old.
**When**: `StaleWorkflowCleanupProcessor.process` runs.
**Then**: `repository.list_stale(threshold)` walks each candidate top-level root and computes the subtree max `updated_at` — for this parent, the child's value (10 minutes ago). The subtree is NOT stale. The parent is not abort-cascaded. The active child keeps the parent alive.
**Rationale**: Staleness is a connected-component property, not a per-record property. An active child indicates work is ongoing.

### Scenario: First-time start of a loop step with non-empty items

**Given**: Parent is at step `02-prepare` STARTED. Step `03-process` declares `loop: process-item` and is `PENDING`.
**When**: The step agent for `02-prepare` calls `complete_step()` and then the cascade encounters the loop step.
**Then**: After the first call, the cascade marks `02-prepare` COMPLETED, advances, finds `03-process` is a loop step, halts auto-start. A TaskInstance is enqueued for the loop step with its instructions. The step agent evaluates the loop context, provides items, and the cascade spawns iteration 0 with `current_item` set.
**Rationale**: Auto-start cannot fabricate items; halting is the correct default. Once the step agent provides items, iteration 0 spawns through the same `_try_spawn_child` path used by composition.

### Scenario: Iteration N+1 spawns in the same cascade as iteration N's finalize

**Given**: Parent has `loop_state["03-process"] = {items: ["a","b","c"], index: 1}`; iteration child for item `b` is at its last step.
**When**: Step agent calls `complete_step()`.
**Then**: Cascade pop SoftDeletes child_1, advances `loop_state["03-process"].index` to 2, spawns child_2 with `current_item="c"`, and enqueues a new TaskInstance for child_2's first step.
**Rationale**: Spawn-in-same-cascade keeps the progression linear — one `complete_step` per iteration, no manual restart.

### Scenario: Empty items auto-completes the loop step

**Given**: Loop step `03-process` is `PENDING`. The cascade encounters it with empty items.
**When**: The cascade runs.
**Then**: `03-process` transitions PENDING -> COMPLETED in one atomic batch (no STARTED intermediate), `loop_state["03-process"] = {items: [], index: 0}` is persisted (audit trail), no iteration child is created, and the cascade auto-advances to the parent's next pending step (or finalizes the top-level if `03-process` was last).
**Rationale**: Empty items is a valid expression of "no items to process" — the engine treats it as zero-iteration completion, not an error.

### Scenario: Items-on-non-loop-step rejected

**Given**: Step `01-plan` is a regular (non-loop) step with a `condition` that would evaluate non-passing.
**When**: Items are provided for a non-loop step.
**Then**: Validation rejects with "items not allowed for non-loop steps" before condition evaluation runs. No state change. Step stays `PENDING`.
**Rationale**: Splitting items validation into a structural gate (before condition) and a semantic gate (after condition) means non-loop-step + items errors surface even when the step's condition would have skipped the step anyway.

### Scenario: Loop + composes mutex rejected at registry load

**Given**: A workflow declares a step with both `loop: a` and `composes: b`.
**When**: The registry validates dependencies.
**Then**: The mutex pre-pass rejects the parent workflow with a malformed-step warning identifying `loop` and `composes` as conflicting fields. The workflow is removed from `_workflows` *before* edge collection runs, so cycle/reference DFS never sees its edges.
**Rationale**: Mutex check runs once before edge collection, mirroring `validate_references`'s "reject the parent on malformed `composes` value" pattern.

### Scenario: Cycle introduced via cross-edge chain

**Given**: Workflow `A` has `loop: B`; `B` has `composes: C`; `C` has `loop: A`.
**When**: The registry validates dependencies.
**Then**: `detect_cycles` finds the cycle `{A, B, C}` over the unified edge set (loop and composes treated identically). All three workflows are removed; one cycle warning is logged listing the members.
**Rationale**: Loop edges and composes edges share the same cycle graph, so chains mixing both edge types are rejected by the same DFS.

### Scenario: Recovery after context loss mid-iteration

**Given**: An active loop on `03-process` with items=[a,b,c,d,e]. `loop_state["03-process"].index = 3` (the iteration for `items[3] = "d"` is active). The main agent loses context.
**When**: The main agent calls `list_active_workflows` then `get_workflow_state(parent_id)`.
**Then**: `list_active_workflows` returns only the parent (iteration child is hidden). `get_workflow_state` returns the parent's state with a `### Loop step` block showing `Items (5): a, b, c, d, e`, `Current iteration: 4 / 5` (1-indexed display of the underlying 0-indexed `index = 3`), `Current item: d`, plus the active iteration child block, plus the breadcrumb. The main agent has everything needed to monitor progress. The workflow continues autonomously in the background.
**Rationale**: State lives on the parent's `loop_state` column; nothing is in conversation memory.

### Scenario: Nested loops — inner loop_state lives on the outer iteration child

**Given**: Loop target `process-batch` has a step `02-each` declaring `loop: process-item`. Outer loop on top-level parent runs with `items=[A, B]`; the inner loop on the outer iteration child runs with its own per-iteration items.
**When**: Outer iteration 0 (item `A`) reaches step `02-each`, the cascade encounters the inner loop step.
**Then**: The cascade halts at the inner loop step. The step agent provides items and the cascade spawns inner iterations. Inner iterations run as grandchildren of the top-level parent. The inner loop's `loop_state` lives on the **outer iteration child's record** (whose snapshot defines `02-each`); the top-level parent's `loop_state` only carries the outer loop's progress.
**Rationale**: `loop_state` always lives on the record whose snapshot owns the loop step. Each layer carries its own runtime state — no cross-record coordination required.

### Scenario: Abort cascade with active iteration

**Given**: Top-level parent is active with iteration child for item `b` mid-flight (`loop_state["03-process"].index = 1`).
**When**: Step agent calls `abort_workflow()`.
**Then**: `abort_cascade` BFS-walks descendants via `parent_workflow_id`, soft-deletes parent + iteration child in one transaction, deletes the shared scratchpad once. The parent's `loop_state` column remains intact on the soft-deleted row (audit trail).
**Rationale**: Iteration children are regular composition children; `abort_cascade` handles them by structure, not by special-casing.

## Notes

- Workflow steps run as background tasks — each step gets a fresh SDK session with full step context injected. The main session only initiates and monitors workflows.
- The scratchpad concept is prompting-based — the step agent is instructed to maintain notes in a file, not through a dedicated MCP tool
- `list_active_workflows` is intentionally simple (no filtering, no pagination) — it's a recovery/monitoring tool, not a management tool
- Hand-off messages are transient bridges between consecutive steps — they are stored on the workflow state (`pending_handoff`), consumed by the next step's context provider, then cleared
- Scratchpad file location convention: `.tachikoma/scratchpads/workflow-<workflow_id>.md`
- Loop progress is durable; iteration children are ephemeral. The parent's `loop_state` column is the recovery anchor — if every iteration child were soft-deleted (e.g., abort cascade) the items list and index would still be readable on the soft-deleted parent row for audit
- The `loop_state` column key is the loop step's ID **on the record whose snapshot defines that step**. A record with two loop steps can carry two keys simultaneously (one active, one historical) without conflict. Nested loops live on different records — outer on top-level, inner on the outer iteration child
- Empty-string items (`items=["", "foo"]`) are accepted — the breadcrumb suffix renders as `(item: )` for the empty entry. By design: items are opaque references and the iteration body decides what they mean. Authors who want to reject empty items can do so with a `condition` on the iteration body's first step
- Audit-trail retention for `loop_state`: soft-deleted parent rows retain their `loop_state` payload intact. Stale-cleanup (24h threshold) eventually soft-deletes idle workflows but does not touch already-soft-deleted rows
- `request_input` is a thin wrapper around `send_notification(await_response=true)` — it validates the question is non-empty and provides a domain-specific tool name, but delegates entirely to the existing pause/resume infrastructure
- The `WorkflowStepContextProvider` receives `TaskInstance`, `WorkflowStateRepository`, and `SkillRegistry` via constructor injection — the executor creates it when `workflow_id` is set
- The `WorkflowFailureProcessor` receives `TaskInstance`, `WorkflowStateRepository`, and `EventBus` via constructor injection — same pattern as existing processors
- The cascade extraction from `tools.py` into `cascade.py` separates computation from response formatting — both the main-session tools and the step-session tools share the same cascade engine
- Workflow step tasks inherit all background task infrastructure: crash recovery, concurrency gating, stderr accumulation, pre/post processing, git commit. No duplication of these concerns
- `WorkflowFailureProcessor` errors are logged and swallowed — matching the post-processing pipeline's error-isolation semantics. A failure to clean up the workflow state should not prevent the executor from completing its own error handling
- The `pending_handoff` column on `workflow_states` required a schema migration via the existing pragma-based `ALTER TABLE` pattern. The field is nullable for backward compatibility
- All workflow step TaskInstances (parent steps, composition children, loop iterations) share the top-level `workflow_id` — consistent with the existing single-ID driving model where the cascade engine walks to the deepest active layer internally
- `list_tasks` excludes workflow step instances by filtering `workflow_id IS NOT NULL` — they don't pollute the user-facing task list
- Workflow step tasks use `definition_id=None` (transient instances) — they don't correspond to recurring task definitions and are one-shot executions
