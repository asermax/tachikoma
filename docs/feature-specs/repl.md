# REPL Channel

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A terminal channel for developing against the agent locally: a readline prompt reads lines, submits them through the coordinator, and renders the streamed agent events inline. It is the default channel (`channels.default = "repl"`) and deliberately minimal. Streamed agent text is rendered as ANSI-styled markdown (headings, bold/italic, inline code, code fences, list bullets); tool/status/error lines use ANSI dim/red styling. Persistent input history and multiline composition remain out of scope.

## User Stories

- As a developer, I want to type messages in a terminal and see the agent's streamed response so that I can exercise the full message loop locally
- As a developer, I want tool activity and status shown inline so that I can see what the agent is doing during pauses
- As a developer, I want the agent's response rendered as styled markdown so that headings, code, and lists are readable in the terminal
- As a developer, I want Ctrl-C to abort the current response (not kill the process) so that I can stop a runaway exchange and keep typing

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | The extension registers a `ReplChannel` (name `repl`) via `app.channels.register`; the core shell starts whichever channel is selected (see [core-shell](core-shell.md)) |
| R1 | Non-empty input lines are trimmed and submitted as inbound text messages; empty lines just re-prompt |
| R2 | Agent events stream to stdout: `text` is buffered per exchange and rendered as styled markdown on the next non-text event (or at `result`); `tool-start` as a dim line with a gear marker and the tool name; `status` dim; `error` in red with an `error:` prefix (a non-recoverable error additionally appends ` (<errorKind>, not recoverable)`); `result` prints a dim cost/token summary line (`· $<cost> · <tokens> tokens`) when `event.result` is non-null, then a blank line, then re-prompts |
| R3 | `thinking` and `tool-end` events are not rendered |
| R4 | Non-text events that interrupt buffered text are preceded by a newline so status lines never splice into a sentence |
| R5 | Background deliveries (`deliver`) print the delivery text on its own line with an inbox marker, then re-prompt |
| R6 | Closing stdin (Ctrl+D) detaches the readline interface and signals SIGINT so shutdown flows through the app's regular signal path; an exchange still rendering must not touch the closed interface |
| R7 | Streamed agent text is rendered through a markdown renderer supporting headings, bold, italic, inline code, fenced code blocks, and list bullets via ANSI styling; rendering operates on finalized buffered text (not per-chunk) because inline spans can straddle stream chunks |
| R8 | While an exchange is streaming, Ctrl-C (SIGINT) aborts the in-flight exchange via the coordinator's abort API rather than killing the process; when idle, Ctrl-C and Ctrl-D keep the existing shutdown behavior |
| R9 | `ReplChannel` implements the optional `Channel.status(text)` method, printing the text as a dim `· <text>` line to stdout, so coordinator/extension progress lines (`app.status`) surface in the terminal |

## Behaviors

### Input Loop (R0, R1)

**Acceptance Criteria**:
- Given the channel starts, then a `you> ` prompt appears and each non-empty line is submitted via `runtime.submit` as a text message from channel `repl`
- Given the user presses Enter on an empty line, then nothing is submitted and the prompt is shown again

### Event Rendering (R2, R3, R4)

**Acceptance Criteria**:
- Given the agent streams text, when chunks arrive, then they are buffered and rendered as styled markdown when flushed (on the next non-text event or at `result`)
- Given a tool starts mid-stream, when the event arrives, then buffered text is flushed (terminated by a newline) before the dim tool line prints
- Given the exchange ends, when the `result` event arrives, then buffered text is flushed; if `event.result` is non-null a dim `· $<cost> · <tokens> tokens` summary line is printed (cost to 4 decimals, total tokens), then a blank line is printed and the prompt returns
- Given a recoverable error event, then `error: <message>` prints in red and the loop continues
- Given a non-recoverable error event, then `error: <message> (<errorKind>, not recoverable)` prints in red and the loop continues

### Markdown Rendering (R7)

**Acceptance Criteria**:
- Given streamed text containing headings, bold/italic spans, inline code, fenced code, or list items, when it is flushed, then it is rendered with ANSI styling and markdown markers (`#`, `**`, backticks, fences) are not shown verbatim
- Given a fenced code block, when it is rendered, then the fence delimiter lines (the opening and closing ` ``` `) are stripped entirely (consumed, never written) and only the lines inside the fence are emitted, dim-styled
- Given plain prose, then it passes through unchanged

### Ctrl-C Exchange Interrupt (R8)

**Acceptance Criteria**:
- Given an exchange is streaming, when the user presses Ctrl-C, then the in-flight exchange is aborted via the coordinator's abort API and the process keeps running
- Given no exchange is running, when the user presses Ctrl-C or Ctrl-D, then the app shuts down through the regular SIGINT path

### Background Delivery and Shutdown (R5, R6)

**Acceptance Criteria**:
- Given a background-originated delivery reaches the channel, then its text prints on a fresh line and the prompt returns
- Given the user presses Ctrl+D, then the process receives SIGINT and the app shuts down cleanly; any in-flight exchange rendering skips prompt calls on the closed interface
