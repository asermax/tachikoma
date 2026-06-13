# Workflows

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Workflows map multi-step processes to directory trees inside skill packages: each workflow is a folder of ordered step directories, each step carrying an `instructions.md` with frontmatter. Running a workflow creates a database-persisted step state machine (pending/started/completed/skipped) that the agent drives through four lifecycle tools — start, update, query, end. Because state lives in the database and progress notes live in a scratchpad file, workflows survive context compaction and session boundaries; instances abandoned across sessions are expired by a cleanup post-processor.

Steps can also compose other workflows. A step may declare `composes` (run a sub-workflow to completion, then resume the parent), `loop` (run a sub-workflow once per agent-supplied item), or `condition` (a natural-language predicate that halts auto-advance so the agent decides whether to start or skip). Composition nests to arbitrary depth; the agent drives the whole nested run with a single top-level workflow ID, and a cascade engine routes each transition to the deepest active layer.

## User Stories

- As a skill developer, I want to define multi-step workflows inside my skill so that the agent executes ordered sequences without skipping steps or losing its place
- As the assistant, I want tools that validate state transitions so that I progress through steps with ordering and completion guarantees
- As the assistant, I want workflow state to survive context loss so that I can find and resume active workflows after compaction or a new session
- As the system, I want stale instances cleaned up so that abandoned workflows do not accumulate
- As a skill developer, I want a step to inline-reference another workflow so reusable sub-sequences are shared across parents instead of duplicated
- As a skill developer, I want a step to iterate a referenced workflow once per item in an agent-supplied list so batch processes are expressed without unrolling steps by hand
- As a skill developer, I want steps gated by natural-language conditions so workflows branch around unnecessary work without hardcoded logic

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Workflows are defined as `skills/<skill>/workflows/<workflow>/` directories of step subdirectories; steps are ordered by directory-name sort (convention: `01-`, `02-` prefixes) |
| R1 | Each step requires an `instructions.md` with a frontmatter `title`; steps missing the file or a valid title are skipped with a warning |
| R2 | Steps default to `required: true`; `required: false` makes a step skippable; the deprecated `skippable` alias is honored with a deprecation warning; non-reserved frontmatter keys are preserved as extensible properties |
| R3 | Optional `references/` and `scripts/` step subdirectories are detected and recorded on the step definition |
| R4 | Workflow definitions are read fresh from the filesystem on every lookup — edits on disk apply to the next `start_workflow` without a restart |
| R5 | Instance state is persisted in the `workflow_states` table: per-step states, current step, a definition snapshot frozen at start, scratchpad path, and created/updated/deleted timestamps |
| R6 | `start_workflow` creates an instance with a unique ID and a scratchpad file, and returns the step list plus guidance (first-step call, scratchpad usage, progression rules, recovery); it rejects unknown workflows, zero-step workflows, and a second instance while one is active for the same skill+workflow |
| R7 | `update_workflow_state` validates the transition and returns step instructions; completing or skipping a step auto-starts the next pending step and returns its instructions; when no pending step remains the workflow auto-finalizes (soft-deleted, scratchpad removed) with a completed/skipped tally |
| R8 | Transition rules: start only a pending step; complete only a started step; skip only a pending, non-required step; completed and skipped steps are immutable; unknown step IDs are rejected listing the valid IDs |
| R9 | `query_workflow` with an ID returns the full state view (skill, workflow, current step, scratchpad path, timestamps, per-step states); without an ID it lists all active workflows — the recovery path after context loss |
| R10 | `end_workflow` (actions `complete`/`abort`) soft-deletes the instance and removes its scratchpad; normal completion is handled by auto-finalize, so the tool is primarily for aborting |
| R11 | All reads exclude soft-deleted records; ended or stale-cleaned IDs answer with a "not found or no longer active" error |
| R12 | A session-close post-processor soft-deletes instances whose `updated_at` is older than a configurable threshold (default 24 hours) and removes their scratchpads; failures are isolated per record and never propagate |
| R13 | Configuration: `[extensions.workflows]` supports `enabled` (default true) and `staleHours` (default 24) |
| R14 | Scratchpads live under `{workspace}/.tachikoma/scratchpads/`, created by a bootstrap hook; the scratchpad is seeded with the workflow name and ID; if instance creation fails after the scratchpad is written, the file is rolled back |
| R15 | A step may declare `composes: <workflow>` (same-skill) or `composes: <skill>/<workflow>` (cross-skill); activating it spawns the referenced child workflow, runs it to completion, then auto-completes the parent step and resumes the parent — one continuous nested run from the agent's view |
| R16 | A step may declare `loop: <ref>` (same syntax as `composes`, mutually exclusive with it); `update_workflow_state` accepts an optional `items` string array on the `start` action, and the loop target runs once per item in order. `items` is required on a loop step, rejected on a non-loop step, and rejected on any non-`start` action |
| R17 | An empty `items` list completes the loop step with zero iterations and the cascade advances normally; each iteration is a full composition child inheriting all step-level semantics (per-iteration snapshot, condition, nested composition/loops) |
| R18 | Loop bookkeeping (`{<step_id>: {items, index}}`) is persisted in a `loop_state` JSON column on the parent layer's record; the immutable definition snapshot is not mutated |
| R19 | A step may declare a `condition` (natural-language predicate); when the cascade auto-advances to it, auto-start halts, the step stays pending, and the response surfaces the predicate with instructions to `start` (passes) or `skip` (fails) — the agent is the evaluator. A `condition` makes an otherwise-required step skippable |
| R20 | When auto-advance reaches a pending loop step, it halts (the step stays pending) and the response prompts the agent to start it with `items` |
| R21 | The agent drives a nested run with the single top-level workflow ID; transitions route to the deepest active layer. An unknown step ID returns an error naming the deepest layer's workflow and listing its valid step IDs |
| R22 | Activation responses while nested are prefixed with a breadcrumb showing the active path (`parent/step > child/step`, separator ` > `); the deepest segment carries an `(item: <value>)` suffix during a loop iteration |
| R23 | Composed children inherit the parent's scratchpad path and are exempt from the one-active-instance uniqueness check; `query_workflow` listing surfaces only top-level workflows |
| R24 | `query_workflow(id)` on a top-level workflow inlines the active child path and any loop blocks; a child ID returns a standalone view with a note pointing at the parent; an active composition step whose target is no longer registered prepends a corruption warning directing abort |
| R25 | `end_workflow` abort on a top-level tears down all transitive descendants atomically; a call on a child ID is rejected, directing to the top-level. Stale cleanup considers the freshest `updated_at` across the whole active stack — an active child keeps the parent alive |
| R26 | Composition references are validated at bootstrap: a step declaring both `composes` and `loop`, a cycle (composes/loop edges share one graph), and missing/empty/rejected targets reject the workflow with a logged warning; rejection cascades to parents. At runtime the cascade additionally guards against exceeding a safe nesting depth |

## Behaviors

### Definition Loading (R0, R1, R2, R3, R4)

`findWorkflow` (`src/extensions/workflows/loader.ts`) resolves `skills/<skill>/workflows/<workflow>` from disk on every call.

**Acceptance Criteria**:
- Given step directories `02-review`, `01-plan`, when the workflow loads, then steps are ordered `01-plan`, `02-review` with frontmatter titles, `required` flags, and extra keys in `properties`
- Given a step with `skippable: true`, when loaded, then it behaves as `required: false` and a deprecation warning is logged
- Given a step directory without `instructions.md`, or with a missing/invalid `title`, when loaded, then the step is skipped with a warning and remaining steps load
- Given a step containing `references/`, when loaded, then `referencesPath` is set and `scriptsPath` is null (and vice versa)
- Given a step declares `condition`, `composes`, or `loop`, when loaded, then each is preserved as a string on the step definition; a non-string value logs a warning and is treated as unset (the step still loads)
- Given a workflow directory that does not exist, when looked up, then the result is null and `start_workflow` reports the workflow as not found

### Lifecycle Tools (R6, R7, R8, R9, R10, R11)

Four pi tools registered per agent session (`registerWorkflowTools` in `src/extensions/workflows/tools.ts`); handler errors surface as tool errors.

**Acceptance Criteria**:
- Given `start_workflow(skill_name, workflow_name)` for a valid workflow, when it runs, then a record is created with every step `pending`, a scratchpad file exists, and the response contains the step list (skippable steps marked), the workflow ID, and progression/recovery guidance
- Given an instance is already active for the same skill+workflow, when `start_workflow` is called again, then the call fails citing the existing instance's ID
- Given `update_workflow_state(id, step, "start")` on a pending step, when it runs, then the step becomes `started`, `current_step` updates, and the response contains the step's `instructions.md` body plus its directory path
- Given `action="complete"` on a started step with later pending steps, when it runs, then the next pending step is auto-started and its instructions are returned in the same response
- Given `action="skip"` on a pending non-required step, when it runs, then the step becomes `skipped` and the next pending step (in snapshot order) auto-starts
- Given the last unfinished step is completed or skipped, when the update runs, then the workflow is auto-finalized: record soft-deleted, scratchpad removed, response tallying completed and skipped steps
- Given an invalid transition (complete before start, skip a required step, act on a finished step, unknown step ID, unknown workflow ID), when attempted, then the tool fails with a specific explanation and state is unchanged
- Given `query_workflow(workflow_id)`, when it runs, then the full state view is returned; without an ID, all active instances are listed with IDs and current steps; with none active, "No active workflows."
- Given `end_workflow(id, "abort")`, when it runs, then the record is soft-deleted, the scratchpad removed, and subsequent tool calls with that ID fail as not active

### State Persistence and Snapshot (R5)

**Acceptance Criteria**:
- Given a workflow starts, when the record is written, then it embeds a `definition_snapshot` (step IDs, titles, required flags, step paths) frozen from the definition at that moment
- Given the definition on disk changes while an instance is active, when tools validate transitions, then they validate against the frozen snapshot — step structure cannot shift under a running instance
- Given the process restarts, when `query_workflow` is called, then active instances created before the restart are still listed and resumable

### Composition, Loops, and Conditions (R15–R26)

`runCascade` (`src/extensions/workflows/cascade.ts`) reads the active chain (top-level root down through composed/loop children), routes the transition to the deepest layer, applies it, and auto-advances across layer boundaries, staging every change as a single atomic `MutationBatch`. `validateWorkflowGraph` (`composition.ts`) runs at bootstrap to reject unsafe definitions.

**Acceptance Criteria**:
- Given a step with `composes`, when it activates, then a child workflow is spawned, the breadcrumb prefixes the response, and the child's first step's instructions are returned (not the composition step's body)
- Given the agent completes the child's last step, when the update runs, then in one response the child is finalized, the parent's composition step auto-completes, and the parent's next step auto-starts
- Given a step with `loop`, when auto-advance reaches it, then auto-start halts and the response prompts for `items`; calling `start` with `items=["a","b"]` runs the target for `a`, and completing that iteration spawns the iteration for `b`; after the last item the loop step completes and the parent resumes
- Given `start` with `items=[]` on a loop step, when it runs, then the loop step completes with zero iterations and the cascade advances
- Given `items` on a non-loop step, a non-`start` action, or omitted on a loop step, when attempted, then the tool fails with a specific explanation and state is unchanged
- Given a step with a `condition`, when auto-advance reaches it, then it halts pending and surfaces the predicate; `start` proceeds and `skip` is allowed even when the step is `required`
- Given the agent operates on a composed child's ID, when it calls `update_workflow_state` or `end_workflow`, then the call is rejected, directing to the top-level workflow
- Given `query_workflow(top_level_id)` while nested, when it runs, then the view inlines the active child path, the breadcrumb, and any loop blocks (items list, 1-indexed current iteration, current item)
- Given `end_workflow(top_level_id, "abort")` on a nested run, when it runs, then the root and every descendant are soft-deleted atomically and the scratchpad removed
- Given definitions with a cycle, a `composes`+`loop` step, or a missing/empty/rejected target, when the extension bootstraps, then each offending workflow is logged as rejected; rejection cascades to parents

### Stale Cleanup (R12, R13)

`createStaleWorkflowCleanup` (`src/extensions/workflows/cleanup.ts`) registers a `main`-phase post-processor that runs when a session closes.

**Acceptance Criteria**:
- Given an active instance untouched for longer than `staleHours`, when a session closes, then the instance is soft-deleted and its scratchpad removed; fresher instances are untouched
- Given an active child keeps a stale root's subtree fresh, when cleanup runs, then the whole stack is preserved; only stacks whose entire subtree exceeds `staleHours` are expired
- Given the repository fails while listing or deleting, when cleanup runs, then errors are logged and the processor resolves without throwing — session close is never blocked

## Notes

- `items` is a real string array (pi tool parameters support arrays); the values are opaque references the loop target interprets, not paths the engine reads
- Skill loading is pi-native: there is no `required_skills` injection — a step relies on pi's progressive disclosure for skills, and a composition step's body is not read (the child's steps carry the instructions)
- Conditions are agent-evaluated: the engine halts and delegates the decision rather than calling a model to evaluate the predicate
- TodoWrite-style task tracking has no pi equivalent; the start guidance instead directs the agent to maintain the scratchpad file and use `query_workflow` for recovery
- Listing active workflows and reading one workflow's state are a single `query_workflow` tool rather than two separate tools

Workflow definitions ship inside skill packages — see [skills](skills.md).
