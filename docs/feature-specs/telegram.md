# Telegram Channel

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A grammY-based Telegram bot, shipped as the `telegram` extension, that receives text, media, and emoji-reaction updates from a single authorized chat, submits them to the conversation loop, and renders agent exchanges and background deliveries back as Telegram messages. It records the session that produced or received each message so that replying to a past message routes the new turn back to that conversation. It also registers agent-facing tools for sending files, reacting with emoji, pinning messages, and presenting inline-button prompts through the same bot instance.

The channel contract it implements (`start`/`respond`/`deliver`/`stop`) and the idle/immediate delivery gating are owned by the conversation loop — see [conversation-loop](conversation-loop.md). Background-originated text reaches `deliver()` through the notifications extension and other producers — see [notifications](notifications.md).

## User Stories

- As a user, I want to talk to Tachikoma from any Telegram client so that I am not tied to a terminal
- As a user, I want to send photos, voice messages, and documents so that the agent can work with my non-text content
- As a user, I want background notifications to arrive as a single push alert so that I am notified without being spammed per chunk
- As a user, I want to answer the agent's structured prompts by tapping a button so that yes/no and multiple-choice answers do not require typing
- As a user, I want to react to a message with an emoji and have the agent see it so that I can give quick feedback without typing
- As a user, I want to reply to one of the agent's earlier messages and have my new message picked up by that conversation so that I can resume an older topic without re-explaining it

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | The extension registers a `telegram` channel when `[extensions.telegram]` provides `botToken` and `chatId`; with either unset it logs that the channel is disabled and registers nothing |
| R1 | The bot token is validated (`bot.init()` / `getMe`) before long polling starts; after init the channel registers two bot commands in Telegram's command menu via `setMyCommands` — `/new` ("Start a new conversation, ignoring the current topic") and `/queue` ("Queue a message for the next turn instead of interrupting"), the channel-agnostic prefixes the coordinator parses; a `setMyCommands` failure is logged as a warning and does not block startup. Polling subscribes to `message`, `callback_query`, and `message_reaction` updates |
| R2 | Only updates from the configured `chatId` are processed; messages from other chats, callback taps from other user IDs, and reactions from other user IDs are dropped (taps are still acknowledged first) |
| R3 | Inbound text is trimmed and submitted to the runtime with the Telegram message ID in metadata; empty or whitespace-only messages are dropped |
| R4 | `respond()` consumes the exchange event stream through a progressive `StreamRenderer`: it sends a message early and edits it as text accumulates (throttled, no-op edits skipped), rendering only complete paragraphs (text up to the last `\n\n`) while streaming so token-level deltas never churn mid-word. `tool-start` shows a live italic line `_🔧 <friendly activity>_` (a wrench glyph plus an args-aware present-progressive label from `formatToolActivity` — e.g. "Reading <path>", "Running: <command>" — falling back to a humanized tool name) and is recorded; when text resumes or the exchange finalizes the recorded tools collapse into a persistent italic `_🔧 <summary>_` marker baked into the message (via `summarizeToolActivities`), blank-line separated from the surrounding text. `status` events render as a transient italic line only, replaced by the next text. A typing indicator refreshes throughout, and the message finalizes when the stream ends; `error` events are sent immediately as `⚠️ Error: …` notices — a recoverable error sends just `⚠️ Error: <message>`, while a non-recoverable one appends a second paragraph: `This needs your attention — the next message won't recover on its own.` |
| R5 | Outbound text is split at Telegram's 4096-character (UTF-16) limit, preferring paragraph boundaries, then line boundaries, then a hard split that never cuts a surrogate pair |
| R6 | Messages are sent with `parse_mode: "Markdown"`; on a Telegram entity-parse rejection the same text is resent as plain text; other send errors propagate |
| R7 | All channel sends (`respond()` and `deliver()`) are serialized through a FIFO mutex so two send sequences never interleave their API calls, and a rejected task does not stall the queue |
| R8 | Inbound media (photo, voice, audio, document, sticker, video, video note, animation) is resolved with per-kind metadata, size-checked against Telegram's 20 MB bot download limit, downloaded to the media directory under a unique filename, and submitted as a `MediaAttachment` with the caption as message text — gated by the `allowMedia` config flag |
| R9 | A bootstrap hook creates the media directory (`{dataDir}/media`) and prunes files older than 30 days |
| R10 | Media failures (file too large, download error) send an explanatory notice to the chat and leave the conversation usable |
| R11 | `deliver()` sends background text chunked; when `pushNotifications` is enabled, all chunks are sent silently and the last message is copy+deleted so exactly one push notification fires; copy failure preserves the original, delete failure is retried 3 times before accepting the duplicate |
| R12 | Each agent session registers five tools: `send_telegram_file`, `react_to_message`, `pin_message`, `unpin_message`, `send_message_with_buttons`; failures throw from `execute` |
| R13 | `send_telegram_file` resolves workspace-relative paths, requires an existing regular file under an allowed root (workspace, system temp dir, configured `extraFileRoots`), names the allowed roots on rejection, and auto-detects photo/audio/video/document from the extension |
| R14 | `pin_message` pins the channel's last outbound message audibly (the pin delivers the push notification); `react_to_message` defaults to the user's last inbound message; both fail when no target message exists |
| R15 | `send_message_with_buttons` validates the layout (≥1 row, ≥1 button per row, non-empty labels, non-empty values, values ≤58 UTF-8 bytes, ≤100 buttons) and sends an inline keyboard whose `callback_data` encodes the value and the single-use flag |
| R16 | Button taps are acknowledged immediately (before authorization), unpacked from the wire format, routed as a framed inbound turn carrying the tapped value; single-use taps remove the keyboard via a detached call whose failure never blocks routing |
| R17 | A `/stop` text message aborts the in-flight agent run instead of being submitted as a turn; it is acknowledged with `⏹ Stopped.` and the abort wires to the coordinator's exchange abort |
| R18 | An authorized emoji reaction is surfaced to the agent as an inbound turn that names the emoji(s) added and/or removed; a reaction update whose emoji set is unchanged is dropped |
| R19 | The channel records every message id it handles — inbound user messages and the bot's outbound replies — against the session that received or produced them in the `channel_messages` table, keyed by channel and message id (re-pointed on conflict) |
| R20 | When an inbound message (text, media, or reaction) targets a previously recorded message — via Telegram reply-to or the reacted-to message id — and that message maps to a known session, the turn carries `metadata.resumeSessionId` so the boundary force-routes it to the owning session; a target with no recorded session falls back to normal active-session/boundary routing |
| R21 | A reply to a message that carries text quotes a truncated form of that text as a `Replied to:` prefix on the turn so the agent sees what was replied to |
| R22 | `shutdownStatus(text)` renders shutdown-sequence progress on a single dedicated message: the first call sends it as an italic `_<text>_` message, subsequent calls edit that message in place; it is serialized through the send mutex and the final line is left visible in the chat. The coordinator calls it during teardown (see [conversation-loop](conversation-loop.md) R15/R17) because the streaming renderer that hosts normal `status()` lines no longer exists |

## Behaviors

### Channel Lifecycle and Authorization (R0, R1, R2)

The extension wires one `Bot` instance shared by the channel and the agent tools; misconfiguration disables the feature without failing startup.

**Acceptance Criteria**:
- Given `botToken` is `""` or `chatId` is `0`, when the extension sets up, then it logs that the channel is disabled and registers no channel, hook, or tools
- Given valid config, when `start()` runs, then `bot.init()` validates the token before polling begins, `setMyCommands` registers the `/new` and `/queue` menu commands (a failure is logged and ignored), and polling runs detached with `allowed_updates: ["message", "callback_query", "message_reaction"]`
- Given a message from a chat other than `chatId`, when received, then it is ignored without response
- Given polling or update handling throws, when the error surfaces, then it is logged via `bot.catch` / the polling catch handler and the process continues

### Inbound Text (R3, R17)

**Acceptance Criteria**:
- Given an authorized text message, when handled, then `mapTextMessage` produces an `InboundMessage` with trimmed text, `channel: "telegram"`, and `metadata.messageId`, submitted via `runtime.submit()`
- Given empty or whitespace-only text, when handled, then `mapTextMessage` returns `null` and nothing is submitted
- Given any inbound message (text or media), when handled, then the channel records its message ID as the last inbound message (target for `react_to_message`)
- Given a `/stop` message, when handled, then the in-flight run is aborted via the configured `stop` callback (it is never submitted as a turn), `⏹ Stopped.` is sent as acknowledgement, and an abort failure is logged without breaking the channel

### Inbound Reactions (R18, R2)

**Acceptance Criteria**:
- Given an authorized `message_reaction` update, when handled, then `mapReaction` diffs the new and old emoji sets and produces an `InboundMessage` naming what was added and/or removed (e.g. `The user reacted 👍 to a previous message.`)
- Given a reaction update from a user other than `chatId`, when handled, then it is dropped and nothing is submitted
- Given a reaction update whose added and removed emoji sets are both empty, when handled, then `mapReaction` returns `null` and nothing is submitted
- Given a reaction is surfaced, when submitted, then `metadata.reaction` is `true` and `metadata.replyToMessageId` is the reacted-to message id so reply routing can apply

### Message Recording and Reply Routing (R19, R20, R21)

The channel persists the message↔session mapping so a later reply or reaction can be force-routed to the conversation that owns the referenced message, bypassing topic classification.

**Acceptance Criteria**:
- Given an exchange finished rendering, when `respond()` finalizes, then the inbound message id (when present) and the finalized outbound message id are recorded against the current session as `incoming`/`outgoing`; with no active session, nothing is recorded
- Given a recording call throws, when handled, then the failure is logged at debug and the exchange proceeds
- Given an inbound message replies to (or a reaction targets) a message id that maps to a known session, when routed, then the turn carries `metadata.resumeSessionId` set to that session's id
- Given the reply-to or reacted-to message id is not recorded, when routed, then no `resumeSessionId` is attached and the turn follows normal routing
- Given a turn carries `metadata.resumeSessionId` for a session other than the active one, when the boundary middleware runs, then it resumes that session and skips topic classification entirely; a `resumeSessionId` equal to the active session, or for an unknown session, is a no-op that continues normally
- Given a reply targets a message that carries text, when mapped, then a truncated `Replied to:` quote of that text is prepended to the turn

### Response Rendering (R4, R5, R6, R7)

The channel renders each exchange progressively under the mutex: a `StreamRenderer` sends a message early and edits it live as whole paragraphs accumulate, baking a marker for each tool→text transition, then finalizes it when the stream ends.

**Acceptance Criteria**:
- Given an exchange starts, when events are being consumed, then a typing chat action is sent and refreshed every 5 seconds until the stream ends
- Given `text` events stream in, when the renderer flushes, then it renders only complete paragraphs (text up to the last `\n\n`, the trailing partial paragraph held back), sends or edits the streaming message at most once per ~1500 ms, and skips edits that would not change the rendered text
- Given a `tool-start` event arrives, when handled, then a live italic line `_🔧 <friendly activity>_` (a wrench glyph plus the args-aware `formatToolActivity` label, e.g. "Reading <path>") is shown below the settled text, and the tool is recorded for the segment summary
- Given text resumes after one or more `tool-start` events (or the exchange finalizes with tools pending), when handled, then the recorded tools collapse via `summarizeToolActivities` into a persistent italic marker `_🔧 <summary>_` baked into the buffer, blank-line separated from the surrounding text so consecutive text segments never run together
- Given a `status` event arrives, when handled, then a transient italic line showing the status text is shown below the streamed text and is dropped as soon as more text arrives
- Given the streamed text grows past the 4096-character edit limit, when the renderer flushes, then full chunks are finalized in place (paragraph > line > hard split, surrogate-safe) and only the tail keeps streaming
- Given the stream ends with non-blank text, when `finalize()` runs, then any pending tool summary is baked, the dangling trailing separator is trimmed, the streaming message is upgraded to its final chunked form bypassing the throttle, and the last sent message ID becomes the pin target; an exchange that produced no text deletes the streaming message
- Given a streaming send or edit fails, when the renderer marks itself broken, then it stops editing and `finalize()` deletes the partial message and resends the full text fresh via `sendChunked`
- Given a recoverable `error` event arrives mid-stream, when handled, then `⚠️ Error: <message>` is sent immediately and consumption continues
- Given a non-recoverable `error` event arrives mid-stream, when handled, then `⚠️ Error: <message>` plus the paragraph "This needs your attention — the next message won't recover on its own." is sent immediately and consumption continues
- Given Telegram rejects a send with a "can't parse entities" error, when the fallback runs, then the identical text is resent without `parse_mode` and the message is delivered unformatted
- Given a `respond()` and a `deliver()` overlap, when both run, then the mutex serializes them FIFO and their API calls never interleave
- Given the coordinator calls `shutdownStatus()` during teardown, when the first line arrives, then a dedicated italic message is sent; when subsequent lines arrive (e.g. `Post-processing: …`, `Done`), then that same message is edited in place rather than new messages being sent

### Inbound Media (R8, R9, R10)

**Acceptance Criteria**:
- Given a supported media message, when resolved, then the first match in priority order (animation, sticker, video note, photo, voice, video, audio, document) determines kind, label, extension, MIME type, and a human-readable summary; the largest photo size is used and video notes map onto the `video` kind
- Given a resolved file with `fileSize` over 20 MB, when downloading, then `MediaTooLargeError` aborts before any network call and its message is sent to the user
- Given a successful download, when submitted, then the `InboundMessage` carries the caption (or empty text) and one `MediaAttachment` with the saved path, MIME type, and description (e.g. `Photo (800 × 600, 117 KB)`)
- Given a stored file, when named, then a random hex prefix guarantees uniqueness while preserving the original filename or falling back to the resolved extension
- Given `allowMedia` is `false`, when media arrives, then it is ignored
- Given the bootstrap hook runs, when the media directory contains files older than 30 days, then they are deleted

### Background Deliveries and Push Notifications (R11)

**Acceptance Criteria**:
- Given `pushNotifications` is `true`, when `deliver()` runs, then all chunks are sent with `disable_notification: true`, the last message is copied (firing one push) and the original deleted
- Given the copy fails, when notifying, then no delete is attempted and the original silent message is preserved
- Given the delete fails, when retried, then up to 3 attempts run with 500 ms backoff and the duplicate is accepted after exhaustion
- Given `pushNotifications` is `false`, when `deliver()` runs, then chunks are sent with default notification behavior and no copy+delete occurs

### Agent Tools (R12, R13, R14, R15)

**Acceptance Criteria**:
- Given a `filePath` resolving outside every allowed root, missing, or not a regular file, when `send_telegram_file` runs, then it throws an error naming the allowed roots or the failure
- Given a valid file, when sent, then the extension selects `sendPhoto`/`sendAudio`/`sendVideo`/`sendDocument` (case-insensitive extension match, document fallback) with the optional caption
- Given no `messageId` argument, when `react_to_message` runs, then it targets the last inbound message and throws if none exists; the emoji string is passed through so the Telegram API rejects unsupported emoji
- Given a response was sent, when `pin_message` runs, then the last outbound message is pinned with `disable_notification: false` and its ID is returned; with no outbound message it throws
- Given an invalid button layout (empty rows, blank label, empty value, value over 58 bytes, over 100 buttons), when `send_message_with_buttons` validates, then it throws naming the offending row/field before any API call (an empty label throws "has an empty label"; an empty value throws "has an empty value")

### Button Taps (R16)

**Acceptance Criteria**:
- Given any callback query, when handled, then `answerCallbackQuery()` runs first so the tapper's spinner clears regardless of authorization or agent state
- Given a tap from a user other than `chatId`, when handled, then it is dropped after acknowledgement with no keyboard removal or submission
- Given an authorized tap with recognized callback data (`btn1:`/`btnN:` prefix), when routed, then the turn text is `` The user tapped the option `<value>` out of the options you displayed. `` with `metadata.buttonValue`
- Given a single-use tap (`btn1:`), when routed, then keyboard removal runs as a detached call and its failure is logged without blocking the turn
- Given unrecognized callback data, when handled, then it is logged and dropped
