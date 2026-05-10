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

**Interactions:**
- Skill registry (`skills`): discovers and exposes workflow definitions for lookup by MCP tools
- Coordinator (`core-architecture`): receives workflow MCP tools via `mcp_servers` parameter
- Post-processing pipeline (`post-processing-pipeline`): runs stale cleanup processor in `pre_finalize` phase
- System preamble (`context/loading`): static Workflows section in `SYSTEM_PREAMBLE_TEMPLATE`
- Bootstrap (`__main__.py`): `workflows_hook` initializes the repository

## Design Overview

Workflows are optional subdirectories within skills (`workflows/<name>/`), each containing ordered step directories with instructions, references, and scripts. Five MCP tools (`start_workflow`, `update_workflow_state`, `get_workflow_state`, `end_workflow`, `list_active_workflows`) form the state machine boundary — the agent cannot change workflow state except through these tools, which validate all transitions. State is persisted in a database table, making it resilient to context compaction and process restarts. Stale workflow cleanup runs as a post-processor in the `pre_finalize` phase, committed alongside session changes.

Steps may declare a `condition` (natural-language predicate) that gates activation; non-passing results auto-skip the step and the cascade advances to the next pending step. Steps may also declare a `composes` reference to another workflow; activating such a step pauses the parent, runs the referenced child to completion (with full step-level semantics — `condition`, `required`, `required_skills`), then auto-resumes the parent. A third edge type, `loop`, runs the referenced workflow once per item in an agent-supplied list — each iteration is a full composition child, and the engine reuses every existing composition primitive (cycle detection, spawn, snapshot, scratchpad inheritance, abort cascade, atomic mutations) plus a small set of loop-specific extensions (an `items` parameter on `update_workflow_state`, a `loop_state` JSON column on the parent record, an auto-start halt at pending loop steps, and an iteration-advance branch on cascade pop). `loop` and `composes` are mutually exclusive on a single step; their edges share the same cycle/reference graph. From the agent's perspective the run is one continuous workflow: a single ID drives everything, tool responses route to the deepest active layer, and a breadcrumb (`<parent>/<step> > <child>/<step>`, with `(item: <value>)` suffixed on the deepest segment when an iteration is active) identifies the current location in the stack.

```
┌──────────────────────────────────────────────────────────────┐
│                    Workflow MCP Tools                         │
│  ┌────────────┐ ┌─────────────┐ ┌──────────┐ ┌───────────┐  │
│  │ start_     │ │ update_     │ │ get_     │ │ end_      │  │
│  │ workflow   │ │ workflow_   │ │ workflow │ │ workflow  │  │
│  │            │ │ state       │ │ _state   │ │           │  │
│  └─────┬──────┘ └──────┬──────┘ └────┬─────┘ └─────┬─────┘  │
│        │               │              │              │        │
│        ▼               ▼              ▼              ▼        │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              WorkflowStateRepository                 │    │
│  │              (SQLAlchemy async)                      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌────────────┐                    ┌──────────────────────┐  │
│  │ list_      │                    │ StaleWorkflow        │  │
│  │ active_    │                    │ CleanupProcessor     │  │
│  │ workflows  │                    │ (pre_finalize)       │  │
│  └────────────┘                    └──────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│                    Skill Registry                             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ get_workflow(skill_name, workflow_name)              │    │
│  │ workflows property → dict[(skill, name), Definition] │    │
│  └──────────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────────┤
│                    Skill Sources                              │
│  ┌───────────────────────────────────┐                       │
│  │ my-skill/                         │                       │
│  │ ├── SKILL.md                      │                       │
│  │ └── workflows/                    │                       │
│  │     └── my-workflow/             │                       │
│  │         ├── 01-first-step/        │                       │
│  │         │   ├── instructions.md   │                       │
│  │         │   ├── references/       │                       │
│  │         │   └── scripts/          │                       │
│  │         ├── 02-next-step/         │                       │
│  │         │   └── instructions.md   │                       │
│  │         └── 03-final-step/        │                       │
│  │             └── instructions.md   │                       │
│  └───────────────────────────────────┘                       │
└──────────────────────────────────────────────────────────────┘
```

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/workflows/definition.py` | `StepDefinition` and `WorkflowDefinition` frozen dataclasses for filesystem-parsed workflow model; includes optional `condition: str \| None` (natural-language predicate evaluated at activation), `composes: str \| None` (sub-workflow reference), and `loop: str \| None` (target workflow run once per agent-supplied item; mutually exclusive with `composes`) | Follows skill dataclass pattern; uses directory name as step ID |
| `src/tachikoma/workflows/loader.py` | `load_workflows()` — discovers `workflows/` subdirectories within skill dirs, reads step subdirectories sorted alphabetically, parses `instructions.md` frontmatter via python-frontmatter; parses `condition`, `composes`, and `loop` (warn-and-fall-back-to-`None` on type mismatch) | Uses same frontmatter library as skill loading; logs warnings for invalid steps |
| `src/tachikoma/workflows/composition.py` | Pure helpers and in-memory dataclasses for composition: `resolve_composes`, `detect_cycles`, `validate_references`, `_composition_edges` (yields composes-or-loop value per step — single point that unifies the two edge types), `MutationBatch`, `UpdateState` (with optional `loop_state: dict \| None` field), `CreateChild`, `SoftDelete`, `CascadeOutcome` | No SDK / DB / async dependencies — keeps composition logic testable in isolation; shared between registry validation and cascade engine; `_composition_edges` keeps cycle detection and reference validation single-codepath across both edge types |
| `src/tachikoma/workflows/conditions.py` | `evaluate_condition()` — forks an SDK sub-agent to evaluate a natural-language predicate against the current `step_states` and scratchpad; returns `ConditionResult(passes, reason, is_error)`; on parse failure (non-JSON response), sends a single retry to the same forked session asking the agent to reformat as JSON | Follows DES-004 (sub-agent spawning conventions); evaluation is fail-closed — a non-passing result (genuine fail or evaluator error) causes the step to be skipped; retry is transparent to callers — double failure returns the original parse error |
| `src/tachikoma/workflows/model.py` | `StepState` type alias, `WorkflowState` frozen dataclass (with nullable `loop_state: dict \| None`), `WorkflowStateRecord` ORM model on `workflow_states` table; `to_domain()` method (decodes `loop_state` JSON when present); JSON serialization helpers; `parent_workflow_id`, `parent_step_id`, and `loop_state` columns; composite `Index("ix_workflow_states_parent", "parent_workflow_id", "deleted_at")` | Follows ADR-007 (SQLAlchemy async + aiosqlite); JSON columns for step_states, definition_snapshot, and loop_state; soft delete via `deleted_at`; non-partial composite parent index serves both top-level filter and active-child lookup |
| `src/tachikoma/workflows/repository.py` | `WorkflowStateRepository` — async CRUD: create, get (non-deleted), get_active (top-level only), update, soft_delete, list_active (top-level only), list_stale (subtree-aware), get_active_chain, get_active_child, abort_cascade, apply_mutation_batch (writes `loop_state` to the parent record when `UpdateState.loop_state is not None`) | Application-level duplicate prevention (only enforced for top-level instances per R26); always bumps `updated_at`; cascade methods own one session each via `async with db.begin()` for atomicity (ADR-007); `loop_state` write is conditional on the field being non-None (None means "don't touch the column") |
| `src/tachikoma/workflows/tools.py` | `create_workflow_tools_server()` — MCP server factory with 5 tools; Pydantic arg models (including optional `items: str \| None` on `UpdateWorkflowStateArgs` — a JSON-encoded string rather than a native list, decoded via `_decode_items` in the tool wrapper to work around the SDK MCP transport's rejection of array-typed arguments; same pattern as `_decode_scrub_paths` in `git/tools.py`); extracted handler functions; transition validation logic with the 5-gate items ordering (routing → transition → step-kind/items consistency → condition → items-required-for-loop); `_run_cascade` engine wrapping `_evaluate_and_advance` for per-layer step advancement, with two new branches for loop steps (auto-start halt at pending loop step; iteration advance on cascade pop reading/writing `loop_state`); `_render_breadcrumb` (suffixes `(item: <value>)` on the deepest segment when an iteration is active); `_try_spawn_child` (accepts optional `current_item` threaded onto the spawned layer); deepest-active routing; child-ID rejection on `update_workflow_state` and `end_workflow` (covers iteration children); nested view + corruption detection in `handle_get_workflow_state` (renders a `### Loop step` block on a parent's loop step showing items/index/current item; iteration child renders identically to a regular composition child — no parallel rendering path) | Follows DES-006 (MCP tool server factory); handlers testable without SDK; cascade stages mutations in memory and applies via single `apply_mutation_batch` for atomicity; loop-specific logic is localised to two cascade branches plus the breadcrumb suffix and the Loop step block — no parallel cascade engine |
| `src/tachikoma/workflows/cleanup.py` | `StaleWorkflowCleanupProcessor(PostProcessor)` — calls `repository.abort_cascade` per stale top-level root for atomic subtree teardown, then deletes the scratchpad file once; runs in `pre_finalize` phase | Extends PostProcessor directly (not PromptDrivenProcessor — no SDK fork needed); subtree-aware staleness via repository |
| `src/tachikoma/workflows/hooks.py` | `workflows_hook` — bootstrap hook: creates repository from shared Database, stores in extras | Follows DES-003 (subsystem-owned bootstrap hooks) |
| `src/tachikoma/skills/registry.py` | Extended with `_workflows` dict, `get_workflow()`, `workflows` property; workflow definitions discovered during `_load_skill()`; `_validate_deps` runs (a) a `loop`/`composes` mutex pre-pass that removes any offending parent workflow before edge collection, then (b) composition cycle detection and reference validation over the unified edge set, then (c) existing depends_on / required_skills checks; rejected workflows are removed from `_workflows` | Extends existing registry without creating a new one; composition validation co-located with skill dependency validation; mutex runs before edge collection so a rejected workflow cannot contribute partial edges to the cycle graph |
| `src/tachikoma/database.py` | Pragma migration adds `parent_workflow_id` and `parent_step_id` columns to `workflow_states` (nullable, no DEFAULT); follow-up migration adds nullable `loop_state TEXT` column for loop iteration progress; the composite index is auto-created via `Base.metadata.create_all` | Same `pragma_table_info` + `ALTER TABLE` pattern across all three migrations; nullable for backward compatibility with pre-loop-iteration records |
| `src/tachikoma/context/loading.py` | Static `# Workflows` section in `SYSTEM_PREAMBLE_TEMPLATE` documents single-ID driving, breadcrumb format, top-level filter on `list_active_workflows`, nested view in `get_workflow_state`, condition auto-skip semantics, and a `## Loops` sub-section covering the `loop` field, `items` parameter, current-item exposure, auto-completion when items are exhausted, mutex with `composes`, items required for loop steps, and the auto-start halt | Follows ADR-008 (system prompt composition via append); the Loops sub-section slots in alongside the existing `## Workflow Composition` sub-section |
| `src/tachikoma/skills/builtin/workflow-authoring-guide/` | Built-in skill with SKILL.md and `references/step-design.md`; documents `composes`, `condition`, and `loop` frontmatter fields with worked examples (including a paired producer + loop step example) | Follows existing built-in skill pattern |

### Cross-Layer Contracts

**MCP Tool Schemas**:

```
start_workflow(skill_name: str, workflow_name: str)
  → { workflow_id: str, steps: [{id, title, skippable}], scratchpad_path: str, guidance: str }
  | { error: str, existing_workflow_id: str }

update_workflow_state(
    workflow_id: str,
    step: str,
    action: "start" | "complete" | "skip",
    items: str | None = None,   # JSON-encoded string (e.g. '["a.md","b.md"]');
                                # required for loop-step starts; rejected for non-loop starts;
                                # decoded to list[str] by the tool wrapper before handler
)
  → { step: str, status: str, step_path: str, instructions: str }
  | { workflow_complete: true, message: str }
  | { error: str }

get_workflow_state(workflow_id: str)
  → { workflow_id, skill_name, workflow_name, current_step, steps: [{id, title, status}], created_at, updated_at }
  | { error: str }

end_workflow(workflow_id: str, action: "complete" | "abort")
  → { message: str }
  | { error: str }

list_active_workflows()
  → { workflows: [{workflow_id, skill_name, workflow_name, current_step, started_at}] }
```

**Transition validation rules** (enforced by `update_workflow_state`):
- `start`: step must be `pending`. Items validation runs in a fixed five-gate order — routing → transition → step-kind/items consistency (structural — items provided iff step is a loop step) → condition → items-required-for-loop (semantic — only reached when the step is a loop step that survived its condition). If the step has a `condition`, the cascade evaluates it (forks SDK sub-agent); a non-passing result (genuine fail or evaluator error) marks the step `skipped` and the cascade advances to the next pending step — items (if provided) are silently discarded. Otherwise marks `started`. If the step has `composes` (and the start transition resolved to `started`), the engine spawns the referenced child workflow (allocates child_id, snapshots the *current* registered child definition, queues a `CreateChild` mutation, and descends the cascade into the child). If the step has `loop`, the engine reads `items`: with non-empty items it queues an `UpdateState` (marking the loop step `started` and writing `loop_state[<step>] = {items, index: 0}`) and spawns iteration 0 with `current_item=items[0]`; with `items=[]` it short-circuits to the exhaustion branch (no STARTED intermediate — the loop step transitions directly to `completed`). All validation gates run before any `MutationBatch` is queued; first failure short-circuits with no partial state.
- `complete`: step must be `started` (not `pending` — must start before completing); marks as `completed`; **auto-starts** next pending step (internal transition, bypasses validate_transition since `_find_next_pending_step` guarantees the step is pending) and returns its instructions; if no next step in the current layer, the layer auto-finalizes — for the top-level layer this means soft-delete + scratchpad cleanup; for a child layer this means soft-delete + auto-completion of the parent's composition/loop step + auto-start of the parent's next pending step (cascade pops up). When the cascade pops to a parent whose just-completed child was a loop iteration, the engine advances `loop_state[<loop_step>].index`; if more items remain, iteration N+1 spawns in the same tool call; if the loop is exhausted, the loop step is marked `completed` and the cascade-up logic continues.
- `skip`: step must be `skippable` (i.e. `required: false`) in frontmatter AND `pending`; marks as `skipped`; same auto-advance / auto-finalize rules as `complete`. A `skip` action on a `required: true` composition or loop step returns the standard "step is required" error and does not spawn a child record (no spawn side effect).
- The composition or loop step's `instructions.md` body is never read at runtime — only its frontmatter (title, `required`, `condition`, `required_skills`, `composes`/`loop`) is honoured.
- Any action on a completed/skipped step: error with explanation.
- All actions update `updated_at` on the workflow state record.

**Cascade routing rules** (deepest-active):
- The agent always passes the *top-level* workflow ID. The engine reads the active chain via `get_active_chain(top_level_id)` and routes the action to the deepest active layer.
- A step ID that does not match the deepest layer's expected step (typo, parent step while a child is active, stale reference to a soft-deleted child's step) returns: `Invalid step '{step}'. The deepest active layer is '{name}'. Valid steps: {ids}.`
- Calls to `update_workflow_state` or `end_workflow` made with a composed child's or iteration child's ID return an error directing the agent to the top-level workflow. `get_workflow_state` retains backwards compatibility with child IDs and returns a standalone view with a note pointing to the parent.
- When the cascade auto-advances to a pending loop step, auto-start halts: the loop step stays `pending` and the response prompts the agent to call `update_workflow_state(..., action="start", items=[...])`. The cascade caller in `_run_cascade` inspects `next_info["loop"]` after `_evaluate_and_advance` returns the next pending step; if set, it returns the halt-prompt response without queuing a STARTED transition or a spawn. The parent's `current_step` is left at the just-finalized step (deliberate deviation from normal auto-advance — the loop step is reachable via re-evaluation on the next tool call, so `current_step` does not need to point at it).

**Cascade atomicity**:
- Conditions are evaluated *outside* any database transaction (each evaluator forks an SDK sub-agent and may take seconds).
- All resulting state changes accumulate in an in-memory `MutationBatch` (`UpdateState`, `CreateChild`, `SoftDelete`).
- Once the cascade loop terminates, `repository.apply_mutation_batch(batch)` applies every queued mutation in a single `async with db.begin():` block. Any raise rolls everything back — partial state never reaches the database.

**Repository surface (composition-aware)**:

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

# Modified semantics (no signature change):
get_active(skill, workflow)        # adds parent_workflow_id IS NULL
list_active()                      # adds parent_workflow_id IS NULL
list_stale(threshold)              # subtree-aware: returns top-level
                                   # roots whose entire subtree exceeds
                                   # the threshold (early-exit: roots
                                   # with fresh own updated_at skip the
                                   # descendant walk)
```

**MCP tool behavior changes (no schema changes)**:
- `update_workflow_state(workflow_id, step, action)` — `workflow_id` is always the top-level ID; `step` must match the deepest active layer.
- `get_workflow_state(workflow_id)` — top-level returns nested view (parent state + `### Active Child` section + breadcrumb header); child ID returns standalone view with a note recommending parent access.
- `end_workflow(workflow_id, action)` — top-level triggers `abort_cascade`; child ID returns an error directing to the top-level.
- `list_active_workflows()` — only top-level workflows surface; nested children never appear.

**Mutex pre-pass** (runs first in `SkillRegistry._validate_deps`):

Walks every workflow and every step. If any step declares both `composes` and `loop`, the parent workflow is removed from `_workflows` up-front (logging a malformed-step warning) — *before* edge collection. This guarantees a rejected workflow's other steps cannot contribute partial edges to the cycle graph, mirroring `validate_references`'s "reject the parent on malformed `composes` value" pattern.

**Cycle detection algorithm** (used by `SkillRegistry._validate_deps`):

Three-color DFS over the unified composition graph. Vertices = `(skill_name, workflow_name)` pairs; edges = `composes` edges OR `loop` edges (a step contributes at most one — mutex enforced upstream). Both edge types resolve via the same `resolve_composes` helper and are indistinguishable for cycle purposes — a `loop: A` cycle is rejected by the same DFS that rejects a `composes: A` cycle. A back-edge to a vertex on the current DFS stack identifies a cycle; the slice of the stack from that vertex is recorded as one detected cycle (not a full Tarjan SCC — the algorithm extracts cycles directly from the DFS stack on back-edge encounters). Self-loops are detected on the first DFS step. One warning per detected cycle; every vertex in the cycle is removed from `_workflows`.

**Reference validation algorithm** (after cycle detection):

Fixed-point iteration over `_workflows`. For each non-rejected workflow, walk its composition steps and resolve each `composes` or `loop` value (whichever is set) via `resolve_composes(value, parent_skill)`; the helper does not care which frontmatter key carried the value. Reject the parent if the value is malformed (raises `ValueError`), the target is absent from `_workflows`, the target has zero steps, or the target is itself rejected. The loop repeats while any rejection was added in the previous pass; cascading rejections converge after at most `depth` passes because the graph is acyclic post-cycle-removal. Each rejection logs one warning identifying the parent, the underlying cause, and which edge type (`composes` or `loop`) carried the rejected value.

**Required-skill expansion at activation** (applied in both the explicit `start` branch and the auto-start branch after `complete`/`skip`):
- `_render_required_skills(step_info, skill_registry)` reads `step_info["required_skills"]` from the step's snapshot entry
- Each declared anchor is expanded via `SkillRegistry.resolve_chain(name)` (deps-first, anchor-last, cycle-tolerant, unknown-tolerant, memoized)
- A `KeyError` from `resolve_chain` (anchor name not registered) is swallowed with a debug log — matching the silent-skip posture at resolution time
- A cross-anchor `seen: set[str]` dedups shared transitive deps so each skill's `<skill name="X" directory="...">…</skill>` block is emitted exactly once per activation response
- When no skills resolve (no declarations, or all anchors unknown), the helper returns an empty string — activation responses match the pre-existing format

**Integration Points**:
- MCP tools call skill registry to resolve skill_name -> workflow definition
- MCP tools call workflow state repository for all DB operations
- Stale cleanup runs as post-processor in `pre_finalize` phase, before GitProcessor in `finalize`
- System preamble static text added to preamble template
- `start_workflow` guidance instructs agent to Read the scratchpad file first, then Edit to update it
- `end_workflow` primarily for aborting workflows — normal completion is handled by auto-finalize
- Workflow MCP tools registered alongside task MCP tools in coordinator's `mcp_servers` dict
- DB errors surface as MCP tool errors via `{"is_error": true, ...}` (consistent with task tools pattern)

### Shared Logic

- **Workflow definition model**: Parsed step structure (id, title, instructions path, references, scripts, frontmatter properties) shared between loader and MCP tools
- **Step state enum**: `pending | started | completed | skipped` — shared between state model and transition validation
- **Transition validation**: Central logic in MCP tool handler, enforcing step ordering and frontmatter properties

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
  condition: str | None (natural-language predicate evaluated at activation;
                         non-passing result auto-skips the step)
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
```

The composition graph is a transient in-memory structure built only during `_validate_deps`:

```
CompositionGraph (in-memory, transient):
  vertices: set[(skill_name, workflow_name)]
  edges: dict[vertex, list[(vertex, edge_type)]]
                # edge_type ∈ {"composes", "loop"}; cycle detection
                # treats them uniformly. Mutex enforced before edge
                # collection so no vertex contributes both kinds.
```

## Data Flow

### Normal workflow execution

```
1. Skill detection loads skill with workflows/
2. Agent reads SKILL.md -> discovers available workflows
3. Agent calls start_workflow(skill_name, workflow_name)
   -> Loader parses workflow definition from skill directory
   -> Creates WorkflowState record in DB (with definition snapshot, scratchpad_path)
   -> Returns step list + scratchpad_path + agent guidance (TodoWrite, scratchpad)
4. Agent reads scratchpad file, then edits it to persist workflow ID and progress notes
5. Agent creates TodoWrite tasks for steps
6. Agent calls update_workflow_state(id, first_step, "start")
   -> Validates step exists and is pending
   -> Updates step_states in DB
   -> Returns step_path + instructions content (instructions.md body)
7. Agent executes step, using step_path to navigate references/ and scripts/ as needed
8. Agent calls update_workflow_state(id, step, "complete")
   -> Marks step completed, auto-starts next pending step, returns its instructions
9. Repeat 7-8 until all steps done
10. On last step completion, workflow is auto-finalized (DB record soft-deleted, scratchpad file deleted)
```

To abort mid-workflow: `end_workflow(id, "abort")` at any point.

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
   2d. Run reference validation (target exists, has ≥1 step, not
       already rejected, composes value parses). Iterate to fixed-point
       so cascading rejections converge.
   2e. Remove rejected references' parents from _workflows. Log warnings
       identifying parent + cause.
   2f. Existing depends_on / required_skills validation runs as before.
3. After _validate_deps, _workflows contains only valid composable workflows.
```

### Nested workflow execution

```
Agent: update_workflow_state(parent_id, "01-plan", "complete")
  -> chain = get_active_chain(parent_id) = [parent]
  -> deepest = parent; step "01-plan" matches; transition validates.
  -> Cascade loop:
       mark "01-plan" completed in mutable_ss[parent].
       _evaluate_and_advance finds next pending: "02-handle-inbox" (composes set).
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
  -> Response prefixes a breadcrumb header
       "weekly-review/02-handle-inbox > process-inbox-note/01-check"
       and returns the child's "01-check" instructions.
```

### Cascade up to top-level (child auto-finalizes on parent's last step)

```
Agent: update_workflow_state(parent_id, <child_last_step>, "complete")
  -> chain = [parent, child]; deepest = child; step matches.
  -> Cascade loop:
       mark child's <child_last_step> completed.
       _evaluate_and_advance on child returns None (no pending steps).
       queue UpdateState(child, ...) + SoftDelete(child); pop layer.
       parent's composition step (parent_step_id) auto-completes
         in mutable_ss[parent].
       _evaluate_and_advance on parent returns None (parent's last step).
       queue UpdateState(parent, ...) + SoftDelete(parent);
         set finalized_top_level=True.
  -> MutationBatch: 4 mutations applied atomically.
  -> After commit: scratchpad file deleted (idempotent unlink).
  -> Response: "Workflow weekly-review complete and finalized."
```

### Abort cascade

```
Agent: end_workflow(parent_id, "abort")
  -> handle_end_workflow:
       reads parent state; rejects child IDs.
       calls abort_cascade(parent_id) — atomic.
         BFS-walks descendants inside the transaction (each iteration
         issues a SELECT against the open session); collects all IDs;
         issues one UPDATE WHERE id IN (...) setting deleted_at.
       after commit: deletes the shared scratchpad once.
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
Agent: update_workflow_state(parent_id, "03-process", "start", items=["a","b","c"])
  -> Validation gates: routing → transition → step-kind/items consistency
       → condition → items-required-for-loop. All pass.
  -> Cascade loop:
       mark "03-process" STARTED in mutable_ss[parent].
       _try_spawn_child(loop_target, current_item="a"):
         allocates child_id; snapshots target; child_ss = {first: PENDING, ...}.
       queue UpdateState(parent, ..., loop_state={"03-process": {items, index: 0}}).
       queue CreateChild(child_id_0, parent_id, "03-process", ...,
                         scratchpad_path=parent.scratchpad_path).
       descend into child_0; first step STARTED; cascade terminates.
  -> apply_mutation_batch commits everything atomically.
  -> Response breadcrumb: "<parent>/03-process > <iter-wf>/01-step (item: a)"
       body: child_0's first step instructions.

Later — iteration N's last step completes; iteration N+1 spawns same call:

Agent: update_workflow_state(parent_id, "<last_iter_step>", "complete")
  -> chain = [parent, child_N]; deepest = child_N; step matches.
  -> Cascade loop:
       mark child's last step COMPLETED.
       _evaluate_and_advance on child returns None (no pending steps).
       queue UpdateState(child_N, current_step=None) + SoftDelete(child_N); pop.
       parent's just-completed step is "03-process" (a loop step).
       read loop_state["03-process"].index = N; advance to N+1.
       items[N+1] exists ("b"):
         _try_spawn_child(loop_target, current_item="b") → child_id_N+1.
         queue UpdateState(parent, ..., loop_state={"03-process": {items, index: N+1}}).
         queue CreateChild(child_id_N+1, ..., scratchpad_path=parent.scratchpad_path).
         descend into child_N+1; first step STARTED; cascade terminates.
  -> apply_mutation_batch commits everything atomically (single SQLite tx).
  -> Response breadcrumb suffix: "(item: b)"; body: child_N+1's first step.

When the last item exhausts:

Agent: update_workflow_state(parent_id, "<last_iter_step>", "complete")
  -> Cascade pop: child_last finalizes. parent's "03-process" exhausts
     (index advances to len(items)); queue
       UpdateState(parent, step_states={..., "03-process": COMPLETED},
                   loop_state={"03-process": {items, index: len(items)}}).
     fall through to existing cascade-up logic; advance parent's next
     pending step OR auto-finalize the top-level if "03-process" was last.
```

### Empty items list (auto-complete with zero iterations)

```
Agent: update_workflow_state(parent_id, "03-process", "start", items=[])
  -> All five gates pass (items=[] is valid for a loop step).
  -> Cascade loop:
       The "spawn or exhaust" decision is centralised: index 0 vs.
       len(items)=0 → exhausted on first check.
       queue UpdateState(parent,
                         step_states={..., "03-process": COMPLETED},
                         loop_state={"03-process": {items: [], index: 0}}).
       _evaluate_and_advance on parent: next pending step OR finalize.
  -> MutationBatch: 1-3 mutations (no CreateChild).
  -> Response: parent's next step OR top-level finalization.
       Includes a one-line "Loop step <id> completed with zero iterations." note.
```

### Auto-start halt at a pending loop step

```
Agent: update_workflow_state(parent_id, "02-prepare", "complete")
  -> Cascade marks "02-prepare" COMPLETED.
  -> _evaluate_and_advance returns next pending step "03-process" (loop set).
  -> Halt branch fires: do NOT mark STARTED, do NOT spawn anything.
       queue UpdateState(parent, step_states={..., "02-prepare": COMPLETED},
                         current_step="02-prepare").  (loop_state untouched)
  -> apply_mutation_batch commits the partial state ("02-prepare" only).
  -> Response (no breadcrumb suffix — no child active):
       "Step `02-prepare` completed.
        The next step **Process items** (`03-process`) is a loop step.
        Call update_workflow_state(workflow_id=..., step='03-process',
        action='start', items=[...]) to begin iterating, or items=[] to
        skip with zero iterations."
  -> The loop step's state stays `pending`. Re-evaluation on the agent's
     next call reproduces the halt deterministically.
```

### Recovery flow (context loss)

```
1. Agent loses workflow ID (context compaction, session corruption)
2. Agent reads scratchpad file -> recovers workflow ID
   OR (scratchpad also lost):
3. Agent calls list_active_workflows() -> recovers workflow ID
   (only top-level workflows surface; nested children are hidden —
   iteration children behave identically to composition children).
4. Agent calls get_workflow_state(id) -> full state for resumption.
   For a parent with an active child, the response inlines the
   active child path under "### Active Child" with a breadcrumb
   header — the agent can re-orient without ever knowing the
   child's separate ID. For a parent with an active loop step,
   the response also inlines a "### Loop step" block listing
   the items, current 1-indexed iteration, and current item.
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

**Choice**: All state transitions go through MCP tools that validate and enforce rules
**Why**: Prevents the agent from corrupting state; tools return step-specific instructions at transition points, guiding the agent naturally

**Consequences**:
- Pro: Strong state guarantees regardless of agent capability
- Pro: Tool results naturally guide the agent to the next action
- Con: Agent must call tools correctly (but tool errors provide clear guidance)

### Step path + instructions only in tool response

**Choice**: `update_workflow_state` returns the step's directory path and instructions.md content — no explicit references/scripts listing
**Why**: The step's instructions.md is responsible for guiding the agent to its own resources. Returning only the step path and instructions keeps tool responses minimal and lets the step instructions be the single source of guidance.

**Consequences**:
- Pro: Minimal tool response size
- Pro: Step instructions are the single source of guidance
- Pro: Agent navigates file tree naturally using the step_path

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

**Choice**: `end_workflow` and stale cleanup set `deleted_at` on the DB record instead of removing it
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

### start_workflow guidance as tunable prompt engineering

**Choice**: The `start_workflow` tool constructs a guidance string that instructs the agent to use TodoWrite, persist the workflow ID in a scratchpad file (Read first, then Edit to update), and call `update_workflow_state` to progress
**Why**: This guidance is the primary mechanism for agent task tracking and scratchpad usage. The quality of this guidance directly affects workflow reliability and must be treated as a tunable parameter refined based on agent behavior.

**Consequences**:
- Pro: Guidance adapts as agent behavior is observed in practice
- Pro: No additional infrastructure needed

### Step-declared required skills injected via tool response

**Choice**: Steps declare their required skills via a `required_skills: [names]` frontmatter field. At activation (explicit `start` or auto-start after `complete`/`skip`), the update tool resolves each declared skill's transitive chain through `SkillRegistry.resolve_chain` and appends the resolved skill bodies to the tool response — bypassing the skill classifier. Skills are NOT persisted as `SessionContextEntry`.
**Why**: The classifier infers relevant skills from the user message and can miss foundations a step silently relies on (git operations, API clients, domain knowledge) when instructions are terse. Declarative activation guarantees the required skills are present at the moment the step begins executing. Reusing `resolve_chain` means deps-first ordering, cycle tolerance, unknown-dep tolerance, and memoization are all inherited rather than reimplemented in the workflow subsystem.

**Why tool response, not `SessionContextEntry` persistence**: The MCP tool handler runs mid-SDK-session; it has no access to the `SessionRegistry`. Persisting skills as entries would require threading session state through the handler and reasoning about transactional semantics during a tool call. Injecting the skill content in the tool response is minimal and correct for the activation-moment use case — the agent sees the skills immediately and they flow into the transcript naturally. If the same skill is re-activated by a subsequent step or message, the skills context provider's per-message classifier continues to handle persistence independently. A follow-up delta can add persistence if practice shows it is needed.

**Consequences**:
- Pro: Reliable foundation loading at step activation — independent of classifier phrasing sensitivity
- Pro: Zero new traversal code in `workflows/` — reuses the existing skill dependency resolver
- Pro: Unknown-dep tolerance and cross-anchor dedup inherited from the resolver
- Pro: Load-time validation lives in `SkillRegistry._validate_deps`, co-located with skill `depends_on` validation
- Con: Skills injected via the tool response do not become `SessionContextEntry` records, so their agents are not derived by `derive_agents_from_entries` for the current exchange (classification still can pick them up on subsequent messages)
- Con: Injected bodies are re-emitted on every activation of a declared step (acceptable — activation is a deliberate, infrequent event)

### Auto-start and auto-finalize on complete/skip

**Choice**: Completing or skipping a step automatically starts the next pending step. When all steps are done, the workflow is automatically finalized (soft-deleted, scratchpad cleaned up).
**Why**: Observed during first real-world usage: the agent had to call `start` after every `complete` (redundant) and `end_workflow` after the last step (felt like double-closing). Auto-start and auto-finalize eliminate these friction points, making the workflow progression naturally linear: `start` first step → `complete` (auto-starts next) → `complete` (auto-starts next) → `complete` (auto-finalizes).

**Consequences**:
- Pro: Agent only needs one tool call per step instead of two (no separate `start` after `complete`)
- Pro: No redundant `end_workflow` call for normal completion
- Pro: `end_workflow` remains available for aborting mid-workflow
- Con: Auto-start bypasses `validate_transition` (safe: `_find_next_pending_step` guarantees the step is pending)

### Stage in memory, single atomic commit (cascade atomicity)

**Choice**: Conditions evaluate outside any DB transaction. State changes accumulate in an in-memory `MutationBatch`. One `async with db.begin():` block applies everything atomically at the end of the cascade.
**Why**: Holds the SQLite write lock only during the actual write phase, not across slow LLM calls in the condition evaluator. Mirrors the existing `_evaluate_and_advance` evaluate-then-write pattern. The "entire cascade rolled back atomically" requirement is satisfied trivially because nothing is written until the final commit.

**Consequences**:
- Pro: Lock-hold time matches existing single-row updates (~ms), regardless of how many condition evaluations the cascade fires.
- Pro: Adding new mutation types is uniform — every new transition slots into the same batch+commit pipeline.
- Pro: Cascade rollback on mid-flight error is automatic (the batch is dropped, nothing was committed).
- Con: Slight in-memory bookkeeping overhead per call (~5-10 mutations max); negligible.

### Python-driven chain walk (vs. recursive CTE)

**Choice**: `get_active_chain(root_id)` reads the root via `get`, then iteratively `SELECT WHERE parent_workflow_id = current.id AND deleted_at IS NULL` until no child is found. Stale cleanup uses iterative widening per top-level root.
**Why**: Depth is bounded by load-time cycle detection (real workflows nest 1-3 levels). 2-5 sequential indexed queries cost sub-millisecond each on aiosqlite. The codebase has no existing recursive-CTE usage — introducing one would be a one-off pattern with invisible perf gain against LLM latency.

**Consequences**:
- Pro: Matches the existing repository style (every method is a single `select()` with simple WHERE clauses).
- Pro: Each layer's `WorkflowState` is fully read into the chain — useful for breadcrumb rendering and the nested view without further queries.
- Con: 2-5 round-trips per `update_workflow_state` (acceptable on a local indexed file DB).

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

**Choice**: The agent always passes the top-level workflow ID. The engine internally walks to the deepest active layer. Step IDs that don't match the deepest layer return an error listing that layer's valid step IDs.
**Why**: Asking the agent to track child IDs adds error-prone bookkeeping that's unnecessary given the structural invariant of one active child per parent at any time. The deepest-active routing rule is unambiguous, and self-correcting errors that name the active layer's workflow + valid step IDs help the agent recover from typos and stale references.

**Consequences**:
- Pro: Agent's mental model stays simple — one ID per logical workflow, regardless of nesting depth.
- Pro: Routing errors are educational, not silent failures.
- Pro: `get_workflow_state` retains a backwards-compat path for child IDs that the agent may have persisted before this rule existed.
- Con: A composition step's step ID cannot be addressed directly while a child is active (intentional — the parent's composition step shouldn't be marked complete out-of-band).

### Shared scratchpad inheritance

**Choice**: When a child is spawned, its `scratchpad_path` is set to the parent's path. The scratchpad file is created and removed once at the top-level lifecycle.
**Why**: A parent and its composed children are conceptually one continuous run; notes the agent takes during the child's execution belong in the same file. Two parents running independent compositions of the same child workflow remain isolated because each has its own parent scratchpad — the child inherits its specific parent's path, not a global one.

**Consequences**:
- Pro: One file, one lifecycle, one cleanup point — covers normal finalization, abort, and stale cleanup uniformly.
- Pro: Two top-level parents composing the same child stay isolated (each child writes to its own parent's scratchpad).
- Con: A naïve reader might expect the scratchpad to be named after the active child workflow during the child's execution; documented in the authoring guide.

### Create-then-soft-delete in same transaction (empty children)

**Choice**: When a child workflow's only step (or all steps) condition-skip on first activation, the child record is still created with its definition snapshot, then immediately queued for soft-delete in the same `MutationBatch`. Both writes commit atomically.
**Why**: Condition evaluation is dynamic (forks an SDK sub-agent), so we cannot statically predict which child steps will skip without running them. Creating uniformly and letting the cascade loop drive the rest keeps the spawn path single-codepath. The brief existence of a soft-deleted child record is invisible to readers because the `async with db.begin():` block commits both writes as one SQLite transaction — other readers in the single-process async loop cannot interleave inside it.

**Consequences**:
- Pro: One spawn path; the cascade loop handles all outcomes uniformly.
- Pro: Snapshot-at-spawn-time semantics preserved even for short-lived children.
- Con: A child record briefly exists in the in-flight transaction; invisible outside the transaction boundary.

### Loop iteration as a second edge type in the existing composition graph

**Choice**: `loop` and `composes` are sibling frontmatter fields on the same step model; cycle detection, reference validation, spawn, snapshot-at-spawn, scratchpad inheritance, deepest-active routing, abort cascade, and atomic mutation are all reused. Iteration advancement is a new branch in the existing cascade loop, not a parallel cascade.
**Why**: The composition machinery already encodes "spawn a child, run it, auto-resume parent". An iteration is the same mechanism applied N times with a different `current_item`. Building a parallel pipeline would duplicate cycle detection, snapshot logic, atomicity, routing, and rendering — the risk surface of one well-tested pipeline is dramatically smaller than two.

**Consequences**:
- Pro: One spawn path, one cycle detector, one cascade engine. Bug fixes on either feature benefit both.
- Pro: Iteration children inherit composition's documented invariants (top-level filter, child-ID rejection, abort cascade) for free.
- Pro: Cross-edge cycles (`A` loops `B`, `B` composes `A`) fall out of the unified graph naturally — no special-case algorithm.
- Con: The cascade engine's pop branch grows a new conditional. Mitigated by keeping the loop-specific logic to ~15 lines: read `loop_state`, advance index, decide spawn vs. exhaust.

### `loop_state` as a JSON column on the parent record (not a separate table)

**Choice**: Items list and current iteration index live on the parent's `workflow_states` row in a new nullable `loop_state` JSON column shaped `{<step_id>: {items: list[str], index: int}}`. Iteration child records are *not* augmented — they remain identical to composition children.
**Why**: Loop progress is intrinsic to the parent's loop step, not to the iteration child. The child is ephemeral (one per item, soft-deleted on finalize); the parent's state record is the durable home. Storing on the parent means recovery via `get_workflow_state(parent_id)` returns everything in one query — no JOIN, no N-row aggregation. Project precedent (ADR-007): `step_states` and `definition_snapshot` are already JSON columns on the same table; `loop_state` follows the same shape.

**Consequences**:
- Pro: One read returns everything needed for the parent's view; no JOIN.
- Pro: Schema migration is a single nullable column add via the existing pattern.
- Pro: `loop_state` survives soft-delete intact (audit trail).
- Con: A loop step that ran with a 1000-item list embeds the list in the parent's row even after finalization. Acceptable: realistic batch sizes are dozens, not thousands; the row is soft-deleted (read-only) once the workflow ends.

### Items on the existing `start` action (not a dedicated MCP tool)

**Choice**: The agent passes `items` as a JSON-encoded string parameter to `update_workflow_state` when starting a loop step. No new tool is added. The tool wrapper decodes the string via `_decode_items` before passing `list[str]` to the handler.
**Why**: Adding a tool (e.g., `start_loop_iteration`) would force the agent to learn a parallel state-machine surface — when to call which tool, what the difference is. Reusing `start` keeps the state machine's vocabulary unified: there is exactly one way to begin a step, and the optional `items` parameter is the only difference loop steps require. The Pydantic field is typed `str | None` (not `list[str] | None`) because the SDK MCP transport's client-side JSON Schema validator rejects array-typed arguments — the same constraint that led to the JSON-string encoding of `scrub_paths` on the `push` tool (DES-006). The `_decode_items` helper validates the JSON structure (must be an array of strings) and surfaces clear error messages before the handler runs.

**Consequences**:
- Pro: Zero new tool surface; the agent's mental model stays "one tool drives the state machine".
- Pro: All shape-error modes (invalid JSON / non-array / non-string items / items-on-non-loop / missing items) covered by the decode layer plus one handler check.
- Pro: Atomic by construction — the decode layer rejects malformed input before the handler is called.
- Con: The tool description grows by a few lines to document the JSON-string format. Acceptable.

### Auto-start halts at pending loop steps (cascade does not infer items)

**Choice**: When the cascade auto-advance reaches a pending loop step, it returns the loop step as the next pending step but does *not* mark it `STARTED` and does *not* spawn any iteration. The response prompts the agent for an explicit `start` with `items`.
**Why**: The engine cannot fabricate items. Auto-starting with an empty list would silently skip the loop step — invisible to the agent and contrary to the workflow author's intent. Auto-starting with synthesized items (e.g., from the scratchpad) would tie the engine to a specific producer pattern. The right default is to halt and require explicit input, with a self-explanatory error message that names the tool call to fix it.

**`current_step` semantics on halt** (deliberate deviation): in normal auto-advance, `current_step` is set to the next step whenever a step starts. The halt branch leaves `current_step` at the just-finalized step — `current_step` semantically tracks "the step the engine most recently activated"; on halt no step is activated, so leaving it at the previous step is the correct invariant. The loop step is reachable through `_evaluate_and_advance` on the next tool call, so halt-recovery routing does not require `current_step` to point at it.

**Consequences**:
- Pro: No silent skips; the agent always sees a halt prompt naming the next tool call.
- Pro: The loop step's state stays `PENDING` — no half-activated state.
- Pro: Idempotent retries: if the agent's first attempt at providing items fails validation, the next attempt sees the same prompt.
- Con: The agent must know to provide items; mitigated by the explicit prompt and the system preamble's documentation.

### Five-gate validation order: routing → transition → step-kind/items consistency → condition → items-required

**Choice**: Items validation is split into two distinct gates: a **structural** gate (gate 3) that runs *before* condition evaluation, and a **semantic required** gate (gate 5) that runs *after*.
**Why**: Two pressures pull in opposite directions. (1) Condition gates the entire step — demanding items before a failing condition forces the agent to assemble a list that's about to be discarded. (2) Items on a non-loop step must reject — silently swallowing this error when the step also has a failing condition would mask an agent bug. Splitting the items check resolves the tension. The structural gate (items-vs-step-kind) is a static parameter-vs-step-kind check that requires no dynamic state and surfaces all usage errors deterministically; the semantic gate (items-required-for-loop) only runs when the step is a loop step that survived its condition.

**Consequences**:
- Pro: Items-on-non-loop-step errors surface even when the step's condition would otherwise fail.
- Pro: Failing condition on a loop step skips items-required check; the agent doesn't need to pre-assemble items.
- Pro: Atomic at every gate — first failure short-circuits, no `MutationBatch` is built.
- Pro: Three layers of defence (Pydantic shape → structural step-kind → semantic required).
- Con: Slightly more conditional logic in the handler. Acceptable — each gate is a 1–3-line check.

### Current item exposed via breadcrumb suffix

**Choice**: The current item is appended to the deepest segment of the breadcrumb header as `(item: <value>)`. `get_workflow_state(parent_id)` adds a `### Loop step` block on the parent's view for explicit recovery, but the breadcrumb suffix is the moment-to-moment indicator on every iteration tool response.
**Why**: One rendering hook serves both the per-call surfacing requirement and the explicit current-item label requirement. The breadcrumb is already rendered on every iteration tool response and is a natural location for context tied to the deepest active layer. A separate `Current item: ...` prefix would duplicate the signal and add a second place to keep in sync.

**Consequences**:
- Pro: One rendering hook, two requirements satisfied.
- Pro: The agent's iteration mental model — "I'm in iteration N looking at item X" — maps directly to the breadcrumb.
- Pro: Consistent across all tool responses inside an iteration.
- Con: Long item values (e.g., long filenames) make the breadcrumb wider. Acceptable: items are typically short identifiers; agents truncate on display when needed.

### `UpdateState` extended with optional `loop_state` (vs. a new mutation type)

**Choice**: The existing `UpdateState` mutation dataclass gains an optional `loop_state: dict | None` field. `apply_mutation_batch` writes the column when set. No new mutation type is introduced.
**Why**: `UpdateState` already targets a single workflow row; adding another field-on-that-row keeps the mutation count constant and the transaction shape unchanged. A new mutation type would force the cascade engine to queue two mutations per iteration advancement — coordination that adds nothing because they always commit together.

**Consequences**:
- Pro: Same mutation type covers both step transitions and loop-progress updates.
- Pro: One transaction, one atomic write, one rollback boundary.
- Pro: Existing tests for `apply_mutation_batch` need only one new branch (write `loop_state` when set).
- Con: `UpdateState` becomes slightly less "pure" (mixes step transitions and loop progress). Acceptable: both target the same row, both must commit atomically, conceptually they are one update.

## System Behavior

### Scenario: Workflow resumption after context compaction

**Given**: An active workflow with ID "abc-123" at step "03-review"
**When**: Context compaction occurs and the agent loses the workflow context
**Then**: The agent reads its scratchpad file to recover the workflow ID, calls `get_workflow_state("abc-123")`, receives the full state, and resumes from step "03-review"
**Rationale**: State lives in the database, not conversation memory. The scratchpad file survives context compaction because it's on disk.

### Scenario: Full context loss recovery

**Given**: An active workflow where both the conversation context and scratchpad are lost
**When**: The agent needs to resume but has no workflow ID
**Then**: The agent calls `list_active_workflows()`, identifies the relevant workflow, and calls `get_workflow_state(id)` to resume

### Scenario: Attempting to skip a non-skippable step

**Given**: An active workflow where step "02-execute" has `skippable: false` in its frontmatter
**When**: The agent calls `update_workflow_state(id, "02-execute", "skip")`
**Then**: The tool returns an error explaining the step cannot be skipped

### Scenario: Stale workflow cleanup

**Given**: A workflow state record with `updated_at` 25 hours ago (default threshold: 24h)
**When**: The post-processor runs during session close (pre_finalize phase)
**Then**: The record is soft-deleted (`deleted_at` set) and its scratchpad file is deleted before the git commit

### Scenario: Definition changes during active workflow

**Given**: An active workflow started from a 3-step definition
**When**: The skill author modifies the workflow to have 4 steps
**Then**: The active workflow continues with the original 3-step definition (from snapshot)

### Scenario: Duplicate workflow prevention

**Given**: An active workflow for skill "morning-routine" workflow "morning-routine"
**When**: `start_workflow("morning-routine", "morning-routine")` is called again
**Then**: The tool returns an error with the existing workflow ID, indicating the workflow is already running

### Scenario: Two parents composing the same child concurrently

**Given**: `weekly-review` is active and has just spawned a child instance of `process-inbox-note`. Independently, `daily-review` is started and reaches its own composition step that also references `process-inbox-note`.
**When**: `daily-review`'s composition step activates and tries to spawn its own child.
**Then**: The spawn succeeds. The duplicate-prevention check on `get_active(skill, "process-inbox-note")` filters on `parent_workflow_id IS NULL`, so the existing composed instance (whose `parent_workflow_id` is `weekly-review`'s ID) is invisible. Two child records exist concurrently — one parented by `weekly-review`, one by `daily-review` — and each writes to its own parent's scratchpad.
**Rationale**: Composition is logically scoped — the same child workflow can run multiple times in parallel as long as each instance is contained inside a different parent context. Top-level uniqueness is unchanged.

### Scenario: Child auto-finalizes on a condition-skipped last step

**Given**: A child workflow's last step has `condition: "scratchpad has unprocessed items"`, and the condition evaluates false at activation.
**When**: The cascade reaches that point.
**Then**: `_evaluate_and_advance` condition-skips the last step (in memory). The cascade sees no more pending steps in the child, queues `SoftDelete(child)`, pops to the parent, auto-completes the parent's composition step, and advances into the parent's next pending step. The response carries a `### Condition-Skipped Steps` block listing the skipped step (breadcrumb-prefixed) plus the activated next step.

### Scenario: Cycle introduced by hot skill reload

**Given**: A skill is hot-reloaded with new workflow definitions that introduce a cycle (`A` composes `B`, `B` composes `A`).
**When**: `SkillRegistry.refresh()` runs.
**Then**: `_validate_deps` rejects both `A` and `B`; both are removed from `_workflows` and a cycle warning is logged. If a workflow with a now-invalid composition reference was already running (its `definition_snapshot` still references the deleted target), `get_workflow_state(parent_id)` prepends a corruption warning identifying the affected step and the missing target, and any subsequent `update_workflow_state` that tries to spawn into the missing target returns a corruption error directing abort.

### Scenario: Definition changes between parent-start and composition-step-activate

**Given**: A skill author modifies `process-inbox-note` (adds a new step) between the moment `weekly-review` started and the moment `weekly-review` reaches its composition step.
**When**: The composition step activates.
**Then**: The child is spawned with the *current* (modified) `process-inbox-note` definition snapshot. The parent's snapshot (captured at parent-start) is not consulted for the child.
**Rationale**: Snapshot-at-spawn captures the latest registered definition naturally. Snapshotting at parent-start would force the parent to embed every transitively-referenced child definition, blowing up record size and creating a stale-by-the-time-it-runs problem.

### Scenario: Cascade error mid-flight

**Given**: A cascade is in flight; the condition evaluator on the parent's next step (after auto-resume from a finalized child) raises an unexpected exception.
**When**: The cascade loop is building the `MutationBatch`.
**Then**: The exception propagates out of the cascade loop before any commit. `apply_mutation_batch` is never called. No state is written. The handler catches the exception and returns an error response. The child remains active, the parent's composition step remains `started`, and the parent's `current_step` is unchanged. The agent can retry.

### Scenario: Routing error with a typo'd step ID

**Given**: A child of `weekly-review` is active at step `02-categorize`. The agent calls `update_workflow_state(parent_id, "02-categorise", "complete")` (typo).
**When**: The handler runs.
**Then**: Routing reads the active chain, identifies the deepest layer (the child), and finds no match for the step ID. The tool returns: `Invalid step '02-categorise'. The deepest active layer is 'process-inbox-note'. Valid steps: 01-check, 02-categorize, 03-tag.`
**Rationale**: The error names the active workflow and lists the valid step IDs — the agent doesn't need to know which workflow is active.

### Scenario: Routing rejects a step ID from a non-deepest layer

**Given**: `weekly-review` is at step `02-handle-inbox` (a composition step) with the child `process-inbox-note` active at step `02-categorize`. The agent attempts to operate on the parent's composition step directly: `update_workflow_state(parent_id, "02-handle-inbox", "complete")`.
**When**: The handler runs.
**Then**: Routing identifies the deepest active layer as the child. The step ID `02-handle-inbox` does not match any step in the child's snapshot. The same routing error fires, naming `process-inbox-note` and listing its valid steps.
**Rationale**: There is no fall-through to other layers' snapshots. A parent step ID, a step ID from an unborn child, or a stale reference to a soft-deleted child all return the same self-correcting error — preventing the agent from accidentally completing the parent's composition step while the child is still mid-flight.

### Scenario: Deep nesting (3 levels)

**Given**: A non-cyclic graph `A → B → C` composes to depth 3 at runtime. The agent has progressed to a step in `C`.
**When**: The agent advances through every level.
**Then**: `get_active_chain(parent_id)` returns `[A, B, C]`. Routing always targets `C` until `C` finalizes. When `C`'s last step completes, the cascade pops to `B`, auto-completes `B`'s composition step, and advances `B`. If `B`'s next step is also a composition (spawning a sibling `D`), the cascade descends into `D` in the same tool call. The breadcrumb reflects the full active path.
**Rationale**: Load-time cycle detection bounds depth statically; runtime never encounters infinite recursion.

### Scenario: Stale subtree cleanup

**Given**: A parent's `updated_at` is 30 hours old (past the 24h threshold). Its active child's `updated_at` is 10 minutes old.
**When**: `StaleWorkflowCleanupProcessor.process` runs.
**Then**: `repository.list_stale(threshold)` walks each candidate top-level root and computes the subtree max `updated_at` — for this parent, the child's value (10 minutes ago). The subtree is NOT stale. The parent is not abort-cascaded. The active child keeps the parent alive.
**Rationale**: Staleness is a connected-component property, not a per-record property. An active child indicates work is ongoing.

### Scenario: First-time start of a loop step with non-empty items

**Given**: Parent is at step `02-prepare` STARTED. Step `03-process` declares `loop: process-item` and is `PENDING`.
**When**: Agent calls `update_workflow_state(parent_id, "02-prepare", "complete")` and then `update_workflow_state(parent_id, "03-process", "start", items=["a","b","c"])`.
**Then**: After the first call, the cascade marks `02-prepare` COMPLETED, advances, finds `03-process` is a loop step, halts auto-start, and returns a halt-prompt response naming the loop step. After the second call, all five validation gates pass; the cascade marks `03-process` STARTED, queues `UpdateState(loop_state={"03-process": {items, index: 0}})`, spawns iteration 0 with `current_item="a"`, and returns the iteration's first step's instructions with breadcrumb `<parent>/03-process > <iter-wf>/01-step (item: a)`.
**Rationale**: Auto-start cannot fabricate items; halting is the correct default. Once items are provided, iteration 0 spawns through the same `_try_spawn_child` path used by composition.

### Scenario: Iteration N+1 spawns in the same tool call as iteration N's finalize

**Given**: Parent has `loop_state["03-process"] = {items: ["a","b","c"], index: 1}`; iteration child for item `b` is at its last step.
**When**: Agent calls `update_workflow_state(parent_id, <last_iter_step>, "complete")`.
**Then**: Cascade pop SoftDeletes child_1, advances `loop_state["03-process"].index` to 2, spawns child_2 with `current_item="c"`, and returns child_2's first step instructions with breadcrumb `<parent>/03-process > <iter-wf>/01-step (item: c)`.
**Rationale**: Spawn-in-same-tool-call keeps the agent's progression linear — one `complete` per iteration, no manual re-`start`.

### Scenario: Empty items auto-completes the loop step

**Given**: Loop step `03-process` is `PENDING`. Agent calls `start` with `items=[]`.
**When**: The handler validates and the cascade runs.
**Then**: `03-process` transitions PENDING → COMPLETED in one atomic batch (no STARTED intermediate), `loop_state["03-process"] = {items: [], index: 0}` is persisted (audit trail), no iteration child is created, and the cascade auto-advances to the parent's next pending step (or finalizes the top-level if `03-process` was last).
**Rationale**: Empty items is a valid expression of "no items to process" — the engine treats it as zero-iteration completion, not an error.

### Scenario: Items-on-non-loop-step rejected even when condition would fail

**Given**: Step `01-plan` is a regular (non-loop) step with a `condition` that would evaluate non-passing.
**When**: Agent calls `update_workflow_state(parent_id, "01-plan", "start", items=["a"])`.
**Then**: Validation rejects with "items not allowed for non-loop steps" before condition evaluation runs (gate 3 precedes gate 4). No state change. Step stays `PENDING`.
**Rationale**: Splitting items validation into a structural gate (before condition) and a semantic gate (after condition) means non-loop-step + items errors surface even when the step's condition would have skipped the step anyway.

### Scenario: Loop + composes mutex rejected at registry load

**Given**: A workflow declares a step with both `loop: a` and `composes: b`.
**When**: The registry validates dependencies.
**Then**: The mutex pre-pass rejects the parent workflow with a malformed-step warning identifying `loop` and `composes` as conflicting fields. The workflow is removed from `_workflows` *before* edge collection runs, so cycle/reference DFS never sees its edges.
**Rationale**: Mutex check runs once before edge collection, mirroring `validate_references`'s "reject the parent on malformed `composes` value" pattern. Prevents partial-edge contributions from a workflow that's about to be rejected anyway.

### Scenario: Cycle introduced via cross-edge chain

**Given**: Workflow `A` has `loop: B`; `B` has `composes: C`; `C` has `loop: A`.
**When**: The registry validates dependencies.
**Then**: `detect_cycles` finds the cycle `{A, B, C}` over the unified edge set (loop and composes treated identically). All three workflows are removed; one cycle warning is logged listing the members.
**Rationale**: Loop edges and composes edges share the same cycle graph, so chains mixing both edge types are rejected by the same DFS.

### Scenario: Recovery after context loss mid-iteration

**Given**: An active loop on `03-process` with items=[a,b,c,d,e]. `loop_state["03-process"].index = 3` (the iteration for `items[3] = "d"` is active). The agent loses context.
**When**: The agent calls `list_active_workflows` then `get_workflow_state(parent_id)`.
**Then**: `list_active_workflows` returns only the parent (iteration child is hidden). `get_workflow_state` returns the parent's state with a `### Loop step` block showing `Items (5): a, b, c, d, e`, `Current iteration: 4 / 5` (1-indexed display of the underlying 0-indexed `index = 3`), `Current item: d`, plus the active iteration child block, plus the breadcrumb `<parent>/03-process > <iter-wf>/01-step (item: d)`. The agent has everything needed to resume.
**Rationale**: State lives on the parent's `loop_state` column; nothing is in conversation memory.

### Scenario: Nested loops — inner loop_state lives on the outer iteration child

**Given**: Loop target `process-batch` has a step `02-each` declaring `loop: process-item`. Outer loop on top-level parent runs with `items=[A, B]`; the inner loop on the outer iteration child runs with its own per-iteration items.
**When**: Outer iteration 0 (item `A`) reaches step `02-each`, agent provides `items=[x, y]`, then proceeds through inner iterations.
**Then**: Auto-start halts at the inner loop step; the agent calls `update_workflow_state` with `items` for the inner loop. Inner iterations run as grandchildren of the top-level parent. The inner loop's `loop_state` lives on the **outer iteration child's record** (whose snapshot defines `02-each`); the top-level parent's `loop_state` only carries the outer loop's progress.
**Rationale**: `loop_state` always lives on the record whose snapshot owns the loop step. Auto-start halt fires identically at every layer; each layer carries its own runtime state — no cross-record coordination required.

### Scenario: Abort cascade with active iteration

**Given**: Top-level parent is active with iteration child for item `b` mid-flight (`loop_state["03-process"].index = 1`).
**When**: Agent calls `end_workflow(parent_id, "abort")`.
**Then**: `abort_cascade` BFS-walks descendants via `parent_workflow_id`, soft-deletes parent + iteration child in one transaction, deletes the shared scratchpad once. The parent's `loop_state` column remains intact on the soft-deleted row (audit trail).
**Rationale**: Iteration children are regular composition children; `abort_cascade` handles them by structure, not by special-casing.

## Notes

- Workflow steps use the SDK's native TodoWrite for progress tracking, which is separate from Tachikoma's task management system
- The scratchpad concept is prompting-based — the agent is instructed to maintain notes in a file, not through a dedicated MCP tool
- `list_active_workflows` is intentionally simple (no filtering, no pagination) — it's a recovery tool, not a management tool
- The guidance text returned by `start_workflow` is a tunable parameter — its quality directly affects the reliability of the workflow system
- Scratchpad file location convention: `.tachikoma/scratchpads/workflow-<workflow_id>.md`
- Loop progress is durable; iteration children are ephemeral. The parent's `loop_state` column is the recovery anchor — if every iteration child were soft-deleted (e.g., abort cascade) the items list and index would still be readable on the soft-deleted parent row for audit
- The `loop_state` column key is the loop step's ID **on the record whose snapshot defines that step**. A record with two loop steps can carry two keys simultaneously (one active, one historical) without conflict. Nested loops live on different records — outer on top-level, inner on the outer iteration child
- Empty-string items (`items=["", "foo"]`) are accepted — the breadcrumb suffix renders as `(item: )` for the empty entry. By design: items are opaque references and the iteration body decides what they mean. Authors who want to reject empty items can do so with a `condition` on the iteration body's first step
- Audit-trail retention for `loop_state`: soft-deleted parent rows retain their `loop_state` payload intact. Stale-cleanup (24h threshold) eventually soft-deletes idle workflows but does not touch already-soft-deleted rows
