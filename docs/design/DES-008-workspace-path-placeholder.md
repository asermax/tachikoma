# DES-008: Workspace Path Placeholder in Agent Prompts

**Scope**: Project-wide
**Date**: 2026-04-12

## Pattern

Prompts that reference workspace file paths must use the `$WORKSPACE` placeholder instead of relative paths. The placeholder is replaced with the absolute workspace path (`str(agent_defaults.cwd)`) before the prompt is sent to the agent. This ensures file operations use absolute paths regardless of the agent's runtime working directory.

For `PromptDrivenProcessor` subclasses, the replacement happens automatically in `__init__()`. For other callers (e.g., `GitProcessor`, `MemoryContextProvider`), the replacement is done inline at the call site before the prompt is passed to the SDK.

## Rationale

Claude Code CLI's `--resume --fork-session` mechanism restores the parent session's last-known working directory from the session transcript, overriding the `cwd` set in `ClaudeAgentOptions`. This means forked agents may operate in a subdirectory (e.g., if the user's conversation navigated into an Obsidian vault), causing relative paths like `memories/episodic/` to resolve to the wrong location.

By embedding absolute paths directly in the prompt, the agent puts those absolute paths in tool calls (Read, Write, Edit, Glob, Grep). The CLI resolves absolute paths without consulting CWD, making file operations deterministic.

## Examples

### Do This

```python
AGENT_PROMPT = """You are a file processing agent.

1. Read existing files in `$WORKSPACE/data/input/`
2. Write results to `$WORKSPACE/data/output/`
"""

class MyProcessor(PromptDrivenProcessor):
    def __init__(self, agent_defaults: AgentDefaults) -> None:
        super().__init__(AGENT_PROMPT, agent_defaults)
        # $WORKSPACE is replaced with absolute path automatically
```

**Why**: The agent receives absolute paths like `/home/user/workspace/data/input/` in the prompt. Tool calls use these paths directly, bypassing CWD resolution.

### Don't Do This

```python
AGENT_PROMPT = """You are a file processing agent.

1. Read existing files in `data/input/`
2. Write results to `data/output/`
"""
```

**Why**: Relative paths resolve against the CLI's working directory. If the forked session inherits a subdirectory CWD, files are read from and written to the wrong location.

## Scope

Applies to all prompts that reference workspace file paths:
- `PromptDrivenProcessor` subclasses — replacement in `__init__()`
- Non-PromptDrivenProcessor prompts — inline replacement at call site

Does NOT apply to:
- Prompts without file path references (classification, boundary detection, summarization)

---

## Related

- DES-004: Prompt-Driven Forked Processor — the base class that performs `$WORKSPACE` replacement
- DES-007: Low-Effort Classification Agent — pre-processing fork pattern (may need `$WORKSPACE` if prompts reference file paths)
