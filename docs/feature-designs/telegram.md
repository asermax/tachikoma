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
- Telegram renders formatting via `MessageEntity[]` sent with `parse_mode` omitted (the text is then literal, so ordinary punctuation never fails); the agent's GitHub-flavored markdown must be converted to text + entities, chunking must never split an entity across messages, and the send path must never lose text when rendering fails
- `respond()` and `deliver()` can be invoked concurrently (immediate-gated deliveries arrive mid-exchange), but Telegram message sequences must not interleave
- Everything ships as a `defineExtension` module per DES-001/DES-002; tools register through pi's `registerTool` inside `app.agent.use` factories

**Interactions:**
- `app.channels.register()` / `Channel` contract — conversation loop drives `respond()`/`deliver()`/`stop()`
- `runtime.submit()` — inbound messages enter the coordinator inbox as `InboundMessage` (`src/domain/message.ts`); media attachments are rendered into the prompt by the coordinator
- `app.sessions.current()` — the channel reads the receiving session id so `respond()` and the tools can record the message↔session mapping; the boundary middleware honors `metadata.resumeSessionId` as an explicit resume target (see [conversation-loop](conversation-loop.md))
- `app.db` — the extension constructs its own `TelegramMessageStore` (`store.ts`) over the shared handle, owning the `channel_messages` reads/writes; the reply-to mapping is entirely telegram-local, with no core `SessionsApi` surface for it
- `app.bootstrap("media-dir", …)` — media directory creation and retention pruning at startup
- `app.agent.use()` — per-session registration of the five Telegram tools, closing over the bot API and the channel's last-message-ID getters

## Design Overview

`index.ts` is pure wiring: validate config, construct one `Bot`, instantiate `TelegramChannel`, register the bootstrap hook and the tool factory. The channel (`channel.ts`) owns the grammY lifecycle — update handlers, `bot.init()` token validation, detached long polling — and delegates every behavior to a sibling module: inbound mapping (`inbound.ts`), length-aware splitting (`chunking.ts`), the send/notify primitives (`sending.ts`), media resolution and download (`media.ts`), button wire format (`buttons.ts`), and FIFO serialization (`mutex.ts`). Tools (`tools.ts`) reuse the same bot API through a narrow `ToolApi` interface and read the channel's last inbound/outbound message IDs through getter closures, so tools and channel stay decoupled but consistent.

Rendering is progressive: `respond()` drives a per-exchange `StreamRenderer` (`streaming.ts`) that sends a message early and edits it live, throttled to ~1500 ms and skipping no-op edits, with a typing indicator refreshing throughout. Because pi streams token-level `text` deltas, the renderer gates on paragraph boundaries — only complete paragraphs (text up to the last `\n\n`) render while text streams, so the user never sees mid-word churn; the trailing in-progress paragraph stays buffered until it closes or `finalize()` flushes it. `tool-start` shows the running tool as a live italic line below the text and is *also* tracked: when text resumes, the tools that ran fold into a persistent `_🔧 <summary>_` marker baked into the buffer, which both records the activity (legacy parity) and supplies the blank line separating one text segment from the next. `status` events render as a transient italic line only. On stream end `finalize()` bakes any still-pending tool summary, trims the dangling separator, and upgrades the message to its final chunked form. Any streaming send/edit failure trips the renderer into a broken state and `finalize()` falls back to a plain `sendChunked` of the full text, so text is never lost.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/telegram/index.ts` | Extension entry: config schema (`botToken`, `chatId`, `allowMedia`, `pushNotifications`, `extraFileRoots`), disable-on-missing-config, bot/channel construction, `TelegramMessageStore` construction over `app.db`, allowed-roots computation, tool factory registration | Silent disable (info log) instead of startup failure; allowed roots deduplicated from workspace + `tmpdir()` + expanded extras; the message↔session store is telegram-owned and shared between the channel and the tools |
| `src/extensions/telegram/store.ts` | `TelegramMessageStore implements ChannelMessageStore` over `app.db`: `record()` upserts a message id↔session row (optionally storing outgoing text), `findSessionId()` resolves a replied-to message back to its owning session id, and `findMessage()` resolves a recorded message to its owning session, stored text, and whether it is that session's most recent recorded message | Owns the `channel_messages` reads/writes that previously lived on the core `SessionRegistry`, removing the core→telegram dependency; `findSessionId` reads the session id straight off the mapping row (the only thing reply routing needs) rather than round-tripping the registry; `findMessage` centralizes the recency gate — `isLatest`, computed from the latest message id by max `(created_at, id)` — that keeps context off the session's latest message, returning the stored text and owning session in one lookup so callers (replies, reactions, button taps) don't re-derive them; `record`'s `onConflictDoUpdate` only overwrites `text` when a value is supplied, so a re-record without text preserves a stored prompt |
| `src/extensions/telegram/channel.ts` | `TelegramChannel implements Channel`: grammY handlers (message, `callback_query:data`, `message_reaction`), token validation, detached polling, `respond()` driving a per-exchange `StreamRenderer` under the mutex, `/stop` abort handling, `deliver()`/`status()`/`shutdownStatus()`, media handler with user-facing failure notices, last inbound/outbound ID tracking, reply-to routing (`routeReply`), recency-gated context prepending for replies/reactions/button-taps (`shouldSkipQuote` plus inline `findMessage(...).isLatest` checks, fed by `lastExchangeOf`), and message recording in `respond()` | `shutdownStatus()` renders teardown progress on one dedicated italic message edited in place under the mutex (legacy parity) — used because the streaming renderer that hosts normal `status()` lines is gone by shutdown; the final "Done" line is left in the chat; `answerCallbackQuery()` before the auth check so unauthorized tappers' spinners still clear; polling runs detached because grammY's `start()` only resolves when polling stops; `/stop` aborts the live run via the injected `stop` callback rather than reaching the agent; recording happens in `respond()` after routing settles so the inbound and outbound ids attach to the *receiving* session; `routeReply` stamps `metadata.resumeSessionId` only when the target maps to a known session; a one-shot `pingTyping()` fires on receipt of a submitted text/media message (excl. `/stop`) and during preparation status, and `status()` with no active renderer surfaces a provisional lead-in message (`leadInMessageId`) that `respond()` reclaims as the streaming message; under `pushNotifications`, `respond()` copy-deletes the finalized last message on completion (`forceNotification`) to force a single push — gated by `pushNotificationMinSeconds` and the `NOTIFYING_TOOLS` set (`send_telegram_file`/`pin_message`/`send_message_with_buttons`), recording the copied id |
| `src/extensions/telegram/streaming.ts` | `StreamRenderer`: progressive per-exchange renderer — `appendText`/`appendTool`/`showTransient`/`finalize` over one or more Telegram messages, paragraph-gated streaming (renders to the last `\n\n`), ~1500 ms `EDIT_THROTTLE_MS`, no-op-edit skipping, overflow finalization at the 4096 limit, broken-state fallback to a fresh `sendChunked` | One renderer per `respond()`; constructor accepts an optional seed `messageId` (the reclaimed preparation lead-in) and a `silent` flag — when set (push notifications on) every send is silent so the work-in-progress and overflow chunks never fire partial pushes (the single push is forced on completion by `forceNotification`); streamed text only renders complete paragraphs (the trailing partial paragraph waits for `finalize()`), so token-level deltas don't churn mid-word; `appendTool` shows a live tool line *and* bakes a `_🔧 <summary>_` marker into the buffer at the next text/finalize so tool activity persists with blank-line separation; `showTransient` is status-only and replaced by the next text; a single send/edit failure stops live editing and defers the whole text to `finalize()` |
| `src/extensions/telegram/inbound.ts` | Pure mappers from grammY `Message`/tap/reaction values to domain `InboundMessage`: `mapTextMessage`, `mapMediaMessage`, `mapButtonTap`, `mapReaction`, plus `replyQuote`/`replyTargetId` helpers | Button taps and reactions framed as explicit prose; replies carry `metadata.replyToMessageId` (string) and a truncated `Replied to:` quote; `mapReaction` diffs old/new emoji sets and returns `null` on no change. Each mapper takes an optional context the channel supplies: `mapReaction({ lastExchange })` prepends a `Reacted to:` block + interpretation guidance, `mapButtonTap({ prompt })` prepends the original prompt, and `mapTextMessage`/`mapMediaMessage` accept `{ skipQuote }` to suppress the quote — each only when the channel resolves that the referenced message is older than its session's latest (the latest is already live in the agent's context) |
| `src/extensions/telegram/schema.ts` | `channel_messages` drizzle table (channel, message id, session id, direction, timestamp, and a nullable `text`) plus the `ChannelMessageDirection` const map; re-exported from `src/db/schema.ts` | Unique index on `(channel, message_id)` so re-recording the same id re-points it via `onConflictDoUpdate`; index on `session_id`; message ids stored as text since they are opaque keys; `text` is nullable and populated only for outgoing button prompts so a later tap can recover the question |
| `src/extensions/telegram/chunking.ts` | `splitMessage`: paragraph > line > hard split within 4096 UTF-16 units (used by the streaming overflow path, which must keep a raw tail) | JS `string.length` is already UTF-16 code units — Telegram's actual constraint, no conversion layer needed; hard split backs off one unit at a low surrogate. Entity-aware splitting of converted payloads lives in `entities.ts` (`splitMessageWithEntities`) |
| `src/extensions/telegram/entities.ts` | `toTelegramEntities` (markdown-it token walker → `{ text, entities }`) and `splitMessageWithEntities` (entity-safe chunker) | A shared `markdown-it` instance (default preset — GFM strikethrough on; HTML/linkify/typography off) parses the agent's GFM after `flattenTables` runs; the walker emits literal text plus `bold`/`italic`/`strikethrough`/`code`/`pre`(+language)/`text_link`(+url)/`blockquote` entities and bold headings, tracking UTF-16 offsets as `text.length` snapshots. The chunker converts the whole text once, pre-drops entities larger than the limit, then splits at the largest entity-safe position (paragraph > line > surrogate-safe hard) rebasing offsets per chunk — so no entity is ever cut across two messages |
| `src/extensions/telegram/sending.ts` | `sendWithFallback`, `editWithFallback` (payload-taking), `convertAndSplit`, `sendChunked`, `deliverText`, `forceNotification`, `startTyping`; narrow `SendApi` interface (incl. `copyMessage`) | Every send/edit carries a `{ text, entities }` payload with `parse_mode` omitted and resends the raw text plain on a render rejection (`isRenderError` = parse / entities-too-many / too-long); `convertAndSplit` converts then entity-safely splits; `deliverText` silences all but the last chunk under `notifyOnlyLast` so a delivery fires exactly one push; `forceNotification` copies a message within the same chat and best-effort deletes the original to force a push on an already-delivered (edited) message; typing refreshed every 5 s (Telegram expires chat actions at ~5 s) |
| `src/extensions/telegram/markdown.ts` | `flattenTables`: rewrites GFM tables into a bullet list before conversion | Telegram has no table `MessageEntity` and can't render tables, so a table reaching the converter would lose its structure; `flattenTables` runs first (from `entities.ts`) so the surrounding inline formatting survives as entities. Tables inside fenced code are left intact |
| `src/extensions/telegram/tool-labels.ts` | `formatToolActivity` (present-progressive live line), `formatToolName` (underscore-split + title-case, MCP server prefix stripped), and `summarizeToolActivities` (collapses a tool→text segment into one capitalized phrase) | Live label and baked summary use separate maps, keyed by pi's tool names (`read`, `grep`, `bash`, … and `delegate_to_agent`) with pi's arg shape (`path`/`pattern`/`command`) — not the legacy Claude-Code names; they differ in full path vs basename and full quotes vs inline-code; same-typed tools aggregate past 2 uses; >5 phrases fold into a trailing "more"; unmapped tools (including snake_case tachikoma tools) fall back to the humanized name; the `delegate_to_agent` label appends the delegation's display-only `description` (truncated to 60 chars, degrading to the agent-only label when empty) when present — e.g. "Delegating to general-purpose: find refs" / "delegating to general-purpose: find refs"; bash commands truncate to 40 characters in both the live label and the baked summary; the `bash-description` extension overrides the built-in bash tool to require a `description` parameter, and the formatter surfaces it verbatim in both the live label and the baked summary when present |
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
- Pro: Render failures and edit errors degrade to a fresh plain `sendChunked` instead of breaking the exchange
- Pro: Overflow is handled by finalizing full chunks in place and streaming only the tail, so the 4096 limit never aborts streaming
- Con: More moving parts than a single send — paragraph gating, tool baking, throttle, overflow commit, and broken-state bookkeeping all live in `streaming.ts`
- Con: A long single paragraph with no `\n\n` shows nothing until it closes or `finalize()` runs (the typing indicator covers liveness)

### Completion push via copy-delete, gated by duration and notifying tools

**Choice**: Under `pushNotifications` the renderer sends every message silently (the streamed work-in-progress, the lead-in, overflow chunks), and on completion `respond()` forces exactly one push by copying the finalized *last* message within the same chat and deleting the streamed original (`forceNotification` = `copyMessage` + best-effort `deleteMessage`). The copy is skipped for turns shorter than `pushNotificationMinSeconds` (default 10) and whenever a notifying tool (`send_telegram_file`, `pin_message`, `send_message_with_buttons`) ran during the turn. Reply routing records the copied id (the streamed id when the push is skipped).
**Why**: A streamed response is built by editing one message in place, and Telegram edits never notify — so without intervention the user gets no push at all when they've stepped away. Telegram has no notify-in-place API, so copying the finalized message (a fresh send that notifies) and deleting the original is the only way to push an already-delivered message; doing it on the last chunk yields exactly one push for multi-message responses. Silencing the in-progress sends prevents partial pushes from stacking. The duration threshold avoids a copy-delete flicker on every quick reply while the user is still watching (Telegram suppresses the push itself when the chat is open, but the copy-then-delete is still visible). Skipping when a notifying tool ran avoids a double push — those tools already delivered a notifying message of their own.
**Alternatives Considered**: Re-sending the final text fresh instead of `copyMessage` (re-converts markdown and needs the text in hand; `copyMessage` preserves the exact rendered message); forcing a push on every response regardless of duration (flicker on every quick reply); routing background deliveries through `deliver()` instead of `respond()` (would require un-doing the coordinator's queue→agent-turn design and loses the agent's processing of the digest).
**Consequences**:
- Pro: A user who stepped away gets a reliable single push when a substantive response completes
- Pro: Quick conversational turns stream normally with no flicker; notifying tools don't double-push
- Pro: The copy's id is recorded for reply routing, so replying to the delivered message resolves correctly
- Con: For a watching user every qualifying completion shows a brief copy-delete flicker (inherent to forcing a push on an edited Telegram message)
- Con: Two extra API calls (`copyMessage` + `deleteMessage`) per qualifying turn

### Preparation lead-in message reclaimed by the streaming response

**Choice**: While no streaming renderer exists (preparation — boundary/middleware/session-open, before `respond()`), `status()` surfaces each line on a single provisional italic message (`leadInMessageId`, created on the first line then edited in place under the send mutex) and refreshes the typing indicator. `respond()` seeds the `StreamRenderer` with that id, so the streamed response edits the lead-in in place — replacing it with the response text — and `finalize()` deletes it when the exchange yields no text. A one-shot `pingTyping()` also fires on receipt of a submitted text/media message (excluding `/stop`) to cover the instant before the first preparation status and the no-status case (e.g. a simple continue where boundary returns early).
**Why**: Previously `status()` with no renderer discarded the text and fired only a typing action, so the lines boundary emits during preparation ("Checking conversation topic…", "Resuming a previous conversation", …) were never visible — the user saw nothing between sending and the first streamed paragraph. Reclaiming the provisional message as the streaming message (rather than deleting it and sending fresh) means preparation feedback and the response occupy one message, so there is no flash of a second message and nothing lingers. Steering and queueing skip middleware, so they never produce a lead-in — matching the intent that feedback appears only when a response is being prepared.
**Alternatives Considered**: Firing typing only and discarding the text (the prior behavior — the reported gap); deleting the lead-in and sending the response as a fresh message (a visible second message and a lost handoff); a coordinator "preparation started" signal with a default `Thinking…` line on every exchange (noisier for fast/turn-one replies that skip boundary detection — the status-driven lead-in appears only when there is meaningful preparation to report).
**Consequences**:
- Pro: Preparation status is visible and becomes the response with no extra message
- Pro: No regression — exchanges without a lead-in (no preparation status) behave exactly as before (the renderer sends its own first message)
- Pro: A single `leadInMessageId` field means at most one lead-in ever exists; a stale one (a rare exchange that aborts after boundary already showed a line) is reclaimed or overwritten by the next preparation status or `respond()`
- Con: If an exchange throws after a preparation status fired, that `_Checking topic…_` line may linger until the next message (rare — commands run before boundary, so handled messages never create a lead-in)
- Con: For preparations longer than Telegram's ~5 s chat-action lifetime the typing indicator can briefly lapse until `respond()` starts; the visible lead-in covers liveness in that window

### Entity-based rendering via markdown-it with entity-safe chunking

**Choice**: Convert the agent's GitHub-flavored markdown to a literal display string plus `MessageEntity[]` with `markdown-it` (`toTelegramEntities` in `entities.ts`) and send with `parse_mode` omitted and the entities attached; GFM tables are flattened to a bullet list first (`flattenTables` in `markdown.ts`) before conversion. Entities cover bold, italic, strikethrough, code, pre(+language), `text_link`, blockquote, and headings (as bold). The whole text is converted once and then split by `splitMessageWithEntities` so no entity ever spans two messages (the split snaps to just before an entity; an entity larger than the limit is dropped, its text still rendering plain). If a send/edit is still rejected for a render reason ("can't parse entities", "entities too many", "message is too long"), the raw text is resent plain (`isRenderError`). The convert-then-fallback applies to every send and edit (streaming flush, overflow commit, `finalize()`, and `deliver()`), so a chunk that fails mid-stream resends unformatted and the renderer keeps going — formatting is best-effort, text is never lost.
**Why**: Telegram applies formatting from attached `MessageEntity[]` and treats the text as literal when `parse_mode` is omitted, so ordinary punctuation (`.`, `-`, `!`, `(`) can't trigger a parse failure and escaping can't inflate length — the two failure modes that made the prior MarkdownV2-string pipeline fall back to plain text on most real responses. The legacy Python channel used this same entity model; the JS `telegramify-markdown` port only emits MarkdownV2 strings (and no maintained JS package produces entities), so the converter is built on `markdown-it`'s token stream, which yields the entity spans directly. Converting the whole text once (rather than per-chunk) is what lets chunking be entity-aware: global entity offsets are rebased per chunk, so a bold span or fenced block crossing the 4096 limit moves wholesale into one message instead of being cut in half. GFM tables are flattened first because Telegram has no table entity or rendering; the bullet form is the readable fallback and the surrounding inline formatting survives. Tables inside fenced code blocks are left intact.
**Alternatives Considered**: A MarkdownV2-string pipeline (the prior approach — any unescaped punctuation mark or escaping-driven overflow rejects the whole chunk and falls back to plain); a hand-rolled GFM→entity converter (owns every edge case that `markdown-it` already handles); the JS `telegramify-markdown` port (only emits MarkdownV2 strings, not entities); an entity-producing JS library (none is maintained); a MarkdownV2+entities hybrid (two code paths, no benefit); per-chunk conversion (can't keep an entity whole across a chunk boundary — the gap that entity-safe splitting closes).

**Consequences**:
- Pro: The agent's markdown renders correctly (bold, italic, strikethrough, code, links, blockquotes, lists) instead of degrading to plain text, and ordinary punctuation never costs formatting
- Pro: A formatting span (bold, fenced code) crossing a chunk boundary stays whole in one message instead of being cut, so multi-message responses keep their formatting
- Pro: Tables flatten to a readable bullet list instead of mangling the whole message, so a table no longer costs the surrounding formatting
- Pro: A message that still fails to render degrades to unformatted text instead of an error — text is never lost
- Con: One third-party dependency (`markdown-it`)
- Con: Constructs with no markdown mapping (spoiler, underline, text_mention, custom_emoji) render as plain literal text
- Con: The streaming overflow path (`commitOverflow`) splits the raw buffer at paragraph boundaries to keep a raw tail streaming, so a single oversize paragraph with formatting spanning a hard split is the one edge where an entity can still be cut there (rare; text always survives; the `finalize` path is fully entity-safe)

### Promise-chain mutex serializing all sends

**Choice**: A 19-line `Mutex` (`run()` chains onto a tail promise) wraps both `respond()` and `deliver()`.
**Why**: Immediate-gated deliveries can arrive while an exchange is rendering. Without serialization, the chunked send of a response and the chunked send of a delivery would interleave in the chat. `tests/telegram/mutex.test.ts` proves two concurrent `deliverText` calls never interleave their API calls.
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

### Scenario: Render rejection falls back to plain text

**Given**: An entity send/edit is rejected for a render reason (invalid or overlapping entities "can't parse entities", too many entities "entities too many", or text over 4096 "message is too long")
**When**: `sendWithFallback` / `editWithFallback` receive the rejection
**Then**: The raw text is resent with no entities and delivered unformatted (`isRenderError`); any other error (e.g. "chat not found") propagates instead. This is rare — omitting `parse_mode` means ordinary punctuation can't be rejected.

### Scenario: Response contains a markdown table

**Given**: A response mixes prose with a GFM table whose cells contain special characters (e.g. `Piano 7-8 PM`)
**When**: `toTelegramEntities` converts it
**Then**: `flattenTables` rewrites the table into a bullet list (`- **Monday**: Piano 7-8 PM`) before `markdown-it` runs, so the prose and the bolded first cell keep their entities and the cell's `-` is plain literal text. The table no longer triggers any fallback, and a table inside a fenced code block is left intact.

### Scenario: A formatting span crosses the message-length limit

**Given**: A response longer than 4096 UTF-16 units where a bold span or fenced code block straddles the chunk boundary
**When**: `splitMessageWithEntities` chunks the converted text + entities
**Then**: Each entity falls entirely within one chunk (the split snaps to just before it) instead of being cut, so the formatting survives across the multi-message response, and each chunk's entity offsets are relative to that chunk's own text. An entity larger than 4096 (e.g. a giant fenced block) is dropped and its text rendered plain across the chunks, never as a broken half-entity.

### Scenario: Delivery arrives while a response is rendering

**Given**: `respond()` holds the mutex draining an event stream
**When**: The coordinator calls `deliver()` with an immediate-gated notification
**Then**: `deliver()` queues behind the in-flight `respond()`; its chunked send (silent until the last, audible final chunk) runs only after the response chunks are fully sent.

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
**Then**: `mapReaction` diffs the emoji sets and submits a turn reading `The user reacted 👍 to a previous message.` with `metadata.reaction` and `metadata.replyToMessageId`; if that message id maps to another session, `routeReply` adds `resumeSessionId` so the reaction lands in the owning conversation. When the reacted-to message is *older* than its session's most recently recorded message, the owning session's last exchange is prepended as a `Reacted to:` block with an interpretation-guidance suffix (an unrecorded target falls back to the active session's exchange); a reaction to the session's *latest* message is left bare, since that exchange is already live in the agent's context. A reaction from any other user id is dropped, and a no-change reaction update submits nothing.

### Scenario: User taps an inline button

**Given**: The authorized user taps a value on an inline-keyboard message the bot sent earlier
**When**: The `callback_query:data` handler runs
**Then**: The tap is acknowledged first, the keyboard is removed for a single-use tap, and `mapButtonTap` submits a turn reading `` The user tapped the option `<value>` out of the options you displayed. `` with `metadata.buttonValue`. When the tapped button message is *older* than its session's latest recorded message, the original prompt stored against it is prepended (`<prompt>\n\n…`) so the agent sees the question its tap answers; a tap on the *latest* button message is left bare (the prompt is already live). An unauthorized tap is dropped after acknowledgement, and unrecognized callback data is logged and dropped.

### Scenario: Push notification for a background delivery

**Given**: `pushNotifications` is enabled and a task summary spans two chunks
**When**: `deliverText` runs
**Then**: The first chunk sends silently and the last sends audibly (`disable_notification` omitted), so the delivery fires exactly one push — on the final chunk. With `pushNotifications` disabled every chunk sends audibly.

### Scenario: Push notification when a streamed response completes

**Given**: `pushNotifications` is enabled, `pushNotificationMinSeconds` is 10, and the user has stepped away while a 30-second streamed response finishes
**When**: `respond()` finalizes
**Then**: The streamed message (edited in place throughout, so never pushed on its own) is copied within the chat and the original deleted; the fresh copy fires exactly one push, and reply routing records the copy's id. A quick 3-second reply would instead skip the copy-delete (no flicker while the user watches); a turn that called `send_telegram_file` would skip it too (the file already pushed).

## Notes

- `respond()` renders `text`, `tool-start`, `status`, and `error` events, and handles `result` in its own `case 'result':` branch (it logs the exchange's `costUsd`/`totalTokens` and session id via `runtime.log.info`, no user-facing output). Only `tool-end` and `thinking` reach the event switch without rendering and fall through the `default` branch — `result` does not fall through.
- A `tool-start` shows a live italic line `_🔧 <friendly activity>_` while the tool runs — the wrench glyph plus an args-aware present-progressive label from `formatToolActivity` (`tool-labels.ts`), e.g. "Reading <path>", "Running: <command>", "Searching for '<pattern>'", falling back to a humanized tool name (underscore-split and title-cased, MCP server prefix stripped). The tools that ran since the last text are also tracked and, when text resumes or the exchange finalizes, collapsed via `summarizeToolActivities` into a persistent italic marker `_🔧 <summary>_` baked into the message (basename paths, inline-code args, same-typed tools aggregated past two uses, >5 phrases folded into a trailing "more").
- On `start()`, after `bot.init()`, the channel calls `bot.api.setMyCommands` to register the `/new` and `/queue` prefix commands in Telegram's command menu (discoverability for the coordinator-parsed prefixes); a failure is caught and logged as a warning so it never blocks polling.
- `sendErrorNotice` sends two distinct forms: a recoverable error is `⚠️ Error: <message>`; a non-recoverable one appends a second paragraph ("This needs your attention — the next message won't recover on its own.").
- Button validation rejects both an empty label and an empty value (each with its own message) in addition to the ≤58-byte value and ≤100-button limits.
- `status()` routes through the active `StreamRenderer` when one exists (showing the transient line); during preparation (no streaming renderer yet) it surfaces the line on a single provisional lead-in message (`leadInMessageId` — created on the first line, edited in place thereafter) and refreshes the typing indicator. `respond()` reclaims that message as the streaming message so the response text replaces it in place, or `finalize()` deletes it when the exchange yields no text. A one-shot `pingTyping()` also fires on receipt of a submitted text/media message (excluding `/stop`) to bridge the gap until `respond()`'s typing refresh loop. (During shutdown the coordinator routes status lines to `shutdownStatus()` instead — see below.)
- `shutdownStatus(text)` is the teardown-time status surface (the streaming renderer is gone by then): it sends one dedicated italic message and edits it in place on each subsequent call, serialized through the send mutex. The coordinator awaits it so the line lands before the process exits; the final "Done" is intentionally left in the chat. The dedicated message id is tracked on the channel (`shutdownMessageId`) and never reused for anything else.
- The `pushNotifications` flag governs pushes on both paths. `deliver()` uses `notifyOnlyLast` (every chunk silent except the last). `respond()` forces a single push on completion: under `pushNotifications` the streamed work-in-progress, the lead-in, and any overflow chunks are sent silently (`disable_notification`), and when the turn lasted at least `pushNotificationMinSeconds` (default 10, configurable under `[extensions.telegram]`) the finalized *last* message is copied within the same chat and the streamed original deleted (`forceNotification` = `copyMessage` + best-effort `deleteMessage`), so the fresh copy fires the one push; the copy's id is what reply routing records (`lastOutboundId`, the `channel_messages` row). The push is skipped for quick turns under the threshold (no flicker while the user is still watching) and when a notifying tool (`send_telegram_file`, `pin_message`, `send_message_with_buttons`) already delivered a push during the turn. A copy-API failure leaves the streamed message in place rather than losing it. With `pushNotifications` off, both paths use default notification behavior and `respond()` never copy-deletes.
- `react_to_message` passes the emoji through rather than validating against Telegram's evolving allowed set — the API rejection is surfaced to the agent.
- The send-file allowed-roots check is a prefix test on resolved paths (`validateFilePath`); symlinks are not canonicalized before the check.
