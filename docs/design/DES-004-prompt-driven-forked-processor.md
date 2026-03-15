# DES-004: Prompt-Driven Forked Processor

**Scope**: Project-wide
**Date**: 2026-03-15
**Last Updated**: 2026-03-15

## Pattern

Post-processors that fork the SDK session with a prompt should extend `PromptDrivenProcessor` rather than implementing the full `PostProcessor` interface directly. This base class provides a reusable structure for prompt-driven extraction and update logic.

The pattern: extend `PromptDrivenProcessor`, provide a prompt constant, and call `super().__init__()` with the prompt and working directory. The base class handles storing prompt/cwd and implementing `process()` via `fork_and_consume()`.

## Rationale

All prompt-driven processors follow the same structure:
1. Store a prompt constant and working directory
2. In `process()`, fork the SDK session with the prompt
3. Let the forked agent autonomously manage files

Extracting this into a base class:
- Eliminates identical boilerplate across processors
- Makes simple processors near-trivial — just a prompt constant and `super().__init__()` call
- Allows complex processors to override `process()` for pre/post steps while still using `fork_and_consume()` internally
- Standardizes the fork pattern across all processors

## Examples

### Do This

```python
from pathlib import Path

from tachikoma.post_processing import PromptDrivenProcessor
from tachikoma.sessions.model import Session

MY_PROMPT = """\
You are a memory extraction agent...

Instructions:
1. Read existing files...
2. Analyze the conversation...
3. Create or update files...
"""

class MyProcessor(PromptDrivenProcessor):
    """Processor that extracts memories from conversations."""

    def __init__(self, cwd: Path) -> None:
        super().__init__(MY_PROMPT, cwd)
```

**Why**: Simple, minimal boilerplate. The processor inherits `process()` from the base class, which calls `fork_and_consume()` with the prompt and cwd.

### Don't Do This

```python
from pathlib import Path

from tachikoma.post_processing import PostProcessor, fork_and_consume
from tachikoma.sessions.model import Session

MY_PROMPT = """\
You are a memory extraction agent...
"""

class MyProcessor(PostProcessor):
    """Processor that manually implements the fork pattern."""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    async def process(self, session: Session) -> None:
        await fork_and_consume(session, MY_PROMPT, self._cwd)
```

**Why**: This reimplements the same pattern that `PromptDrivenProcessor` already provides. It adds unnecessary boilerplate and creates inconsistency with other processors.

## Exceptions

When a processor needs radically different forking behavior (e.g., different session handling, custom options beyond `mcp_servers`), it may be appropriate to extend `PostProcessor` directly. However, consider whether the base class can be extended to support the new use case first.

When a processor needs pre/post steps around the fork:
```python
class ComplexProcessor(PromptDrivenProcessor):
    async def process(self, session: Session) -> None:
        # Pre-step: cleanup, setup, etc.
        await some_pre_step()

        # Fork with custom tools
        await fork_and_consume(
            session, self._prompt, self._cwd,
            mcp_servers={"custom": custom_server},
        )

        # Post-step: logging, validation, etc.
        await some_post_step()
```

This is the expected pattern for complex processors — override `process()` entirely and call `fork_and_consume()` directly.

---

## Related

- See also: [DES-002](DES-002-logging-conventions.md) - Logging conventions for processors
- Related feature: [../feature-designs/memory/memory-extraction.md](../feature-designs/memory/memory-extraction.md) - Memory extraction processors
