# Terminal REPL

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

An interactive terminal REPL that reads user input, sends it through the coordinator, and streams the agent's response to the terminal. Primarily a developer tool for interacting with the agent locally.

## User Stories

- As a developer, I want to type messages in a terminal and see the agent's streamed response so that I can interact with the agent during development
- As a developer, I want to see what tools the agent is using while it works so that I understand what is happening during pauses
- As a developer, I want my input history preserved across sessions so that I can recall previous messages

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Terminal REPL that reads input, sends through coordinator, and streams the response |
| R1 | Streaming display: text inline, tool activity as status lines |
| R2 | Input management: persistent history across sessions, empty input prevention |
| R3 | Clean exit handling: Ctrl+C, Ctrl+D, exit/quit commands |

## Behaviors

### Streaming Display (R1)

The REPL renders domain events to the terminal as they arrive: text chunks appear inline, tool activity shows as gray italic status lines with tool-specific details, errors print to stderr.

**Acceptance Criteria**:
- Given the agent responds with text, when text events arrive, then text is printed inline (no trailing newline until complete)
- Given the agent uses a tool, when a tool activity event arrives, then a gray italic status line shows tool name and key parameters (e.g., "Reading src/main.py...")
- Given the agent uses an unknown tool, when a tool activity event arrives, then the tool name is shown as a fallback
- Given the agent completes its response, when a result event arrives, then a newline is printed and the REPL shows a new prompt
- Given an error occurs, when an error event arrives, then the error message prints to stderr

### Input Management (R2)

The REPL provides async input with persistent file history and input validation.

**Acceptance Criteria**:
- Given the REPL is running, when the user sends multiple messages, then conversation context is preserved between them
- Given the user presses enter without typing (or whitespace only), then input is rejected and the prompt stays
- Given a previous session's history exists, when the REPL starts, then prior inputs are available via history navigation

### Exit Handling (R3)

The REPL exits cleanly on standard terminal signals without stack traces.

**Acceptance Criteria**:
- Given the REPL is waiting for input, when Ctrl+C is pressed, then the REPL exits cleanly
- Given the REPL is waiting for input on an empty prompt, when Ctrl+D is pressed, then the REPL exits cleanly
- Given the user types "exit" or "quit", then the REPL exits cleanly
- Given the agent is streaming, when Ctrl+C is pressed, then the stream is interrupted and the REPL exits
- Given a non-recoverable error event, then the REPL exits with the error message
- Given a recoverable error event, then the error is shown and the REPL continues
