---
name: workflow-authoring
description: |
  Activates when the user wants to create, define, set up, build, or scaffold a new workflow; encode a multi-step process or save a procedure for reuse; define automation for workflows. Also triggers on requests to author a workflow, write a workflow, help me make a workflow, guide me through workflow authoring, how to create a workflow, make a workflow, build a workflow, new workflow.
---

# Workflow Authoring Guide

This guide provides everything you need to create well-structured workflows. Read this document when asked to create, define, or set up a new workflow for a skill.

## What Workflows Are

Workflows are ordered multi-step processes defined within skills. They provide:

- **Structured execution**: steps run in sequence with clear boundaries
- **State persistence**: progress is saved between steps, enabling resumption after interruptions
- **Recovery**: lost context can be recovered by querying active workflows and resuming

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

- Workflow directories: lowercase with hyphens (e.g. `feature-planning`, `code-refactor`)
- Step directories: two-digit prefix for ordering (e.g. `01-analyze`, `02-design`, `03-implement`)

Steps execute in **lexicographic order of their directory names** — the numeric prefix is what orders them. Gaps are allowed (`01-*`, `05-*`, `10-*`) to leave room for future steps.

## SKILL.md Integration Pattern

Critical: workflows are discovered by reading the skill's SKILL.md body, not by automatic detection. You **must** document workflows in the SKILL.md for the agent to know they exist:

```markdown
## Available Workflows

### feature-planning
Use when planning a new feature. Steps through requirements analysis,
design considerations, and implementation planning.
```

For each workflow, explain when to use it, what it does, and the expected outcome.

## Step Format

Each step directory contains:

- **instructions.md** (required): the step's content with YAML frontmatter
- **references/** (optional): detailed documentation read on demand
- **scripts/** (optional): executable scripts for the step

A step directory without an `instructions.md`, or whose frontmatter is missing a valid `title`, is skipped at load time with a warning — the rest of the workflow still loads.

### instructions.md

```yaml
---
title: "Analyze Requirements"
---

# Step Instructions

The step's guidance goes here. Explain what the step does,
what to produce, and how to validate completion.
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `title` | Yes | Human-readable step title (non-empty string) |
| `required` | No | If `false`, the step may be skipped when not applicable (default: `true`) |
| `condition` | No | Natural-language predicate; auto-advance halts so the agent decides start vs skip (see [Composing Workflows](#composing-workflows)) |
| `composes` | No | Run another workflow to completion when this step activates |
| `loop` | No | Run another workflow once per agent-supplied item |
| `*` | No | Custom fields are preserved as step metadata but are not interpreted by the engine |

`skippable: true` is a deprecated alias for `required: false` — use `required` in new workflows. `composes` and `loop` are mutually exclusive on one step.

### Body Content

The body is returned to the agent when the step starts. Explain:

- What the step accomplishes
- What actions to take
- What outputs to produce
- How to validate completion

## Workflow Execution Flow

The agent drives workflows through four tools:

1. **Start**: `start_workflow(skill_name, workflow_name)` creates a tracked instance with a unique ID, a scratchpad file for progress notes, and returns the step list. Only one instance per skill/workflow pair can be active at a time.
2. **First step**: `update_workflow_state(workflow_id, step, action="start")` begins the first step and returns its instructions.
3. **Execute**: the agent performs the step's actions, producing outputs and updating the scratchpad.
4. **Advance**: `update_workflow_state(workflow_id, step, action="complete")` marks the step done, **auto-starts** the next pending step, and returns its instructions — no separate `start` call needed. `action="skip"` does the same for steps declared `required: false`.
5. **Finalize**: when the last step is completed or skipped, the workflow is **auto-finalized** — state and scratchpad are cleaned up automatically.

To abort a workflow early, use `end_workflow(workflow_id, action="abort")`.

### Recovery

Workflow state survives context loss and restarts:

- `query_workflow()` without arguments lists all active workflows
- `query_workflow(workflow_id=...)` returns the full state: per-step status, current step, and scratchpad path
- Resume from the current step — all progress is preserved

Starting a workflow while an instance of it is already active is rejected, naming the existing instance's ID. When that happens, recover the existing instance rather than discarding it: inspect its state and scratchpad, resume it if it serves the request, and otherwise tell the user what the interrupted run had done and ask whether to resume or start fresh before ending it. Both `end_workflow` actions discard the state and scratchpad — never end an active instance without surfacing what it had done.

Instances abandoned across sessions are expired automatically after a configurable staleness window.

## Composing Workflows

Beyond a flat sequence, a step can pull in another workflow. This lets you share reusable sub-sequences, iterate over a list, and branch — all driven by the **single top-level workflow ID**. When a sub-workflow is active, your `update_workflow_state` calls route to its steps automatically, and responses carry a breadcrumb (`parent/step > child/step`) so you know where you are.

A composition reference is `<workflow>` for a workflow in the same skill, or `<skill>/<workflow>` to reach across skills. A composed step's own `instructions.md` body is not shown — the child's steps carry the instructions.

### `composes` — run a sub-workflow inline

```yaml
---
title: "Process the inbox note"
composes: process-inbox-note
---
```

When this step activates, the engine starts `process-inbox-note`, returns its first step, and you drive it like any workflow. When its last step completes, this step auto-completes and the parent's next step starts — one continuous run.

A `composes` step can also be `required: false` or carry a `condition`: skipping it (or failing its condition) advances the parent **without** running the sub-workflow.

### `loop` — run a sub-workflow once per item

```yaml
---
title: "Handle every pending reminder"
loop: send-one-reminder
---
```

A loop step does not auto-start. When the cascade reaches it, it halts and asks you to start it with an `items` list:

```
update_workflow_state(workflow_id, "03-handle", action="start", items=["r1", "r2", "r3"])
```

`items` is a list of opaque strings — IDs, filenames, whatever the sub-workflow knows how to interpret. The loop target runs once per item, in order; completing one iteration spawns the next. The current item appears in the breadcrumb (`... (item: r2)`) and in `query_workflow`. Pass `items=[]` to complete the loop step with zero iterations. `items` is required on a loop step and rejected anywhere else.

### `condition` — branch on a natural-language predicate

```yaml
---
title: "Escalate to the user"
condition: "the issue could not be resolved automatically"
---
```

When auto-advance reaches a condition step it halts and shows you the predicate. You evaluate it against the current context and either `action="start"` (condition holds) or `action="skip"` (it does not). A condition makes the step skippable even if it is `required`.

### Authoring rules

- `composes` and `loop` cannot both be on one step.
- Composition graphs must be acyclic — `A composes B`, `B composes A`, or a step composing its own workflow are rejected at load with a warning, as are references to missing or empty workflows. Check the logs after adding composition.
- Document composed/looped sub-workflows in SKILL.md like any other workflow so they can also be started on their own.

## Step Design Patterns

### Atomic Steps

Each step should address one concern. Break complex work into smaller, focused steps:

```
# Good: atomic steps
01-requirements/  # Gather and analyze requirements
02-design/        # Design the solution
03-implement/     # Write the code
04-test/          # Validate the implementation

# Bad: one giant step
01-do-everything/  # Too broad — hard to track progress
```

### Clear Instructions

Each step should provide actionable guidance:

```markdown
# Good: specific actions
## Your Task
1. Read the feature request in `docs/feature-specs/my-feature.md`
2. Identify the core requirements
3. List any ambiguities or missing information

## Output
Create a requirements checklist as a markdown file
```

Avoid vague directives like "think about the requirements and write them down".

### Provide Clear Validation Criteria

Each step should define what "done" looks like:

```markdown
## Validation
- [ ] All files pass linting
- [ ] All tests pass
- [ ] Type checking succeeds
```

### Mark Steps as Optional When Needed

Use `required: false` for steps that are conditionally needed, and say in the body how to decide:

```yaml
---
title: "Configure Database"
required: false
---

# Database Configuration

Skip this step if the project already has a configured database.
Check for `config/database.toml` to verify.
```

### Leveraging References and Scripts

Use `references/` for detailed content that doesn't fit in the main instructions, and `scripts/` for executable code the step should run:

```
02-validate/
├── instructions.md          # Main: "Run validation checks"
├── references/
│   └── lint-rules.md        # Detailed: rule explanations
└── scripts/
    └── lint-checks.sh       # Executable: runs project linting
```

## Example Workflow

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

## Testing Your Workflow

Before considering a workflow complete:

1. **Manual walkthrough**: read through each step's instructions — do they make sense?
2. **Ordering check**: are steps in the right order? Do dependencies flow correctly?
3. **Validation check**: does each step have clear success criteria?
4. **SKILL.md check**: is the workflow documented in the skill's SKILL.md?

## Common Patterns

- **Planning**: requirements → design → implementation-plan → validation
- **Refactoring**: analyze-current → design-refactor → implement-changes → validate
- **Onboarding**: read-context → understand-architecture → setup-environment → first-task
