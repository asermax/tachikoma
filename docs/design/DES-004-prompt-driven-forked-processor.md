# DES-004: Prompt-Driven Forked Processor

**Scope**: Project-wide
**Date**: 2026-03-15
**Last Updated**: 2026-03-21

## Pattern

Post-processors that fork the SDK session with a prompt should extend `PromptDrivenProcessor` rather than implementing the full `PostProcessor` interface directly. This base class provides a reusable structure for prompt-driven extraction and update logic.

The pattern: extend `PromptDrivenProcessor`, provide a prompt constant, and call `super().__init__()` with the prompt and an `AgentDefaults` instance. The base class handles storing prompt/agent_defaults and implementing `process()` via `fork_and_consume()`.

## Rationale

All prompt-driven processors follow the same structure:
1. Store a prompt constant and `AgentDefaults` (cwd, cli_path, env)
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
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import PromptDrivenProcessor

MY_PROMPT = """\
You are a memory extraction agent...

Instructions:
1. Read existing files...
2. Analyze the conversation...
3. Create or update files...
"""

class MyProcessor(PromptDrivenProcessor):
    """Processor that extracts memories from conversations."""

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        super().__init__(MY_PROMPT, agent_defaults)
```

**Why**: Simple, minimal boilerplate. The processor inherits `process()` from the base class, which calls `fork_and_consume()` with the prompt and agent_defaults.

### Don't Do This

```python
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import PostProcessor, fork_and_consume

MY_PROMPT = """\
You are a memory extraction agent...
"""

class MyProcessor(PostProcessor):
    """Processor that manually implements the fork pattern."""

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        self._agent_defaults = agent_defaults

    async def process(self, session: Session) -> None:
        await fork_and_consume(session, MY_PROMPT, self._agent_defaults)
```

**Why**: This reimplements the same pattern that `PromptDrivenProcessor` already provides. It adds unnecessary boilerplate and creates inconsistency with other processors.

## Resumption Augmentation Contract

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

        # Fork with custom tools
        await fork_and_consume(
            session, prompt, self._agent_defaults,
            mcp_servers={"custom": custom_server},
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
- Related feature: [../feature-designs/memory/memory-extraction.md](../feature-designs/memory/memory-extraction.md) - Memory extraction processors
