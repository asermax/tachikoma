# DES-006: SDK MCP Tool Server Factory

**Scope**: Python
**Date**: 2026-03-21
**Last Updated**: 2026-04-26

## Pattern

When a forked processor (DES-004) needs to give custom tools to the forked agent, define a factory function in a dedicated `tools.py` module within the subsystem package. The factory takes processor-specific configuration as parameters, defines tools inside the factory body via the `@tool()` decorator (closure over parameters), extracts handler logic into standalone async functions for testability, and returns an `McpSdkServerConfig` via `create_sdk_mcp_server()`.

## Rationale

Forked processors often need to give the agent tools for constrained file access, data queries, or side-effect operations. Without a standard pattern:
- Tool definitions get inlined in processor code, mixing orchestration with tool logic
- Handler logic is trapped inside `@tool` closures, making it untestable without spinning up the SDK
- Per-invocation state (snapshots, configuration) has no clear path into tool closures
- Each new tool server reinvents the factory shape

This pattern standardizes the factory boundary:
- Factory function is the single integration point between processor and tools
- Closure captures per-invocation state (snapshots, paths) at factory-call time
- Extracted handlers are plain async functions testable with direct calls
- Module organization (`tools.py`) is predictable and discoverable

## Examples

### Do This

```python
from pathlib import Path

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel


class MyToolArgs(BaseModel):
    item_id: str


async def handle_my_tool(item_id: str, data_dir: Path) -> dict:
    """Extracted handler — testable without SDK."""
    # ... business logic ...
    return {"content": [{"type": "text", "text": f"Processed {item_id}"}]}


def create_my_server(data_dir: Path, snapshot: list) -> McpSdkServerConfig:
    """Factory: takes config, returns server config."""

    @tool("my_tool", "Description for the agent", MyToolArgs.model_json_schema())
    async def my_tool(args: dict) -> dict:
        parsed = MyToolArgs.model_validate(args)
        return await handle_my_tool(parsed.item_id, data_dir)

    return create_sdk_mcp_server(
        name="my-server",
        version="1.0.0",
        tools=[my_tool],
    )
```

**Why**: Factory owns tool registration; handler owns logic; processor just calls `create_my_server(...)` and passes the result to `fork_and_consume(mcp_servers=...)`. Handler is directly testable. Pydantic models provide type coercion (e.g., string `"true"` → Python `True` for booleans), required-field validation, and generate rich JSON schemas with defaults and optionality for the agent.

### Don't Do This

```python
def create_my_server(data_dir: Path) -> McpSdkServerConfig:

    @tool("my_tool", "Description", {"item_id": str})
    async def my_tool(args: dict) -> dict:
        item_id = args.get("item_id", "")
        # Manual extraction and validation
        if not item_id:
            return {"is_error": True, "content": [...]}
        # ... 30 lines of business logic ...
        return {"content": [{"type": "text", "text": "Done"}]}

    return create_sdk_mcp_server(name="my-server", version="1.0.0", tools=[my_tool])
```

**Why**: Manual `args.get()` with defaults is error-prone — booleans arrive as strings from JSON, required fields need manual checking, and the simple dict schema loses type information (defaults, optionality). Use Pydantic models for arg extraction and the `model_json_schema()` for the `input_schema`.

### Don't Do This

```python
# Module-level tool — can't capture per-invocation state
@tool("my_tool", "Description", {"item_id": str})
async def my_tool(args: dict) -> dict:
    # No access to processor-specific snapshot or config
    return {"content": [{"type": "text", "text": "..."}]}

MY_SERVER = create_sdk_mcp_server(name="my-server", version="1.0.0", tools=[my_tool])
```

**Why**: Module-level tools are singletons — they can't capture per-invocation state like snapshots or processor-specific paths. Each processor run may need different configuration passed through the factory.

### Array-Typed Args (JSON-String Fallback)

The Claude Agent SDK's MCP transport occasionally serializes `array`-typed tool arguments as JSON-encoded strings before they reach the receiving model, producing validation errors of the form `'[...]' is not valid under any of the given schemas`. For any MCP tool whose `args` model has a `list[T]` (or `list[T] | None`) field, add a `field_validator(..., mode="before")` that decodes the value when it arrives as a string, then defers to standard list validation.

```python
import json

from pydantic import BaseModel, field_validator


class MyArgs(BaseModel):
    paths: list[str] | None = None

    # Workaround: SDK MCP transport may stringify array arguments. Decode
    # defensively so the tool keeps working without broadening the schema.
    @field_validator("paths", mode="before")
    @classmethod
    def _decode_paths(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        try:
            decoded = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"paths must be a JSON-encoded array: {exc}") from exc
        if not isinstance(decoded, list):
            raise ValueError(
                f"paths JSON string must encode an array, got {type(decoded).__name__}"
            )
        return decoded
```

**Why a `field_validator`, not a union type.** Declaring the field as `list[T] | str` would broaden the JSON Schema (`anyOf` of `array` and `string`) and advertise the JSON-string form as a first-class input. The schema should still describe what the tool conceptually accepts — an array. The validator is a defensive coercion at the transport boundary, not part of the public contract. Pydantic builds the JSON Schema from the declared type annotation; validators are invisible to schema generation, which is exactly what we want here.

**Item validation still applies.** After the validator decodes the string, Pydantic's standard `list[str]` validation runs against the result, so non-string elements (e.g. `'[1, 2]'`) are still rejected with a clear error.

## Exceptions

When a processor needs only trivially simple tools with no per-invocation state and minimal logic (e.g., a single tool that returns a static string), extracting a handler may be over-engineering. Use judgment — the factory pattern is still recommended for consistency, but handler extraction can be skipped if the closure body is 1-3 lines.

---

## Related

- [DES-004](DES-004-prompt-driven-forked-processor.md): Processor pattern that consumes these tool servers via `fork_and_consume(mcp_servers=...)`
- [DES-001](DES-001-testing-conventions.md): Testing conventions for extracted handler functions
