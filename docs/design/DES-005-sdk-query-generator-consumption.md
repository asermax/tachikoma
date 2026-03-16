# DES-005: SDK query() Generator Consumption

**Scope**: Project-wide
**Date**: 2026-03-16
**Last Updated**: 2026-03-16

## Pattern

Always fully consume `query()` async generators. Never use `return`, `break`, or early exit inside an `async for` loop over a `query()` generator.

## Rationale

The Claude Agent SDK's `query()` function returns an async generator that manages an underlying CLI subprocess. If the generator is not fully consumed (e.g., by breaking out of the loop after receiving a `ResultMessage`), the SDK resources are not properly cleaned up. This can leave orphaned CLI processes that busy-loop the event loop, causing CPU spikes and resource exhaustion.

The `receive_response()` method on `ClaudeSDKClient` handles this correctly by design (it stops at `ResultMessage`), but standalone `query()` calls require the caller to consume the entire generator.

## Examples

### Do This

```python
# Fully consume the generator — extract what you need without breaking
result: str | None = None

async for sdk_message in query(prompt=prompt, options=options):
    if isinstance(sdk_message, ResultMessage):
        if sdk_message.result is not None:
            result = sdk_message.result

# Use result after the loop
```

**Why**: The generator runs to completion, ensuring the SDK subprocess terminates cleanly.

### Do This (alternative with flag)

```python
# Use a flag variable instead of breaking on first result
continues = True
got_result = False

async for sdk_message in query(prompt=prompt, options=options):
    if isinstance(sdk_message, ResultMessage):
        got_result = True
        if sdk_message.structured_output is not None:
            continues = bool(sdk_message.structured_output.get("continues_conversation", True))

if not got_result:
    logger.warning("Query produced no ResultMessage")
```

**Why**: All messages are consumed even though only one is needed for the result.

### Don't Do This

```python
# BAD: breaking out of the generator leaves SDK resources orphaned
async for sdk_message in query(prompt=prompt, options=options):
    if isinstance(sdk_message, ResultMessage):
        result = sdk_message.result
        break  # Generator not fully consumed — orphaned subprocess!
```

**Why**: The `break` statement exits the async for loop without consuming the remaining generator items, leaving the SDK subprocess in a partially consumed state that can busy-loop the event loop.

### Don't Do This

```python
# BAD: returning from inside the generator loop
async for sdk_message in query(prompt=prompt, options=options):
    if isinstance(sdk_message, ResultMessage):
        return sdk_message.result  # Generator not fully consumed!
```

**Why**: Same problem as `break` — the generator is abandoned mid-iteration.

## Exceptions

- `ClaudeSDKClient.receive_response()` is safe to iterate with a normal `async for` loop because it is designed to stop at `ResultMessage` and handle cleanup internally.
- If the generator needs to be cancelled due to a timeout or signal, use proper async cancellation (e.g., `asyncio.Task.cancel()`) rather than breaking out of the loop — this triggers the generator's cleanup code.

---

## Related

- See also: [DES-002](DES-002-logging-conventions.md) - Logging for warning on unexpected generator behavior
- Related feature: [../feature-designs/agent/core-architecture.md](../feature-designs/agent/core-architecture.md) - Per-message client uses `receive_response()` which avoids this issue
- Related feature: [../feature-designs/agent/boundary-detection.md](../feature-designs/agent/boundary-detection.md) - Boundary detector and summary processor both use standalone `query()`
- Related feature: [../feature-designs/memory/memory-context-retrieval.md](../feature-designs/memory/memory-context-retrieval.md) - Memory context provider uses standalone `query()`
