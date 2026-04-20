# DES-007: Low-Effort Classification Agent

**Scope**: Project-wide
**Date**: 2026-03-21
**Last Updated**: 2026-04-08

## Pattern

Pre-processing context providers that need to classify or search before returning context should use a standalone `query()` call with `model="opus"` and `effort="low"`. When conversation context is needed for informed decisions, the pipeline threads the session's summary and last exchange to providers, which render them into the prompt via the shared `render_conversation_context()` helper. This is the pre-processing counterpart to DES-004 (which covers post-processing via `fork_and_consume()`).

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
- Conversation context rendering (see "Conversation Context" section below)
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
        max_turns=10,
        tools=[],
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

**Why**: Uses the standard invariant core (Opus low effort, full generator consumption, sentinel pattern, graceful error handling) with the tool-less agent pattern (empty base tool set via `tools=[]`, default permission mode, `max_turns=10`). See "Disabling Tools" section for rationale.

### Don't Do This

```python
# BAD: using a different model or effort level without justification
options = ClaudeAgentOptions(
    model="sonnet",
    effort="high",
    max_turns=50,
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

## Conversation Context

When a classification agent needs conversation context to make informed decisions, the pipeline threads the session's summary and last exchange to providers. Providers render them into their prompts via the shared `render_conversation_context()` helper (defined in `per_message_pre_processing.py`).

**When to include conversation context**:
- The agent's classification depends on what has already been discussed (e.g., "does this message introduce a topic not already covered?")
- The agent should skip work when the conversation context already suffices (e.g., returning a sentinel because "the conversation already covers what's needed")

**When to omit conversation context** (first message):
- No session summary is available yet — the `render_conversation_context()` helper returns an empty string
- The agent classifies based solely on the current message

**Rendering pattern**:

```python
from tachikoma.per_message_pre_processing import render_conversation_context

# In provider.provide():
conversation_context_section = render_conversation_context(
    session_summary, session_last_exchange
)
prompt = TEMPLATE.format(
    conversation_context_section=conversation_context_section,
    message=message,
)
```

The helper returns a "## Conversation Context" section with the summary and an optional "Last assistant response" subsection, or an empty string when no summary exists.

**Implementation**: See `memory/context_provider.py` and `skills/context_provider.py` for examples of conversation context rendering.

## Disabling Tools

**For agents that need tools** (e.g., memory context provider):
- `permission_mode="bypassPermissions"` — tools must execute without interactive approval
- `allowed_tools=["Read", "Glob", "Grep"]` — explicit tool allowlist
- `max_turns=8` — generous limit for multi-step tool use

**For agents that should NOT use tools** (e.g., boundary detection, summarization, skill classification):
- `tools=[]` — sets an empty base tool set (passes `--tools ""` to the CLI, removing all tools)
- Omit `permission_mode` (default mode) — headless `query()` calls have no `can_use_tool` callback, so any tool permission request raises an exception
- `max_turns=10` — hard limit prevents runaway execution as an additional safeguard

**Note**: Do not use `allowed_tools=[]` to disable tools — the SDK treats empty lists as falsy and never passes `--allowedTools` to the CLI. Use `tools=[]` instead, which correctly passes `--tools ""`.

**Implementation**: All tool-less call sites use the same inline pattern with a comment referencing this section. See `boundary/detector.py`, `boundary/summary.py`, and `skills/context_provider.py` for examples.

## Exceptions

- If a provider needs higher effort (e.g., complex multi-step reasoning), it should document why in its design and may use `effort="medium"` or higher.
- If a provider needs a different model (e.g., for cost reasons on a high-frequency path), it should document the tradeoff.
- Post-processing tasks that fork from an existing session should use DES-004 instead.
- Forking adds overhead per message — only use when conversation context genuinely improves classification quality.

---

## Related

- See also: [DES-004](DES-004-prompt-driven-forked-processor.md) - Post-processing counterpart (uses `fork_and_consume()` on existing sessions). DES-007's forking variant adapts DES-004's fork pattern for pre-processing.
- See also: [DES-005](DES-005-sdk-query-generator-consumption.md) - Generator consumption requirement for standalone `query()`
- See also: [DES-002](DES-002-logging-conventions.md) - Logging conventions for error handling
- Related feature: [../feature-designs/memory/memory-context-retrieval.md](../feature-designs/memory/memory-context-retrieval.md) - Memory context provider (standalone with tools, uses conversation context)
- Related feature: [../feature-designs/agent/skills.md](../feature-designs/agent/skills.md) - Skills context provider (standalone without tools, uses conversation context)
- Related feature: [../feature-designs/agent/boundary-detection.md](../feature-designs/agent/boundary-detection.md) - Boundary detector uses same standalone `query()` with Opus low effort pattern (not a context provider, but same technical approach)
