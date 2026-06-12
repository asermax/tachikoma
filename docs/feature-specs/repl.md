# REPL Channel

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A terminal channel for developing against the agent locally: a readline prompt reads lines, submits them through the coordinator, and renders the streamed agent events inline. It is the default channel (`channels.default = "repl"`) and deliberately minimal — plain stdout writes with ANSI dim/red styling, no markdown rendering.

## User Stories

- As a developer, I want to type messages in a terminal and see the agent's streamed response so that I can exercise the full message loop locally
- As a developer, I want tool activity and status shown inline so that I can see what the agent is doing during pauses

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | The extension registers a `ReplChannel` (name `repl`) via `app.channels.register`; the core shell starts whichever channel is selected (see [core-shell](core-shell.md)) |
| R1 | Non-empty input lines are trimmed and submitted as inbound text messages; empty lines just re-prompt |
| R2 | Agent events stream to stdout as they arrive: `text` written raw; `tool-start` as a dim line with a gear marker and the tool name; `status` dim; `error` in red with an `error:` prefix; `result` prints a newline and re-prompts |
| R3 | `thinking` and `tool-end` events are not rendered |
| R4 | Non-text events that interrupt streaming text are preceded by a newline so status lines never splice into a sentence |
| R5 | Background deliveries (`deliver`) print the delivery text on its own line with an inbox marker, then re-prompt |
| R6 | Closing stdin (Ctrl+D) detaches the readline interface and signals SIGINT so shutdown flows through the app's regular signal path; an exchange still rendering must not touch the closed interface |

## Behaviors

### Input Loop (R0, R1)

**Acceptance Criteria**:
- Given the channel starts, then a `you> ` prompt appears and each non-empty line is submitted via `runtime.submit` as a text message from channel `repl`
- Given the user presses Enter on an empty line, then nothing is submitted and the prompt is shown again

### Event Rendering (R2, R3, R4)

**Acceptance Criteria**:
- Given the agent streams text, when chunks arrive, then they are written to stdout verbatim as they arrive
- Given a tool starts mid-stream, when the event arrives, then a newline terminates the in-progress text before the dim tool line prints
- Given the exchange ends, when the `result` event arrives, then a blank line is printed and the prompt returns
- Given an error event, then `error: <message>` prints in red and the loop continues

### Background Delivery and Shutdown (R5, R6)

**Acceptance Criteria**:
- Given a background-originated delivery reaches the channel, then its text prints on a fresh line and the prompt returns
- Given the user presses Ctrl+D, then the process receives SIGINT and the app shuts down cleanly; any in-flight exchange rendering skips prompt calls on the closed interface
