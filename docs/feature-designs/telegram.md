# Design: Telegram Channel

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/telegram.md](../feature-specs/telegram.md)
**Status**: Current

## Purpose

Explain how the Telegram extension is assembled: one shared grammY `Bot` feeding a `Channel` implementation and a set of agent tools, with rendering split into small pure modules (chunking, sending, media, buttons) that take narrow fakeable dependencies. It also covers how emoji reactions enter the loop and how a message↔session mapping (the `channel_messages` table) lets a reply force-route to the conversation that owns the replied-to message.

## Problem Context

Tachikoma's production interface is a Telegram bot for a single user. The channel must satisfy the conversation loop's `Channel` contract (`src/channels/types.ts`): consume an exchange's `AgentEvent` stream in `respond()` and render background-originated `Delivery` items in `deliver()` — see [conversation-loop](conversation-loop.md) for the contract and idle/immediate gating, and [notifications](notifications.md) for the main `deliver()` producer.

**Constraints:**
- Telegram hard limits: 4096 UTF-16 characters per message, 20 MB bot file downloads, 64 bytes of `callback_data` per button, ~50 MB uploads
- Telegram's Markdown parsing rejects whole messages on malformed entities — a send path that can never lose text is required
- `respond()` and `deliver()` can be invoked concurrently (immediate-gated deliveries arrive mid-exchange), but Telegram message sequences must not interleave
- Everything ships as a `defineExtension` module per DES-001/DES-002; tools register through pi's `registerTool` inside `app.agent.use` factories

**Interactions:**
- `app.channels.register()` / `Channel` contract — conversation loop drives `respond()`/`deliver()`/`stop()`
- `runtime.submit()` — inbound messages enter the coordinator inbox as `InboundMessage` (`src/domain/message.ts`); media attachments are rendered into the prompt by the coordinator
- `app.sessions.current()` / `recordChannelMessage()` / `findSessionByMessageId()` — the channel reads the receiving session id for recording and resolves a reply-to target back to its owning session; the boundary middleware honors `metadata.resumeSessionId` as an explicit resume target (see [conversation-loop](conversation-loop.md))
- `app.bootstrap("media-dir", …)` — media directory creation and retention pruning at startup
- `app.agent.use()` — per-session registration of the five Telegram tools, closing over the bot API and the channel's last-message-ID getters

## Design Overview

`index.ts` is pure wiring: validate config, construct one `Bot`, instantiate `TelegramChannel`, register the bootstrap hook and the tool factory. The channel (`channel.ts`) owns the grammY lifecycle — update handlers, `bot.init()` token validation, detached long polling — and delegates every behavior to a sibling module: inbound mapping (`inbound.ts`), length-aware splitting (`chunking.ts`), the send/notify primitives (`sending.ts`), media resolution and download (`media.ts`), button wire format (`buttons.ts`), and FIFO serialization (`mutex.ts`). Tools (`tools.ts`) reuse the same bot API through a narrow `ToolApi` interface and read the channel's last inbound/outbound message IDs through getter closures, so tools and channel stay decoupled but consistent.

Rendering is progressive: `respond()` drives a per-exchange `StreamRenderer` (`streaming.ts`) that sends a message early and edits it live, throttled to ~1500 ms and skipping no-op edits, with a typing indicator refreshing throughout. Because pi streams token-level `text` deltas, the renderer gates on paragraph boundaries — only complete paragraphs (text up to the last `\n\n`) render while text streams, so the user never sees mid-word churn; the trailing in-progress paragraph stays buffered until it closes or `finalize()` flushes it. `tool-start` shows the running tool as a live italic line below the text and is *also* tracked: when text resumes, the tools that ran fold into a persistent `_🔧 <summary>_` marker baked into the buffer, which both records the activity (legacy parity) and supplies the blank line separating one text segment from the next. `status` events render as a transient italic line only. On stream end `finalize()` bakes any still-pending tool summary, trims the dangling separator, and upgrades the message to its final chunked form. Any streaming send/edit failure trips the renderer into a broken state and `finalize()` falls back to a plain `sendChunked` of the full text, so text is never lost.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/telegram/index.ts` | Extension entry: config schema (`botToken`, `chatId`, `allowMedia`, `pushNotifications`, `extraFileRoots`), disable-on-missing-config, bot/channel construction, allowed-roots computation, tool factory registration | Silent disable (info log) instead of startup failure; allowed roots deduplicated from workspace + `tmpdir()` + expanded extras |
| `src/extensions/telegram/channel.ts` | `TelegramChannel implements Channel`: grammY handlers (message, `callback_query:data`, `message_reaction`), token validation, detached polling, `respond()` driving a per-exchange `StreamRenderer` under the mutex, `/stop` abort handling, `deliver()`/`status()`/`shutdownStatus()`, media handler with user-facing failure notices, last inbound/outbound ID tracking, reply-to routing (`routeReply`), and message recording in `respond()` | `shutdownStatus()` renders teardown progress on one dedicated italic message edited in place under the mutex (legacy parity) — used because the streaming renderer that hosts normal `status()` lines is gone by shutdown; the final "Done" line is left in the chat; `answerCallbackQuery()` before the auth check so unauthorized tappers' spinners still clear; polling runs detached because grammY's `start()` only resolves when polling stops; `/stop` aborts the live run via the injected `stop` callback rather than reaching the agent; recording happens in `respond()` after routing settles so the inbound and outbound ids attach to the *receiving* session; `routeReply` stamps `metadata.resumeSessionId` only when the target maps to a known session |
| `src/extensions/telegram/streaming.ts` | `StreamRenderer`: progressive per-exchange renderer — `appendText`/`appendTool`/`showTransient`/`finalize` over one or more Telegram messages, paragraph-gated streaming (renders to the last `\n\n`), ~1500 ms `EDIT_THROTTLE_MS`, no-op-edit skipping, overflow finalization at the 4096 limit, broken-state fallback to a fresh `sendChunked` | One renderer per `respond()`; streamed text only renders complete paragraphs (the trailing partial paragraph waits for `finalize()`), so token-level deltas don't churn mid-word; `appendTool` shows a live tool line *and* bakes a `_🔧 <summary>_` marker into the buffer at the next text/finalize so tool activity persists with blank-line separation; `showTransient` is status-only and replaced by the next text; a single send/edit failure stops live editing and defers the whole text to `finalize()` |
| `src/extensions/telegram/inbound.ts` | Pure mappers from grammY `Message`/tap/reaction values to domain `InboundMessage`: `mapTextMessage`, `mapMediaMessage`, `mapButtonTap`, `mapReaction`, plus `replyQuote`/`replyTargetId` helpers | Button taps and reactions framed as explicit prose; replies carry `metadata.replyToMessageId` (string) and a truncated `Replied to:` quote; `mapReaction` diffs old/new emoji sets and returns `null` on no change |
| `src/extensions/telegram/schema.ts` | `channel_messages` drizzle table (channel, message id, session id, direction, timestamp) plus the `ChannelMessageDirection` const map; re-exported from `src/db/schema.ts` | Unique index on `(channel, message_id)` so re-recording the same id re-points it via `onConflictDoUpdate`; index on `session_id`; message ids stored as text since they are opaque keys |
| `src/extensions/telegram/chunking.ts` | `splitMessage`: paragraph > line > hard split within 4096 UTF-16 units | JS `string.length` is already UTF-16 code units — Telegram's actual constraint, no conversion layer needed; hard split backs off one unit at a low surrogate |
| `src/extensions/telegram/sending.ts` | `sendWithMarkdownFallback`, `sendChunked`, `notifyViaCopyDelete`, `deliverText`, `startTyping`; narrow `SendApi` interface | Copy-first ordering keeps text safe (copy fails → original preserved; delete fails → duplicate accepted after 3 retries); typing refreshed every 5 s (Telegram expires chat actions at ~5 s) |
| `src/extensions/telegram/tool-labels.ts` | `formatToolActivity` (present-progressive live line), `formatToolName` (underscore-split + title-case, MCP server prefix stripped), and `summarizeToolActivities` (collapses a tool→text segment into one capitalized phrase) | Live label and baked summary use separate maps, keyed by pi's tool names (`read`, `grep`, `bash`, … and `delegate_to_agent`) with pi's arg shape (`path`/`pattern`/`command`) — not the legacy Claude-Code names; they differ in full path vs basename and full quotes vs inline-code; same-typed tools aggregate past 2 uses; >5 phrases fold into a trailing "more"; unmapped tools (including snake_case tachikoma tools) fall back to the humanized name |
| `src/extensions/telegram/mutex.ts` | Promise-chain FIFO `Mutex` serializing all channel sends | Tail swallows rejections so a failed delivery never wedges the queue |
| `src/extensions/telegram/media.ts` | `resolveMedia` priority table, `downloadMedia` with 20 MB pre-check, `generateMediaFilename`, `buildAttachment`, `ensureMediaDir` (create + 30-day prune) | Animation checked before document (Telegram sets both fields); video notes map onto the `video` domain kind; size check happens before any network call |
| `src/extensions/telegram/buttons.ts` | `callback_data` wire format (`btn1:`/`btnN:` + value), layout validation, `InlineKeyboardMarkup` construction | Single-use bit lives in the callback data itself so semantics survive process restarts with no per-message state |
| `src/extensions/telegram/tools.ts` | Five `pi.registerTool` registrations plus extracted handlers (`handleSendFile`, `handleReactToMessage`, `handlePinMessage`, `handleUnpinMessage`, `handleSendMessageWithButtons`), path validation, extension-based media-type detection | Handlers take `Pick<ToolDeps, …>` so tests fake only what each needs; errors signal by throwing per DES-002 |
| `tests/telegram/` | Vitest suites for chunking, sending, mutex serialization, inbound/media/reaction mapping, reply routing, message recording, tools, buttons | Fake `SendApi`/`ToolApi`/`ChannelMessageStore` objects; no live network. Registry mapping + migration application covered in `tests/db.test.ts` |

## Key Decisions

### Progressive paragraph-gated rendering with baked tool markers and a broken-state fallback

**Choice**: `respond()` drives a `StreamRenderer` that sends a message early and edits it as text accumulates, throttled to `EDIT_THROTTLE_MS` (~1500 ms) with no-op edits skipped. Streaming renders whole paragraphs only — text up to the last `\n\n` — leaving the in-progress trailing paragraph buffered until it closes or `finalize()` flushes it. `tool-start` shows a live italic tool line *and* is recorded; at the next text (or at `finalize()`) the recorded tools collapse into a persistent `_🔧 <summary>_` marker baked into the buffer, separated from surrounding text by blank lines. `status` renders as a transient italic line only. The first send/edit failure flips the renderer `broken`, after which `finalize()` deletes the partial message and resends the full text via `sendChunked`.
**Why**: pi streams token-level `text` deltas; editing on every delta showed mid-word churn that read poorly in Telegram, so the renderer gates on paragraph boundaries — the legacy Claude SDK delivered coarser blocks and never exposed this. Baking the tool summary into the buffer (rather than dropping the transient line) restores the legacy behavior of recording what tools ran *and* supplies the blank-line separation between consecutive assistant text segments, which otherwise concatenated into a run-on. The cost — paragraph gating, edit throttling, overflow reconciliation, partial-markdown risk — is contained in one renderer module, and the broken-state path means any Telegram edit failure degrades cleanly to a plain whole-message send rather than losing or duplicating text.
**Alternatives Considered**: Whole-message rendering (accumulate then send once after the stream) — simplest, but no streaming feedback and no hook for tool/status markers; per-delta editing (what the throttle alone produced) — token-by-token churn that looks broken in Telegram; line-gated (`\n`) instead of paragraph-gated — more responsive but fragments prose mid-thought.

**Consequences**:
- Pro: Incremental feedback at paragraph granularity without mid-word churn
- Pro: Tool activity persists in the final message (the baked summary) and consecutive text segments stay separated by blank lines
- Pro: Markdown parse failures and edit errors degrade to a fresh plain `sendChunked` instead of breaking the exchange
- Pro: Overflow is handled by finalizing full chunks in place and streaming only the tail, so the 4096 limit never aborts streaming
- Con: More moving parts than a single send — paragraph gating, tool baking, throttle, overflow commit, and broken-state bookkeeping all live in `streaming.ts`
- Con: A long single paragraph with no `\n\n` shows nothing until it closes or `finalize()` runs (the typing indicator covers liveness)

### Markdown with plain-text fallback instead of an entity converter

**Choice**: Send with legacy `parse_mode: "Markdown"`; if Telegram rejects with a "can't parse entities" error, resend the identical text with no parse mode (`isMarkdownParseError` matches on the error description).
**Why**: An entity-conversion pipeline (telegramify-markdown style) is a large surface for a single-user channel. The Markdown-then-plain fallback applies to every send and edit (streaming flush, overflow commit, and `finalize()`), so a chunk that fails to parse mid-stream resends unformatted and the renderer keeps going — formatting is best-effort, text is never lost.
**Alternatives Considered**: MarkdownV2 with escaping (strict, breaks easily on LLM output); a TS entity-conversion library; plain text always.

**Consequences**:
- Pro: Two code paths, no escaping logic, no dependency
- Pro: A malformed message degrades to unformatted text instead of an error
- Con: Occasional messages render with raw markdown syntax
- Con: Legacy Markdown supports fewer constructs than MarkdownV2 (no underline, spoilers)

### Promise-chain mutex serializing all sends

**Choice**: A 19-line `Mutex` (`run()` chains onto a tail promise) wraps both `respond()` and `deliver()`.
**Why**: Immediate-gated deliveries can arrive while an exchange is rendering. Without serialization, the chunked send of a response and the silent-send/copy/delete sequence of a delivery would interleave in the chat. `tests/telegram/mutex.test.ts` proves two concurrent `deliverText` calls never interleave their API calls.
**Alternatives Considered**: async-mutex dependency; queueing deliveries inside the channel; relying on the coordinator's delivery queue alone (insufficient — `immediate: true` command acks and the shutdown digest still call `channel.deliver()` directly, racing an in-flight render).

**Consequences**:
- Pro: Send sequences are atomic with respect to each other; FIFO order preserved
- Pro: Rejection-swallowing tail means one failed send cannot deadlock the channel
- Con: A slow delivery delays rendering of the next response (acceptable for a single-user chat)

### Single-use flag encoded in the callback data

**Choice**: `packCallbackData` prefixes the button value with `btn1:` (single-use) or `btnN:` (multi-use); the tap handler reads the behavior back from the wire.
**Why**: Whether a tap should remove the keyboard is a property of the prompt that must survive process restarts. Encoding it in the 5-byte prefix avoids any per-message persistence; the cost is capping values at 58 of Telegram's 64 `callback_data` bytes, enforced by `validateButtons`.
**Alternatives Considered**: per-message state in `app.state`; always removing the keyboard.

**Consequences**:
- Pro: Stateless across restarts; `buttons.ts` is the single source of truth for both pack and unpack sides
- Pro: Unknown prefixes are detectably foreign and dropped with a warning
- Con: 58-byte value ceiling surfaces as a validation error the agent must work around

### Reply routing via inbound metadata the boundary honors

**Choice**: The channel records every handled message id ↔ session in `channel_messages` (inbound and outbound, written in `respond()` once routing has settled). When an inbound message replies to — or a reaction targets — a recorded id, `routeReply` resolves the owning session and stamps `metadata.resumeSessionId` on the turn. The boundary middleware checks for `resumeSessionId` first and, when it points to a different known session, resumes it and returns *before* running topic classification.
**Why**: A reply is an unambiguous, explicit signal of which conversation the user means — far stronger than the LLM topic classifier the boundary normally uses. Expressing the target as inbound metadata keeps the channel ignorant of session internals (it only knows ids) and reuses the boundary's existing `resumeSession` path, so no new coordinator surface is needed. An unknown target simply omits the metadata and falls through to normal routing, so a stale or foreign reply-to never strands the message.
**Alternatives Considered**: A dedicated coordinator method to force-resume from the channel (couples the channel to session lifecycle); classifying the reply text as usual (loses the explicit signal); persisting the full reply graph (unnecessary — only the id↔session edge matters).

**Consequences**:
- Pro: Explicit, deterministic routing for replies and reactions; no extra LLM call on the boundary
- Pro: The channel stays decoupled — it records and looks up by id through `SessionsApi`, never touching session files
- Pro: Recording in `respond()` attaches both ids to the actually-receiving session, so the mapping is correct even after a topic shift moved the turn to another session
- Con: Reactions and replies only route when the referenced message was recorded; messages predating this feature, or sent outside `respond()`, are not resolvable and fall back to normal routing
- Con: Recording is best-effort (failures logged at debug), so a write error silently disables routing for that message

### Media stored under the data dir with retention pruning

**Choice**: Downloads land in `{workspace}/.tachikoma/media` (via `app.workspace.dataDir`), named `<12-hex>-<original-name>`; the `media-dir` bootstrap hook prunes files older than 30 days.
**Why**: The agent needs the file path to remain valid across process restarts and topic boundaries — the OS temp dir offers no such guarantee. Bootstrap-time pruning bounds disk growth without a background job.
**Alternatives Considered**: OS temp dir; per-session directories; no retention.

**Consequences**:
- Pro: Paths referenced in past transcripts stay resolvable for ~30 days
- Pro: Cleanup is idempotent and costs one directory scan at startup
- Con: Stale files linger until the next restart

## System Behavior

### Scenario: Progressive rendering of a long response with a tool call

**Given**: An exchange that emits some text, a `tool-start`, then a long stream of `text` events exceeding 4096 characters
**When**: `respond()` consumes the stream
**Then**: The renderer shows the settled text with `_🔧 <live tool label>_` as a transient line below it; when text resumes it bakes a persistent `_🔧 <summary>_` marker (blank-line separated) in place of the live line, renders whole paragraphs at most once per ~1500 ms, finalizes each full chunk in place once the buffer overflows the limit while the tail keeps streaming, and on stream end `finalize()` bakes any pending tool summary, trims the dangling separator, and flushes the remainder; the last message ID becomes the pin target.

### Scenario: User sends `/stop` mid-run

**Given**: The agent is generating a response
**When**: The user sends `/stop`
**Then**: `handleStop` records the message as the last inbound, calls the injected `stop` callback (wired to the coordinator's exchange abort) instead of submitting a turn, and replies `⏹ Stopped.`; an abort failure is logged but the acknowledgement still sends.

### Scenario: Markdown parse failure on a response chunk

**Given**: The agent's response contains unbalanced markdown
**When**: `sendWithMarkdownFallback` sends the chunk and Telegram answers "can't parse entities"
**Then**: The same text is resent without `parse_mode` and delivered unformatted; any non-parse error (e.g. "chat not found") propagates instead.

### Scenario: Delivery arrives while a response is rendering

**Given**: `respond()` holds the mutex draining an event stream
**When**: The coordinator calls `deliver()` with an immediate-gated notification
**Then**: `deliver()` queues behind the in-flight `respond()`; its silent send + copy+delete runs only after the response chunks are fully sent.

### Scenario: Voice message ingestion

**Given**: The user sends a 5-second voice note
**When**: `handleMedia` runs
**Then**: `resolveMedia` yields kind `voice` with `.ogg` extension and duration summary, the 20 MB pre-check passes, the file downloads to `{dataDir}/media/<hex>.ogg`, and an `InboundMessage` with empty text and one attachment (`Voice message (5 seconds, audio/ogg)`) is submitted; on download failure a notice is sent and nothing is submitted.

### Scenario: User replies to an older bot message

**Given**: The agent answered about topic A in session 5, that session later closed, and the user is now talking about topic B in session 9
**When**: The user replies to the old topic-A message and the channel handles it
**Then**: `routeReply` looks up the replied-to message id, finds it maps to session 5, and stamps `metadata.resumeSessionId: 5`; the boundary middleware resumes session 5 (skipping classification) so the new turn lands in the topic-A conversation. Had the replied-to id not been recorded, no metadata is attached and the turn stays in session 9 via normal routing.

### Scenario: User reacts 👍 to a message

**Given**: The authorized user adds a 👍 reaction to a previous message
**When**: The `message_reaction` handler runs
**Then**: `mapReaction` diffs the emoji sets and submits a turn reading `The user reacted 👍 to a previous message.` with `metadata.reaction` and `metadata.replyToMessageId`; if that message id maps to another session, `routeReply` adds `resumeSessionId` so the reaction lands in the owning conversation. A reaction from any other user id is dropped, and a no-change reaction update submits nothing.

### Scenario: Push notification for a background delivery

**Given**: `pushNotifications` is enabled and a task summary spans two chunks
**When**: `deliverText` runs
**Then**: Both chunks send silently, the last one is copied (firing exactly one push) and the original deleted; if the copy fails the original silent message stands, and if the delete keeps failing after 3 retries the duplicate is accepted and logged.

## Notes

- `respond()` renders `text`, `tool-start`, `status`, and `error` events, and handles `result` in its own `case 'result':` branch (it logs the exchange's `costUsd`/`totalTokens` and session id via `runtime.log.info`, no user-facing output). Only `tool-end` and `thinking` reach the event switch without rendering and fall through the `default` branch — `result` does not fall through.
- A `tool-start` shows a live italic line `_🔧 <friendly activity>_` while the tool runs — the wrench glyph plus an args-aware present-progressive label from `formatToolActivity` (`tool-labels.ts`), e.g. "Reading <path>", "Running: <command>", "Searching for '<pattern>'", falling back to a humanized tool name (underscore-split and title-cased, MCP server prefix stripped). The tools that ran since the last text are also tracked and, when text resumes or the exchange finalizes, collapsed via `summarizeToolActivities` into a persistent italic marker `_🔧 <summary>_` baked into the message (basename paths, inline-code args, same-typed tools aggregated past two uses, >5 phrases folded into a trailing "more").
- On `start()`, after `bot.init()`, the channel calls `bot.api.setMyCommands` to register the `/new` and `/queue` prefix commands in Telegram's command menu (discoverability for the coordinator-parsed prefixes); a failure is caught and logged as a warning so it never blocks polling.
- `sendErrorNotice` sends two distinct forms: a recoverable error is `⚠️ Error: <message>`; a non-recoverable one appends a second paragraph ("This needs your attention — the next message won't recover on its own.").
- Button validation rejects both an empty label and an empty value (each with its own message) in addition to the ≤58-byte value and ≤100-button limits.
- `status()` routes through the active `StreamRenderer` when one exists (showing the transient line); with no in-flight exchange it falls back to a typing chat action to signal liveness. (During shutdown the coordinator routes status lines to `shutdownStatus()` instead — see below.)
- `shutdownStatus(text)` is the teardown-time status surface (the streaming renderer is gone by then): it sends one dedicated italic message and edits it in place on each subsequent call, serialized through the send mutex. The coordinator awaits it so the line lands before the process exits; the final "Done" is intentionally left in the chat. The dedicated message id is tracked on the channel (`shutdownMessageId`) and never reused for anything else.
- The `pushNotifications` flag only affects `deliver()`; `respond()`'s streaming sends use default notification behavior.
- `react_to_message` passes the emoji through rather than validating against Telegram's evolving allowed set — the API rejection is surfaced to the agent.
- The send-file allowed-roots check is a prefix test on resolved paths (`validateFilePath`); symlinks are not canonicalized before the check.
