import { join } from "node:path";
import { type Bot, GrammyError, HttpError } from "grammy";
import type { Message } from "grammy/types";

import type { Channel, ChannelRuntime, Delivery, Exchange } from "../../channels/types.ts";
import type { AgentEvent } from "../../domain/agent-events.ts";
import type { InboundMessage } from "../../domain/message.ts";
import type { Logger } from "../../log.ts";
import { unpackCallbackData } from "./buttons.ts";
import { toTelegramEntities } from "./entities.ts";
import {
  mapButtonTap,
  mapMediaMessage,
  mapReaction,
  mapTextMessage,
  replyTargetId,
} from "./inbound.ts";
import {
  buildAttachment,
  downloadMedia,
  generateMediaFilename,
  MediaTooLargeError,
  resolveMedia,
} from "./media.ts";
import { Mutex } from "./mutex.ts";
import type { ChannelMessageDirection } from "./schema.ts";
import {
  deliverText,
  editWithFallback,
  forceNotification,
  sendWithFallback,
  startTyping,
} from "./sending.ts";
import { StreamRenderer } from "./streaming.ts";

/** A Telegram message's place in the daily trunk: which tree entry and which branch produced it. */
export interface MessageRouting {
  treeEntryId: string;
  branchId: string;
}

/** Persists message id → trunk routing so a reply/reaction/button can be force-routed to its branch. */
export interface ChannelMessageStore {
  record(messageId: string, routing: MessageRouting, direction: ChannelMessageDirection): void;
  /** Resolve a recorded message to its trunk routing, or null when unrecorded. */
  resolve(messageId: string): MessageRouting | null;
}

export interface TelegramChannelOptions {
  chatId: number;
  allowMedia: boolean;
  pushNotifications: boolean;
  /** Minimum streamed-response duration (seconds) before a completion push is forced. */
  pushNotificationMinSeconds: number;
  mediaDir: string;
  /** Abort the in-flight agent run — wired from sessions.abortExchange. */
  stop: () => Promise<void>;
  /** Record/lookup message→branch mappings for reply-to routing. */
  store: ChannelMessageStore;
  /**
   * The trunk routing for the message just produced/received (live leaf entry + live branch id), or
   * null when no trunk is active. Read post-append so the tree entry id exists.
   */
  currentRouting: () => MessageRouting | null;
  /** The last user+assistant exchange of a recorded branch, used as reaction/reply context. */
  branchLastExchange: (branchId: string) => string | null;
}

export const STOP_COMMAND = "/stop";
export const STOP_ACKNOWLEDGEMENT = "⏹ Stopped.";

/**
 * Appended to a text/media reply whose target couldn't be resolved, so the agent
 * knows the turn was routed to the current conversation rather than the original one.
 */
export const UNRESOLVED_REPLY_HINT =
  "\n\n(This reply could not be matched to its original conversation, so it is being handled in the current one.)";

/** Notice sent when a reaction targets a message whose owning session can't be found. */
export const UNRESOLVED_REACTION_NOTICE =
  "I couldn't find the conversation that message belongs to, so I couldn't apply your reaction there.";

/**
 * Agent tools that already deliver a notifying message during the turn (a file, a
 * pin, an inline-button message). When one of these runs the user is already
 * pushed, so forcing an extra push on the streamed response would double-notify —
 * the copy-delete is skipped.
 */
const NOTIFYING_TOOLS = new Set(["send_telegram_file", "pin_message", "send_message_with_buttons"]);

export class TelegramChannel implements Channel {
  readonly name = "telegram";

  private readonly bot: Bot;
  private readonly options: TelegramChannelOptions;
  private readonly mutex = new Mutex();
  private runtime: ChannelRuntime | null = null;
  private lastInboundId: number | null = null;
  private lastOutboundId: number | null = null;
  private activeRenderer: StreamRenderer | null = null;
  /**
   * Provisional message shown during preparation (boundary/preprocessor status
   * lines). respond() seeds the StreamRenderer with it so the streamed response
   * edits it in place — or deletes it via finalize when the exchange has no text.
   */
  private leadInMessageId: number | null = null;
  private shutdownMessageId: number | null = null;

  constructor(bot: Bot, options: TelegramChannelOptions) {
    this.bot = bot;
    this.options = options;
  }

  /** Last message received from the user — target for react_to_message. */
  get lastInboundMessageId(): number | null {
    return this.lastInboundId;
  }

  /** Last message this channel sent — target for pin_message. */
  get lastOutboundMessageId(): number | null {
    return this.lastOutboundId;
  }

  async start(runtime: ChannelRuntime): Promise<void> {
    this.runtime = runtime;

    this.bot.on("message", async (ctx) => {
      if (ctx.chat.id !== this.options.chatId) return;

      if (ctx.message.text != null) {
        await this.handleText(ctx.message);
      } else {
        await this.handleMedia(ctx.message);
      }
    });

    this.bot.on("callback_query:data", async (ctx) => {
      await ctx
        .answerCallbackQuery()
        .catch((error) => runtime.log.warn({ err: error }, "callback answer failed"));

      if (ctx.callbackQuery.from.id !== this.options.chatId) {
        runtime.log.debug({ userId: ctx.callbackQuery.from.id }, "dropping unauthorized tap");
        return;
      }

      const unpacked = unpackCallbackData(ctx.callbackQuery.data);
      if (unpacked == null) {
        runtime.log.warn({ data: ctx.callbackQuery.data }, "unrecognized callback data");
        return;
      }

      const messageId = ctx.callbackQuery.message?.message_id ?? null;

      if (unpacked.singleUse && messageId != null) {
        void this.bot.api
          .editMessageReplyMarkup(this.options.chatId, messageId)
          .catch((error) => runtime.log.warn({ err: error }, "keyboard removal failed"));
      }

      // Resolve the tapped button message to its branch. A tap on an earlier branch forces a shift
      // (stamped in metadata for the boundary); an unrecorded target falls through to normal handling.
      const reference = messageId != null ? this.options.store.resolve(String(messageId)) : null;

      runtime.submit(this.applyForcedRouting(mapButtonTap(unpacked.value, messageId), reference));
    });

    this.bot.on("message_reaction", async (ctx) => {
      if (ctx.chat.id !== this.options.chatId) return;
      if (ctx.messageReaction.user?.id !== this.options.chatId) {
        runtime.log.debug(
          { userId: ctx.messageReaction.user?.id },
          "dropping unauthorized reaction",
        );
        return;
      }

      const event = ctx.messageReaction;
      const messageId = String(event.message_id);

      // A reaction's meaning is tied to its target: an unrecorded target is dropped with a notice.
      const reference = this.options.store.resolve(messageId);
      if (reference == null) {
        await this.notifyUnresolvedReaction();
        return;
      }

      // Prepend the referenced branch's last exchange only when the reaction targets an earlier
      // branch; a reaction on the live branch is already in the agent's context.
      const lastExchange =
        reference.branchId !== this.currentBranchId()
          ? this.options.branchLastExchange(reference.branchId)
          : null;

      const inbound = mapReaction(event, { lastExchange });
      if (inbound == null) return;

      runtime.submit(this.applyForcedRouting(inbound, reference));
    });

    this.bot.catch(({ error }) => {
      if (error instanceof GrammyError) {
        runtime.log.error(
          { err: error, errorCode: error.error_code, description: error.description },
          "telegram api rejected request",
        );
      } else if (error instanceof HttpError) {
        runtime.log.error({ err: error }, "could not reach telegram");
      } else {
        runtime.log.error({ err: error }, "telegram update handling failed");
      }
    });

    // init() validates the token via getMe before going live; start() resolves
    // only when polling stops, so it runs detached.
    await this.bot.init();

    // Surface the channel-agnostic prefix commands in Telegram's command menu so
    // they're discoverable; the coordinator parses the prefixes (see submit()).
    await this.bot.api
      .setMyCommands([
        { command: "new", description: "Start a new conversation, ignoring the current topic" },
        {
          command: "queue",
          description: "Queue a message for the next turn instead of interrupting",
        },
      ])
      .catch((error) => runtime.log.warn({ err: error }, "setting bot commands failed"));

    void this.bot
      .start({
        allowed_updates: ["message", "callback_query", "message_reaction"],
        onStart: (me) => runtime.log.info({ username: me.username }, "telegram bot polling"),
      })
      .catch((error) => runtime.log.error({ err: error }, "telegram polling crashed"));
  }

  /**
   * Stamp a referenced branch onto an inbound message so the boundary middleware can force the
   * outcome (same branch → append, earlier branch → forced shift + context injection), bypassing the
   * topic classifier. An unrecorded reply target carries no forced metadata and falls through to
   * normal detection; a text/media reply to an unrecorded target is annotated with a hint.
   */
  private applyForcedRouting(
    message: InboundMessage,
    reference: MessageRouting | null,
  ): InboundMessage {
    if (reference != null) {
      return {
        ...message,
        metadata: {
          ...message.metadata,
          forcedBranchId: reference.branchId,
          forcedTreeEntryId: reference.treeEntryId,
        },
      };
    }

    // No recorded routing for the target. A reaction's meaning is tied to its target and is dropped
    // upstream; a reply with no resolvable target still carries value, annotated with a hint so the
    // agent knows it landed in the current conversation.
    const replyToMessageId = message.metadata.replyToMessageId;
    if (typeof replyToMessageId !== "string" || message.metadata.reaction === true) return message;

    const text = message.text
      ? `${message.text}${UNRESOLVED_REPLY_HINT}`
      : UNRESOLVED_REPLY_HINT.trim();
    return { ...message, text };
  }

  /** The branch id of the live trunk leaf, or null when no trunk is active. */
  private currentBranchId(): string | null {
    return this.options.currentRouting()?.branchId ?? null;
  }

  /**
   * Whether to suppress the reply quote: only when the reply targets the live branch (already in the
   * agent's context). An unrecorded or earlier-branch target keeps the quote so context survives.
   */
  private shouldSkipQuote(message: Pick<Message, "reply_to_message">): boolean {
    const target = replyTargetId(message);
    if (target == null) return false;

    const reference = this.options.store.resolve(target);
    return reference != null && reference.branchId === this.currentBranchId();
  }

  async respond({ message, events }: Exchange): Promise<void> {
    const log = this.log();

    // The mutex is held only for the seed handoff and finalize — not for the entire
    // streaming loop. This keeps the lock free during before_agent_start so that
    // preparation-phase status calls (e.g. skill detection) can send their lead-in
    // messages without queueing behind respond(). status() sends that lead-in
    // fire-and-forget, so initStreamingRenderer claims the seed under the same mutex —
    // serialized behind any in-flight lead-in send — so the streamed response always
    // reclaims it (or deletes it on a no-text turn) instead of orphaning it.
    const startedAt = Date.now();
    let notifyingToolUsed = false;

    // Initialize the renderer on first event (agent_start), under a brief mutex hold.
    // Returns null if no events arrive (e.g. aborted before agent_start).
    const initResult = await this.initStreamingRenderer(events);
    if (initResult == null) return;

    const { renderer, remainingEvents, stopTyping } = initResult;

    try {
      for await (const event of remainingEvents) {
        switch (event.kind) {
          case "text":
            await renderer.appendText(event.text);
            break;

          case "tool-start":
            // Tools that already push (file/pin/buttons) make a completion push
            // redundant — skip the copy-delete so the user isn't double-notified.
            if (NOTIFYING_TOOLS.has(event.toolName)) notifyingToolUsed = true;
            await renderer.appendTool(event.toolName, event.args);
            break;

          case "status":
            await renderer.showTransient(event.text);
            break;

          case "error":
            await this.sendErrorNotice(event.message, event.recoverable, log);
            break;

          case "result":
            if (event.result != null) {
              log.info(
                {
                  sessionId: event.sessionId,
                  costUsd: event.result.costUsd,
                  tokens: event.result.usage.totalTokens,
                },
                "exchange complete",
              );
            }
            break;

          default:
            break;
        }
      }
    } finally {
      this.activeRenderer = null;
      stopTyping();
    }

    const outboundId = await this.finalizeResponse(renderer, startedAt, notifyingToolUsed, log);

    // Routing has settled by now: map both the user's message and the bot's reply to the trunk leaf
    // entry + live branch so a future reply/reaction/button can resolve them to a branch.
    const routing = this.options.currentRouting();
    if (routing != null) {
      const inboundId = message.metadata.messageId;
      if (typeof inboundId === "number") {
        this.recordMessage(String(inboundId), routing, "incoming");
      }

      if (outboundId != null) this.recordMessage(String(outboundId), routing, "outgoing");
    }
  }

  /**
   * Consume the first event from the stream and use it to initialize the streaming
   * renderer under a brief mutex hold. Returns null if no events arrive (e.g.
   * aborted before agent_start). The lead-in message (if any) is claimed as the
   * renderer's seed so the streamed response edits it in place.
   */
  private async initStreamingRenderer(events: AsyncIterable<AgentEvent>): Promise<{
    renderer: StreamRenderer;
    remainingEvents: AsyncIterable<AgentEvent>;
    stopTyping: () => void;
  } | null> {
    const iterator = events[Symbol.asyncIterator]();
    const first = await iterator.next();
    if (first.done) return null;

    // Re-wrap the remaining events as a new iterable.
    const remaining: AsyncIterable<AgentEvent> = {
      [Symbol.asyncIterator]: () => iterator,
    };

    const log = this.log();
    const stopTyping = startTyping(this.bot.api, this.options.chatId, log);

    // Claim the lead-in (and build the renderer) under the send mutex. status() sends the
    // preparation lead-in fire-and-forget (showLeadIn is async and not awaited by status()),
    // so a lead-in send can still be in flight when agent_start arrives — e.g. when skill
    // classification resolves faster than the lead-in API call. Claiming here, serialized
    // behind showLeadIn (FIFO mutex), guarantees we see the settled lead-in id (if any)
    // rather than racing past it and orphaning the message after the streamed response.
    const renderer = await this.mutex.run(async () => {
      const seedMessageId = this.leadInMessageId;
      this.leadInMessageId = null;
      const created = new StreamRenderer(
        this.bot.api,
        this.options.chatId,
        log,
        seedMessageId,
        this.options.pushNotifications,
      );
      this.activeRenderer = created;
      return created;
    });

    return { renderer, remainingEvents: remaining, stopTyping };
  }

  private async finalizeResponse(
    renderer: StreamRenderer,
    startedAt: number,
    notifyingToolUsed: boolean,
    log: Logger,
  ): Promise<number | null> {
    return this.mutex.run(async () => {
      const id = await renderer.finalize();

      // Telegram edits never notify, so a streamed response — edited in place as
      // it arrives — never fires a push. Force one on completion by copying the
      // finalized message (a fresh send that notifies) and deleting the streamed
      // original, but only when the turn ran long enough that the user likely
      // stepped away (quick turns skip it to avoid a flicker while they're still
      // watching) and no notifying tool already pushed. For a multi-message
      // response only the final chunk is copied — it carries the push and its id is
      // what reply routing records; a failure leaves the streamed message in place.
      const elapsedSeconds = (Date.now() - startedAt) / 1000;
      const shouldPush =
        this.options.pushNotifications &&
        !notifyingToolUsed &&
        id != null &&
        elapsedSeconds >= this.options.pushNotificationMinSeconds;

      let outboundId = id;
      if (shouldPush && id != null) {
        try {
          outboundId = await forceNotification(this.bot.api, this.options.chatId, id, log);
        } catch (error) {
          log.warn(
            { err: error },
            "push notification copy-delete failed; delivered without a push",
          );
        }
      }

      if (outboundId != null) this.lastOutboundId = outboundId;

      return outboundId;
    });
  }

  private recordMessage(
    messageId: string,
    routing: MessageRouting,
    direction: ChannelMessageDirection,
  ): void {
    try {
      this.options.store.record(messageId, routing, direction);
    } catch (error) {
      this.log().warn({ err: error, messageId, direction }, "recording channel message failed");
    }
  }

  async deliver(delivery: Delivery): Promise<void> {
    await this.mutex.run(async () => {
      const lastId = await deliverText(
        this.bot.api,
        this.options.chatId,
        delivery.text,
        this.options.pushNotifications,
      );

      if (lastId != null) this.lastOutboundId = lastId;
    });
  }

  status(text: string): void {
    const renderer = this.activeRenderer;

    if (renderer != null) {
      void renderer
        .showTransient(text)
        .catch((error) => this.log().debug({ err: error }, "status rendering failed"));
      return;
    }

    // Preparation phase (no streaming renderer yet): keep the typing indicator
    // alive and surface the line on a provisional lead-in message that respond()
    // hands to the StreamRenderer — so the streamed response replaces it (or
    // deletes it when the exchange yields no text).
    this.pingTyping();
    void this.showLeadIn(text);
  }

  /** One-shot typing action to signal liveness on receipt or during preparation. */
  private pingTyping(): void {
    void this.bot.api
      .sendChatAction(this.options.chatId, "typing")
      .catch((error) => this.log().debug({ err: error }, "typing action failed"));
  }

  /**
   * Send `_${text}_` as a dedicated message, or edit the existing one in place —
   * the shared create-then-edit core of the preparation lead-in and shutdown
   * progress. Returns the message id to keep tracking (a new id on first send,
   * the same id on edit).
   */
  private async upsertDedicatedMessage(
    messageId: number | null,
    text: string,
  ): Promise<number | null> {
    const display = `_${text}_`;
    if (messageId == null) {
      // Silent under push notifications: this serves the preparation lead-in (later
      // reclaimed into the streamed response, which forces its own push on
      // completion) and shutdown status (informational, no push wanted). With push
      // notifications off, default notification behavior applies.
      return sendWithFallback(this.bot.api, this.options.chatId, toTelegramEntities(display), {
        silent: this.options.pushNotifications,
      });
    }
    await editWithFallback(
      this.bot.api,
      this.options.chatId,
      messageId,
      toTelegramEntities(display),
      this.log(),
    );
    return messageId;
  }

  /**
   * Surface a preparation status line on a single provisional message, created on
   * the first call and edited in place thereafter. Serialized through the send
   * mutex so it orders cleanly behind any in-flight send; initStreamingRenderer
   * claims this id under that same mutex, so the streamed response reclaims it (or
   * deletes it on a no-text turn) and the lead-in never lingers across an exchange.
   */
  private async showLeadIn(text: string): Promise<void> {
    await this.mutex.run(async () => {
      try {
        this.leadInMessageId = await this.upsertDedicatedMessage(this.leadInMessageId, text);
      } catch (error) {
        this.log().debug({ err: error }, "lead-in status failed");
      }
    });
  }

  /**
   * Render shutdown-sequence progress on one dedicated italic message, edited
   * in place across calls. Runs under the mutex so it serializes with the final
   * delivery flush, and is awaited by the coordinator so the update lands before
   * the process exits. The last line ("Done") is intentionally left in the chat.
   */
  async shutdownStatus(text: string): Promise<void> {
    await this.mutex.run(async () => {
      try {
        this.shutdownMessageId = await this.upsertDedicatedMessage(this.shutdownMessageId, text);
      } catch (error) {
        this.log().warn({ err: error }, "shutdown status update failed");
      }
    });
  }

  async stop(): Promise<void> {
    if (this.bot.isRunning()) await this.bot.stop();
  }

  private async handleText(message: Message): Promise<void> {
    if (message.text?.trim() === STOP_COMMAND) {
      this.lastInboundId = message.message_id;
      await this.handleStop();
      return;
    }

    const inbound = mapTextMessage(message, { skipQuote: this.shouldSkipQuote(message) });
    if (inbound == null) return;

    this.log().debug(
      { messageId: message.message_id, isReply: message.reply_to_message != null },
      "telegram text message received",
    );

    this.lastInboundId = message.message_id;
    // Bridge the gap until respond()'s typing loop takes over.
    this.pingTyping();
    this.runtimeOrThrow().submit(
      this.applyForcedRouting(inbound, this.resolveReplyTarget(message)),
    );
  }

  /** The trunk routing for a message's reply target, or null when not a reply or unrecorded. */
  private resolveReplyTarget(message: Pick<Message, "reply_to_message">): MessageRouting | null {
    const target = replyTargetId(message);
    return target != null ? this.options.store.resolve(target) : null;
  }

  /** Abort the in-flight run instead of submitting — "/stop" never reaches the agent. */
  private async handleStop(): Promise<void> {
    const log = this.log();

    try {
      await this.options.stop();
    } catch (error) {
      log.warn({ err: error }, "exchange abort failed");
    }

    await this.bot.api
      .sendMessage(this.options.chatId, STOP_ACKNOWLEDGEMENT)
      .catch((error) => log.warn({ err: error }, "stop acknowledgement failed"));
  }

  private async handleMedia(message: Message): Promise<void> {
    const log = this.log();

    if (!this.options.allowMedia) {
      log.debug("ignoring media message — allowMedia is disabled");
      return;
    }

    const resolved = resolveMedia(message);
    if (resolved == null) {
      log.debug("ignoring unresolvable media message");
      return;
    }

    this.lastInboundId = message.message_id;
    // Bridge the download latency until respond()'s typing loop takes over.
    this.pingTyping();

    try {
      const destPath = join(this.options.mediaDir, generateMediaFilename(resolved));

      log.debug(
        { messageId: message.message_id, kind: resolved.kind, destPath },
        "downloading telegram media",
      );

      await downloadMedia(this.bot.api, this.bot.token, resolved, destPath);

      this.runtimeOrThrow().submit(
        this.applyForcedRouting(
          mapMediaMessage(message, buildAttachment(resolved, destPath), {
            skipQuote: this.shouldSkipQuote(message),
          }),
          this.resolveReplyTarget(message),
        ),
      );
    } catch (error) {
      log.warn({ err: error }, "media download failed");

      const notice =
        error instanceof MediaTooLargeError
          ? error.message
          : "Failed to download the file. Please try again.";

      await this.bot.api
        .sendMessage(this.options.chatId, notice)
        .catch((sendError) => log.warn({ err: sendError }, "media failure notice failed"));
    }
  }

  private async sendErrorNotice(message: string, recoverable: boolean, log: Logger): Promise<void> {
    const notice = recoverable
      ? `⚠️ Error: ${message}`
      : `⚠️ Error: ${message}\n\nThis needs your attention — the next message won't recover on its own.`;

    await this.bot.api
      .sendMessage(this.options.chatId, notice)
      .catch((error) => log.warn({ err: error }, "error notice failed"));
  }

  /** Tell the user a reaction couldn't be matched to a conversation and was dropped. */
  private async notifyUnresolvedReaction(): Promise<void> {
    await this.bot.api
      .sendMessage(this.options.chatId, UNRESOLVED_REACTION_NOTICE)
      .catch((error) => this.log().warn({ err: error }, "unresolved reaction notice failed"));
  }

  private runtimeOrThrow(): ChannelRuntime {
    if (this.runtime == null) throw new Error("telegram channel not started");

    return this.runtime;
  }

  private log(): Logger {
    return this.runtimeOrThrow().log;
  }
}
