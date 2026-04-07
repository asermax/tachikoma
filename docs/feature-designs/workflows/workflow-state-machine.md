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
| `src/tachikoma/workflows/definition.py` | `StepDefinition` and `WorkflowDefinition` frozen dataclasses for filesystem-parsed workflow model | Follows skill dataclass pattern; uses directory name as step ID |
| `src/tachikoma/workflows/loader.py` | `load_workflows()` — discovers `workflows/` subdirectories within skill dirs, reads step subdirectories sorted alphabetically, parses `instructions.md` frontmatter via python-frontmatter | Uses same frontmatter library as skill loading; logs warnings for invalid steps |
| `src/tachikoma/workflows/model.py` | `StepState` type alias, `WorkflowState` frozen dataclass, `WorkflowStateRecord` ORM model on `workflow_states` table; `to_domain()` method; JSON serialization helpers | Follows ADR-007 (SQLAlchemy async + aiosqlite); JSON columns for step_states and definition_snapshot; soft delete via `deleted_at` |
| `src/tachikoma/workflows/repository.py` | `WorkflowStateRepository` — async CRUD: create, get (non-deleted), get_active, update, soft_delete, list_active, list_stale | Application-level duplicate prevention (SQLite doesn't support partial unique indexes); always bumps `updated_at` |
| `src/tachikoma/workflows/tools.py` | `create_workflow_tools_server()` — MCP server factory with 5 tools; Pydantic arg models; extracted handler functions; transition validation logic | Follows DES-006 (MCP tool server factory); handlers testable without SDK |
| `src/tachikoma/workflows/cleanup.py` | `StaleWorkflowCleanupProcessor(PostProcessor)` — soft-deletes stale workflows, deletes scratchpad files, runs in `pre_finalize` phase | Extends PostProcessor directly (not PromptDrivenProcessor — no SDK fork needed) |
| `src/tachikoma/workflows/hooks.py` | `workflows_hook` — bootstrap hook: creates repository from shared Database, stores in extras | Follows DES-003 (subsystem-owned bootstrap hooks) |
| `src/tachikoma/skills/registry.py` | Extended with `_workflows` dict, `get_workflow()`, `workflows` property; workflow definitions discovered during `_load_skill()` | Extends existing registry without creating a new one |
| `src/tachikoma/context/loading.py` | Static `# Workflows` section in `SYSTEM_PREAMBLE_TEMPLATE` | Follows ADR-008 (system prompt composition via append) |
| `src/tachikoma/skills/builtin/workflow-authoring-guide/` | Built-in skill with SKILL.md and `references/step-design.md` | Follows existing built-in skill pattern |

### Cross-Layer Contracts

**MCP Tool Schemas**:

```
start_workflow(skill_name: str, workflow_name: str)
  → { workflow_id: str, steps: [{id, title, skippable}], scratchpad_path: str, guidance: str }
  | { error: str, existing_workflow_id: str }

update_workflow_state(workflow_id: str, step: str, action: "start" | "complete" | "skip")
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
- `start`: step must be `pending`; marks as `started`; returns step_path + instructions
- `complete`: step must be `started` (not `pending` — must start before completing); marks as `completed`; advances to next pending step
- `skip`: step must be `skippable: true` in frontmatter AND `pending`; marks as `skipped`; advances to next pending step
- Any action on a completed/skipped step: error with explanation
- All actions update `updated_at` on the workflow state record

**Integration Points**:
- MCP tools call skill registry to resolve skill_name -> workflow definition
- MCP tools call workflow state repository for all DB operations
- Stale cleanup runs as post-processor in `pre_finalize` phase, before GitProcessor in `finalize`
- System preamble static text added to preamble template
- `start_workflow` guidance instructs agent to persist workflow ID in scratchpad file
- `end_workflow` soft-deletes workflow (sets `deleted_at`) and deletes associated scratchpad file
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
  skippable: bool (default false)
  properties: dict (extensible frontmatter fields)

WorkflowState (database — table: `workflow_states`):
  id: str (UUID, PK)
  skill_name: str (Indexed)
  workflow_name: str (Indexed)
  current_step: str | None
  step_states: JSON ({"01-plan": "completed", "02-execute": "started", ...})
  definition_snapshot: JSON (serialized step definitions at start time)
  scratchpad_path: str (absolute path, e.g., ".tachikoma/scratchpads/workflow-abc123.md")
  deleted_at: datetime | None (NULL = active, set on soft delete)
  created_at: datetime
  updated_at: datetime

  Duplicate prevention: application-level check via get_active() before create
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
4. Agent persists workflow ID and progress notes in scratchpad file at scratchpad_path
5. Agent creates TodoWrite tasks for steps
6. Agent calls update_workflow_state(id, step, "start")
   -> Validates step exists and is next in order
   -> Updates step_states in DB
   -> Returns step_path + instructions content (instructions.md body)
7. Agent executes step, using step_path to navigate references/ and scripts/ as needed
8. Agent calls update_workflow_state(id, step, "complete")
   -> Marks step completed, advances to next step
   -> Returns next step_path + instructions (or completion message)
9. Repeat 6-8 until all steps done
10. Agent calls end_workflow(id, "complete") -> DB record soft-deleted, scratchpad file deleted
```

### Recovery flow (context loss)

```
1. Agent loses workflow ID (context compaction, session corruption)
2. Agent reads scratchpad file -> recovers workflow ID
   OR (scratchpad also lost):
3. Agent calls list_active_workflows() -> recovers workflow ID
4. Agent calls get_workflow_state(id) -> full state for resumption
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

**Choice**: The `start_workflow` tool constructs a guidance string that instructs the agent to use TodoWrite, persist the workflow ID in a scratchpad file, and call `update_workflow_state` to progress
**Why**: This guidance is the primary mechanism for agent task tracking and scratchpad usage. The quality of this guidance directly affects workflow reliability and must be treated as a tunable parameter refined based on agent behavior.

**Consequences**:
- Pro: Guidance adapts as agent behavior is observed in practice
- Pro: No additional infrastructure needed

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

## Notes

- Workflow steps use the SDK's native TodoWrite for progress tracking, which is separate from Tachikoma's task management system
- The scratchpad concept is prompting-based — the agent is instructed to maintain notes in a file, not through a dedicated MCP tool
- `list_active_workflows` is intentionally simple (no filtering, no pagination) — it's a recovery tool, not a management tool
- The guidance text returned by `start_workflow` is a tunable parameter — its quality directly affects the reliability of the workflow system
- Scratchpad file location convention: `.tachikoma/scratchpads/workflow-<workflow_id>.md`
