# Design: Telegram Channel

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/telegram.md](../feature-specs/telegram.md)
**Status**: Current

## Purpose

Explain how the Telegram extension is assembled: one shared grammY `Bot` feeding a `Channel` implementation and a set of agent tools, with rendering split into small pure modules (chunking, sending, media, buttons) that take narrow fakeable dependencies.

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
- `app.bootstrap("media-dir", …)` — media directory creation and retention pruning at startup
- `app.agent.use()` — per-session registration of the five Telegram tools, closing over the bot API and the channel's last-message-ID getters

## Design Overview

`index.ts` is pure wiring: validate config, construct one `Bot`, instantiate `TelegramChannel`, register the bootstrap hook and the tool factory. The channel (`channel.ts`) owns the grammY lifecycle — update handlers, `bot.init()` token validation, detached long polling — and delegates every behavior to a sibling module: inbound mapping (`inbound.ts`), length-aware splitting (`chunking.ts`), the send/notify primitives (`sending.ts`), media resolution and download (`media.ts`), button wire format (`buttons.ts`), and FIFO serialization (`mutex.ts`). Tools (`tools.ts`) reuse the same bot API through a narrow `ToolApi` interface and read the channel's last inbound/outbound message IDs through getter closures, so tools and channel stay decoupled but consistent.

Rendering is whole-message: `respond()` drains the event stream while a typing indicator refreshes, then sends the accumulated text once via `sendChunked`. There are no progressive message edits; `tool-start`/`tool-end`/`status`/`thinking` events are currently not rendered.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/telegram/index.ts` | Extension entry: config schema (`botToken`, `chatId`, `allowMedia`, `pushNotifications`, `extraFileRoots`), disable-on-missing-config, bot/channel construction, allowed-roots computation, tool factory registration | Silent disable (info log) instead of startup failure; allowed roots deduplicated from workspace + `tmpdir()` + expanded extras |
| `src/extensions/telegram/channel.ts` | `TelegramChannel implements Channel`: grammY handlers (message, `callback_query:data`), token validation, detached polling, `respond()`/`deliver()` under the mutex, media handler with user-facing failure notices, last inbound/outbound ID tracking | `answerCallbackQuery()` before the auth check so unauthorized tappers' spinners still clear; polling runs detached because grammY's `start()` only resolves when polling stops |
| `src/extensions/telegram/inbound.ts` | Pure mappers from grammY `Message`/tap values to domain `InboundMessage` | Button taps framed as explicit prose so the agent distinguishes taps from typed input |
| `src/extensions/telegram/chunking.ts` | `splitMessage`: paragraph > line > hard split within 4096 UTF-16 units | JS `string.length` is already UTF-16 code units — Telegram's actual constraint, no conversion layer needed; hard split backs off one unit at a low surrogate |
| `src/extensions/telegram/sending.ts` | `sendWithMarkdownFallback`, `sendChunked`, `notifyViaCopyDelete`, `deliverText`, `startTyping`; narrow `SendApi` interface | Copy-first ordering keeps text safe (copy fails → original preserved; delete fails → duplicate accepted after 3 retries); typing refreshed every 5 s (Telegram expires chat actions at ~5 s) |
| `src/extensions/telegram/mutex.ts` | Promise-chain FIFO `Mutex` serializing all channel sends | Tail swallows rejections so a failed delivery never wedges the queue |
| `src/extensions/telegram/media.ts` | `resolveMedia` priority table, `downloadMedia` with 20 MB pre-check, `generateMediaFilename`, `buildAttachment`, `ensureMediaDir` (create + 30-day prune) | Animation checked before document (Telegram sets both fields); video notes map onto the `video` domain kind; size check happens before any network call |
| `src/extensions/telegram/buttons.ts` | `callback_data` wire format (`btn1:`/`btnN:` + value), layout validation, `InlineKeyboardMarkup` construction | Single-use bit lives in the callback data itself so semantics survive process restarts with no per-message state |
| `src/extensions/telegram/tools.ts` | Five `pi.registerTool` registrations plus extracted handlers (`handleSendFile`, `handleReactToMessage`, `handlePinMessage`, `handleUnpinMessage`, `handleSendMessageWithButtons`), path validation, extension-based media-type detection | Handlers take `Pick<ToolDeps, …>` so tests fake only what each needs; errors signal by throwing per DES-002 |
| `tests/telegram/` | Vitest suites for chunking, sending, mutex serialization, inbound/media mapping, tools, buttons | Fake `SendApi`/`ToolApi` objects; no live network |

## Key Decisions

### Whole-message rendering instead of progressive edits

**Choice**: `respond()` accumulates `text` events and sends once after the stream completes, with a typing indicator covering the wait.
**Why**: Progressive editing (the Python channel's approach) requires edit throttling, split-message reconciliation, and partial-markdown-safe formatting — a large surface for the first TS cut. Draining the stream then chunk-sending needs none of it, and the conversation loop already hands the channel a complete event stream per exchange.
**Alternatives Considered**: Progressive `editMessageText` with throttle (Python parity); buffered edits per N seconds.

**Consequences**:
- Pro: No edit rate-limit handling, no split-message bookkeeping, no partial-markdown parse failures
- Pro: Chunking runs once on final text, so splits land at stable boundaries
- Con: No streaming feedback beyond the typing indicator; long responses appear all at once
- Con: Tool-activity and status events have no rendering hook yet (silently dropped in the event switch)

### Markdown with plain-text fallback instead of an entity converter

**Choice**: Send with legacy `parse_mode: "Markdown"`; if Telegram rejects with a "can't parse entities" error, resend the identical text with no parse mode (`isMarkdownParseError` matches on the error description).
**Why**: The Python channel needed telegramify-markdown's entity pipeline because progressive edits re-parse partial markdown constantly. With whole-message sends, parse failures are rare, and the fallback guarantees delivery — formatting is best-effort, text is never lost.
**Alternatives Considered**: MarkdownV2 with escaping (strict, breaks easily on LLM output); a TS entity-conversion library; plain text always.

**Consequences**:
- Pro: Two code paths, no escaping logic, no dependency
- Pro: A malformed message degrades to unformatted text instead of an error
- Con: Occasional messages render with raw markdown syntax
- Con: Legacy Markdown supports fewer constructs than MarkdownV2 (no underline, spoilers)

### Promise-chain mutex serializing all sends

**Choice**: A 19-line `Mutex` (`run()` chains onto a tail promise) wraps both `respond()` and `deliver()`.
**Why**: Immediate-gated deliveries can arrive while an exchange is rendering. Without serialization, the chunked send of a response and the silent-send/copy/delete sequence of a delivery would interleave in the chat. The mutex is the TS analogue of the Python channel's delivery lock; `tests/telegram/mutex.test.ts` proves two concurrent `deliverText` calls never interleave their API calls.
**Alternatives Considered**: async-mutex dependency; queueing deliveries inside the channel; relying on the coordinator's idle gating alone (insufficient — `gate: "immediate"` bypasses it).

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

### Media stored under the data dir with retention pruning

**Choice**: Downloads land in `{workspace}/.tachikoma/media` (via `app.workspace.dataDir`), named `<12-hex>-<original-name>`; the `media-dir` bootstrap hook prunes files older than 30 days.
**Why**: The agent needs the file path to remain valid across process restarts and topic boundaries — the OS temp dir offers no such guarantee. Bootstrap-time pruning bounds disk growth without a background job.
**Alternatives Considered**: OS temp dir (Python's approach); per-session directories; no retention.

**Consequences**:
- Pro: Paths referenced in past transcripts stay resolvable for ~30 days
- Pro: Cleanup is idempotent and costs one directory scan at startup
- Con: Stale files linger until the next restart

## System Behavior

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

### Scenario: Push notification for a background delivery

**Given**: `pushNotifications` is enabled and a task summary spans two chunks
**When**: `deliverText` runs
**Then**: Both chunks send silently, the last one is copied (firing exactly one push) and the original deleted; if the copy fails the original silent message stands, and if the delete keeps failing after 3 retries the duplicate is accepted and logged.

## Notes

- `tool-start`/`tool-end`, `status`, and `thinking` events reach `respond()` but are intentionally unhandled today — tool-activity markers are a known gap against DLT-032.
- The `pushNotifications` flag only affects `deliver()`; `respond()` sends with default notification behavior (one notification per chunk).
- `react_to_message` passes the emoji through rather than validating against Telegram's evolving allowed set — the API rejection is surfaced to the agent.
- The send-file allowed-roots check is a prefix test on resolved paths (`validateFilePath`); symlinks are not canonicalized before the check.
