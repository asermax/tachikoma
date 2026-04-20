# Step Design Reference

This reference provides detailed guidance on designing effective workflow steps.

## Atomic Step Design

Each step should address a single, well-defined concern. This makes progress easier to track, validation clearer, and recovery simpler.

### What Makes a Step Atomic

A step is atomic when it:
- Has one primary goal (e.g., "gather requirements" not "plan the entire feature")
- Produces one main output (e.g., a requirements document, not the entire codebase)
- Can be validated independently (e.g., requirements are complete, not the whole feature)

### Examples: Atomic vs Non-Atomic

#### Example 1: Feature Planning

**Non-Atomic (too broad)**:
```
01-implement-feature/
└── instructions.md  # "Build the entire feature"
```

**Atomic (focused)**:
```
01-gather-requirements/
└── instructions.md  # "Identify what to build"
02-design-solution/
└── instructions.md  # "Plan how to build it"
03-implement-core/
└── instructions.md  # "Build the main functionality"
04-add-validation/
└── instructions.md  # "Add tests and checks"
```

#### Example 2: Code Refactoring

**Non-Atomic (mixed concerns)**:
```
01-refactor-and-test/
└── instructions.md  # "Refactor the code and add tests"
```

**Atomic (separated)**:
```
01-analyze-current/
└── instructions.md  # "Understand existing code"
02-design-refactor/
└── instructions.md  # "Plan the refactoring"
03-implement-changes/
└── instructions.md  # "Apply the refactoring"
04-validate-results/
└── instructions.md  # "Run tests and verify behavior"
```

## Good vs Bad Step Examples

### Good Step Characteristics

**Clear scope**:
```yaml
---
title: "Analyze Existing Code"
---

# Code Analysis

## Your Task
1. Read the main module file
2. Identify functions that need refactoring
3. Document current dependencies

## Output
Create an analysis document at `docs/analysis/{module-name}.md`
```

**Actionable instructions**:
```markdown
## Actions
1. Use `Glob` to find all test files
2. Use `Grep` to search for test patterns
3. Use `Read` to examine test coverage gaps
```

**Clear validation**:
```markdown
## Validation
- [ ] All test files are listed
- [ ] Coverage gaps are identified
- [ ] Recommendations are documented
```

### Bad Step Characteristics

**Vague instructions**:
```yaml
---
title: "Do Stuff"
---

# Step

Look at the code and make it better.
```

**No validation criteria**:
```yaml
---
title: "Write Code"
---

# Implementation

Write the code for this feature.
```

**Mixed concerns**:
```yaml
---
title: "Everything"
---

# All The Things

Design, implement, test, and deploy the feature.
```

## When to Use references/

The `references/` subdirectory is for detailed content that supplements the main instructions but doesn't need to be in the primary flow.

### Good Use Cases for references/

**Detailed technical documentation**:
```
01-setup/
├── instructions.md          # Main: "Configure the database"
└── references/
    ├── postgres-setup.md    # Detailed: PostgreSQL-specific steps
    ├── mysql-setup.md       # Detailed: MySQL-specific steps
    └── troubleshooting.md   # Detailed: Common issues and fixes
```

**Extended examples**:
```
02-write-code/
├── instructions.md          # Main: "Implement the API endpoint"
└── references/
    ├── rest-examples.md     # Detailed: REST API design patterns
    └── error-handling.md    # Detailed: Error handling strategies
```

**Reference material**:
```
03-validate/
├── instructions.md          # Main: "Run validation checks"
└── references/
    ├── lint-rules.md        # Detailed: Project linting configuration
    └── test-standards.md    # Detailed: Testing guidelines
```

### When NOT to Use references/

Don't use `references/` for:
- **Critical instructions**: If the agent must read it to complete the step, put it in the main instructions
- **Step-specific outputs**: If the reference is the primary output of the step, it should be the main content
- **Small content**: If the reference is just a few lines, include it directly in the instructions

## When to Use scripts/

The `scripts/` subdirectory is for executable code that the step should run as part of its execution.

### Good Use Cases for scripts/

**Validation scripts**:
```
03-validate/
├── instructions.md          # Main: "Run all validation checks"
└── scripts/
    ├── lint.sh              # Runs project linting
    ├── test.sh              # Runs test suite
    └── typecheck.sh         # Runs type checking
```

**Code generation**:
```
02-generate-boilerplate/
├── instructions.md          # Main: "Generate initial code structure"
└── scripts/
    └── generate.py          # Creates boilerplate files
```

**Data processing**:
```
01-process-input/
├── instructions.md          # Main: "Process input data"
└── scripts/
    └── transform.py         # Transforms data format
```

### When NOT to Use scripts/

Don't use `scripts/` for:
- **Documentation**: Documentation belongs in `references/` or the main instructions
- **Configuration**: Configuration files should be in the step directory or parent skill
- **Large executables**: Scripts should be small, focused helpers. Large tools should be separate project dependencies

## Frontmatter Field Reference

The `instructions.md` frontmatter supports these fields:

### Required Fields

**title** (string, required)
- Human-readable title for the step
- Displayed in workflow progress and state
- Should be concise and descriptive

```yaml
---
title: "Gather Requirements"
---
```

### Optional Fields

**skippable** (boolean, optional, default: false)
- If `true`, the step may be skipped when not applicable
- The agent should check conditions before executing
- Use for conditional steps (e.g., database setup when database exists)

```yaml
---
title: "Configure Database"
skippable: true
---
```

**required_skills** (list of strings, optional, default: empty)
- Names of skills the agent needs to execute this step
- On step activation, the declared skills — and their transitive
  dependencies via each skill's `depends_on` — are resolved through
  the skill registry and appended to the tool response, bypassing
  the normal skill classifier
- Use this for steps whose instructions are too terse or technical
  for the classifier to reliably infer the right foundations (git
  operations, API clients, domain-specific knowledge)
- Unknown skill names are warned about at workflow-load time and
  silently skipped at activation
- Shared transitive deps across declared skills are emitted once

```yaml
---
title: "Open a Pull Request"
required_skills:
  - git-operations
  - github-api
---
```

**Custom Fields** (any, optional)
- Workflow-specific metadata
- Can be used by the step logic or for documentation
- No predefined schema — extend as needed

```yaml
---
title: "Generate Code"
estimated_time: "5 minutes"
requires_tools:
  - Write
  - Edit
complexity: "low"
---
```

## Naming Conventions

### Workflow Directory Names

Use lowercase with hyphens:
```
workflows/
├── feature-planning/
├── bug-investigation/
└── code-refactor/
```

Good patterns:
- **Verb-noun**: `feature-planning`, `bug-investigation`, `code-refactor`
- **Descriptive**: `onboarding-new-developer`, `deploying-to-production`

Avoid:
- **Uppercase**: `FeaturePlanning` (incorrect)
- **Underscores**: `feature_planning` (incorrect)
- **Abbreviations**: `feat-plan` (unclear)

### Step Directory Names

Use two-digit prefix for ordering:
```
01-analyze/
02-design/
03-implement/
04-validate/
```

**Prefix format**:
- Always two digits: `01`, `02`, `03`
- Leading zero for single digits: `01` not `1`
- Gaps allowed for future expansion: `01`, `05`, `10`

**Suffix format**:
- Lowercase with hyphens
- Descriptive of the step's purpose
- Verb-noun pattern preferred

Good examples:
```
01-requirements/
02-design-considerations/
03-core-implementation/
04-validation/
05-deployment/
```

## Step Content Structure

A well-structured step includes these sections:

### 1. Context (optional)

Briefly explain why this step exists and what it builds on:

```markdown
# Requirements Gathering

This step builds on the feature request to produce a complete
requirements document. The requirements will guide the design
phase in the next step.
```

### 2. Your Task (required)

Clear, actionable instructions:

```markdown
## Your Task
1. Read the feature request at `docs/requests/my-feature.md`
2. Identify functional requirements (what the feature does)
3. Identify non-functional requirements (performance, security, etc.)
4. List assumptions and constraints
```

### 3. Output (required)

What the step should produce:

```markdown
## Output

Create a requirements document at `docs/requirements/my-feature.md`
with the following sections:
- Functional Requirements
- Non-Functional Requirements
- Assumptions
- Constraints
- Open Questions
```

### 4. Validation (required)

How to verify completion:

```markdown
## Validation

- [ ] All functional requirements are listed
- [ ] Non-functional requirements are specified
- [ ] Assumptions are explicit
- [ ] Constraints are documented
- [ ] Open questions are identified
```

### 5. References (optional)

Point to supplemental material:

```markdown
## References

- `docs/standards/requirements.md` — Requirements format standard
- `docs/templates/requirements.md` — Requirements template
```

## Common Step Patterns

### Analysis Steps

**Purpose**: Understand existing state or requirements

**Structure**:
```markdown
# Analysis

## Your Task
1. Read [source material]
2. Identify [key elements]
3. Document [findings]

## Output
Create an analysis document at [path]

## Validation
- [ ] All [elements] are identified
- [ ] Findings are documented
```

### Design Steps

**Purpose**: Plan a solution or approach

**Structure**:
```markdown
# Design

## Your Task
1. Review [requirements or analysis]
2. Propose [solution approach]
3. Document [design decisions]

## Output
Create a design document at [path]

## Validation
- [ ] Design addresses all requirements
- [ ] Decisions are justified
- [ ] Trade-offs are documented
```

### Implementation Steps

**Purpose**: Create or modify code

**Structure**:
```markdown
# Implementation

## Your Task
1. Review [design document]
2. Implement [changes]
3. Follow [coding standards]

## Output
Create or modify [files]

## Validation
- [ ] Code follows project standards
- [ ] Changes match design
- [ ] Tests pass (if applicable)
```

### Validation Steps

**Purpose**: Verify correctness or completeness

**Structure**:
```markdown
# Validation

## Your Task
1. Run [validation checks]
2. Review [results]
3. Document [findings]

## Validation
- [ ] All checks pass
- [ ] Issues are documented
- [ ] Next steps are clear
```

## Error Handling and Recovery

### When Steps Fail

If a step cannot be completed:
1. Document the failure in the workflow state
2. Provide context about what was attempted
3. Suggest recovery options

```markdown
## Error Handling

If [condition] fails:
1. Document the error in the workflow state
2. Capture error logs or messages
3. Suggest alternative approaches
```

### Recovery Context

Each step should provide enough context for recovery:

```markdown
## Recovery Context

This step produces:
- Requirements document: `docs/requirements/my-feature.md`
- Workflow state update: `{"requirements_complete": true}`

If interrupted after this step, resume by verifying the
requirements document exists and is complete.
```

## Testing Steps

Before finalizing a step, verify:

1. **Instructions are complete**: Can the agent follow them without additional context?
2. **Validation is clear**: Is there a way to confirm the step is done?
3. **Outputs are specified**: What files or state changes result from this step?
4. **Dependencies are clear**: What must exist before this step can run?
5. **Recovery is possible**: Can the agent resume after interruption?

## Checklist

Use this checklist when designing a step:

- [ ] **Atomic**: Does the step address one concern?
- [ ] **Clear instructions**: Are the actions specific and actionable?
- [ ] **Defined output**: Is it clear what the step produces?
- [ ] **Validation criteria**: Can completion be verified?
- [ ] **Appropriate naming**: Does the directory name describe the step?
- [ ] **Recovery support**: Is there enough context to resume after interruption?
- [ ] **Frontmatter complete**: Are required fields (title) provided?
- [ ] **References organized**: Is supplemental material in `references/`?
- [ ] **Scripts prepared**: Are executable helpers in `scripts/`?

---

For workflow-level guidance, see the main `SKILL.md`.
