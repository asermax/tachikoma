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

Instances idle too long are expired at session close (`staleHours`, default 24).

## Configuration

`[extensions.workflows]`: `enabled` (default `true`); `staleHours` (default `24`).
