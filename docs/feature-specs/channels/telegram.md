# Telegram Channel

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A Telegram bot that receives text messages and media messages from a single authorized user, forwards them to the coordinator, and streams responses back as formatted Telegram messages with progressive editing. The production-facing communication channel for interacting with Tachikoma from any Telegram client.

## User Stories

- As a user, I want to interact with Tachikoma through Telegram so that I can send messages and receive responses from my phone or any Telegram client without needing a terminal
- As a user, I want to see what tools the agent is using while it works so that I understand what is happening during pauses
- As a user, I want messages I send during an active response to be processed so that I can provide follow-up input without waiting
- As a user, I want to send images, voice messages, and other media through Telegram so that the agent can see and work with my non-text content
- As a user, I want the agent to pin important messages in our Telegram chat so that key responses (summaries, decisions, reference material) stay accessible at the top of the conversation without me having to manually pin them

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Telegram bot that receives user messages and sends coordinator responses back through Telegram |
| R1 | Bot initialization: connect to Telegram API, validate bot token at startup, handle unreachable API |
| R2 | Message receiving: accept incoming text messages and media messages (photos, voice, audio, documents, stickers, video, video notes, animations) and forward them to the coordinator; media messages are downloaded to a temp folder and described as text with metadata and file path; message buffering for mid-stream messages |
| R3 | Response rendering: stream agent response via progressive message edits with correct markdown formatting, splitting at paragraph boundaries before hitting the Telegram message size limit; respect Telegram API rate limits on edits; when push notifications are enabled, send all streaming messages silently and replace the final message via copy+delete to trigger exactly one push notification per response |
| R4 | Tool activity display: show tool activity as an inline status line within the current response message; each new tool replaces the previous tool line; when text resumes, each tool→text transition inserts a dynamic summary of the tools that ran (e.g., "🔧 Reading 3 files and searching for 'config'") and text continues below it |
| R5 | User authorization: only process messages from the configured authorized user; silently ignore all others |
| R6 | Connection resilience: detect polling disconnects and reconnect automatically with backoff |
| R7 | Graceful shutdown: clean exit on SIGTERM/SIGINT or `q` keypress (when running in a TTY); in-flight responses are sent as-is (partial text delivered) before stopping |
| R8 | Telegram configuration: bot token and authorized chat ID stored in TOML config `[telegram]` section |
| R9 | CLI entry point: `tachikoma run --channel` flag selects between REPL (default) and Telegram; bare `tachikoma` defaults to `run`; CLI flags override TOML config values at runtime |
| R10 | Message validation: silently ignore empty messages and unsupported message types (contacts, locations, polls, venues, dice) |
| R11 | Error display: surface coordinator errors (recoverable and non-recoverable) as messages in the Telegram chat |
| R12 | Event bus integration: subscribe to `BufferedDelivery` events and route prompts through the coordinator as new message turns; flush pending buffer items on graceful shutdown (see [delivery/priority-buffer](../delivery/priority-buffer.md)) |
| R13 | File delivery via `send_file` tool: the agent can send a file to the user's Telegram chat. Accepted `file_path` forms are workspace-relative, absolute paths inside the workspace, absolute paths under the system temporary directory, and absolute paths under operator-configured extra roots. Paths outside all allowed roots are rejected with an error that names the allowed roots. Only existing regular files can be sent. |
| R14 | Message pinning: the agent can pin and unpin messages in the active Telegram chat. Pins are silent (no notification). Both operations are idempotent. Permission failures and API errors return clear error responses to the agent |

## Behaviors

### Bot Initialization (R1)

The bot connects to the Telegram API at startup, validates the bot token, and begins polling for updates. If validation fails or the API is unreachable, startup aborts with a clear error.

**Acceptance Criteria**:
- Given a valid bot token in config, when the application starts with `tachikoma run --channel telegram`, then the bot connects to the Telegram API and begins polling for updates
- Given an invalid bot token, when the application starts with `tachikoma run --channel telegram`, then it exits with a clear error message before entering the main loop
- Given no `[telegram]` section in config, when the application starts with `tachikoma run --channel telegram`, then it prompts for token and chat ID if running interactively, or exits with a clear error if non-interactive
- Given a valid bot token but the Telegram API is unreachable at startup, when the connection fails, then the bot retries with backoff and exits with a clear error after exhausting retries

### Message Receiving (R2)

The bot accepts incoming text messages from the authorized user and forwards them to the coordinator. Messages arriving during an active response are buffered via `coordinator.enqueue()` and processed by the coordinator's re-queue loop within the same session, or as new sessions after the full stream completes.

**Acceptance Criteria**:
- Given the bot is running, when an authorized user sends a text message, then the message text is forwarded to the coordinator via `send_message()`
- Given the bot is streaming a response to message A, when the user sends message B, then `enqueue()` is called to buffer message B; B is processed by the coordinator's re-queue loop within the same session or as a new session after the full stream completes
- Given multiple messages arrive while a response is streaming, when each arrives, then each is enqueued and processed in order

Media messages from the authorized user are downloaded to a dedicated temp folder and forwarded to the coordinator as natural-language text descriptions. The agent receives a text description containing metadata and the saved file path, and can use its existing tools to process the file. Media messages follow the same buffering behavior as text messages.

**Media Acceptance Criteria**:
- Given the bot is running, when an authorized user sends a supported media message (photo, voice, audio, document, sticker, video, video note, or animation), then the file is downloaded to the temp folder and a text description including metadata and file path is enqueued to the coordinator
- Given any media type, when the description is constructed, then it follows a consistent structure: media type label, type-specific metadata, the saved file path, and optional caption
- Given a saved file, when named, then it uses a UUID-based unique name with an appropriate file extension matching its content type
- Given a media file that exceeds 20 MB (Telegram bot download limit), when the file size is known, then an error message is sent to the user in the chat and no file is saved
- Given a download fails due to a network or API error, then an error message is sent to the user and the conversation remains usable
- Given any media message includes a caption, when the description is constructed, then the caption text is included as the user's accompanying message

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
- Given multiple consecutive Status events are yielded (e.g., during cold start resumption), when the second Status arrives while the first is still showing, then the existing message is edited in place instead of creating a new one
- Given two consecutive Status events carry identical text, when the edit is attempted and Telegram rejects it with `"message is not modified"`, then the bot silently ignores the rejection (debug log only — no stack trace or error visible to the user)
- Given a response is split into multiple messages, when `_send_chunks()` is called again (during streaming or finalize), then existing split messages are edited in-place with updated content (no stale duplicates left behind)
- Given a re-split produces fewer chunks than the previous split, when excess messages exist, then they are deleted
- Given a re-split produces more chunks than the previous split, when additional chunks are needed, then new messages are sent for the additional chunks
- Given content was previously split but now fits in a single message, when the next edit cycle runs, then the first split message is edited with full content and excess messages are deleted (shrink-to-unsplit)
- Given `_send_chunks()` fails to edit an existing tracked split message, when the Telegram API error occurs, then the system continues without crash and the failure is logged

**Push notifications (when enabled)**:
- Given push notifications are enabled (default), when messages are created during streaming (initial, splits, status), then all are sent silently (no push notification for incomplete content)
- Given the agent streams a response, when the Result event arrives, then the last message is copied (triggering a push notification) and the original is deleted
- Given a response was split into multiple messages, when the Result event arrives, then only the last message is copy+deleted; earlier splits remain unchanged
- Given the copy succeeds but delete fails with a transient error, when retrying, then delete is re-attempted up to 3 times with 0.5s backoff; if all retries fail, the duplicate is accepted and the failure is logged
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
- Given the agent completes a tool while text is streaming, when the ToolActivity event arrives, then an italicized tool status line (e.g., "*🔧 Reading \`src/main.py\`*") is appended to the current response message via edit, separated from preceding text by a blank line
- Given another tool completes, when the new ToolActivity event arrives, then the previous tool line in the message is replaced with the new tool's status line
- Given tools complete before any text has streamed, when the first ToolActivity arrives, then the response message is created with just the tool status line
- Given tool execution finishes and text streaming resumes, when the first TextChunk arrives after tools, then the tool line is replaced with an italicized dynamic summary of the tools that ran (e.g., "*🔧 Reading 3 files and searching for \`config\`*"), with tool activity listed in chronological order (oldest first), separated from surrounding text by blank lines, and new text continues below it in the same message
- Given a response contains multiple tool→text transitions (e.g., tools run, text streams, tools run again, text streams again), when each transition from ToolActivity to TextChunk occurs, then each transition independently generates its own tool activity summary
- Given tools run but no text follows (response ends with tools), when the response is finalized, then the tool activity summary is rendered in the final message
- Given tool activity occurs near the message size boundary, when there's insufficient room, then the current message is sent and the tool line starts the next message
- Given an unknown tool, when a ToolActivity event arrives, then the tool name is shown as a fallback in both the live status line and the summary; MCP tool names (starting with `mcp__`) are formatted into human-readable labels (e.g., `mcp__projects__list_projects` shows as "List Projects")

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

The CLI entry point supports channel selection via `tachikoma run --channel`. Bare `tachikoma` invocation defaults to `tachikoma run` with default settings. CLI flags override TOML config values at runtime only (no file persistence).

**Acceptance Criteria**:
- Given `tachikoma` with no subcommand, when the application starts, then it defaults to `tachikoma run` and the REPL channel starts
- Given `tachikoma run --channel telegram`, when the application starts, then the Telegram channel starts
- Given `tachikoma run --channel repl`, when the application starts, then the REPL channel starts
- Given CLI flags that override TOML config values, when the application starts, then CLI flags take precedence for that session only (no file write)
- Given the CLI is invoked with `tachikoma --help`, then available subcommands are listed (including `run`); `tachikoma run --help` shows run-specific flags including `--channel`

### Message Validation (R10)

Unsupported content types and empty messages are silently ignored.

**Acceptance Criteria**:
- Given an unsupported message type (contact, location, poll, venue, dice) from the authorized user, when received, then it is silently ignored
- Given an empty or whitespace-only text message, when received, then it is silently ignored

### Error Display (R11)

Coordinator errors are surfaced as messages in the Telegram chat.

**Acceptance Criteria**:
- Given the coordinator yields a recoverable Error event, when received, then an error message is sent to the user in the chat and the conversation remains usable
- Given the coordinator yields a non-recoverable Error event, when received, then an error message is sent to the user and the bot logs the failure

### Event Bus Integration (R12)

The Telegram channel subscribes to `BufferedDelivery` events dispatched by the priority buffer. Each delivery carries a ready-to-send prompt (single item or shutdown digest) which Telegram routes through the coordinator as a new message turn. The channel does not subscribe to `Notification` or session-task events directly — the priority buffer handles enqueueing, ordering, and idle gating (see [delivery/priority-buffer](../delivery/priority-buffer.md)).

**Acceptance Criteria**:
- Given a `BufferedDelivery` event is received while the channel is idle, then the prompt is routed through the coordinator via the shared `_process_through_coordinator()` method and the response is rendered normally; per-item `on_delivered` callbacks fire on completion
- Given a `BufferedDelivery` event is received while a response is active, then delivery is deferred to the next idle moment (the buffer waits for the coordinator busy→idle transition before re-emitting)
- Given a shutdown digest `BufferedDelivery` event arrives, then the combined prompt is routed through the coordinator as a final exchange before the bot stops polling

### File Delivery via send_file (R13)

The agent delivers a file to the user via the `send_file` tool. The tool validates `file_path` against a set of allowed roots: the workspace, the system temporary directory, and any roots declared in `[telegram.send_file] extra_roots`. Paths inside any allowed root are accepted; paths outside all of them are rejected. Symlinks are resolved before the membership check, so the canonical target governs the decision.

**Acceptance Criteria**:
- Given a workspace-relative `file_path` (e.g. `exports/report.pdf`), when the tool is called, then it resolves against the workspace root and the file is sent if it exists there
- Given an absolute `file_path` under the workspace, the system temporary directory, or a configured extra root, when the file exists and is a regular file, then it is sent
- Given a `file_path` whose resolved target is outside all allowed roots, when the tool is called, then it returns `is_error: True` with an error message that names every allowed root so the agent can self-correct
- Given a `file_path` that does not exist or is not a regular file (e.g. a directory), when the tool is called, then it returns `is_error: True`
- Given a symlink that crosses an allowed-root boundary, when the tool resolves it, then the resolved target governs the membership check (symlinks inside pointing outside are rejected; symlinks outside pointing inside are accepted)
- Given the operator declares extra roots under `[telegram.send_file] extra_roots`, when the config is loaded, then `~` is expanded and each entry must be absolute; entries need not exist at load time

### Message Pinning (R14)

The agent can pin and unpin messages in the active Telegram chat via two MCP tools. `pin_message` pins the last response message silently (no Telegram notification) and returns its Telegram message ID. `unpin_message` accepts a message ID and unpins that specific message. Both tools are idempotent.

**Acceptance Criteria**:
- Given the agent has sent a response in the Telegram chat, when `pin_message` is called, then the last response message is pinned silently and the tool returns `{"content": [{"type": "text", "text": "Message pinned (ID: 123)"}]}`
- Given the agent has sent a response that was split into multiple messages, when `pin_message` is called, then the final message in the split sequence is pinned and its message ID is returned
- Given `pin_message` is called with no active response (no tracked message ID, e.g. from a background task with no active renderer), when the tool executes, then it returns `{"is_error": true, "content": [{"type": "text", "text": "No message available to pin"}]}`
- Given a message is already pinned, when `pin_message` is called on it again, then the operation succeeds without error (idempotent)
- Given a pinned message with a known message ID, when `unpin_message` is called with that ID, then the message is unpinned and the tool returns `{"content": [{"type": "text", "text": "Message unpinned (ID: 123)"}]}`
- Given a message ID that is not currently pinned, when `unpin_message` is called, then the operation succeeds without error (idempotent)
- Given the bot lacks pin permissions in a group/channel, when `pin_message` or `unpin_message` is called, then the tool returns `{"is_error": true, "content": [{"type": "text", "text": "Failed to pin/unpin message: <API error details>"}]}`
- Given the Telegram channel is active, when the channel's `get_mcp_servers()` is called, then a "telegram-pinning" server is included with both tools

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
  +-----+-----+-----+
  |           |     |
  v           v     v
  Text Msg   Media  Unsupported
  --------   -----  -----------
  |          |      (silently drop)
  |          v
  |       Download & Describe
  |       --------------------
  |       - download to temp folder
  |       - build text description
  |         with metadata + path
  |       - include caption if any
  |           |
  |     +-----+-----+
  |     |           |
  |     v           v
  |   Success     Error
  |   -------     -----
  |   |           - size limit or
  |   |             download failure
  |   |           - error message
  |   |             in chat
  |   v
  |   Enqueue Description
  |   -------------------
  |   - (same as text
  |      from here)
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
      |
      v
  Pin Message
  -----------
  - agent calls pin_message
  - pins last response
    silently
  - returns message ID
        |
  +-----+-----+
  |           |
  v           v
  Success     Error
  -------     -----
  - pinned    - no active message
  - return ID - permission denied
              - API failure
```

### Flow Description

**Entry point**: User sends a message (text or media) to the Telegram bot from any Telegram client.

**Happy path (text)**: The bot receives the message, confirms the sender is authorized, checks it's a non-empty text message, sends a typing indicator, and forwards the text to the coordinator. As the agent processes and responds, the bot progressively edits a single message showing the accumulating text (throttled for rate limits). Tool activity appears as an italicized inline status line within the same message — appended below any text already streamed, separated by a blank line. Each new tool replaces the previous tool line. When text resumes, the tool line is replaced with an italicized dynamic summary (e.g., "*🔧 Reading 3 files and searching for \`config\`*") with blank lines before and after, and new text continues below it. If the response exceeds the message size limit, it splits at the last paragraph boundary and continues in a new message. The final response is delivered as one or more formatted messages. All messages during streaming are sent silently (no push notifications). After the response is finalized, the last message is replaced with a fresh copy to trigger a push notification. The copied message appears after any steering messages the user sent during processing, preserving correct chronological order. Push notifications are enabled by default (`push_notifications = true`).

**Happy path (media)**: The bot receives a supported media message (photo, voice, audio, document, sticker, video, video note, or animation), confirms the sender is authorized, checks the file size is within the 20 MB Telegram bot download limit, downloads the file to the dedicated temp folder with a unique name and appropriate extension, constructs a natural-language text description with relevant metadata and the saved file path (plus caption if present), and enqueues it to the coordinator. From there, processing follows the same path as text messages — the agent receives the description and can use its tools to interact with the file. Multiple media messages arriving during an active response are buffered and processed in order, the same as text messages.

**Steering path (mid-exchange)**: If the user sends another message (text or media) while an exchange is in flight — at any phase: boundary detection, pre-processing, SDK streaming, or teardown — the channel checks `self._delivery_lock.locked()`. Because the lock is held by the in-flight exchange, it calls `coordinator.enqueue()` directly — bypassing the lock — and returns. The message lands in `_message_buffer`; once the coordinator's forwarder is alive (created right after pre-processing completes), it moves the message onto the per-turn `sdk_inbox` and the message source yields it to the SDK as a steering message that influences the in-flight response. If the message arrives after the SDK exchange has already torn down but before the lock is released, the coordinator's internal re-queue loop automatically processes it as a follow-up exchange within the same `send_message()` generator call, yielding its events as a continuation of the same stream. The channel sees one continuous event stream and renders all responses in order. (Buffered-delivery events from the priority buffer always start new exchanges via the lock and are never delivered as steering messages.)

**Decision points**: Authorization check (authorized → process, unauthorized → drop). Message type check (text → process text, supported media → download/describe/process, unsupported → drop). Empty check (empty text → drop). File size check (within 20 MB → download, too large → error message). Download result (success → describe and enqueue, failure → error message). Message length check (under limit → continue editing, approaching limit → split at paragraph boundary). Error type (recoverable → show error, continue; non-recoverable → show error, log). Push notification check (enabled and message was sent → copy+delete, disabled or no message → no-op, copy fails → preserve original). Pin decision (agent calls pin_message after response → pinned message stays at top of chat).

**Exit points**: Response complete (Result event received), recoverable error (error shown, conversation continues), non-recoverable error (error shown, failure logged), media file too large (error message sent, conversation continues), media download failed (error message sent, conversation continues), unauthorized (silently dropped), unsupported message type or empty text (silently dropped), pin success (message pinned, ID returned), pin error (no message, permission denied, or API failure).

## Requires

Dependencies:
- None

Assumes existing:
- Coordinator `send_message()` async iterator API (core-architecture)
- Coordinator `enqueue()` method for message buffering (core-architecture)
- Configuration system with TOML loading and auto-generation (config-system)
- Domain event model: TextChunk, ToolActivity, Result, Error (core-architecture)
- Bootstrap hook system (config-system)
