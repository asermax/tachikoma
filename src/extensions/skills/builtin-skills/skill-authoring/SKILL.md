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
    ├── workflows/     # Optional: multi-step workflow definitions
    └── tests/         # Optional: tests for bundled executables
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

Skills can bundle executable scripts or data files anywhere in the skill directory; the agent runs them with its shell tool when the SKILL.md body says to. Organize additional content as needed (`data/`, `templates/`, `examples/`). Keep generated runtime state out of the skill directory — a skill should stay a read-only package. Executable logic gets tests — see [Testing Bundled Executables](#testing-bundled-executables).

## Testing Bundled Executables

A skill that bundles executable logic — a CLI, scripts, or any code the agent runs — ships tests with it. Run them with your shell tool whenever you change that logic.

- **Colocate tests in the skill directory** (e.g. `skills/my-skill/tests/`), so the skill stays a self-contained package.
- **Write tests when the executable is created** — every command, flag, and output shape it exposes gets a test.
- **Change and test together**: a new command, a changed flag or output, a bug fix — the change and its tests land in the same change, and the tests pass before the work is done.
- **Keep tests deterministic and offline**: no network, no live workspace state; fixtures live inside the skill directory.
- **Use the language's standard runner** so no special tooling is needed — e.g. for a Node CLI, run `node --test tests/` from the skill directory (`skills/my-skill/`).
- **Point to the tests from SKILL.md** — a `Tests` row in the Key Paths table naming where they live and the command that runs them, not a test write-up.

## Body Structure Guide

Not every skill needs every section. Use this guide to pick what applies:

### If your skill has workflows

Add a **Triggers & Workflows** section that maps every user action to a specific workflow. Open with the guardrail, then list each trigger as a bold heading:

**Always use a workflow when one matches the request.** Do not call CLI commands directly — the workflow handles the correct sequence.

#### Trigger name → `workflow-name`

**Anytime [condition], immediately start `workflow-name`.** Don't run `command` directly. The workflow handles X, Y, Z in the correct order.

This prevents agents from shortcutting past workflows when they can see the raw commands.

### If your skill has reference files

Add a **References** section as a table with consistent format:

| Task | Reference |
|------|-----------|
| CLI commands | `references/cli.md` |
| Setup instructions | `references/setup.md` |

### If your skill has a CLI

Move all commands to `references/cli.md`. Don't embed them in SKILL.md — only include a pointer in the **Key Paths** table:

| What | Where |
|------|-------|
| **CLI** | `skills/my-skill/my-skill` — see `references/cli.md` |
| **Tests** | `skills/my-skill/tests/` — run `node --test tests/` from the skill directory |

Agents will shortcut past workflows if they can see the raw commands. The CLI is an implementation detail for workflows to use, not a routing surface.

### If your skill has a pipeline or domain model

Add a section explaining states, scoring rules, or lifecycle — whatever's specific to the domain. Keep it concise; detailed specs belong in reference files.

### If your skill has none of the above

Keep it simple — an Overview and a When to Use section is enough.

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
