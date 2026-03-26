---
description: |
  Activates when the user wants to create, define, set up, build, or scaffold a new skill; encode a workflow or save a process for reuse; define automation for the assistant. Also triggers on requests to author a skill, write a skill, help me make a skill
 guide me through skill authoring
 create-skill
 how to create a skill
 make a skill
 build a skill
 new skill
---

# Skill Authoring Guide

This guide provides everything you need to create well-structured skills. Read this document when asked to create, define, or set up a new skill for the assistant.

## Directory Conventions

Skills live in the `skills/` directory. Each skill is a subdirectory containing a `SKILL.md` file:

```
skills/
├── my-skill/
│   ├── SKILL.md      # Required: metadata + content
│   ├── agents/       # Optional: agent definitions
│   │   └── helper.md
│   └── references/  # Optional: detailed docs loaded on demand
│       └── api.md
```

**Naming**: Use lowercase with hyphens (e.g., `code-review`, `git-workflow`)

## SKILL.md Format

A `SKILL.md` file has YAML frontmatter followed by markdown body:

```yaml
---
description: "A clear description of what this skill does"
version: "1.0.0"  # Optional
---

# Skill Title

The skill's content goes here. Explain what the skill does,
when it use it, and how to use it.
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `description` | Yes | Human-readable description. Used for skill detection. |
| `version` | No | Optional version string for tracking |

### Body Content

The body is the markdown document that gets injected into the assistant's context when the skill is detected. Write it to be if-then style: explain the what, how to accomplish it, and any constraints or tips.

## Available Capabilities

### Agents (`agents/`)

**When to use**: Delegate specialized sub-tasks to an agent.

Agents are sub-assistants the can perform focused work on behalf of the main assistant. Use agents when:
- The task benefits from a different model (e.g., Opus for complex reasoning)
- Work should happen in a separate context
- You need tool access different from the main agent

**Format**: Markdown files in `agents/` with YAML frontmatter. See `references/agents.md` for detailed format.

### Reference Files (`references/`)

**When to use**: Provide detailed documentation loaded on demand.

Reference files are regular files in the skill directory that the agent reads via file-system tools (Read, Grep, Glob) when directed by the SKILL.md body. They are **not automatically injected** — use them for:
- Detailed API documentation
- Extended examples or code templates
- Large content that would clutter the main body

**Format**: Any file type (`.md`, `.txt`, `.json`, etc.). Reference from SKILL.md like: "See `references/api.md` for detailed options."

### Other Subdirectories

Organize additional content as needed:
- `data/` — Static data files
- `templates/` — Template files
- `examples/` — Example files

## Detection Tuning

Skills are detected via LLM classification using each skill's description. Writing effective descriptions is critical.

### How Detection Works

1. The system assembles a list of all skills with their descriptions
2. An LLM classifier compares the user's message against skill descriptions
3. Skills with relevant descriptions are injected into context

### Tips for Effective Descriptions

**Be specific about triggers**: Mention concrete actions and requests

```yaml
# Good: Specific triggers
description: "Activates when the user wants to create, define, or scaffold a new skill"

# Bad: Too vague
description: "A skill for skills"
```

**Include synonyms**: Cover different ways users might phrase the same intent

```yaml
description: |
  Activates when the user wants to create, define, set up, build, or scaffold a new skill.
  Also triggers on requests to author a skill, write a skill, help me make a skill.
```

**Avoid false positives**: Don't trigger on unrelated mentions

```yaml
# Bad: Triggers on any "skill" mention
description: "Activates for skill-related tasks"

# Good: Only triggers on authoring intent
description: |
  Activates when creating, defining, or setting up a new skill.
  Does NOT trigger when listing or using existing skills.
```

## Writing Best Practices

### Explain the Why

Don't just say what to do — explain the reasoning behind choices. This helps the assistant understand the intent and make better decisions.

```markdown
# Good
Use the `deep-analysis` agent for complex multi-file reasoning. This agent
has access to all tools and can explore the codebase thoroughly.

# Why: Complex analysis often requires reading multiple files, searching
# for patterns, and understanding relationships. The agent pattern keeps
# this focused and tool-equipped.
```

### Keep It Lean

Only include what's necessary. Avoid:
- Redundant explanations
- Overly verbose instructions
- Information covered by other skills or system prompts

### Progressive Disclosure

Put essential information in the main body. Move detailed reference material to `references/` files. This keeps injected context focused while making details available on demand.

```markdown
# Main body: Essential usage
Use the `analyze` tool for quick single-file analysis.

# In references/advanced.md: Detailed options and edge cases
```

## Example

Here's a complete example skill:

```yaml
---
description: |
  Activates when the user wants to create, update, or manage git commits.
  Triggers on requests to commit changes, create a commit, make a commit.
---

# Git Commit Workflow

Guides the user through creating well-structured git commits.

## When to Use

Use this skill when:
- User asks to commit changes
- User wants to create a commit with a specific message
- User needs help with commit message format

## Workflow

1. **Stage changes**: Review what files have been modified
2. **Draft message**: Propose a conventional commit message
3. **Create commit**: Execute the git commit

## Tips

- Use conventional commit format (type(scope): message)
- Group related changes into logical commits
- Keep commits focused and atomic
```

## Future Capabilities

The following capabilities are planned but not yet available:

- **MCP Tool Servers** (DLT-054): Skills will be able to expose MCP tools that the main agent can call directly. This enables interactive capabilities without delegation.

---

For detailed agent definition format, see `references/agents.md`.
