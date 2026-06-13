# Design: Workflows

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/workflows.md](../feature-specs/workflows.md)
**Status**: Current

## Purpose

Explain how directory-based workflow definitions become database-persisted step state machines, and why the engine is split into a pure handler layer under a thin pi tool surface.

## Problem Context

Agents are unreliable at executing long ordered procedures from prose alone: steps get skipped, order drifts, and context compaction erases progress. The workflow engine moves the source of truth out of the conversation — definitions live on disk inside skills, instance state lives in SQLite — and makes the tools the only way to transition state. Beyond the flat sequence, steps compose: a step can run another workflow to completion (`composes`), iterate one per agent-supplied item (`loop`), or gate on a natural-language predicate (`condition`). A cascade engine collapses these nested transitions into single tool calls so the agent experiences one continuous run addressed by a single top-level ID.

**Constraints:**
- State must survive context compaction, session replacement, and process restarts
- Tool handlers must be testable without pi or a live model ([DES-002](../design/DES-002-extension-authoring.md))
- Definitions are user-edited files; a running instance must not break when its definition is edited mid-flight
- Tables follow the central drizzle schema/migration flow ([DES-001](../design/DES-001-unified-extension-api.md))

**Interactions:**
- Definitions live under `skills/<skill>/workflows/` inside skill packages ([skills](skills.md))
- Tools are registered per agent session via `app.agent.use` (see [conversation-loop](conversation-loop.md))
- Stale cleanup runs in the session-close post-processing pipeline (`app.sessions.registerProcessor`)

## Design Overview

```
disk definitions          db instance state
loader.ts  ──snapshot──▶  repository.ts / schema.ts
     │                          ▲   (applyMutationBatch / abortCascade / chains)
     │                          │
     └─ tools.ts handlers ─ cascade.ts ─ composition.ts
                 ▲          (runCascade)   (resolve/validate + mutation types)
        registerWorkflowTools (pi)      cleanup.ts (post-processor)
```

`loader.ts` reads definitions fresh from disk. `handleStartWorkflow` freezes the step list into a `definition_snapshot` and seeds the scratchpad. From then on, `handleUpdateWorkflowState` delegates to `runCascade` (`cascade.ts`), which reads the active chain, routes the transition to the deepest layer, auto-advances across composition/loop boundaries, and stages every change as a `MutationBatch` the repository applies atomically. `composition.ts` holds the pure helpers — `resolveComposes`, cycle/reference validation, and the mutation/outcome types. `query_workflow` doubles as the recovery tool and renders the nested view. `cleanup.ts` expires abandoned stacks at session close. For a flat workflow (no `composes`/`loop`/`condition`) the chain is one layer and the cascade reduces to the original auto-start behavior.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/workflows/index.ts` | Wiring: config, repository, scratchpad bootstrap, graph validation, per-session tool registration, cleanup processor registration | `findWorkflow` closure binds the skills dir so tools never see paths; a bootstrap hook runs `validateWorkflowGraph` and logs rejections |
| `src/extensions/workflows/loader.ts` | Parse workflow/step directories into `WorkflowDefinition`/`StepDefinition`; `loadAllWorkflows` for graph validation | Fresh filesystem read per call; invalid steps skipped with warnings, never fatal; `condition`/`composes`/`loop` parsed as optional strings; `skippable` kept as a deprecated alias of `required: false` |
| `src/extensions/workflows/model.ts` | `STEP_STATES` const map, `StepStates`, `StepSnapshot`, `LoopState` | Snapshot keeps the step *path* plus `condition`/`composes`/`loop` so the cascade reads structure from the frozen snapshot |
| `src/extensions/workflows/schema.ts` | `workflow_states` drizzle table | JSON columns for `step_states`, `definition_snapshot`, and `loop_state`; nullable `parent_workflow_id`/`parent_step_id` link children; indexes on skill/workflow, the active-lookup pair, and the parent lookup |
| `src/extensions/workflows/composition.ts` | `resolveComposes`, `detectCycles`, `validateWorkflowGraph`; `Mutation`/`MutationBatch`/`CascadeOutcome`/`BreadcrumbPart` types | Pure, SDK/DB-free so cycle and reference validation are testable in isolation |
| `src/extensions/workflows/cascade.ts` | `runCascade` (the engine), `validateTransition`, `stepToSnapshot`, `renderBreadcrumb` | Synchronous (better-sqlite3 is sync); stages a `MutationBatch` and throws on routing failure so state is untouched; a depth guard backstops cycles at runtime |
| `src/extensions/workflows/repository.ts` | `WorkflowStateRepository` CRUD + chains + `applyMutationBatch`/`abortCascade` | Every read filters `deleted_at IS NULL`; `getActive`/`listActive` are top-level only; `getActiveChain` walks a stack; batch apply and abort run in a single transaction; `listStale` is subtree-aware |
| `src/extensions/workflows/tools.ts` | The four `handle*` functions, nested query rendering, `registerWorkflowTools` | Handlers are pure functions over `WorkflowToolDeps` — pi registration is a thin `execute` that wraps the string result |
| `src/extensions/workflows/cleanup.ts` | `createStaleWorkflowCleanup` post-processor | Depends on `Pick<WorkflowStateRepository, "listStale" \| "softDelete">`; per-record error isolation |

## Key Decisions

### Freeze the step structure at start, read instruction bodies live

**Choice**: `start_workflow` snapshots step IDs, titles, required flags, and paths into the record; `instructions.md` content is re-read from the snapshotted path each time a step activates.
**Why**: A running instance must validate transitions against a stable step list even if the author reshuffles directories mid-run — but instruction *text* edits are harmless and useful to pick up (fixing a typo in step 3 while step 1 runs).
**Alternatives Considered**:
- Snapshot full instruction bodies: safest, but bloats rows and freezes harmless text fixes
- No snapshot, always re-read structure: renamed or removed step directories would corrupt active instances

**Consequences**:
- Pro: definition edits apply cleanly to the next `start_workflow` (loader has no cache) without disturbing running instances
- Con: deleting a step directory mid-run makes its instructions unreadable — the response then carries only the transition confirmation and step path

### Pure handlers under a thin pi tool surface

**Choice**: `handleStartWorkflow`/`handleUpdateWorkflowState`/`handleQueryWorkflow`/`handleEndWorkflow` are exported functions over a `WorkflowToolDeps` bag; `registerWorkflowTools` only maps params and wraps results.
**Why**: The state machine is the part worth testing exhaustively; with handlers free of pi types, `tests/workflows/tools.test.ts` exercises the full lifecycle against a real SQLite database and tmp-dir fixtures with no pi session.
**Consequences**:
- Pro: transition rules, cascade, and finalization are covered end-to-end in fast tests
- Pro: errors are plain `throw`s — pi turns them into tool errors uniformly
- Con: parameter schemas and handler signatures must be kept in sync by hand

### Soft delete instead of hard delete

**Choice**: Ending, finalizing, and stale-cleaning all set `deleted_at`; every repository read filters it out.
**Why**: Finished and expired instances keep an audit trail for debugging agent behavior, and "not found or no longer active" stays one uniform error path whether an ID was finished, aborted, or expired.
**Consequences**:
- Pro: no destructive cleanup; history is queryable directly in SQLite
- Con: the table grows unboundedly until some future vacuum mechanism exists

### One `query_workflow` tool for both state lookup and listing

**Choice**: One tool with an optional `workflow_id` covers both state lookup and listing, instead of separate `get_workflow_state` and `list_active_workflows` tools.
**Why**: They serve the same recovery moment ("what was I doing?"); a single tool with a natural narrowing parameter is one less name for the model to choose between.
**Consequences**:
- Pro: recovery guidance reduces to "call `query_workflow()` then `query_workflow(workflow_id=...)`"
- Con: two response shapes behind one tool name

### Cascade engine staging an atomic MutationBatch

**Choice**: `runCascade` walks the active chain entirely in memory — applying the transition, descending into spawned children, advancing parents when children finish — and emits an ordered `MutationBatch` (`update`/`create`/`softDelete`) that the repository applies in one transaction. It throws on any routing/validation failure before staging side effects.
**Why**: A single agent tool call can ripple across several layers (complete a child's last step → finalize child → complete the parent's composition step → start the parent's next step → spawn the next loop iteration). Computing the whole effect first, then committing atomically, keeps nested state consistent even if a spawn fails mid-cascade — the batch rolls back and the agent sees an error with state untouched.
**Alternatives Considered**:
- Mutate the database step-by-step inside the walk: simpler to write, but a failure halfway leaves a half-advanced stack
- Recurse with the model in the loop (one tool call per layer): defeats the "one continuous run" goal and multiplies latency

**Consequences**:
- Pro: nested transitions are all-or-nothing; handlers stay synchronous and pure over the chain
- Con: the engine holds a mirror of the chain's mutable state, which must track the real records faithfully

### Address nested runs by the top-level ID, route to the deepest layer

**Choice**: Children carry `parent_workflow_id`/`parent_step_id`; the agent only ever names the top-level ID, and the cascade resolves the active chain and applies the transition at the deepest layer. Child IDs are rejected by `update_workflow_state` and `end_workflow`.
**Why**: The agent should not have to track which sub-workflow it is "inside" — that is bookkeeping the engine already has. One stable ID per run also means recovery (`query_workflow`) and abort have a single handle, and the one-active-instance rule cleanly applies to top-levels only.
**Consequences**:
- Pro: the agent drives arbitrarily deep nesting with one ID and a breadcrumb to orient
- Con: routing errors must name the deepest layer's workflow and valid steps, since the agent's mental model can lag the real depth

### Loop iterations are repeated composition children; conditions halt and delegate

**Choice**: A `loop` step spawns the target as a fresh composition child per item, tracking `{items, index}` in a `loop_state` JSON column on the parent; iteration N+1 spawns in the same call that finalizes N. A `condition` step halts auto-advance and asks the agent to `start` (passes) or `skip` (fails) — the agent is the evaluator, and a condition makes a required step skippable.
**Why**: Reusing the composition machinery means iterations inherit every step-level semantic (nested composition, conditions, snapshots) for free. Delegating condition evaluation to the agent avoids a second model round-trip inside a tool call and keeps the engine deterministic — it never calls a model.
**Consequences**:
- Pro: batch processes and branches need no new execution model; the engine stays model-free and testable
- Con: loop items are opaque strings the target must interpret; a careless condition step the agent always starts is just a normal step with a prompt

### Validate the composition graph at bootstrap, guard depth at runtime

**Choice**: `validateWorkflowGraph` runs once at bootstrap over every workflow, rejecting cycles, `composes`+`loop` steps, and missing/empty/rejected targets (rejection cascades). Because the loader reads fresh on every call, the cascade additionally caps nesting depth to backstop a cycle introduced by a mid-session edit.
**Why**: A composition cycle would spin the cascade's auto-advance forever inside one tool call. Bootstrap validation gives authors early, specific warnings; the runtime depth guard guarantees termination regardless of edits between reloads.
**Consequences**:
- Pro: authoring mistakes surface at startup, not mid-run; infinite loops are structurally impossible
- Con: a workflow edited to introduce a cycle after bootstrap fails at runtime with a depth error rather than a load-time warning until the next reload

## System Behavior

### Scenario: Auto-start cascade and auto-finalize

**Given**: A three-step workflow where step `02-research` is `required: false`
**When**: The agent skips `02-research`, completes `01-plan`, then completes `03-write`
**Then**: Skipping auto-starts `01-plan` (the first *pending* step in snapshot order, not necessarily the one after the skipped step); completing `01-plan` auto-starts `03-write`; completing `03-write` finds nothing pending, soft-deletes the record, removes the scratchpad, and reports "2 completed, 1 skipped".

### Scenario: Recovery after context loss

**Given**: An active instance whose ID was compacted out of the conversation
**When**: The agent calls `query_workflow()` with no arguments
**Then**: All active instances are listed with IDs and current steps; `query_workflow(workflow_id)` then returns per-step states, the scratchpad path, and timestamps — enough to resume at the correct step.

### Scenario: Stale expiry at session close

**Given**: An instance last updated 25 hours ago and another updated 1 hour ago, with `staleHours = 24`
**When**: The session closes and post-processing runs
**Then**: The old instance is soft-deleted and its scratchpad removed; the fresh one survives. A repository failure is logged and the processor still resolves — session close never fails on cleanup.

### Scenario: Failed start leaves no debris

**Given**: The scratchpad was written but the database insert throws
**When**: `handleStartWorkflow` propagates the error
**Then**: The scratchpad file is deleted before rethrowing — no orphan files for instances that never existed.

### Scenario: Completing a child resumes the parent in one call

**Given**: A `outer` workflow whose `02-sub` step `composes: inner`, with the agent on `inner`'s last step
**When**: The agent calls `update_workflow_state(outer_id, <inner_last_step>, "complete")`
**Then**: In one response the cascade marks the child's step complete, finds nothing pending in the child, soft-deletes it, marks `outer`'s `02-sub` complete, auto-starts `outer`'s next step, and returns that step's instructions — the whole `MutationBatch` (update child, soft-delete child, update parent) commits atomically.

### Scenario: Loop iterates then resumes; abort tears down the stack

**Given**: A `02-each` step `loop: handle-one`, started with `items=["x","y"]`
**When**: The agent completes each `handle-one` run in turn, then later aborts the top-level
**Then**: Completing iteration `x` spawns iteration `y` in the same call (breadcrumb suffix `(item: y)`); completing `y` exhausts the loop, marks `02-each` complete, and resumes the parent. An `end_workflow(outer_id, "abort")` at any point soft-deletes the root and every active descendant in one transaction.

## Notes

- Scratchpad deletion is best-effort (`deleteScratchpad` swallows fs errors): a leftover file must never block a state transition
- Tests run the real central migrations (`drizzle/`): `0001_extensions.sql` creates `workflow_states`, `0002_workflow_composition.sql` adds the parent links and `loop_state` column additively (safe for existing databases)
- `required_skills` injection is intentionally absent: skill loading is pi-native (progressive disclosure), so a composition step relies on pi rather than the engine resolving skill chains into the tool response
