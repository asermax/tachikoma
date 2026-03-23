# DES-007: Low-Effort Classification Agent

**Scope**: Project-wide
**Date**: 2026-03-21
**Last Updated**: 2026-03-22

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
- Sentinel string for "no results" (e.g., `NO_RELEVANT_MEMORIES`, `NO_RELEVANT_SKILLS`)
- Full generator consumption (DES-005)
- Graceful error handling: catch exceptions, log per DES-002, return None
- Structured logging with provider context

**Variable parts** (differ per provider):
- Tool access and permission mode (see "Disabling Tools" section below)
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
        allowed_tools=[],
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

**Why**: Uses the standard invariant core (Opus low effort, full generator consumption, sentinel pattern, graceful error handling) with the tool-less agent pattern (default permission mode, `allowed_tools=[]`, `max_turns=3`). See "Disabling Tools" section for rationale.

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

## Disabling Tools

Agents that should not use tools require defense in depth to prevent rogue execution. This is critical because the `allowed_tools` parameter has an SDK bug where an empty list `[]` is treated as falsy and never passed to the CLI — so `allowed_tools=[]` alone does not restrict tools.

**For agents that need tools** (e.g., memory context provider):
- `permission_mode="bypassPermissions"` — tools must execute without interactive approval
- `allowed_tools=["Read", "Glob", "Grep"]` — explicit tool allowlist
- `max_turns=8` — generous limit for multi-step tool use

**For agents that should NOT use tools** (e.g., boundary detection, summarization, skill classification):
- Omit `permission_mode` (default mode) — headless `query()` calls have no `can_use_tool` callback, so any tool permission request raises an exception
- `allowed_tools=[]` — documents intent; will also enforce once the SDK bug is fixed
- `max_turns=3` — hard limit prevents runaway execution as an additional safeguard

The three layers work independently: default permission mode denies tools at the permission level, `allowed_tools=[]` will deny at the allowlist level (once fixed), and `max_turns=3` caps execution regardless.

**Implementation**: All tool-less call sites use the same inline pattern with the defense-in-depth comment referencing this section. See `boundary/detector.py`, `boundary/summary.py`, and `skills/context_provider.py` for examples.

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
- Related feature: [../feature-designs/agent/boundary-detection.md](../feature-designs/agent/boundary-detection.md) - Boundary detector uses same standalone `query()` with Opus low effort pattern (not a context provider, but same technical approach)
