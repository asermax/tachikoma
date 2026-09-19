# Workflows

Multi-step processes backed by a persisted state machine. Owned by the workflows extension.

## Tools

| Tool | Role |
|------|------|
| `start_workflow` | Begin an instance of a workflow a skill defines |
| `update_workflow_state` | Start, complete, or skip a step (always with the **top-level** id — the engine resolves the step id against the active chain and shows a breadcrumb) |
| `query_workflow` | Inspect active workflows and their step states |
| `end_workflow` | Abort or close out an instance |

Step ids resolve across the active composed/loop chain, so an id the engine announced
keeps working after a sub-workflow spawns. Completing the in-flight composes/loop step
of a waiting layer ends its sub-workflow early — the active child layers are discarded
and the parent resumes from its next step.

## Model

A workflow is a step-directory tree inside a skill. Each instance's state lives in the
database, not the conversation — which is the point: a long procedure survives context
compaction and session boundaries and resumes cleanly. Composed and looping layers are
supported; the breadcrumb in each update result shows where you are in the nesting.

A step carrying a `condition` surfaces its predicate **before** it starts — in the start
guidance when it gates the first step, when auto-advance halts at it, and as an `(if: ...)`
marker in `query_workflow`'s step list. Evaluate it before calling `start`: a step can only
be skipped while it is pending.

## Driving a Workflow

`start_workflow(skill_name, workflow_name)` creates the instance (one active per
skill+workflow), returns the step list, and seeds a scratchpad file for progress notes.
From there a single tool drives everything: `update_workflow_state`, always with the
**top-level** workflow id.

**Instructions arrive in the tool result.** The result that starts a step — an explicit
`action="start"`, or the auto-start that follows completing/skipping the previous step —
carries that step's `instructions.md` body plus its step path. Until a step starts, its
instructions are unread; a result that halts at an `(if: ...)` or `(loop: ...)` step
instead carries the decision to make, not instructions. If you lose a started step's
instructions, the query state view names the current step and its step path —
re-read its instructions.md (what you read back is the current body).

**Auto-advance.** Completing or skipping a step auto-starts the next pending step and
returns its instructions in the same response — no separate start call. When the last
step finishes, the workflow is auto-finalized (state and scratchpad cleaned up; no
`end_workflow` call needed).

**Per-step mechanics** — the markers shown in the step list and `query_workflow`:

| Step | Start | Skip |
|------|-------|------|
| plain | begins the step; returns its instructions | only when not `required` |
| `(skippable)` | as plain | allowed while pending |
| `(if: ...)` | auto-advance halts — evaluate the predicate, then start if it holds | the fail path: allowed while pending, even when `required` |
| `(loop: ...)` | halts — requires `items=[...]` (opaque strings; the target runs once per item, in order; current item rides the breadcrumb) | start with `items=[]` instead — completes with zero iterations |
| `(composes: ...)` | spawns the sub-workflow; drive its steps with the same top-level id (the step's own body is never shown) | allowed while pending if skippable — advances without running the sub-workflow |

Completing the in-flight composes/loop step of a waiting layer ends its sub-workflow
early. Completing a loop's current iteration child spawns the next item's iteration in
the same response; after the last item the loop step completes and the parent resumes.

**Recovery.** After context loss, call `query_workflow()` to list active workflows, then
`query_workflow(workflow_id=...)` for the full state: per-step statuses with their
markers, the current step and its step path, and the scratchpad path. Resume from the
current step — all progress is preserved.

## Stale instances

A `start_workflow` rejection naming an existing ID means a prior run of that workflow is
still active — often one interrupted mid-run in an earlier session. Recover it before
starting over:

1. **Find**: `query_workflow()` lists active workflows; `query_workflow(workflow_id=...)`
   returns the full state — per-step statuses, current step, and the scratchpad path.
2. **Inspect**: read the scratchpad for progress notes. If the file is gone, the state
   view itself is the evidence of what was done — and the current step's instructions can
   be re-read from the step path the view shows.
3. **Decide**: compare the per-step states and scratchpad contents against the current
   request. If the instance serves it, resume from the current step — all progress is
   preserved, and resuming is preferred over restarting. End it only when the work is
   superseded or unwanted.
4. **Surface**: before ending, tell the user what the interrupted run had done and ask
   whether to resume or start fresh. Both `end_workflow` actions discard the state and
   scratchpad identically. Ending a top-level instance tears down its whole nested stack
   of composed/loop children too.

An instance no longer listed by `query_workflow()` was ended or expired: instances idle
too long are expired at session close (`staleHours`, default 24). Starting fresh then
succeeds with nothing to recover.

## Configuration

`[extensions.workflows]`: `enabled` (default `true`); `staleHours` (default `24`).
