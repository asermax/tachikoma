# Terminal REPL

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

An interactive terminal REPL that reads user input, sends it through the coordinator, and streams the agent's response to the terminal. Primarily a developer tool for interacting with the agent locally.

## User Stories

- As a developer, I want to type messages in a terminal and see the agent's streamed response so that I can interact with the agent during development
- As a developer, I want to see what tools the agent is using while it works so that I understand what is happening during pauses
- As a developer, I want my input history preserved across sessions so that I can recall previous messages
- As a developer, I want to compose multiline messages so that I can send structured content or code snippets to the agent

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Terminal REPL that reads input, sends through coordinator, and streams the response |
| R1 | Streaming display: markdown-rendered text, tool activity as styled status lines |
| R2 | Input management: persistent history across sessions, empty input prevention |
| R3 | Clean exit handling: Ctrl+C, Ctrl+D, exit/quit commands |
| R4 | Multiline input: Enter submits, Escape+Enter inserts newline |
| R5 | Event bus integration: subscribe to `BufferedDelivery` events and route prompts through the coordinator as new message turns; flush pending buffer items on shutdown (see [delivery/priority-buffer](../delivery/priority-buffer.md)) |

## Behaviors

### Streaming Display (R1)

The REPL renders domain events to the terminal as they arrive: text chunks are rendered as formatted markdown (headings, emphasis, syntax-highlighted code blocks, lists) via `rich`, tool activity shows as styled status lines with tool-specific details, errors print to stderr.

**Acceptance Criteria**:
- Given the agent responds with text, when text events arrive, then text is rendered with markdown formatting (headings, emphasis, syntax-highlighted code blocks, lists)
- Given the agent responds with plain text (no markdown), when rendered, then it displays normally without artifacts
- Given the agent uses a tool, when a tool activity event arrives, then a gray italic status line shows a wrench icon, tool name, and key parameters (e.g., "🔧 Reading src/main.py")
- Given the agent uses an unknown tool, when a tool activity event arrives, then the tool name is shown as a fallback; MCP tool names (starting with `mcp__`) are formatted into human-readable labels (e.g., `mcp__projects__list_projects` shows as "List Projects")
- Given the agent completes its response, when a result event arrives, then a newline is printed and the REPL shows a new prompt
- Given an error occurs, when an error event arrives, then the error message prints to stderr
- Given the coordinator yields a Status event (e.g., "Thinking..."), when received, then the status message is displayed as dim italic text

### Input Management (R2)

The REPL provides async input with persistent file history and input validation.

**Acceptance Criteria**:
- Given the REPL is running, when the user sends multiple messages, then conversation context is preserved between them
- Given the user presses enter without typing (or whitespace only), then input is rejected and the prompt stays
- Given a previous session's history exists, when the REPL starts, then prior inputs are available via history navigation
- Given the REPL starts, then input history is loaded from a temporary file path (`/tmp/tachikoma_repl_history`), persisting across sessions within the same system boot

### Multiline Input (R4)

The REPL accepts multiline input using prompt_toolkit with custom key bindings that override the default multiline behavior.

**Acceptance Criteria**:
- Given the user is typing, when they press Enter, then the message is submitted to the coordinator
- Given the user is composing multiline input, when they press Escape followed by Enter (or Alt+Enter on terminals that encode Alt as Escape prefix), then a newline is inserted at the cursor position
- Given the user has entered a newline via Escape+Enter, when the continuation line is displayed, then it is indented to align with the prompt's content area

### Event Bus Integration (R5)

The REPL subscribes to `BufferedDelivery` events dispatched by the priority buffer. Each delivery carries a ready-to-send prompt (single item or shutdown digest) which the REPL wraps in a `TextMessage` envelope (per [core-architecture](../agent/core-architecture.md) R21) and routes through the coordinator as a new message turn. The REPL does not subscribe to `Notification` or session-task events directly — the priority buffer handles enqueueing, ordering, and idle gating (see [delivery/priority-buffer](../delivery/priority-buffer.md)).

**Acceptance Criteria**:
- Given a `BufferedDelivery` event arrives while the REPL is waiting for input, then the REPL routes the event's prompt through `coordinator.send_message()` as a new message turn, renders the response normally, and fires the per-item `on_delivered` callbacks on completion
- Given a `BufferedDelivery` event arrives while the REPL is actively processing a previous exchange, then delivery is deferred to the next idle moment (the buffer waits for the coordinator busy→idle transition before re-emitting)
- Given the REPL receives a shutdown digest `BufferedDelivery`, then the combined prompt is routed through the coordinator as a final exchange before exit

### Exit Handling (R3)

The REPL exits cleanly on standard terminal signals without stack traces.

**Acceptance Criteria**:
- Given the REPL is waiting for input, when Ctrl+C is pressed, then the REPL exits cleanly
- Given the REPL is waiting for input on an empty prompt, when Ctrl+D is pressed, then the REPL exits cleanly
- Given the user types "exit" or "quit", then the REPL exits cleanly
- Given the agent is streaming, when Ctrl+C is pressed, then the stream is interrupted and the REPL exits
- Given a non-recoverable error event, then the REPL exits with the error message
- Given a recoverable error event, then the error is shown and the REPL continues
