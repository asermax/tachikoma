---
description: |
  Activates when the user wants to create, define, set up, build, or scaffold a new skill; encode a workflow or save a process for reuse; define automation for the assistant. Also triggers on requests to author a skill, write a skill, help me make a skill,
  guide me through skill authoring, create-skill, how to create a skill, make a skill, build a skill, new skill
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
| `depends_on` | No | List of skill names to load as dependencies when this skill activates. |

### Body Content

The body is the markdown document that gets injected into the assistant's context when the skill is detected. Explain what the skill does, when to use it, how to use it, and any constraints or tips.

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

### Executable Code (CLI subpackage)

**When to use**: The skill needs deterministic work the assistant shouldn't improvise (rendering, API calls, file manipulation) or a reusable command the user may invoke directly.

**Default stack**: Python + `uv` + a shell wrapper script at the skill root. Use another stack only when the user asks.

The standard layout pairs a hyphenated skill folder (`my-skill/`) with an underscored Python subpackage (`my_skill_cli/`) and a thin bash shim at the skill root that the assistant invokes. Every executable skill in the repo follows this pattern.

**Format**: See `references/scripting.md` for the full directory anatomy, naming rules, shim template, and base `pyproject.toml` wiring.

### Configuration

**When to use**: The skill needs user-configurable settings (API keys, thresholds, preferences, paths).

Skills store configuration in `.tachikoma/config/<skill-name>/config.toml` within the workspace. This is separate from the main application config at `~/.config/tachikoma/config.toml`. Skills own their config lifecycle — they create the directory on first use and load settings via stdlib `tomllib`.

For persistent runtime state (not user-configurable), use `.tachikoma/state/<skill-name>/` instead. Never read or write inside the skill directory itself.

**Format**: See `references/scripting.md` for the config directory layout, loading pattern, and config/state distinction.

### Other Subdirectories

Organize additional content as needed:
- `data/` — Static data files
- `templates/` — Template files
- `examples/` — Example files

## Testing

**Any skill that ships executable code — a CLI, script, or programmatic logic — must include tests.** Skills that are pure prompt content (SKILL.md + `references/` only) don't need tests.

**Why**: When skills evolve, regressions in executable code go unnoticed until they break in production.

At a glance (for the default Python + uv stack):

- Tests live in the CLI subpackage's `tests/` folder, next to `src/` (see `references/scripting.md` for the subpackage layout)
- Files are named `test_<module>.py`, one per source module under test
- Wired through `[dependency-groups] dev = ["pytest>=8.0"]` and `[tool.pytest.ini_options] testpaths = ["tests"]` in `pyproject.toml`
- Run with `uv run --project skills/<skill-name>/<skill_name>_cli pytest`

See `references/testing.md` for the full pytest wiring, file conventions, coverage guidance, and notes on non-Python stacks.

## Skill Dependencies

Skills can declare dependencies on other skills via the `depends_on` frontmatter field. When a skill is detected and loaded, all its dependencies are loaded too — transitively and automatically.

### When to Use

Use `depends_on` when your skill assumes another skill is already present in context. Common cases:
- Your skill builds on a **shared utility skill** that provides reusable instructions
- Your skill's agents reference concepts defined in a **foundation skill**
- Multiple skills share a common **base configuration or convention**

Don't use it for skills that are merely related — only declare dependencies on skills whose content must be present for your skill to work correctly.

### Format

A list of skill folder names:

```yaml
---
description: "My skill description"
depends_on:
  - workflow-authoring-guide
  - shared-conventions
---
```

Names are case-sensitive and must match the skill's folder name exactly.

### How It Works

1. When your skill is detected (via classification), the registry resolves its full dependency chain transitively
2. Dependencies are loaded **before** your skill, in depth-first order (deepest deps first)
3. Cycles and self-references are handled gracefully — each skill loads exactly once
4. Already-loaded skills are not re-loaded (deduped against the current session)
5. Unknown dependency names log a warning at startup but don't prevent your skill from loading

### Example

```yaml
---
description: |
  Activates when the user wants to deploy a project to production.
  Triggers on deploy, release, ship to prod.
depends_on:
  - git-workflow
  - deployment-conventions
---

# Production Deployment

Guides the user through deploying to production, following
the git workflow and deployment conventions defined in
dependency skills.
```

## Detection Tuning

Skills are detected via LLM classification using each skill's description. Writing effective descriptions is critical.

### Pinning Skills to Task Definitions

Skills can be pinned to scheduled task definitions so they are loaded unconditionally every time the task fires, bypassing LLM-based classification. This is useful when a task always needs a specific skill's context.

Use the `skills` parameter when creating or updating a task:

```
create_task(
  name="Research digest",
  schedule="0 9 * * *",
  type="background",
  prompt="Summarize recent findings",
  skills='["research", "planning"]'
)
```

The `skills` parameter is a JSON-encoded array of skill folder names. Dependency resolution still applies — if a pinned skill declares `depends_on`, those dependencies are loaded too. Unknown skill names produce a warning but don't prevent task creation.

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
depends_on:
  - git-workflow
---

# Git Commit Workflow

Guides the user through creating well-structured git commits,
building on the conventions defined in git-workflow.

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
For executable-code layout (CLI subpackage, shim, pyproject), see `references/scripting.md`.
For test layout and conventions, see `references/testing.md`.
