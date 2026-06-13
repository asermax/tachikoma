# Design: REPL Channel

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/repl.md](../feature-specs/repl.md)
**Status**: Current

## Purpose

Explain why the REPL is a single-file readline channel rather than a full-featured TUI.

## Problem Context

The REPL is the walking-skeleton channel: it proves message-in/response-out end to end and remains the default developer surface. The Channel contract it implements is owned by the core ([core-shell](core-shell.md)); the coordinator pushes one `Exchange` event stream per message and background `Delivery` items (see [conversation-loop](conversation-loop.md)).

**Constraints:**
- Channels are thin — all logic lives in the coordinator; the channel only reads input and renders events
- Must coexist with the app's signal-based shutdown (SIGINT/SIGTERM handled centrally)
- Developer tool: functional beats fancy

**Interactions:**
- Registered via `app.channels.register`; the core shell selects and starts one channel per process
- The [telegram](telegram.md) channel implements the same `Channel` interface for the user-facing surface

## Design Overview

One class, `ReplChannel` in `src/extensions/repl/index.ts`, implementing `Channel` (`src/channels/types.ts`) over `node:readline`: `start` wires the line, `SIGINT`, and close handlers, `respond` consumes one exchange's `AsyncIterable<AgentEvent>` switch-casing on event kind, `deliver` prints background items, `stop` closes the interface. Tool/status/error lines use raw ANSI escape constants (dim, red). Agent text is rendered as markdown by a small pure helper, `renderMarkdown` in `src/extensions/repl/markdown.ts`.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/repl/index.ts` | `ReplChannel` (input loop + rendering) and the `defineExtension` wiring | `node:readline` over a TUI library; streamed text is buffered per exchange and flushed through `renderMarkdown` so interrupting events flush a newline first; a `streaming` flag gates Ctrl-C between abort and exit; `thinking`/`tool-end` intentionally unrendered |
| `src/extensions/repl/markdown.ts` | `renderMarkdown`: pure markdown-to-ANSI line renderer | Line-oriented, zero-dependency; covers headings/bold/italic/inline-code/fences/lists; operates on finalized text rather than per-chunk because inline spans can straddle stream chunks |

## Key Decisions

### node:readline over a rich TUI stack

**Choice**: Use the Node standard library's readline; styling is hand-rolled ANSI escape constants and a small in-tree markdown renderer. No persistent history, no multiline composition.
**Why**: The REPL only has to prove the channel contract end to end and stay a comfortable developer surface. A TUI framework is a heavy dependency for a single-screen loop; history and multiline editing are not worth the complexity here and are explicitly out of scope.
**Alternatives Considered**:
- ink / blessed style TUI: heavy dependency for a developer loop
- A markdown library (e.g. marked-terminal): a runtime dependency where a ~70-line renderer covers the constructs the agent actually emits

**Consequences**:
- Pro: zero runtime dependencies; the channel stays small
- Con: the markdown renderer is intentionally lossy on exotic syntax; no input history across runs; no multiline input

### Buffer streamed text and render markdown as a block

**Choice**: `respond` accumulates `text` events into a buffer and renders it through `renderMarkdown` only when flushed — on the next non-text event or at `result` — rather than styling each chunk.
**Why**: Markdown inline spans (`**bold**`, `` `code` ``) and block fences routinely straddle stream-chunk boundaries; per-chunk styling cannot know where a span ends. Buffering to a natural boundary gives the renderer a complete line set to work on. The trade-off — text appears in a burst at the flush point rather than truly character-by-character — is acceptable for a developer loop.
**Consequences**:
- Pro: correct rendering of multi-chunk markdown; the renderer stays a pure string→string function that is trivially unit-tested
- Con: streamed text is not shown incrementally within a block; it lands when flushed

### Ctrl-C aborts the exchange when streaming, exits when idle

**Choice**: A `streaming` flag is set for the duration of `respond`. The readline `SIGINT` handler aborts the in-flight exchange via the injected `abort()` callback (wired to `app.sessions.abortExchange()`, the same coordinator API Telegram's `/stop` uses) while streaming, and closes the interface when idle. The `close` handler still nulls the interface and self-signals SIGINT for the central shutdown path (covers Ctrl-D and the idle Ctrl-C case).
**Why**: readline intercepts Ctrl-C and emits its own `SIGINT` event instead of letting the process default fire, so the channel owns the abort-vs-exit decision. Aborting only the exchange lets a developer stop a runaway response and keep typing, mirroring Telegram. Shutdown stays centrally owned by the app's signal handling; the channel does not fork it.
**Consequences**:
- Pro: mid-stream interrupt without losing the session; one shutdown path regardless of how exit is triggered
- Con: the streaming/idle branch and the self-signal indirection take a moment to follow when reading the code

## System Behavior

### Scenario: Tool call mid-stream

**Given**: The agent is streaming text and then invokes a tool
**When**: the `tool-start` event arrives after `text` events
**Then**: the buffered text is rendered as markdown and flushed (terminated by a newline), the dim tool line prints, and subsequent text buffers afresh.

### Scenario: Ctrl-C during an exchange

**Given**: The agent is still streaming (`streaming` is true)
**When**: the user presses Ctrl-C
**Then**: the readline `SIGINT` handler calls `abort()` (the coordinator's `abortExchange`), the in-flight run stops, the event stream completes, and the prompt returns — the process keeps running.

### Scenario: Ctrl+D / idle Ctrl-C

**Given**: No exchange is streaming
**When**: the user closes stdin (Ctrl-D) or presses Ctrl-C
**Then**: the interface is closed and nulled, SIGINT triggers the app shutdown sequence, and any straggling event rendering writes to stdout without calling `prompt()` on the closed interface.

## Notes

- The markdown renderer is unit-tested in `tests/repl/markdown.test.ts`; the channel I/O itself stays untested, exercised through the event stream produced by the tested adapter (`tests/adapter.test.ts`)
- Renders with text markers (a gear for tools, an inbox symbol for deliveries) defined inline in `index.ts`
- Persistent input history and multiline composition are explicitly out of scope
