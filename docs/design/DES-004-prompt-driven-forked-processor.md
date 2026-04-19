# DES-004: Sub-Agent Spawning Conventions

**Scope**: Project-wide
**Date**: 2026-03-15
**Last Updated**: 2026-04-13

## Pattern

Every sub-agent spawned outside the main conversation session must declare **explicit tool restrictions** and **allow-only permission rules** appropriate to its task. No sub-agent should run with unrestricted `bypassPermissions`.

Two layers of restriction:
1. **`tools` list** — which tools the agent can access (first line of defense)
2. **Allow-only permission rules** via `dontAsk` mode — which paths/commands those tools can operate on

For post-processors that fork the SDK session with a prompt, extend `PromptDrivenProcessor` and pass `tools` and `allow` to `super().__init__()`. The base class handles storing prompt/agent_defaults, replacing `$WORKSPACE` placeholders (see [DES-008](DES-008-workspace-path-placeholder.md)), and implementing `process()` via `fork_and_consume()`.

## Rationale

Sub-agents serve specific purposes — memory extraction writes to one directory, git agents run git commands, search agents only read files. Granting unrestricted access violates the principle of least privilege. Explicit declarations make each agent's scope auditable and prevent accidental writes to wrong directories.

The `dontAsk` permission mode auto-denies any tool call not covered by the allow list — no prompting, no fallback. This is ideal for headless sub-agents that have no interactive user.

## Permission Scoping

### The `dontAsk` Mode

When `tools` and `allow` are provided to `fork_and_consume`, `fork_and_capture`, or `query_and_consume`, the agent runs in `dontAsk` permission mode instead of `bypassPermissions`. In this mode:
- Only tool calls matching an entry in `permissions.allow` succeed
- Everything else is silently auto-denied
- No prompting occurs (safe for headless execution)

Allow rules use Claude Code's permission rule syntax with absolute paths (via `abs_rule()` helper):
- `Read(//home/user/workspace/memories/episodic/**)` — Read restricted to a directory (best-effort applies to Glob/Grep too)
- `Edit(//home/user/workspace/context/**)` — Edit restricted to a directory
- `Write(//home/user/workspace/memories/facts/**)` — Write restricted to a directory
- `Bash(git *)` — Bash restricted to commands matching a prefix (declarative; enforced by PreToolUse hook — see below)
- `Glob`, `Grep` — unrestricted (no path-specific syntax supported for these tools)
- `mcp__<server>__<tool>` — MCP tools must be explicitly allowed (e.g., `mcp__pending-signals__add_pending_signal`)

**Important**: Path-scoped rules must use absolute paths with the `//` prefix. Relative paths (`./`) don't reliably match when agents resolve tool inputs to absolute paths. Use the `abs_rule(tool, path)` helper to generate correct rules.

### Agent Tiers

| Tier | Tools | Allow Rules | Example |
|------|-------|-------------|---------|
| **Tool-less** | `tools=[]` | None needed | BoundaryDetector, SummaryProcessor |
| **Read-only** | `Read, Glob, Grep` | Path-scoped Read + unrestricted Glob/Grep | MemoryContextProvider |
| **Scoped writer** | `Read, Glob, Grep, Bash, Edit, Write` | Path-scoped Read/Edit/Write + unrestricted Glob/Grep/Bash + PreToolUse hook (`UTILITY_BASH_HOOK`) gating Bash to read-only inspection prefixes (`ls `, `find `, `file `, `echo `, `date `, `cat `, `grep `, `head `, `sort `, `tail `, `wc `, `stat `, `cd`, `pwd`) | Memory processors, CoreContextProcessor |
| **Git agent** | `Read, Glob, Grep, Bash, Edit, Write` | Unrestricted Read/Glob/Grep/Edit/Write + `Bash(git *)` + PreToolUse hook gating Bash to `git ` plus the same utility inspection prefixes as scoped writer | GitProcessor, ProjectsProcessor |

### Infrastructure

- `abs_rule(tool, path)` — builds absolute-path permission rules using the `//` prefix (e.g., `Write(//home/user/workspace/memories/episodic/**)`). Always use this for path-scoped rules.
- `build_permissions_settings(allow)` — serializes allow rules into the JSON format expected by `ClaudeAgentOptions.settings`.
- `make_bash_gate_hook(allowed_prefixes)` — creates a PreToolUse `HookMatcher` that gates Bash commands by prefix. Required because `Bash(git *)` allow rules are not reliably enforced by the CLI (known upstream issue).
- `UTILITY_BASH_PREFIXES` — shared list of read-only inspection command prefixes (`ls`, `find`, `file`, `echo`, `date`, `cat`, `grep`, `head`, `sort`, `tail`, `wc`, `stat`, `cd`, `pwd`). Used by scoped writer processors and composable into git agent hooks.
- `UTILITY_BASH_HOOK` — pre-built `HookMatcher` from `make_bash_gate_hook(UTILITY_BASH_PREFIXES)`. Shared across all scoped writer processors.
- The `dontAsk` mode is passed via `extra_args={"permission-mode": "dontAsk"}` since the Python SDK's `PermissionMode` type doesn't include it yet.

### Bash Command Enforcement

Claude Code's `dontAsk` mode does not reliably enforce Bash command restrictions via allow rules (known upstream issue — multiple open bugs in the CLI). To enforce Bash command scoping, use a **PreToolUse hook** via `make_bash_gate_hook()`:

```python
from tachikoma.post_processing import make_bash_gate_hook

# Only allow git commands
bash_hook = make_bash_gate_hook(["git "])

await query_and_consume(
    prompt, defaults,
    tools=GIT_TOOLS, allow=GIT_ALLOW,
    pre_tool_use_hooks=[bash_hook],
)
```

The hook inspects the `command` field in the tool input before execution and denies non-matching commands with a descriptive reason. The hook supports compound commands — commands joined by `&&`, `||`, `|`, or `;` are split and each sub-command is validated independently. If any sub-command fails, the entire command is denied.

The hook compiles the allowed prefixes into a single regex at creation time. A command matches if it exactly equals a prefix, or starts with a prefix followed by a space and arguments (e.g., `"cd"` matches both `cd` and `cd /path`, but not `cdeject`). Prefixes are deduplicated and sorted longest-first to ensure correct regex alternation.

### Prompt Permissions Section

Every sub-agent prompt should end with a `## Permissions` section explaining its constraints in plain language. This prevents confusion when tool calls are denied and helps the agent stay within its scope. Only mention what the agent *can* do — don't list tools that aren't available to it.

### Model Selection — Role-Based Taxonomy

Sub-agents fall into three roles, each driven by a dedicated config setting on `AgentDefaults`. Pick the role that matches the agent's job and pass the corresponding `agent_defaults.<role>_model` to the SDK options. Never hardcode a model alias.

| Role | Field | Default | When to use |
|------|-------|---------|-------------|
| **Searcher** | `searcher_model` | `"opus"` | Smart retrieval / high-stakes judgment: picking relevant items from a set, routing decisions, topic-shift classification. Quality matters more than speed because the output steers the rest of the pipeline. |
| **Processor** | `processor_model` | `"haiku"` | Mechanical extraction, rewriting, committing, resolving. Often runs many in parallel on session close or per-turn. Speed and cost dominate; nuance is not required. |
| **Classifier** | `classifier_model` | `"haiku"` | Rule-based mapping of an input to one of a few discrete outputs (e.g. `complete | continue | error`). Checklists are explicit; the cheapest model suffices. |

**Current assignments:**

| Role | Sub-agents |
|------|------------|
| Searcher | `MemoryContextProvider`, `SkillsContextProvider`, `BoundaryDetector` |
| Processor | `EpisodicProcessor`, `FactsProcessor`, `PreferencesProcessor`, `CoreContextProcessor`, `SummaryProcessor` (per-message), `GitProcessor` (via `query_and_consume`), `ProjectsProcessor` (submodule commits), `_agent_rebase` in `git/sync.py` |
| Classifier | task evaluator in `tasks/executor.py` |

`query_and_consume` (used by `GitProcessor` / `ProjectsProcessor`) and `fork_and_consume` / `fork_and_capture` (used by `PromptDrivenProcessor` subclasses) all accept an optional `model` parameter. When omitted, the call inherits the parent session's model — avoid this: every sub-agent has a role, so pass the matching `agent_defaults.<role>_model` explicitly so users can tune each role independently.

## Examples

### Do This — Scoped Writer (PromptDrivenProcessor)

```python
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import UTILITY_BASH_HOOK, PromptDrivenProcessor, abs_rule

MY_PROMPT = """\
You are a memory extraction agent...

## Permissions

You can only access files within `$WORKSPACE/memories/episodic/`. Reads, edits, \
and writes outside this directory will be denied. For Bash, read-only inspection \
commands (`ls`, `find`, ...) and navigation (`cd`, `pwd`) are allowed.
"""

class MyProcessor(PromptDrivenProcessor):
    """Processor that extracts memories from conversations."""

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        scope = agent_defaults.cwd / "memories" / "episodic"

        super().__init__(
            MY_PROMPT, agent_defaults,
            tools=["Read", "Glob", "Grep", "Bash", "Edit", "Write"],
            allow=[
                abs_rule("Read", scope), "Glob", "Grep", "Bash",
                abs_rule("Edit", scope), abs_rule("Write", scope),
            ],
            pre_tool_use_hooks=[UTILITY_BASH_HOOK],
        )
```

**Why**: Explicit tool set and path scoping. The agent can only read/write within its designated directory. Glob/Grep are unrestricted (read-only, low risk). Bash is gated to utility-only commands via `UTILITY_BASH_HOOK`. The prompt's permissions section tells the agent its boundaries upfront.

### Do This — Git Agent (query_and_consume)

```python
from tachikoma.post_processing import make_bash_gate_hook

GIT_TOOLS = ["Read", "Glob", "Grep", "Bash", "Edit", "Write"]
GIT_ALLOW = ["Read", "Glob", "Grep", "Edit", "Write", "Bash(git *)"]
GIT_BASH_HOOK = make_bash_gate_hook([
    "git ", "ls ", "find ", "file ", "echo ",
    "date ", "cat ", "head ", "sort ", "tail ", "wc ",
    "stat ", "cd", "pwd",
])

await query_and_consume(
    prompt, agent_defaults,
    tools=GIT_TOOLS, allow=GIT_ALLOW,
    pre_tool_use_hooks=[GIT_BASH_HOOK],
)
```

**Why**: Full read/write access (git agents need the whole workspace). Bash is restricted to git commands via both the allow rule (declarative intent) and the PreToolUse hook (programmatic enforcement).

### Do This — Tool-less Agent

```python
options = ClaudeAgentOptions(
    tools=[],
    ...
)
```

**Why**: Classification and summary agents don't need tools at all. `tools=[]` is the strongest restriction.

### Don't Do This

```python
options = ClaudeAgentOptions(
    permission_mode="bypassPermissions",
    # No tools restriction, no allow rules
)
```

**Why**: Grants unrestricted access to all tools and all paths. The agent can read, write, and execute anywhere in the workspace. Every sub-agent should declare exactly what it needs.

## PromptDrivenProcessor Base Class

Post-processors that fork the SDK session with a prompt should extend `PromptDrivenProcessor` rather than implementing the full `PostProcessor` interface directly. This base class provides a reusable structure for prompt-driven extraction and update logic.

The pattern: extend `PromptDrivenProcessor`, provide a prompt constant, and call `super().__init__()` with the prompt, an `AgentDefaults` instance, and `tools`/`allow` parameters. The base class handles storing all fields and implementing `process()` via `fork_and_consume()`.

### Resumption Augmentation Contract

The base `process()` method automatically calls `augment_prompt_for_resumption(self._prompt, session)` before `fork_and_consume()`. This appends a resumption boundary instruction when `session.last_resumed_at` is set, telling the forked agent to skip already-processed content.

**Subclasses that override `process()` must also call `augment_prompt_for_resumption()`** before passing the prompt to `fork_and_consume()`. This is a convention, not an enforced contract — but omitting it means the forked agent will re-extract content from the pre-resumption part of the conversation.

```python
from tachikoma.post_processing import (
    PromptDrivenProcessor, augment_prompt_for_resumption, fork_and_consume,
)

class ComplexProcessor(PromptDrivenProcessor):
    async def process(self, session: Session) -> None:
        # Pre-step
        await some_pre_step()

        # Apply resumption augmentation before forking
        prompt = augment_prompt_for_resumption(self._prompt, session)

        # Fork with custom tools — pass through tools/allow/hooks from __init__
        await fork_and_consume(
            session, prompt, self._agent_defaults,
            mcp_servers={"custom": custom_server},
            tools=self._tools, allow=self._allow,
            pre_tool_use_hooks=self._pre_tool_use_hooks,
        )

        # Post-step
        await some_post_step()
```

## Exceptions

When a processor needs radically different forking behavior (e.g., different session handling, custom options beyond `mcp_servers`), it may be appropriate to extend `PostProcessor` directly. However, consider whether the base class can be extended to support the new use case first.

When a processor needs pre/post steps around the fork, override `process()` entirely, call `augment_prompt_for_resumption()` on the prompt, then call `fork_and_consume()` directly (see example above).

---

## Related

- See also: [DES-002](DES-002-logging-conventions.md) - Logging conventions for processors
- See also: [DES-005](DES-005-sdk-query-generator-consumption.md) - Always fully consume query() generators
- See also: [DES-007](DES-007-low-effort-classification-agent.md) - Tool-less classification agents (a tier within this pattern)
- See also: [DES-008](DES-008-workspace-path-placeholder.md) - `$WORKSPACE` placeholder convention for absolute paths
- Related feature: [../feature-designs/memory/memory-extraction.md](../feature-designs/memory/memory-extraction.md) - Memory extraction processors
