# Telegram Channel

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A Telegram bot that receives text messages from a single authorized user, forwards them to the coordinator, and streams responses back as formatted Telegram messages with progressive editing. The production-facing communication channel for interacting with Tachikoma from any Telegram client.

## User Stories

- As a user, I want to interact with Tachikoma through Telegram so that I can send messages and receive responses from my phone or any Telegram client without needing a terminal
- As a user, I want to see what tools the agent is using while it works so that I understand what is happening during pauses
- As a user, I want messages I send during an active response to be processed so that I can provide follow-up input without waiting

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Telegram bot that receives user messages and sends coordinator responses back through Telegram |
| R1 | Bot initialization: connect to Telegram API, validate bot token at startup, handle unreachable API |
| R2 | Message receiving: accept incoming text messages and forward them to the coordinator; message buffering for mid-stream messages |
| R3 | Response rendering: stream agent response via progressive message edits with correct markdown formatting, splitting at paragraph boundaries before hitting the Telegram message size limit; respect Telegram API rate limits on edits; when push notifications are enabled, send all streaming messages silently and replace the final message via copy+delete to trigger exactly one push notification per response |
| R4 | Tool activity display: show tool activity as an inline status line within the current response message; each new tool replaces the previous tool line; when text resumes, each tool→text transition inserts a dynamic summary of the tools that ran (e.g., "🔧 Read 3 files and searched for 'config'") and text continues below it |
| R5 | User authorization: only process messages from the configured authorized user; silently ignore all others |
| R6 | Connection resilience: detect polling disconnects and reconnect automatically with backoff |
| R7 | Graceful shutdown: clean exit on SIGTERM/SIGINT or `q` keypress (when running in a TTY); in-flight responses are sent as-is (partial text delivered) before stopping |
| R8 | Telegram configuration: bot token and authorized chat ID stored in TOML config `[telegram]` section |
| R9 | CLI entry point with `--channel` flag to select between REPL (default) and Telegram; CLI flags override TOML config values at runtime |
| R10 | Message validation: silently ignore empty messages and non-text content (photos, stickers, voice, etc.) |
| R11 | Error display: surface coordinator errors (recoverable and non-recoverable) as messages in the Telegram chat |
| R12 | Event bus integration: subscribe to task events for proactive session task delivery and direct notification display |

## Behaviors

### Bot Initialization (R1)

The bot connects to the Telegram API at startup, validates the bot token, and begins polling for updates. If validation fails or the API is unreachable, startup aborts with a clear error.

**Acceptance Criteria**:
- Given a valid bot token in config, when the application starts with `--channel telegram`, then the bot connects to the Telegram API and begins polling for updates
- Given an invalid bot token, when the application starts with `--channel telegram`, then it exits with a clear error message before entering the main loop
- Given no `[telegram]` section in config, when the application starts with `--channel telegram`, then it prompts for token and chat ID if running interactively, or exits with a clear error if non-interactive
- Given a valid bot token but the Telegram API is unreachable at startup, when the connection fails, then the bot retries with backoff and exits with a clear error after exhausting retries

### Message Receiving (R2)

The bot accepts incoming text messages from the authorized user and forwards them to the coordinator. Messages arriving during an active response are buffered via `coordinator.enqueue()` and processed by the generator within the same session, or as new sessions after completion.

**Acceptance Criteria**:
- Given the bot is running, when an authorized user sends a text message, then the message text is forwarded to the coordinator via `send_message()`
- Given the bot is streaming a response to message A, when the user sends message B, then `enqueue()` is called to buffer message B; B is processed by the message source generator within the same session or as a new session after completion
- Given multiple messages arrive while a response is streaming, when each arrives, then each is enqueued and processed in order

### Response Rendering (R3)

The bot progressively edits a single Telegram message as text chunks arrive, throttled to respect API rate limits. If the response exceeds the message size limit, it splits at paragraph boundaries.

**Acceptance Criteria**:
- Given the coordinator is processing a message, when no text has arrived yet, then the bot sends a typing indicator to the user
- Given text chunks are streaming, when chunks arrive, then the bot progressively edits a single message to show the accumulating response, throttled to at most one edit every 2 seconds
- Given the accumulated formatted text approaches 3800 characters (safety margin), when the next chunk would exceed the limit, then the bot sends the current message (split at the last paragraph boundary) and starts a new message for remaining text
- Given a single paragraph exceeds 3800 characters, when splitting is needed, then the bot splits at the last newline, or hard-splits at the limit if no newline exists
- Given the full response is received, when the Result event arrives, then the final message is sent/edited with the complete formatted text
- Given the agent response contains markdown, when rendered in Telegram, then headings, bold, italic, code blocks, and links display correctly via entity-based formatting
- Given a network error during a message edit, when the edit fails, then the bot skips that edit and continues with the next chunk (no crash, no retry loop)
- Given a TelegramRetryAfter error on edit, when received, then the bot waits the specified duration before the next edit attempt
- Given the coordinator yields a Status event (e.g., "Thinking..."), when received before the response stream, then a transient italic status message is sent; this message is replaced when the first TextChunk or ToolActivity arrives

**Push notifications (when enabled)**:
- Given push notifications are enabled (default), when messages are created during streaming (initial, splits, status), then all are sent silently (no push notification for incomplete content)
- Given the agent streams a response, when the Result event arrives, then the last message is copied (triggering a push notification) and the original is deleted
- Given a response was split into multiple messages, when the Result event arrives, then only the last message is copy+deleted; earlier splits remain unchanged
- Given the copy succeeds but delete fails, then the user sees a duplicate message (acceptable degradation) and the failure is logged
- Given the copy fails, then delete is NOT called, the original is preserved, and the failure is logged
- Given an error is the only response (no text streamed), then the error message triggers a normal push notification; no copy+delete is performed
- Given the agent streams text followed by an error event, then the error message is sent silently because the copy+delete of the text message provides the push notification
- Given an unexpected exception occurs after text was partially streamed, then the last streamed message is copy+deleted (triggering push) and the exception error is sent silently
- Given an unexpected exception occurs before any text was streamed, then the exception error triggers a normal push notification
- Given a Result event arrives with no preceding text, tools, or errors (empty response), then no copy+delete is performed
- Given push notifications are disabled in config, when a response completes, then no copy+delete is performed and messages use default notification behavior

### Tool Activity Display (R4)

Tool activity appears as an inline status line within the current response message. Each new tool replaces the previous line. When text resumes after tools, a dynamic summary describing what tools did is inserted at each tool→text boundary.

**Acceptance Criteria**:
- Given the agent completes a tool while text is streaming, when the ToolActivity event arrives, then a tool status line (e.g., "_Reading src/main.py..._") is appended to the current response message via edit
- Given another tool completes, when the new ToolActivity event arrives, then the previous tool line in the message is replaced with the new tool's status line
- Given tools complete before any text has streamed, when the first ToolActivity arrives, then the response message is created with just the tool status line
- Given tool execution finishes and text streaming resumes, when the first TextChunk arrives after tools, then the tool line is replaced with a dynamic summary of the tools that ran (e.g., "_🔧 Read 3 files and searched for 'config'_") and new text continues below it in the same message
- Given a response contains multiple tool→text transitions (e.g., tools run, text streams, tools run again, text streams again), when each transition from ToolActivity to TextChunk occurs, then each transition independently generates its own tool activity summary
- Given tools run but no text follows (response ends with tools), when the response is finalized, then the tool activity summary is rendered in the final message
- Given tool activity occurs near the message size boundary, when there's insufficient room, then the current message is sent and the tool line starts the next message
- Given an unknown tool, when a ToolActivity event arrives, then the tool name is shown as a fallback in both the live status line and the summary

### User Authorization (R5)

Only messages from the configured authorized chat ID are processed. All others are silently ignored.

**Acceptance Criteria**:
- Given a message from the authorized chat ID, when received, then it is processed normally
- Given a message from any other chat ID, when received, then it is silently ignored (no response, no error)
- Given no authorized chat ID is configured, when the application starts with `--channel telegram`, then it prompts for the chat ID or exits with a clear error

### Connection Resilience (R6)

The bot handles polling disconnects and transient network errors gracefully.

**Acceptance Criteria**:
- Given the polling connection drops, when the bot detects the disconnect, then it retries with exponential backoff via aiogram's built-in BackoffConfig
- Given a transient network error during polling, when it occurs, then the bot logs the error and retries without crashing

### Graceful Shutdown (R7)

The bot exits cleanly on signals or `q` keypress, delivering any partial response before stopping. The `q` shortcut is available when running in a TTY; non-TTY environments (e.g., systemd) use signals only.

**Acceptance Criteria**:
- Given SIGTERM or SIGINT is received, when the bot is idle, then it stops polling and exits cleanly
- Given SIGTERM or SIGINT is received, when a response is in-flight, then the partial response accumulated so far is sent as a final message and the bot exits
- Given the bot is running in a TTY, when the user presses `q`, then it initiates the same shutdown sequence as SIGINT (stops polling, delivers any in-flight partial response, and exits cleanly)
- Given the bot is running without a TTY, when started, then no stdin reader is registered and shutdown is signal-only
- Given the bot is running in a TTY, when shutdown completes by any means, then terminal settings are restored to their original state

### Telegram Configuration (R8)

Bot token and authorized chat ID are stored in the TOML config file. The section is optional (None when not configured).

**Acceptance Criteria**:
- Given the config has a `[telegram]` section, when loaded, then `telegram.bot_token` and `telegram.authorized_chat_id` are available
- Given the auto-generated default config, when created, then the `[telegram]` section is included (commented out) with annotations
- Given the config has no `[telegram]` section, when loaded, then `settings.telegram` is None

### CLI Channel Selection (R9)

The CLI entry point supports channel selection via flag. CLI flags override TOML config values at runtime only (no file persistence).

**Acceptance Criteria**:
- Given no `--channel` flag, when the application starts, then the REPL channel starts (backward-compatible default)
- Given `--channel telegram`, when the application starts, then the Telegram channel starts
- Given `--channel repl`, when the application starts, then the REPL channel starts
- Given CLI flags that override TOML config values, when the application starts, then CLI flags take precedence for that session only (no file write)
- Given the CLI is invoked with `--help`, then available options and their descriptions are shown via cyclopts auto-generated help

### Message Validation (R10)

Non-text content and empty messages are silently ignored.

**Acceptance Criteria**:
- Given a non-text message (photo, sticker, voice, etc.) from the authorized user, when received, then it is silently ignored
- Given an empty or whitespace-only text message, when received, then it is silently ignored

### Error Display (R11)

Coordinator errors are surfaced as messages in the Telegram chat.

**Acceptance Criteria**:
- Given the coordinator yields a recoverable Error event, when received, then an error message is sent to the user in the chat and the conversation remains usable
- Given the coordinator yields a non-recoverable Error event, when received, then an error message is sent to the user and the bot logs the failure

### Event Bus Integration (R12)

The Telegram channel subscribes to task events via the event bus. Session tasks are delivered through the coordinator; notifications are sent directly as messages.

**Acceptance Criteria**:
- Given a `SessionTaskReady` event is received while idle, then the task prompt is sent through the coordinator via the shared `_process_through_coordinator()` method and the response is rendered normally
- Given a `SessionTaskReady` event is received while a response is active, then the task prompt is buffered via `coordinator.enqueue()` and processed after the current response completes
- Given a `TaskNotification` event is received, then the notification message is sent directly to the user in the Telegram chat with appropriate severity formatting (ℹ️ for info, ⚠️ for error)

## User Flow

### Breadboard: Telegram Message Flow

```
  User sends message
  ------------------
  - message in Telegram chat
            |
      +-----+-----+
      |           |
      v           v
  Authorized   Unauthorized
  ----------   ------------
  |            (silently drop)
  |
  +-----+-----+
  |           |
  v           v
  Text Msg   Non-text
  --------   --------
  |          (silently drop)
  v
  Processing
  ----------
  - typing indicator
  - tool line in response msg
            |
      +-----+-----+
      |           |
      v           v
  Streaming     Error
  ---------     -----
  - progressive   - error message
    message edits    in chat
  - inline tool   - (continue if
    status lines     recoverable)
  - split at msg
    size limit
      |
      v
  Response Complete
  -----------------
  - final message(s)
  - tool activity summary
    if tools were used
      |
      v
  Push Notification
  -----------------
  - copy last message
    (triggers push)
  - delete original
  - (skip if disabled,
    no message sent,
    or copy fails)
```

### Flow Description

**Entry point**: User sends a message to the Telegram bot from any Telegram client.

**Happy path**: The bot receives the message, confirms the sender is authorized, checks it's a non-empty text message, sends a typing indicator, and forwards the text to the coordinator. As the agent processes and responds, the bot progressively edits a single message showing the accumulating text (throttled for rate limits). Tool activity appears as an inline status line within the same message — appended below any text already streamed. Each new tool replaces the previous tool line. When text resumes, the tool line is replaced with a dynamic summary (e.g., "_🔧 Read 3 files and searched for 'config'_") and new text continues below it. If the response exceeds the message size limit, it splits at the last paragraph boundary and continues in a new message. The final response is delivered as one or more formatted messages. All messages during streaming are sent silently (no push notifications). After the response is finalized, the last message is replaced with a fresh copy to trigger a push notification. The copied message appears after any steering messages the user sent during processing, preserving correct chronological order. Push notifications are enabled by default (`push_notifications = true`).

**Buffering path**: If the user sends another message while a response is streaming, the channel calls `coordinator.enqueue()` to buffer the message. The message source generator feeds it into the same session, or remaining buffered messages are processed as new full-pipeline sessions after the current one completes. Each buffered message gets its own response message(s) in the chat.

**Decision points**: Authorization check (authorized → process, unauthorized → drop). Message type check (text → process, non-text → drop). Empty check (empty → drop). Message length check (under limit → continue editing, approaching limit → split at paragraph boundary). Error type (recoverable → show error, continue; non-recoverable → show error, log). Push notification check (enabled and message was sent → copy+delete, disabled or no message → no-op, copy fails → preserve original).

**Exit points**: Response complete (Result event received), recoverable error (error shown, conversation continues), non-recoverable error (error shown, failure logged), unauthorized (silently dropped), non-text or empty (silently dropped).

## Requires

Dependencies:
- None

Assumes existing:
- Coordinator `send_message()` async iterator API (core-architecture)
- Coordinator `enqueue()` method for message buffering (core-architecture)
- Configuration system with TOML loading and auto-generation (config-system)
- Domain event model: TextChunk, ToolActivity, Result, Error (core-architecture)
- Bootstrap hook system (config-system)
