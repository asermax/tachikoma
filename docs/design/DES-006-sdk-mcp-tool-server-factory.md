# DES-006: SDK MCP Tool Server Factory

**Scope**: Python
**Date**: 2026-03-21
**Last Updated**: 2026-04-27

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

### Array-Typed Args (JSON-String Argument)

The Claude Agent SDK's MCP transport rejects array-typed tool arguments at its client-side JSON Schema validator before the value ever reaches the receiving model — both the array form and any stringified form fail with `is not valid under any of the given schemas`. For any MCP tool that conceptually accepts an array, declare the field as `str | None` (the JSON Schema becomes `string | null`, which the transport accepts) and parse the JSON string into a list inside a module-level helper called from the tool wrapper. Treat this as the default approach for array arguments — there is no reliable way to declare an `array` schema for an MCP tool.

```python
import json

from claude_agent_sdk import create_sdk_mcp_server, tool
from pydantic import BaseModel


class MyArgs(BaseModel):
    # Declared as a JSON-encoded string (e.g. '["a", "b"]') because the SDK
    # MCP transport's client-side schema validator rejects array-typed
    # arguments. The wrapper parses it via _decode_paths.
    paths: str | None = None


def _decode_paths(raw: str) -> list[str]:
    """Decode the JSON-string form of ``paths`` into a list of strings.

    Raises:
        ValueError: when ``raw`` is not valid JSON, does not encode an array,
            or encodes an array containing non-string items.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"paths must be a JSON-encoded array of strings: {exc}") from exc
    if not isinstance(decoded, list):
        raise ValueError(
            f"paths JSON string must encode an array, got {type(decoded).__name__}"
        )
    if not all(isinstance(item, str) for item in decoded):
        raise ValueError("paths JSON array must contain only strings")
    return decoded


def create_my_server() -> "McpSdkServerConfig":
    @tool("my_tool", "...accepts paths as a JSON-encoded string...", MyArgs.model_json_schema())
    async def my_tool(args: dict) -> dict:
        parsed = MyArgs.model_validate(args)
        paths_list: list[str] | None = None
        if parsed.paths is not None:
            try:
                paths_list = _decode_paths(parsed.paths)
            except ValueError as exc:
                return {"is_error": True, "content": [{"type": "text", "text": f"Error: {exc}"}]}
        # ... delegate to handler with paths_list ...

    return create_sdk_mcp_server(name="my-server", version="1.0.0", tools=[my_tool])
```

**Why parse in the wrapper, not in a Pydantic validator.** Putting the JSON parse inside a `field_validator` would either (a) require declaring the field as `list[T] | None`, which regenerates an `array | null` JSON Schema that the transport rejects, or (b) live on a `str | None` field and mutate the value to a `list[T]`, leaving the type annotation lying about the runtime contents. Wrapper-level parsing keeps the schema honest (`string | null`) and the handler signature honest (`list[T] | None`), at the cost of one extra block in the wrapper.

**Why a module-level helper.** The tool wrapper is a closure inside the factory and is awkward to test directly. A module-level `_decode_<field>` helper is directly importable, gives the parse-and-validate logic full unit-test coverage, and keeps the wrapper short.

**Document the JSON-string form for the agent.** The tool description text shown to the agent must include a concrete example — the agent has no way to infer that the string should be JSON-encoded from the schema alone. State explicitly that `<field>` is a JSON-encoded string and show one full example per tool.

**Tool-description placement of the example.** The tool's `@tool(..., description=...)` block is where this contract is communicated. Without an example, models will frequently send raw arrays or comma-separated strings.

## Exceptions

When a processor needs only trivially simple tools with no per-invocation state and minimal logic (e.g., a single tool that returns a static string), extracting a handler may be over-engineering. Use judgment — the factory pattern is still recommended for consistency, but handler extraction can be skipped if the closure body is 1-3 lines.

---

## Related

- [DES-004](DES-004-prompt-driven-forked-processor.md): Processor pattern that consumes these tool servers via `fork_and_consume(mcp_servers=...)`
- [DES-001](DES-001-testing-conventions.md): Testing conventions for extracted handler functions
