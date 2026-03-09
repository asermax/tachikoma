# DES-002: Logging Conventions

**Scope**: Python
**Date**: 2026-03-09
**Last Updated**: 2026-03-09
**Related**: ADR-006 (Logging Library)

## Pattern

Follow these logging conventions throughout the codebase:

1. **Configure once at startup** via the logging bootstrap hook
2. **Use structured logging** with key=value format (not f-strings)
3. **Use `.bind()` for context** (component names, session IDs)
4. **Never log sensitive data** (user credentials, API keys)
5. **Level selection**:
   - **DEBUG**: Internal details invisible to users
   - **INFO**: Normal operations visible to users
   - **WARNING**: Unexpected but handled situations
   - **ERROR**: Operation failures affecting functionality
   - **CRITICAL**: Cannot continue operation

6. **Exception logging**:
   - Use `.exception()` for unexpected errors (includes full traceback)
   - Use `.error()` for expected/handled errors (no traceback needed)
   - Always include `err=str(e)` for consistency

## Rationale

1. **Structured logging**: Makes logs machine-parseable for analysis
2. **Context binding**: Component names help trace issues across the pipeline
3. **Level discipline**: Enables controlling verbosity in production vs development
4. **Privacy-first**: Never logging sensitive data protects privacy

## Examples

### Do This

```python
from loguru import logger

# Module-level bound logger
_log = logger.bind(component="coordinator")

async def send_message(self, message: str) -> AsyncIterator[Event]:
    _log.debug("Message received: length={n}", n=len(message))

    try:
        async for event in self._client.send(message):
            yield event

        _log.debug("Response complete")
    except Exception as e:
        _log.exception("Message processing failed: error={err}", err=str(e))
        raise
```

### Don't Do This

```python
# BAD: Using f-strings (evaluated even when log level disabled)
logger.debug(f"Result: {expensive_computation()}")

# BAD: Logging sensitive data
logger.info("API key loaded: key={k}", k=api_key)

# BAD: Vague messages without context
logger.info("Started")
logger.error("Failed")
```

## Exceptions

- **Development debugging**: Temporary verbose logging is acceptable, remove before merge
- **Pre-bootstrap code paths**: Config loading error paths that run before logging is configured should use `print()` to stderr

## Related Patterns

- **ADR-006**: Logging library selection (loguru)
- **DES-001**: Testing conventions
