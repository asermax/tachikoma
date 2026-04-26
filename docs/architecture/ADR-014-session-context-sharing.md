# ADR-014: Session Context Sharing for MCP Tool Servers

**Status**: Accepted
**Date**: 2026-04-26

## Context

MCP tool servers (workflow-tools, task-tools, git-tools) run as separate server instances registered with the Claude Agent SDK. Tool handlers execute inside these servers with no access to coordinator state — most importantly, they cannot access the current SDK session ID.

The workflow engine needs to fork the current session (via `fork_and_capture`) for condition evaluation: a read-only sub-agent that inherits conversation context and can inspect the workspace. Session forking requires the SDK session ID, but MCP tool handlers have no way to obtain it.

The coordinator stores `self._sdk_session_id` and updates it from Result events after each message exchange. This value is also persisted to the database via the session registry. However, neither the coordinator's instance state nor the database session record is accessible from MCP tool handlers — they receive only the arguments Claude passes them.

## Decision

Introduce a shared mutable `SessionContext` object — a simple container for the current SDK session ID, created once in `__main__.py` and passed to both the coordinator and any MCP tool server factory that needs session access.

```python
class SessionContext:
    """Mutable reference for the current SDK session ID."""

    def __init__(self) -> None:
        self._sdk_session_id: str | None = None

    def set(self, session_id: str | None) -> None:
        self._sdk_session_id = session_id

    def get(self) -> str | None:
        return self._sdk_session_id
```

### Wiring

```python
# __main__.py
session_context = SessionContext()

workflow_tools = create_workflow_tools_server(
    ...,
    session_context=session_context,
)

async with Coordinator(
    ...,
    session_context=session_context,
) as coordinator:
    ...
```

The coordinator updates the context after each message exchange:

```python
# coordinator.py — where _sdk_session_id is currently set
self._sdk_session_id = event.session_id
self._session_context.set(event.session_id)
```

MCP tool handlers read the session ID through the closure-captured context:

```python
# tools.py — inside handler closure
sdk_session_id = session_context.get()
```

### First-exchange semantics

During the first message exchange, the SDK session ID is not yet available — it arrives in the Result event after the exchange completes. Consumers must handle `None` gracefully. For the workflow condition evaluator, a missing session ID means the condition is assumed to pass (the step runs normally), since first steps rarely have conditions and the ID will be available by the time step 02 needs evaluation.

## Consequences

### Positive

- MCP tool handlers gain session forking capability without coupling to the coordinator or session registry
- Subsystem-agnostic: any MCP server that needs session context gets the same `SessionContext` instance
- No new dependencies, no new persistence mechanism — a shared reference to a mutable attribute
- Coordinator remains the single writer; MCP tools are read-only consumers
- Consistent with the existing pattern of passing shared objects (EventBus, repositories) through `__main__.py` wiring

### Negative

- Mutable shared state requires careful lifecycle management — the coordinator must clear the context alongside `_sdk_session_id` on session close/reset
- Session ID is unavailable during the first exchange of a conversation — consumers must handle `None`
- Not thread-safe (single asyncio event loop, so acceptable in practice)

## Alternatives Considered

### Context variables (`contextvars.ContextVar`)

- **Description**: Set a context variable in the coordinator before each SDK call, read it in MCP tool handlers
- **Why rejected**: MCP tool handlers may run in different async contexts or subprocesses depending on the SDK's MCP transport. Context variables don't reliably propagate across these boundaries. The mutable reference is deterministic regardless of transport.

### Session registry lookup from MCP handlers

- **Description**: MCP tool handlers query the session registry directly for the active session's SDK session ID
- **Why rejected**: Couples MCP tools to the session registry. Requires the registry to expose a "current session" concept that doesn't currently exist. The registry tracks sessions by ID, not "which session is currently active in the coordinator."

### Inject session ID via MCP server config

- **Description**: Recreate the MCP server config before each exchange with the current session ID embedded
- **Why rejected**: MCP server configs are created once during bootstrap and registered as base servers. Recreating them per-exchange adds complexity to the coordinator's `_build_options()` and may interfere with the SDK's server lifecycle management.

---

## Notes

- First consumer: workflow condition evaluator (`src/tachikoma/workflows/conditions.py`)
- `SessionContext` lives in `src/tachikoma/session_context.py` — a standalone module with no subsystem dependencies
- The coordinator clears the context in `_clear_session_state()`, `_handle_encoding_error()`, and wherever `_sdk_session_id` is reset to `None`
