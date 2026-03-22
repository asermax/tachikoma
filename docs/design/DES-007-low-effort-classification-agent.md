# DES-007: Low-Effort Classification Agent

**Scope**: Project-wide
**Date**: 2026-03-21
**Last Updated**: 2026-03-21

## Pattern

Pre-processing context providers that need to classify or search before returning context should use a standalone `query()` call with `model="opus"` and `effort="low"`. This is the pre-processing counterpart to DES-004 (which covers post-processing via `fork_and_consume()`).

## Rationale

Multiple context providers follow the same structure for LLM-based classification/search:
1. Build a prompt with available data and the user's message
2. Call standalone `query()` with Opus low effort
3. Parse the result
4. Return a `ContextResult` or None

Codifying the invariant core ensures consistency across providers while documenting the expected variation points.

**Invariant core** (same across all providers):
- `model="opus"`, `effort="low"`
- `permission_mode="bypassPermissions"`
- Sentinel string for "no results" (e.g., `NO_RELEVANT_MEMORIES`, `NO_RELEVANT_SKILLS`)
- Full generator consumption (DES-005)
- Graceful error handling: catch exceptions, log per DES-002, return None
- Structured logging with provider context

**Variable parts** (differ per provider):
- Tool access: some providers need tools (e.g., `allowed_tools=["Read", "Glob", "Grep"]` with `max_turns=8` for memory), others need none (e.g., skills classification uses no tools with `max_turns=3`)
- Prompt content and result parsing logic
- Sentinel string value

## Examples

### Do This

```python
from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage

NO_RELEVANT_ITEMS = "NO_RELEVANT_ITEMS"

CLASSIFICATION_PROMPT = """\
You are a classification agent...

{items}

User message: {message}

If none are relevant, respond with exactly: NO_RELEVANT_ITEMS
"""

async def _classify(self, message: str) -> str | None:
    options = ClaudeAgentOptions(
        model="opus",
        effort="low",
        max_turns=3,
        permission_mode="bypassPermissions",
        cwd=self._cwd,
        cli_path=self._cli_path,
    )

    prompt = CLASSIFICATION_PROMPT.format(items=items_text, message=message)
    result: str | None = None

    async for sdk_message in query(prompt=prompt, options=options):
        if isinstance(sdk_message, ResultMessage):
            if sdk_message.is_error:
                logger.warning("Classification failed", error=sdk_message.error)
            elif sdk_message.result and NO_RELEVANT_ITEMS not in sdk_message.result:
                result = sdk_message.result

    return result
```

**Why**: Uses the standard invariant core (Opus low effort, bypass permissions, full generator consumption, sentinel pattern, graceful error handling). Variable parts (tools, max_turns, prompt) are provider-specific.

### Don't Do This

```python
# BAD: using a different model or effort level without justification
options = ClaudeAgentOptions(
    model="sonnet",
    effort="high",
    max_turns=10,
)
```

**Why**: Classification tasks benefit from Opus's reasoning quality while `effort="low"` keeps cost and latency reasonable. Using a different model/effort combination without justification breaks the established pattern and makes behavior harder to predict.

### Don't Do This

```python
# BAD: not handling the sentinel — treating empty result as "no matches"
async for sdk_message in query(prompt=prompt, options=options):
    if isinstance(sdk_message, ResultMessage):
        if sdk_message.result:
            return parse_result(sdk_message.result)
return None
```

**Why**: Without a sentinel, the provider can't distinguish "classified and found nothing" from "agent error or unparseable response." The sentinel enables different logging and handling for each case.

## Exceptions

- If a provider needs higher effort (e.g., complex multi-step reasoning), it should document why in its design and may use `effort="medium"` or higher.
- If a provider needs a different model (e.g., for cost reasons on a high-frequency path), it should document the tradeoff.
- Post-processing tasks that fork from an existing session should use DES-004 instead.

---

## Related

- See also: [DES-004](DES-004-prompt-driven-forked-processor.md) - Post-processing counterpart (uses `fork_and_consume()` on existing sessions)
- See also: [DES-005](DES-005-sdk-query-generator-consumption.md) - Generator consumption requirement for standalone `query()`
- See also: [DES-002](DES-002-logging-conventions.md) - Logging conventions for error handling
- Related feature: [../feature-designs/memory/memory-context-retrieval.md](../feature-designs/memory/memory-context-retrieval.md) - Memory context provider (first instance)
- Related feature: [../feature-designs/agent/skills.md](../feature-designs/agent/skills.md) - Skills context provider (second instance)
