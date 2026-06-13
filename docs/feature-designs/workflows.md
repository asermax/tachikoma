# Design: Workflows

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/workflows.md](../feature-specs/workflows.md)
**Status**: Current

## Purpose

Explain how directory-based workflow definitions become database-persisted step state machines, and why the engine is split into a pure handler layer under a thin pi tool surface.

## Problem Context

Agents are unreliable at executing long ordered procedures from prose alone: steps get skipped, order drifts, and context compaction erases progress. The workflow engine moves the source of truth out of the conversation — definitions live on disk inside skills, instance state lives in SQLite — and makes the tools the only way to transition state. The engine deliberately starts from the flat core — no composition, loops, or conditions.

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
     │                          ▲
     └────── tools.ts handlers ─┘   (validateTransition + auto-start cascade)
                 ▲
        registerWorkflowTools (pi)      cleanup.ts (post-processor)
```

`loader.ts` reads definitions fresh from disk. `handleStartWorkflow` freezes the step list into a `definition_snapshot` and seeds the scratchpad; from then on, `handleUpdateWorkflowState` validates transitions against the snapshot, auto-starts the next pending step, and auto-finalizes when nothing pending remains. `query_workflow` doubles as the recovery tool. `cleanup.ts` expires abandoned instances at session close.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/workflows/index.ts` | Wiring: config, repository, scratchpad bootstrap, per-session tool registration, cleanup processor registration | `findWorkflow` closure binds the skills dir so tools never see paths |
| `src/extensions/workflows/loader.ts` | Parse workflow/step directories into `WorkflowDefinition`/`StepDefinition` | Fresh filesystem read per call; invalid steps skipped with warnings, never fatal; `skippable` kept as a deprecated alias of `required: false` |
| `src/extensions/workflows/model.ts` | `STEP_STATES` const map, `StepStates`, `StepSnapshot` | Snapshot keeps the step *path* so instruction bodies are read live at activation |
| `src/extensions/workflows/schema.ts` | `workflow_states` drizzle table | JSON columns for `step_states` and `definition_snapshot`; indexes on skill/workflow and the active-lookup pair |
| `src/extensions/workflows/repository.ts` | `WorkflowStateRepository` CRUD | Every read filters `deleted_at IS NULL`; `getActive(skill, workflow)` backs the one-active-instance rule; `listStale(thresholdMs)` for cleanup |
| `src/extensions/workflows/tools.ts` | `validateTransition`, the four `handle*` functions, `registerWorkflowTools` | Handlers are pure functions over `WorkflowToolDeps` — pi registration is a thin `execute` that wraps the string result |
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

## Notes

- Scratchpad deletion is best-effort (`deleteScratchpad` swallows fs errors): a leftover file must never block a state transition
- `tests/workflows/helpers.ts` mirrors the table DDL until central migrations are regenerated; the real migration lives in `drizzle/0001_extensions.sql`
- Composition, loops, condition steps, and `required_skills` are intentionally absent (see spec Notes); `validateTransition` is the seam where they would re-enter
