---
name: skill-authoring
description: |
  Activates when the user wants to create, define, set up, build, or scaffold a new skill; encode expertise or save a process for reuse; define automation for the assistant. Also triggers on requests to author a skill, write a skill, help me make a skill, guide me through skill authoring, how to create a skill, make a skill, build a skill, new skill.
---

# Skill Authoring Guide

This guide provides everything you need to create well-structured skills. Read this document when asked to create, define, or set up a new skill for the assistant.

## Directory Conventions

User skills live in the `skills/` directory of the workspace (`{workspace}/skills`). Each skill is a subdirectory containing a `SKILL.md` file:

```
skills/
└── my-skill/
    ├── SKILL.md       # Required: metadata + content
    ├── agents/        # Optional: delegable agent definitions
    │   └── helper.md
    ├── references/    # Optional: detailed docs read on demand
    │   └── api.md
    └── workflows/     # Optional: multi-step workflow definitions
```

**Naming**: lowercase with hyphens (e.g. `code-review`, `git-workflow`). Names must be 64 characters or fewer, use only `a-z`, `0-9`, and hyphens, and must not start/end with a hyphen or contain consecutive hyphens.

A directory containing a `SKILL.md` is a skill root — discovery does not recurse further inside it, so nested content (references, agents, scripts) belongs to that skill.

## SKILL.md Format

A `SKILL.md` file follows the Agent Skills standard: YAML frontmatter followed by a markdown body.

```yaml
---
name: my-skill
description: "A clear description of what this skill does and when to use it"
---

# Skill Title

The skill's content goes here. Explain what the skill does,
when to use it, and how to use it.
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | No | Skill identifier. Defaults to the directory name. Same naming rules as directories. |
| `description` | Yes | What the skill does and when it applies (max 1024 characters). Drives skill selection. |

### Body Content

The body is loaded into context when the skill is used. Explain what the skill does, when to use it, how to use it, and any constraints or tips.

## How Skills Are Discovered

Skills use **progressive disclosure**: the name and description of every skill are always visible to the agent, while the body is only loaded when the skill is relevant. There is no separate classification pass — the description alone determines whether the skill gets picked up.

### Tips for Effective Descriptions

**Be specific about triggers** — mention concrete actions and requests:

```yaml
# Good: specific triggers
description: "Activates when the user wants to create, define, or scaffold a new skill"

# Bad: too vague
description: "A skill for skills"
```

**Include synonyms** — cover different phrasings of the same intent.

**Avoid false positives** — say what the skill does *not* cover when a term is ambiguous.

## Available Capabilities

### Agents (`agents/`)

**When to use**: delegate focused sub-tasks to an isolated agent with its own system prompt and tool set.

Agents are markdown files under the skill's `agents/` directory. Each file has YAML frontmatter and a body that becomes the agent's system prompt:

```yaml
---
description: "Finds and summarizes relevant sources"
tools:
  - read
  - grep
---

You are a research scout. Given a topic, locate relevant files
and produce a concise summary of what you found.
```

| Field | Required | Description |
|-------|----------|-------------|
| `description` | Yes | What the agent does — shown to the main agent when choosing whom to delegate to. |
| `name` | No | Agent identifier. Defaults to the file name without `.md`. |
| `tools` | No | Tools available to the agent: a YAML list or a comma-separated string. Defaults to read-only tools (`read`, `grep`, `find`, `ls`). |

Agents are exposed through the `delegate_to_agent` tool, namespaced as `<skill>/<agent>` to avoid collisions. Discovery runs per agent session, so a newly added agent becomes available on the next session without a restart.

### Reference Files (`references/`)

**When to use**: detailed documentation that would clutter the main body.

Reference files are regular files the agent reads with file-system tools when directed by the SKILL.md body. They are **not automatically injected** — use them for detailed API docs, extended examples, or large templates.

**Format**: any file type. Reference from SKILL.md like: "See `references/api.md` for detailed options."

### Workflows (`workflows/`)

**When to use**: the skill encodes a multi-step process that benefits from tracked, resumable execution.

Workflows are directory-based step definitions executed through dedicated lifecycle tools. See the `workflow-authoring` skill for the full format and conventions.

### Scripts and Other Content

Skills can bundle executable scripts or data files anywhere in the skill directory; the agent runs them with its shell tool when the SKILL.md body says to. Organize additional content as needed (`data/`, `templates/`, `examples/`). Keep generated runtime state out of the skill directory — a skill should stay a read-only package.

## Writing Best Practices

### Explain the Why

Don't just say what to do — explain the reasoning behind choices so the agent can make good decisions when the situation deviates from the script.

### Keep It Lean

Only include what's necessary. Avoid redundant explanations, overly verbose instructions, and information already covered by the system prompt or other skills.

### Progressive Disclosure

Put essential information in the main body. Move detailed reference material to `references/` files. This keeps loaded context focused while making details available on demand.

## Example

```yaml
---
name: git-commit
description: |
  Activates when the user wants to create, update, or manage git commits.
  Triggers on requests to commit changes, create a commit, make a commit.
---

# Git Commit Workflow

Guides the user through creating well-structured git commits.

## When to Use

- User asks to commit changes
- User wants a commit with a specific message
- User needs help with commit message format

## Workflow

1. **Stage changes**: review what files have been modified
2. **Draft message**: propose a conventional commit message
3. **Create commit**: execute the git commit

## Tips

- Use conventional commit format (type(scope): message)
- Group related changes into logical commits
- Keep commits focused and atomic
```
