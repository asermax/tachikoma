# Agent Definitions

Agents are sub-assistants that can be delegated specialized tasks. This reference covers the agent definition format in detail.

## Format

Agents are defined in markdown files within a skill's `agents/` directory:

```
skills/
└── my-skill/
    └── agents/
        └── helper.md   # Agent definition
```

## Agent File Structure

An agent file has YAML frontmatter followed by a markdown body (the system prompt):

```yaml
---
description: "What this agent does"
model: sonnet      # Optional: model override
tools:             # Optional: tool list
  - Read
  - Glob
  - Grep
---

# Agent System Prompt

The agent's instructions go here. This becomes the agent's
system prompt when invoked.
```

## Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `description` | Yes | Human-readable description of the agent |
| `model` | No | Model override for this agent |
| `tools` | No | List of tool names this agent can use |

## Model Options

| Value | Description |
|-------|-------------|
| `sonnet` | Balanced capability (default for most tasks) |
| `opus` | Maximum capability (complex reasoning, analysis) |
| `haiku` | Fast and efficient (simple tasks) |
| `inherit` | Use parent session's model |

**Default**: If not specified, the agent inherits the model from the parent session.

## Tool Scoping

The `tools` field restricts which tools an agent can use:

```yaml
tools:
  - Read
  - Glob
  - Grep
```

**No tools field**: Agent inherits all tools from parent session

**Empty list** (`tools: []`): Agent has no tool access

### Valid Tool Names

Use the exact tool names from the available tool set:
- `Read`, `Write`, `Edit` — File operations
- `Glob`, `Grep` — Search operations
- `Bash` — Shell commands
- `Task`, `TaskUpdate`, `TaskOutput` — Task management
- `Agent` — Sub-agent delegation
- `WebSearch`, `WebFetch` — Web access

## Namespacing

Agents are namespaced as `skill-name/agent-name`:

```
skills/
└── code-review/
    └── agents/
        └── analyzer.md  # → code-review/analyzer
```

When invoking an agent, use the full namespaced name.

## Writing Effective Prompts

### Be Specific About the Task

```markdown
# Good: Clear scope
Analyze the provided code diff and identify:
- Potential bugs or errors
- Style inconsistencies
- Missing test coverage

# Bad: Vague
Review the code
```

### Provide Context Structure

Explain what context the agent will receive:

```markdown
You will receive:
- The code diff to analyze
- The project's coding standards (from CLAUDE.md)

Focus on actionable feedback.
```

### Set Clear Output Expectations

```markdown
Provide your analysis as a structured list:
1. **Critical Issues**: Bugs or errors that must be fixed
2. **Suggestions**: Improvements that could be made
3. **Notes**: Observations that don't require action
```

## Example

Here's a complete agent definition:

```yaml
---
description: "Analyzes code diffs for potential issues"
model: opus
tools:
  - Read
  - Glob
  - Grep
---

# Code Diff Analyzer

You are a code review specialist. Analyze code changes for quality issues.

## Input

You will receive a code diff and the project's coding standards.

## Your Task

1. Review the diff for:
   - Logic errors or bugs
   - Style violations
   - Missing tests
   - Security concerns

2. Check surrounding context if needed (use Read/Glob/Grep)

## Output Format

Provide findings as:

### Critical Issues
- [Issue with file:line reference]

### Suggestions
- [Improvement opportunity]

### Notes
- [Observation without action required]
```
