# Workflow State Machine

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A workflow construct within skills that maps multi-step processes to directory trees the agent navigates natively. Workflows are defined as subdirectories within a skill's `workflows/` folder, each containing ordered steps with instructions, references, and scripts. MCP tools manage the full lifecycle — starting a workflow creates tracked state in the database, updating state validates transitions and returns step-specific instructions, and ending a workflow cleans up. State persists across context compaction because it lives in the database, not in conversation memory. The agent uses the SDK's built-in Task tools to track step progress, guided by instructions returned from the workflow tools.

## User Stories

- As a skill developer, I want to define multi-step workflows within my skill so that the agent can reliably execute ordered sequences without skipping steps or losing its place
- As the agent, I want workflow tools that enforce state transitions so that I can progress through steps with guarantees about ordering and completion
- As the agent, I want workflow state to survive context compaction so that I can resume long-running workflows after interruptions
- As the agent, I want a recovery mechanism when I lose my workflow ID so that I can find and resume active workflows
- As the system, I want stale workflow cleanup so that abandoned workflows don't accumulate indefinitely

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Skills can optionally contain a `workflows/` folder with multiple workflow definitions, each as a subdirectory of ordered steps |
| R1 | MCP tools manage the full lifecycle (start, update state, query state, end) — the tools are the state machine boundary |
| R2 | Workflow state persisted in a database table |
| R3 | `start_workflow` creates state with unique ID, returns overview (step list, guidance to create tasks, how to update state) |
| R4 | `update_workflow_state` validates transitions, returns step instructions on start/complete/skip |
| R5 | Step instructions.md has frontmatter with title and extensible properties (e.g., skippable) enforced by transition validation |
| R6 | `end_workflow` completes or aborts, cleans up state |
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
- Given a step without `references/` or `scripts/`, when the workflow runs, then the step functions normally (both are optional)
- Given a step directory without an `instructions.md`, when the workflow definition is loaded, then the step is rejected and a warning is logged
- Given a step with invalid YAML frontmatter, when the workflow definition is loaded, then a warning is logged identifying the step and the invalid field
- Given a workflow directory with no step subdirectories, when `start_workflow` is called for it, then the tool returns an error indicating the workflow has no steps

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
- Given a workflow ID and step identifier with action "complete", when `update_workflow_state` is called, then the step is marked completed; if a next step exists, its instructions are returned; if it was the last step, the result indicates the workflow is complete
- Given a workflow ID and step identifier with action "skip", when `update_workflow_state` is called and the step is marked skippable, then the step is marked skipped and state advances past it
- Given a workflow ID and step identifier with action "skip", when `update_workflow_state` is called and the step is NOT marked skippable, then the transition is rejected with an explanation
- Given a workflow where the current step is not yet started, when "complete" is called on it, then the tool returns an error (must start before completing)
- Given an invalid workflow ID, when `update_workflow_state` is called, then the tool returns an error indicating the workflow is not active
- Given a valid workflow ID but an invalid step identifier, when `update_workflow_state` is called, then the tool returns an error listing the valid step identifiers
- Given a completed workflow, when `update_workflow_state` is called, then the tool returns an error indicating the workflow is no longer active

### MCP Tools — get_workflow_state (R16)

**Acceptance Criteria**:
- Given a workflow ID, when `get_workflow_state` is called, then the tool returns: workflow ID, skill name, workflow name, current step, all step states (pending/started/completed/skipped), and created/updated timestamps
- Given an invalid or completed workflow ID, when `get_workflow_state` is called, then the tool returns an error indicating the workflow is not active
- Given the agent loses conversation context (context compaction, session transition), when it calls `get_workflow_state` with a known ID, then the full state is returned enabling resumption from the correct step

### MCP Tools — end_workflow (R6)

**Acceptance Criteria**:
- Given a workflow ID, when `end_workflow` is called with action "complete", then the workflow state is soft-deleted from the database
- Given a workflow ID, when `end_workflow` is called with action "abort", then the workflow state is soft-deleted from the database
- Given an invalid workflow ID, when `end_workflow` is called, then the tool returns an error
- Given a completed or already-ended workflow, when any workflow tool is called with its ID, then the tool returns an error indicating the workflow no longer exists

### MCP Tools — list_active_workflows (R18)

**Acceptance Criteria**:
- Given `list_active_workflows` is called, then the tool returns all active workflows with id, skill name, workflow name, current step, and started timestamps
- Given no active workflows exist, when `list_active_workflows` is called, then the tool returns an empty list
- Given the agent loses all context (scratchpad and workflow ID), when it calls `list_active_workflows`, then all active workflow IDs are returned for recovery

### State Persistence (R2)

**Acceptance Criteria**:
- Given a workflow is started, when state is written to the database, then the record includes: workflow ID, skill name, workflow name, current step, step states JSON (per-step: pending/started/completed/skipped), definition snapshot JSON, scratchpad path, created timestamp, updated timestamp
- Given a workflow state record, when the agent queries it via `get_workflow_state`, then the full state is available
- Given the process restarts, when active workflows exist in the database, then they remain available (the agent can resume by calling `get_workflow_state` with the stored ID)
- Given a workflow definition is changed while a workflow is active, when the agent calls any workflow tool, then the workflow uses the definition snapshot from when it was started (not the modified definition)

### Agent Task Tracking (R8)

**Acceptance Criteria**:
- Given `start_workflow` returns a step list, when the output is processed by the agent, then it is instructed to create TodoWrite tasks matching each step
- Given a step is started or completed via update_workflow_state, when the agent processes the tool result, then it is instructed to update the corresponding TodoWrite task
- Given the agent does not create TodoWrite tasks, when the workflow tools are called, then the workflow still functions correctly — TodoWrite is advisory guidance, not enforced

### Agent Scratchpad (R9)

**Acceptance Criteria**:
- Given a workflow is active, when the start_workflow output includes scratchpad guidance, then the agent is instructed to maintain notes in a designated file location within the workspace
- Given the scratchpad instructions, when the agent transitions between steps, then it is prompted to review and update its scratchpad
- Given the scratchpad is a file in the workspace, when context compaction occurs, then the agent can read the scratchpad file to recover its notes

### Concurrent Workflows (R10)

**Acceptance Criteria**:
- Given two different skills with workflows, when both are started, then each has its own ID and independent state
- Given the same workflow from the same skill already active, when `start_workflow` is called again, then the tool returns an error indicating the workflow is already running (returns the existing ID) — one active instance per skill+workflow name
- Given multiple active workflows, when update_workflow_state is called for one, then only that workflow's state changes

### System Preamble (R13)

**Acceptance Criteria**:
- Given the system preamble is assembled, when a Workflows section is included, then it explains the workflow concept, available MCP tools (start, update, get state, end, list active), and how workflows relate to skills

### Built-in Workflow Authoring Guide (R14)

**Acceptance Criteria**:
- Given a built-in skill `workflow-authoring-guide` exists, when the registry loads built-in skills, then it is available like the skill-authoring-guide
- Given the workflow-authoring-guide, when the agent reads it, then it provides guidance on creating workflow definitions (directory structure, naming convention for ordering, frontmatter fields, step design patterns, relationship to parent skill resources)

### Stale State Cleanup (R15)

**Acceptance Criteria**:
- Given active workflow records in the database, when a cleanup mechanism runs, then workflows whose `updated_at` is older than a configurable threshold (default 24 hours) are soft-deleted
- Given a stale workflow is cleaned up, when the agent references its ID, then the tool returns an error indicating the workflow no longer exists
- Given a workflow actively being updated within the threshold, when cleanup runs, then it is not removed

## Requires

Dependencies:
- Skill system (skills.md) — workflow definitions live within skills
- Core architecture (core-architecture.md) — MCP tools registration
- Post-processing pipeline (post-processing-pipeline.md) — stale cleanup processor
