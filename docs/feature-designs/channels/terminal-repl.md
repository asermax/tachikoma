# Design: Terminal REPL

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/channels/terminal-repl.md](../../feature-specs/channels/terminal-repl.md)
**Status**: Current

## Purpose

This document explains the design rationale for the terminal REPL: the input handling, event rendering, and control flow approach.

## Problem Context

The agent needs a developer-facing interactive terminal as its first communication channel. The REPL must integrate with the async coordinator, handle standard terminal signals (Ctrl+C, Ctrl+D), and render streamed `AgentEvent`s in a readable format.

**Constraints:**
- Must integrate with asyncio event loop (coordinator is async)
- Standard terminal conventions for exit signals
- Developer tool — functional and pleasant but doesn't need to be fancy

## Design Overview

Two classes with separated concerns: `Repl` owns the input loop and control flow, `Renderer` owns event rendering and line-state tracking. Both live in `src/tachikoma/repl.py` due to high cohesion.

The REPL uses `prompt_toolkit` for async input with persistent file history at `~/.tachikoma/repl_history`.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `Repl` | Input loop, control flow, exit conditions, interrupt handling | Owns `PromptSession` with `FileHistory` and empty-input `Validator` |
| `Renderer` | Event rendering, line-state tracking (`_needs_newline`) | `render()` returns bool: `True` to continue, `False` to exit |

### Event Rendering

| Event Type | Rendering | Line State |
|------------|-----------|------------|
| `TextChunk` | Print inline (`end=""`, `flush=True`) | Sets `_needs_newline = True` |
| `ToolActivity` | Newline if needed, then gray italic status line with tool details | Clears `_needs_newline` |
| `Result` | Print newline | Clears `_needs_newline` |
| `Error` | Print to stderr | Clears `_needs_newline`; returns `False` if non-recoverable |

**Tool display format:** Known tools show contextual details (e.g., "Reading src/main.py...", "Searching for 'authenticate'...", "Globbing \*\*/\*.py..."). Unknown tools show the tool name.

## Key Decisions

### prompt_toolkit for REPL input

**Choice**: Use `prompt_toolkit` with `PromptSession.prompt_async()` for terminal input
**Why**: Provides async-native input that integrates with the asyncio event loop, plus built-in history (`FileHistory` for persistence across sessions) and key bindings — without blocking the event loop.
**Alternatives Considered**:
- `input()` via ThreadPoolExecutor: Simple but no history, needs executor wrappers
- `readline` stdlib: Synchronous only
- `aioconsole`: Async but no history or key bindings

**Consequences**:
- Pro: Persistent input history across sessions
- Pro: Async-native, no executor hacks
- Pro: Extensible (can add completions, key bindings later)
- Con: Extra dependency (~1.5MB)

### Separated Repl/Renderer classes

**Choice**: Separate input (`Repl`) from output (`Renderer`) in the same module
**Why**: The `Renderer` encapsulates line-state tracking (`_needs_newline`) needed for correct newline insertion between text chunks and tool activity lines. This keeps `Repl` focused on control flow while making rendering independently testable.

**Consequences**:
- Pro: Rendering logic is testable without mocking prompt_toolkit
- Pro: Clear separation of concerns
- Con: Two classes in one module (acceptable due to high cohesion)

## System Behavior

### Scenario: Ctrl+C during streaming

**Given**: The agent is streaming a response
**When**: The user presses Ctrl+C
**Then**: The REPL calls `coordinator.interrupt()`. Partial output remains visible. The REPL exits.

### Scenario: Ctrl+C at prompt

**Given**: The REPL is waiting for user input
**When**: The user presses Ctrl+C
**Then**: `prompt_toolkit` raises `KeyboardInterrupt`. The REPL exits cleanly.

### Scenario: Ctrl+D at empty prompt

**Given**: The REPL is waiting for input with an empty buffer
**When**: The user presses Ctrl+D
**Then**: `prompt_toolkit` raises `EOFError`. The REPL exits cleanly.

### Scenario: Empty input prevented

**Given**: The REPL is waiting for input
**When**: The user presses enter without content
**Then**: The validator rejects submission. The cursor stays on the same line.

### Scenario: Agent uses a tool

**Given**: The agent is processing a message
**When**: A `ToolActivity` event arrives
**Then**: A gray italic status line shows tool-specific details (e.g., "Reading src/main.py...", "Searching for 'authenticate'..."). The agent's text response continues streaming after.

## Notes

- Input history is persisted via `prompt_toolkit`'s `FileHistory` at `~/.tachikoma/repl_history`, providing history across REPL sessions
- Ctrl+C always exits the REPL regardless of state (during streaming or at prompt). Use Ctrl+U to clear the current input line without exiting.
- The `Renderer.render()` return value (`bool`) provides the control flow signal: `True` means continue consuming events, `False` means exit the REPL (on non-recoverable errors)
