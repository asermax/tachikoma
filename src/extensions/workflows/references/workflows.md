# Workflows

Multi-step processes backed by a persisted state machine. Owned by the workflows extension.

## Tools

| Tool | Role |
|------|------|
| `start_workflow` | Begin an instance of a workflow a skill defines |
| `update_workflow_state` | Start, complete, or skip a step (always with the **top-level** id — the engine routes to the deepest active layer and shows a breadcrumb) |
| `query_workflow` | Inspect active workflows and their step states |
| `end_workflow` | Abort or close out an instance |

## Model

A workflow is a step-directory tree inside a skill. Each instance's state lives in the
database, not the conversation — which is the point: a long procedure survives context
compaction and session boundaries and resumes cleanly. Composed and looping layers are
supported; the breadcrumb in each update result shows where you are in the nesting.

## Stale instances

A `start_workflow` rejection naming an existing ID means a prior run of that workflow is
still active — often one interrupted mid-run in an earlier session. Recover it before
starting over:

1. **Find**: `query_workflow()` lists active workflows; `query_workflow(workflow_id=...)`
   returns the full state — per-step statuses, current step, and the scratchpad path.
2. **Inspect**: read the scratchpad for progress notes. If the file is gone, the state
   view itself is the evidence of what was done.
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
