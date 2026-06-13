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

One class, `ReplChannel` in `src/extensions/repl/index.ts`, implementing `Channel` (`src/channels/types.ts`) over `node:readline`: `start` wires the line and close handlers, `respond` consumes one exchange's `AsyncIterable<AgentEvent>` switch-casing on event kind, `deliver` prints background items, `stop` closes the interface. Styling is raw ANSI escape constants (dim, red).

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/repl/index.ts` | `ReplChannel` (input loop + rendering) and the `defineExtension` wiring | `node:readline` over a TUI library; an `inText` flag tracks whether streamed text is mid-line so non-text events flush a newline first; `thinking`/`tool-end` intentionally unrendered |

## Key Decisions

### node:readline + raw ANSI instead of a rich TUI stack

**Choice**: Use the Node standard library's readline with two ANSI constants for styling; no markdown rendering, no persistent history, no multiline composition.
**Why**: The REPL only has to prove the channel contract end to end. Dependencies and rendering sophistication can be added when they earn their place.
**Alternatives Considered**:
- ink / blessed style TUI: heavy dependency for a developer loop
- Terminal markdown rendering: meaningful effort with no bearing on the architecture being validated

**Consequences**:
- Pro: zero dependencies; the whole channel fits in one screen of code
- Con: raw markdown source is shown verbatim; no input history across runs; no multiline input

### Route Ctrl+D through SIGINT instead of exiting in-place

**Choice**: The readline `close` handler nulls the interface and sends the process SIGINT.
**Why**: Shutdown (stop channel, stop scheduler, close session with post-processing) is owned centrally by the app's signal handling; re-implementing it in the channel would fork the shutdown path. Nulling `readline` first keeps a still-rendering exchange from prompting on a closed interface.
**Consequences**:
- Pro: one shutdown path regardless of how exit is triggered
- Con: a self-signal is slightly indirect to follow when reading the code

## System Behavior

### Scenario: Tool call mid-stream

**Given**: The agent is streaming text and then invokes a tool
**When**: the `tool-start` event arrives after `text` events
**Then**: a newline closes the partial text line, the dim tool line prints, and subsequent text resumes on a fresh line.

### Scenario: Ctrl+D during an exchange

**Given**: The agent is still streaming
**When**: the user closes stdin
**Then**: the interface is nulled, SIGINT triggers the app shutdown sequence, and the remaining event rendering writes to stdout without calling `prompt()` on the closed interface.

## Notes

- There are no dedicated REPL tests; the channel is plain I/O over the event stream produced by the tested adapter (`tests/adapter.test.ts`)
- Renders with text markers (a gear for tools, an inbox symbol for deliveries) defined inline in `index.ts`
