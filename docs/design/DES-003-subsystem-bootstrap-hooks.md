# DES-003: Subsystem-Owned Bootstrap Hooks

**Scope**: Python / Architecture
**Status**: Established
**Date**: 2026-03-13
**First Used**: DLT-005, DLT-023

## Problem

As the system grows, more modules need first-run setup (workspace creation, context files, git initialization, task queues, etc.). Without a clear pattern, bootstrap logic accumulates in a central location, creating ordering issues and coupling unrelated concerns.

## Solution

Each subsystem owns its bootstrap hook in its own module. The `bootstrap.py` module owns only the mechanism (`Bootstrap` class, `BootstrapContext`, `BootstrapHook` type, `BootstrapError`), not the hooks themselves. Hooks are defined where they logically belong:

- **workspace subsystem** → `src/tachikoma/workspace.py` owns `workspace_hook`
- **git subsystem** → `src/tachikoma/git/hooks.py` owns `git_hook`
- **logging subsystem** → `src/tachikoma/logging/hooks.py` owns `logging_hook`
- **context subsystem** → `src/tachikoma/context.py` owns `context_hook`
- **skills subsystem** → `src/tachikoma/skills/hooks.py` owns `skills_hook`
- **memory subsystem** → `src/tachikoma/memory/hooks.py` owns `memory_hook`
- **sessions subsystem** → `src/tachikoma/sessions/hooks.py` owns `session_recovery_hook`

The `__main__.py` entry point registers hooks in order:

```python
bootstrap.register("workspace", workspace_hook)
bootstrap.register("context", context_hook)
bootstrap.register("sessions", session_recovery_hook)
# future hooks follow the same pattern
await bootstrap.run()
```

## When to Use

Define and place a bootstrap hook in the module that owns the subsystem it initializes:

1. Create a hook function: `async def my_hook(ctx: BootstrapContext) -> None`
2. Place it in the module that owns the subsystem (e.g., `context_hook` in `context.py`)
3. Import and register it in `__main__.py`: `bootstrap.register("name", my_hook)`
4. Ensure the hook is idempotent — it self-determines if action is needed

## Example

**context.py** (owns context subsystem):
```python
async def context_hook(ctx: BootstrapContext) -> None:
    """Initialize core context files and assemble system prompt."""
    workspace_path = ctx.settings_manager.settings.workspace.path
    context_path = workspace_path / "context"

    # Create directory if missing
    context_path.mkdir(exist_ok=True)

    # Write default files if missing
    for filename, _, content in CONTEXT_FILES:
        file_path = context_path / filename
        if not file_path.exists():
            file_path.write_text(content)

    # Load and store assembled prompt
    prompt = load_context(workspace_path)
    if prompt:
        ctx.extras["system_prompt"] = prompt
```

**__main__.py** (registers hooks):
```python
from tachikoma.workspace import workspace_hook
from tachikoma.git import git_hook
from tachikoma.logging import logging_hook
from tachikoma.skills import skills_hook
from tachikoma.context import context_hook
from tachikoma.memory import memory_hook
from tachikoma.sessions import session_recovery_hook

bootstrap.register("workspace", workspace_hook)
bootstrap.register("logging", logging_hook)
bootstrap.register("git", git_hook)
bootstrap.register("skills", skills_hook)
bootstrap.register("context", context_hook)
bootstrap.register("memory", memory_hook)
bootstrap.register("sessions", session_recovery_hook)
```

## Benefits

- **Separation of concerns**: Each subsystem is self-contained; bootstrap mechanism is separate from any one subsystem's logic
- **Discoverability**: Looking at `context.py`, you immediately see `context_hook` — all initialization logic is colocated
- **Extensibility**: New subsystems follow the same pattern; `__main__.py` registration order is the explicit documentation of initialization sequence
- **Testability**: Hooks are plain async functions; easy to test in isolation with injected `BootstrapContext`
- **No coupling**: Subsystems don't depend on a centralized hook registry; they just define and export their hook

## Trade-offs

| Aspect | Trade-off |
|--------|-----------|
| Code organization | Slightly more ceremony (one more function per subsystem), but much clearer separation |
| Registration location | `__main__.py` must list all hooks, but this is explicit documentation of initialization order |
| Discoverability | Hooks are in their subsystems, not a central "hooks" file, but they're easy to find colocated with the subsystem |

## Related Patterns

- **Bootstrap mechanism** (DES-002 Logging Conventions for hook logging): `BootstrapContext` provides a logger binding point per DES-002 conventions
- **Hook idempotency**: Each hook self-determines whether it needs to act; `__main__.py` runs the full sequence every launch

## See Also

- `src/tachikoma/bootstrap.py` — Bootstrap mechanism (not hooks)
- `src/tachikoma/workspace.py` — workspace_hook example
- `src/tachikoma/context.py` — context_hook example
- `src/tachikoma/__main__.py` — Hook registration order
