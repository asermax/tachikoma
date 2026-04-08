---
description: |
  Activates when the user wants to create, define, set up, build, or scaffold a new workflow; encode a multi-step process or save a procedure for reuse; define automation for workflows.
  Also triggers on requests to author a workflow, write a workflow, help me make a workflow, guide me through workflow authoring, create-workflow,
  how to create a workflow, make a workflow, build a workflow, new workflow
---

# Workflow Authoring Guide

This guide provides everything you need to create well-structured workflows. Read this document when asked to create, define, or set up a new workflow for a skill.

## What Workflows Are

Workflows are ordered multi-step processes defined within skills. They provide:
- **Structured execution**: Steps run in sequence with clear boundaries
- **State persistence**: Progress is saved between steps, enabling resumption after interruptions
- **Validation checkpoints**: Each step can validate before proceeding
- **Recovery**: Lost context can be recovered by listing active workflows and resuming

Workflows are **optional** — skills may have zero, one, or many workflows depending on their purpose.

## Directory Conventions

Workflows live in a `workflows/` subdirectory within a skill:

```
skills/
└── my-skill/
    ├── SKILL.md              # Mentions available workflows
    └── workflows/
        └── my-workflow/
            ├── 01-first-step/
            │   ├── instructions.md
            │   ├── references/
            │   └── scripts/
            ├── 02-next-step/
            │   └── instructions.md
            └── 03-final-step/
                └── instructions.md
```

**Naming**:
- Workflow directories: lowercase with hyphens (e.g., `feature-planning`, `code-refactor`)
- Step directories: two-digit prefix for ordering (e.g., `01-analyze`, `02-design`, `03-implement`)

## SKILL.md Integration Pattern

Critical: Workflows are discovered by reading a skill's SKILL.md body, not by automatic detection. You **must** document workflows in the SKILL.md for the agent to know they exist.

### How to Document Workflows

In the SKILL.md body, add a section listing available workflows:

```markdown
## Available Workflows

This skill provides the following workflows:

### feature-planning
Use when planning a new feature or enhancement. Steps through requirements analysis, design considerations, and implementation planning.

### bug-investigation
Use when investigating a bug or unexpected behavior. Steps through reproduction, root cause analysis, and fix validation.
```

For each workflow, explain:
- **When to use it**: What situation triggers the need for this workflow
- **What it does**: High-level description of the steps
- **Expected outcome**: What the workflow produces

This documentation is how the agent discovers which workflows a skill offers and when to invoke them.

## Workflow Structure

Each workflow is a directory containing ordered step directories:

```
workflows/
└── my-workflow/
    ├── 01-first-step/
    │   └── instructions.md
    ├── 02-second-step/
    │   ├── instructions.md
    │   ├── references/
    │   │   └── detailed-guidance.md
    │   └── scripts/
    │       └── helper.py
    └── 03-final-step/
        └── instructions.md
```

### Step Directories

Each step directory contains:
- **instructions.md** (required): The step's content with YAML frontmatter
- **references/** (optional): Detailed documentation loaded on demand
- **scripts/** (optional): Executable scripts for the step

### Step Ordering

Steps are ordered by directory name using a two-digit prefix:
- `01-*`, `02-*`, `03-*`, etc.
- Gaps are allowed (e.g., `01-*`, `05-*`, `10-*` for future expansion)
- The agent executes steps in sorted order

## instructions.md Format

Each step must have an `instructions.md` file with YAML frontmatter:

```yaml
---
title: "Analyze Requirements"
skippable: false
custom_field: "optional metadata"
---

# Step Instructions

The step's guidance goes here. Explain what the step does,
what to produce, and how to validate completion.
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `title` | Yes | Human-readable step title |
| `skippable` | No | If `true`, step may be skipped when not applicable (default: `false`) |
| `*` | No | Custom fields for workflow-specific metadata |

### Body Content

The body is markdown content injected when the step executes. Explain:
- What the step accomplishes
- What actions to take
- What outputs to produce
- How to validate completion

## Step Design Patterns

### Atomic Steps

Each step should address one concern. Break complex work into smaller, focused steps:

```markdown
# Good: Atomic steps
01-requirements/  # Gather and analyze requirements
02-design/        # Design the solution
03-implement/     # Write the code
04-test/          # Validate the implementation

# Bad: One giant step
01-do-everything/  # Too broad — hard to track progress
```

### Clear Instructions

Each step should provide actionable guidance:

```markdown
# Good: Specific actions
## Your Task
1. Read the feature request in `docs/feature-specs/my-feature.md`
2. Identify the core requirements
3. List any ambiguities or missing information

## Output
Create a requirements checklist as a markdown file:
```

```markdown
# Bad: Vague
## Your Task
Think about the requirements and write them down.
```

### Leveraging References and Scripts

Use `references/` for detailed content that doesn't fit in the main instructions:

```
01-setup/
├── instructions.md          # Main: "Configure the database"
└── references/
    ├── postgres-config.md   # Detailed: PostgreSQL setup
    └── mysql-config.md      # Detailed: MySQL setup
```

Use `scripts/` for executable code that the step should run:

```
02-validate/
├── instructions.md          # Main: "Run validation checks"
└── scripts/
    └── lint-checks.sh       # Executable: Runs project linting
```

## Workflow Execution Flow

When the agent executes a workflow:

1. **Start**: `start_workflow(skill_name, workflow_name)` creates the workflow and returns the step list
2. **First step**: `update_workflow_state(workflow_id, step, action="start")` begins the first step and returns its instructions
3. **Execute**: The agent performs the step's actions, producing outputs
4. **Advance**: `update_workflow_state(workflow_id, step, action="complete")` marks the step done, **auto-starts** the next step, and returns its instructions — no separate `start` call needed
5. **Finalize**: When the last step is completed, the workflow is **auto-finalized** (state cleaned up automatically)

To abort a workflow early, use `end_workflow(workflow_id, action="abort")`.

If context is lost during execution:
- `list_active_workflows()` shows in-flight workflows
- `get_workflow_state(workflow_id)` recovers current step and state data
- Resume from the current step

## Example Workflow

Here's a complete example workflow for feature planning:

```
workflows/
└── feature-planning/
    ├── 01-requirements/
    │   └── instructions.md
    ├── 02-design/
    │   ├── instructions.md
    │   └── references/
    │       └── design-patterns.md
    └── 03-implementation-plan/
        └── instructions.md
```

**01-requirements/instructions.md**:
```yaml
---
title: "Gather Requirements"
skippable: false
---

# Requirements Gathering

## Your Task
1. Read the feature request or user story
2. Identify functional and non-functional requirements
3. List assumptions and constraints

## Output
Create a requirements document at `docs/requirements/{feature-name}.md`

## Validation
- All requirements are testable
- Assumptions are explicit
- Constraints are documented
```

**02-design/instructions.md**:
```yaml
---
title: "Design Solution"
skippable: false
---

# Solution Design

## Your Task
1. Review the requirements from the previous step
2. Propose a solution architecture
3. Identify components and their interactions

## Output
Create a design document at `docs/designs/{feature-name}.md`

For detailed design patterns, see `references/design-patterns.md`.

## Validation
- Design addresses all requirements
- Components have clear responsibilities
- Integration points are defined
```

**03-implementation-plan/instructions.md**:
```yaml
---
title: "Create Implementation Plan"
skippable: false
---

# Implementation Planning

## Your Task
1. Break down the design into implementable tasks
2. Order tasks by dependency
3. Estimate effort for each task

## Output
Create an implementation plan at `docs/plans/{feature-name}.md`

## Validation
- Tasks are actionable and sized appropriately
- Dependencies are clear
- Plan accounts for testing and validation
```

## Best Practices

### Write Atomic Steps

Keep steps focused on one concern. If a step feels like it's doing too much, split it:

```
# Before: Too broad
01-implementation/

# After: Split into focused steps
01-setup/
02-implementation/
03-testing/
```

### Provide Clear Validation Criteria

Each step should define what "done" looks like:

```markdown
## Validation
- [ ] All files pass linting (`just lint`)
- [ ] All tests pass (`just test`)
- [ ] Type checking succeeds (`just typecheck`)
```

### Use Skippable Steps Appropriately

Mark steps as `skippable: true` when they're conditionally needed:

```yaml
---
title: "Configure Database"
skippable: true
---

# Database Configuration

Skip this step if the project already has a configured database.
Check for `config/database.toml` to verify.
```

### Document Recovery Scenarios

In the SKILL.md, explain how to resume after interruption:

```markdown
## Recovery

If you lose context during this workflow:
1. Call `list_active_workflows()` to find the workflow
2. Call `get_workflow_state(workflow_execution_id)` to see where you left off
3. Resume from the current step — all progress is preserved
```

## Testing Your Workflow

Before considering a workflow complete:

1. **Manual walkthrough**: Read through each step's instructions — do they make sense?
2. **Ordering check**: Are steps in the right order? Do dependencies flow correctly?
3. **Validation check**: Does each step have clear success criteria?
4. **SKILL.md check**: Is the workflow documented in the skill's SKILL.md?

## Common Patterns

### Planning Workflow
Steps: requirements → design → implementation-plan → validation

### Refactoring Workflow
Steps: analyze-current → design-refactor → implement-changes → validate

### Onboarding Workflow
Steps: read-context → understand-architecture -> setup-environment → first-task

---

For detailed step design guidance, see `references/step-design.md`.
