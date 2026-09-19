import { stat } from "node:fs/promises";
import { basename, extname, isAbsolute, join, resolve, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { InputFile } from "grammy";
import type {
  InlineKeyboardMarkup,
  InputMediaAudio,
  InputMediaDocument,
  InputMediaPhoto,
  InputMediaVideo,
  MessageEntity,
  ReactionTypeEmoji,
} from "grammy/types";
import { type Static, Type } from "typebox";

import type { Logger } from "../../log.ts";
import { buildInlineKeyboard, validateButtons } from "./buttons.ts";
import type { ChannelMessageStore, MessageRouting } from "./channel.ts";
import { toTelegramEntities } from "./entities.ts";
import { messageRef } from "./inbound.ts";
import { sendEntitiesOrFallback } from "./sending.ts";

/**
 * One sendMediaGroup album payload — the groupable InputMedia shapes. Telegram groups
 * photos/videos together while documents and audio group only with their own type, so the
 * union mirrors grammY's sendMediaGroup parameter (minus live photos, never sent here).
 */
type MediaGroupItems =
  | ReadonlyArray<InputMediaPhoto | InputMediaVideo>
  | ReadonlyArray<InputMediaDocument>
  | ReadonlyArray<InputMediaAudio>;

/** Narrow grammY API surface the tools call — fakeable in tests. */
export interface ToolApi {
  sendMessage(
    chatId: number,
    text: string,
    other?: { reply_markup?: InlineKeyboardMarkup; entities?: MessageEntity[] },
  ): Promise<{ message_id: number }>;
  sendPhoto(
    chatId: number,
    photo: InputFile,
    other?: { caption?: string },
  ): Promise<{ message_id: number }>;
  sendAudio(
    chatId: number,
    audio: InputFile,
    other?: { caption?: string },
  ): Promise<{ message_id: number }>;
  sendVideo(
    chatId: number,
    video: InputFile,
    other?: { caption?: string },
  ): Promise<{ message_id: number }>;
  sendDocument(
    chatId: number,
    document: InputFile,
    other?: { caption?: string },
  ): Promise<{ message_id: number }>;
  /** Sends 2-10 same-groupable-type files as one album; resolves with one message per item, in input order. */
  sendMediaGroup(chatId: number, media: MediaGroupItems): Promise<{ message_id: number }[]>;
  setMessageReaction(
    chatId: number,
    messageId: number,
    reaction: ReactionTypeEmoji[],
  ): Promise<unknown>;
  pinChatMessage(
    chatId: number,
    messageId: number,
    other?: { disable_notification?: boolean },
  ): Promise<unknown>;
  unpinChatMessage(chatId: number, messageId: number): Promise<unknown>;
}

export interface ToolDeps {
  api: ToolApi;
  log: Logger;
  chatId: number;
  workspaceRoot: string;
  /** Resolved, deduplicated roots that send_telegram_file accepts. */
  allowedRoots: string[];
  getLastInboundMessageId: () => number | null;
  /**
   * Pin the in-flight response inline and resolve with its message id (or null when there is no
   * response to pin). The channel performs the pin at the `pin_message` tool-start and resolves
   * once the message id is known — so the tool can return the id of what it pinned.
   */
  requestPin: () => Promise<number | null>;
  /** Record/lookup message→branch mappings for reply-to routing. */
  store: Pick<ChannelMessageStore, "record" | "resolve">;
  /** The current trunk routing (live leaf entry + branch), for outbound recording. */
  currentRouting: () => MessageRouting | null;
}

// ---- send_telegram_file -------------------------------------------------------

const MEDIA_TYPES = {
  photo: [".png", ".jpg", ".jpeg", ".gif", ".webp"],
  audio: [".mp3", ".ogg", ".wav", ".flac"],
  video: [".mp4", ".avi", ".mov", ".webm"],
} as const;

type OutboundMediaType = keyof typeof MEDIA_TYPES | "document";

export const detectMediaType = (path: string): OutboundMediaType => {
  const suffix = extname(path).toLowerCase();

  for (const [category, extensions] of Object.entries(MEDIA_TYPES)) {
    if ((extensions as readonly string[]).includes(suffix)) {
      return category as OutboundMediaType;
    }
  }

  return "document";
};

const isWithin = (root: string, path: string): boolean =>
  path === root || path.startsWith(root.endsWith(sep) ? root : `${root}${sep}`);

export const validateFilePath = async (
  filePath: string,
  workspaceRoot: string,
  allowedRoots: string[],
): Promise<string> => {
  const resolved = resolve(isAbsolute(filePath) ? filePath : join(workspaceRoot, filePath));

  let stats: Awaited<ReturnType<typeof stat>>;
  try {
    stats = await stat(resolved);
  } catch {
    throw new Error(`File not found: ${resolved}`);
  }

  if (!stats.isFile()) throw new Error(`Path is not a regular file: ${resolved}`);

  if (!allowedRoots.some((root) => isWithin(root, resolved))) {
    throw new Error(
      `File must be under one of the allowed roots: ${allowedRoots.join(", ")} (got ${resolved})`,
    );
  }

  return resolved;
};

/**
 * Record an outbound tool message against the current trunk routing with a describing label.
 * The store never throws (a recording failure is logged inside it), so this can't break a
 * send that already succeeded. Routing is resolved from the live leaf so a later
 * reply/reaction on the message resolves back to the branch that produced it, and the label
 * lets the reaction recovery quote the message's content from the ledger alone (the session
 * tree can't describe channel-only artifacts).
 */
const recordOutbound = (
  deps: Pick<ToolDeps, "store" | "currentRouting">,
  messageId: number,
  label: string,
): void => {
  const routing = deps.currentRouting();
  if (routing == null) return;
  deps.store.record(String(messageId), routing, "outgoing", { label });
};

const SendFileParams = Type.Object({
  filePath: Type.Union(
    [
      Type.String({
        description:
          "Path to the file — workspace-relative, or absolute under the workspace, " +
          "the system temporary directory, or a configured extra root",
      }),
      Type.Array(Type.String(), {
        description:
          "2-10 file paths delivered as one grouped album: photos and videos group " +
          "together, documents only with documents, audio only with audio",
      }),
    ],
    { description: "File path(s) to send — one file, or a same-type list for one album" },
  ),
  caption: Type.Optional(
    Type.Union(
      [
        Type.String({
          maxLength: 1024,
          description: "Brief description of the file; for an album, shown with the first item",
        }),
        Type.Array(Type.String({ maxLength: 1024 }), {
          description: "One caption per file, mapped positionally onto the file list",
        }),
      ],
      { description: "Brief description of the file — one shared caption, or one per file" },
    ),
  ),
});

/** The single-file send: per-type dispatch, unchanged — one path (bare or one-element list) lands here. */
const sendSingleFile = async (
  deps: Pick<ToolDeps, "api" | "log" | "chatId" | "store" | "currentRouting">,
  resolved: string,
  caption: string | undefined,
): Promise<string> => {
  const file = new InputFile(resolved);
  const other = caption != null ? { caption } : {};

  const mediaType = detectMediaType(resolved);

  let messageId: number;
  switch (mediaType) {
    case "photo":
      messageId = (await deps.api.sendPhoto(deps.chatId, file, other)).message_id;
      break;
    case "audio":
      messageId = (await deps.api.sendAudio(deps.chatId, file, other)).message_id;
      break;
    case "video":
      messageId = (await deps.api.sendVideo(deps.chatId, file, other)).message_id;
      break;
    default:
      messageId = (await deps.api.sendDocument(deps.chatId, file, other)).message_id;
      break;
  }

  // Map the sent file message to the current trunk routing with a describing label so a later
  // reply/reaction on the file resolves to the branch that sent it AND can quote what the file
  // was (mirroring send_message_with_buttons); without this, a reaction on the file is dropped
  // as unresolved.
  const name = basename(resolved);
  recordOutbound(deps, messageId, `${mediaType} ${name}${caption ? ` — ${caption}` : ""}`);

  deps.log.debug(
    { tool: "send_telegram_file", path: resolved, mediaType, messageId },
    "telegram file sent",
  );

  // The id lets the agent tie a later reaction/reply notification (which names the id) to
  // this send without a lookup.
  return `File sent: ${name} ${messageRef(messageId)}`;
};

export const handleSendFile = async (
  deps: Pick<
    ToolDeps,
    "api" | "log" | "chatId" | "workspaceRoot" | "allowedRoots" | "store" | "currentRouting"
  >,
  params: Static<typeof SendFileParams>,
): Promise<string> => {
  deps.log.info({ tool: "send_telegram_file", filePath: params.filePath }, "telegram tool invoked");

  const paths = Array.isArray(params.filePath) ? params.filePath : [params.filePath];
  if (paths.length === 0) {
    throw new Error("filePath must name at least one file to send (got an empty list)");
  }

  if (Array.isArray(params.caption) && params.caption.length !== paths.length) {
    throw new Error(
      `caption has ${params.caption.length} entries but filePath lists ${paths.length} files — ` +
        "provide one shared caption or one caption per file",
    );
  }

  // One caption slot per file: a shared string captions the first item only, an array maps
  // positionally. An empty string means no caption (the same falsy rule the label uses).
  const captions: (string | undefined)[] =
    params.caption == null
      ? paths.map(() => undefined)
      : Array.isArray(params.caption)
        ? params.caption
        : [params.caption, ...paths.slice(1).map(() => undefined)];

  // Resolve and validate every path before the first API call — a media group is sent
  // atomically, so a bad path must never strand a partial album. Failures are aggregated
  // into one error so a single retry can fix them all.
  const failures: Error[] = [];
  const resolved: string[] = [];
  for (const path of paths) {
    try {
      resolved.push(await validateFilePath(path, deps.workspaceRoot, deps.allowedRoots));
    } catch (error) {
      failures.push(error instanceof Error ? error : new Error(String(error)));
    }
  }
  if (failures.length > 0) {
    throw failures.length === 1
      ? failures[0]
      : new Error(failures.map((failure) => failure.message).join("\n"));
  }

  // A single path (bare string or one-element list) keeps the per-type single-file send.
  const single = resolved.length === 1 ? resolved.at(0) : undefined;
  if (single !== undefined) return sendSingleFile(deps, single, captions[0]);

  const types = resolved.map((path) => detectMediaType(path));

  if (resolved.length > 10) {
    throw new Error(
      `Telegram albums hold at most 10 files per message (${resolved.length} given) — ` +
        "send the files in multiple send_telegram_file calls",
    );
  }

  // Telegram's album grouping: photos and videos mix freely; documents and audio group only
  // with their own type.
  const kinds = new Set(types);
  const albumKind: "photo-video" | "document" | "audio" | null = types.every(
    (type) => type === "photo" || type === "video",
  )
    ? "photo-video"
    : kinds.size === 1 && kinds.has("document")
      ? "document"
      : kinds.size === 1 && kinds.has("audio")
        ? "audio"
        : null;

  if (albumKind === null) {
    throw new Error(
      `Telegram albums cannot mix these media types: ${[...kinds].join(", ")} — photos and ` +
        "videos group together, but documents group only with documents and audio only with " +
        "audio; send incompatible files in separate calls",
    );
  }

  const captionField = (caption?: string) => (caption ? { caption } : {});

  const items: MediaGroupItems =
    albumKind === "document"
      ? resolved.map((path, index) => ({
          type: "document" as const,
          media: new InputFile(path),
          ...captionField(captions[index]),
        }))
      : albumKind === "audio"
        ? resolved.map((path, index) => ({
            type: "audio" as const,
            media: new InputFile(path),
            ...captionField(captions[index]),
          }))
        : resolved.map((path, index) => ({
            type: types[index] === "video" ? ("video" as const) : ("photo" as const),
            media: new InputFile(path),
            ...captionField(captions[index]),
          }));

  const messages = await deps.api.sendMediaGroup(deps.chatId, items);

  // Telegram returns one message per album item; a mismatched count would misalign the
  // id→file labels below, so surface it rather than recording wrong labels.
  if (messages.length !== resolved.length) {
    throw new Error(
      `Telegram returned ${messages.length} messages for ${resolved.length} album items`,
    );
  }

  // Every album item is its own Telegram message: record each returned id with its own label
  // so a reply/reaction on any item resolves to the branch that sent it and quotes that file.
  const names = resolved.map((path) => basename(path));
  messages.forEach((message, index) => {
    recordOutbound(
      deps,
      message.message_id,
      `${types[index]} ${names[index]}${captions[index] ? ` — ${captions[index]}` : ""}`,
    );
  });

  deps.log.debug(
    { tool: "send_telegram_file", paths: resolved, count: messages.length },
    "telegram file album sent",
  );

  // The ids let the agent tie a later reaction/reply notification (which names the id) to
  // this send without a lookup.
  return `Files sent: ${names.join(", ")} ${messageRef(...messages.map((m) => m.message_id))}`;
};

// ---- react_to_message ---------------------------------------------------------

const ReactParams = Type.Object({
  emoji: Type.String({
    description: 'Reaction emoji, e.g. "👍" (Telegram supports a fixed set of reaction emoji)',
  }),
  messageId: Type.Optional(
    Type.Number({
      description: "Telegram message ID to react to; defaults to the user's last message",
    }),
  ),
});

export const handleReactToMessage = async (
  deps: Pick<ToolDeps, "api" | "log" | "chatId" | "getLastInboundMessageId">,
  params: Static<typeof ReactParams>,
): Promise<string> => {
  deps.log.info(
    { tool: "react_to_message", emoji: params.emoji, messageId: params.messageId },
    "telegram tool invoked",
  );

  const messageId = params.messageId ?? deps.getLastInboundMessageId();

  if (messageId == null) throw new Error("No message available to react to");

  // Telegram restricts reactions to a fixed emoji set; rather than hardcoding
  // that evolving list, pass the string through and surface the API rejection.
  await deps.api.setMessageReaction(deps.chatId, messageId, [
    { type: "emoji", emoji: params.emoji as ReactionTypeEmoji["emoji"] },
  ]);

  deps.log.debug({ tool: "react_to_message", messageId }, "telegram reaction applied");

  return `Reacted to message ${messageId} with ${params.emoji}`;
};

// ---- pin_message / unpin_message ------------------------------------------------

export const handlePinMessage = async (
  deps: Pick<ToolDeps, "log" | "requestPin">,
): Promise<string> => {
  deps.log.info({ tool: "pin_message" }, "telegram tool invoked");

  // The channel pins the in-flight response inline (at this tool's tool-start, where the message
  // materializes) and resolves with its id — so the tool returns the id of what it pinned, letting
  // a later turn unpin that specific message. Resolves null when there is no response to pin.
  const messageId = await deps.requestPin();
  if (messageId == null) throw new Error("No message available to pin");

  return `Message pinned (ID: ${messageId})`;
};

const UnpinParams = Type.Object({
  messageId: Type.Number({ description: "The Telegram message ID to unpin" }),
});

export const handleUnpinMessage = async (
  deps: Pick<ToolDeps, "api" | "log" | "chatId">,
  params: Static<typeof UnpinParams>,
): Promise<string> => {
  deps.log.info({ tool: "unpin_message", messageId: params.messageId }, "telegram tool invoked");

  await deps.api.unpinChatMessage(deps.chatId, params.messageId);

  deps.log.debug(
    { tool: "unpin_message", messageId: params.messageId },
    "telegram message unpinned",
  );

  return `Message unpinned (ID: ${params.messageId})`;
};

// ---- send_message_with_buttons ---------------------------------------------------

const ButtonsParams = Type.Object({
  prompt: Type.String({ description: "The message text shown above the buttons" }),
  buttons: Type.Array(
    Type.Array(
      Type.Object({
        label: Type.String({ description: "Text shown on the button" }),
        value: Type.String({
          description: "Machine-readable value you receive back on tap (max 58 UTF-8 bytes)",
        }),
      }),
    ),
    { description: "Rows of buttons; each row is a list of {label, value} objects" },
  ),
  singleUse: Type.Optional(
    Type.Boolean({
      description: "Remove the keyboard after the first tap (default true)",
    }),
  ),
});

export const handleSendMessageWithButtons = async (
  deps: Pick<ToolDeps, "api" | "log" | "chatId" | "store" | "currentRouting">,
  params: Static<typeof ButtonsParams>,
): Promise<string> => {
  deps.log.info(
    { tool: "send_message_with_buttons", rows: params.buttons.length },
    "telegram tool invoked",
  );

  validateButtons(params.buttons);

  // Convert the prompt to a Telegram entity payload so agent markdown in the
  // prompt renders, reusing the channel's convert-then-fallback policy (raw text
  // on a render rejection) — best-effort formatting, the message is never lost.
  // Bound to the tool's own ToolApi (not the channel's SendApi) via the send
  // callable, so the inline keyboard rides along in `other` on both attempts.
  const reply_markup = buildInlineKeyboard(params.buttons, params.singleUse ?? true);
  const messageId = await sendEntitiesOrFallback(
    (text, other) => deps.api.sendMessage(deps.chatId, text, other),
    toTelegramEntities(params.prompt),
    { reply_markup },
    deps.log,
  );

  // Map the button message to the current trunk routing with the prompt + choice labels, so a
  // later tap or reaction routes back to the branch that asked the question and can quote it.
  recordOutbound(
    deps,
    messageId,
    `${params.prompt} [${params.buttons
      .flat()
      .map((button) => button.label)
      .join(" | ")}]`,
  );

  deps.log.debug({ tool: "send_message_with_buttons", messageId }, "telegram buttons sent");

  return `Buttons sent (message_id: ${messageId})`;
};

// ---- registration -----------------------------------------------------------------

const SEND_FILE_DESCRIPTION = `Send one or more files to the user via Telegram.

A single path sends one message. Pass 2-10 paths to deliver them as one grouped
album instead of a burst of separate messages. Photos and videos group together;
documents group only with documents and audio only with audio — mixed types are
rejected, so send them in separate calls. An album holds at most 10 files.

caption: a single string captions the whole album (shown on the first item); an
array captions each file in order (same length as the file list).

Supported media types (auto-detected from extension):
- Images (.png, .jpg, .jpeg, .gif, .webp) → sent as photo
- Audio (.mp3, .ogg, .wav, .flac) → sent as audio
- Video (.mp4, .avi, .mov, .webm) → sent as video
- All other files → sent as document

Every file must exist on disk and be a regular file under one of the allowed roots
(the workspace, the system temporary directory, or a configured extra root).
Allowed roots are enumerated in any rejection error. Telegram enforces a 50MB
upload limit per file.`;

const REACT_DESCRIPTION = `React to a Telegram message with an emoji.

Defaults to the user's most recent message when messageId is omitted. Telegram
supports a fixed set of reaction emoji (e.g. 👍 ❤️ 🔥 🎉 🤔 👀); unsupported emoji
are rejected by the API.`;

const PIN_DESCRIPTION = `Pin the most recent response message in the Telegram chat.

The pin always targets the response being produced (never a previous one) and triggers a push
notification so the user sees the pinned message promptly. Returns the pinned message's Telegram
ID — store it so a later turn can unpin that specific message with unpin_message (which needs the
ID). Idempotent: pinning an already-pinned message succeeds.`;

const UNPIN_DESCRIPTION = `Unpin a previously pinned message in the Telegram chat.

Fails when the message ID does not exist or unpinning fails. Idempotent:
unpinning a non-pinned message succeeds.`;

const BUTTONS_DESCRIPTION = `Present a Telegram inline keyboard of tappable buttons in the chat.

buttons is a list of rows; each row is a list of {label, value} buttons. label is
shown on the button; value is the machine-readable identifier you receive back on
tap. When singleUse is true (default) the keyboard is removed from the message
after any button is tapped.

When the user taps a button, you will receive a turn explicitly framed as
"The user tapped the option \`<value>\` out of the options you displayed.",
so you can distinguish taps from typed input. Use this for structured prompts
like yes/no, multiple-choice, or confirm/cancel.

Per-button value must be at most 58 UTF-8 bytes; labels must be non-empty; at
least one row with at least one button is required; at most 100 buttons total.`;

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

/** Run a tool handler, logging a warn with the failure before re-throwing so the tool boundary is traceable. */
const runTool = async (
  log: Logger,
  tool: string,
  params: unknown,
  handler: () => Promise<string>,
) => {
  try {
    return textResult(await handler());
  } catch (error) {
    log.warn({ err: error, tool, params }, "telegram tool failed");
    throw error;
  }
};

export const registerTelegramTools = (pi: ExtensionAPI, deps: ToolDeps): void => {
  pi.registerTool({
    name: "send_telegram_file",
    label: "Send Telegram file",
    description: SEND_FILE_DESCRIPTION,
    promptSnippet: "Send one or more files from disk to the user via Telegram",
    promptGuidelines: [
      "Use send_telegram_file to deliver files (images, audio, video, documents) to the user instead of pasting their contents.",
      "When delivering several related images (diagrams, charts, screenshots), pass all their paths in one send_telegram_file call so they arrive as a single album.",
    ],
    parameters: SendFileParams,
    async execute(_toolCallId, params) {
      return runTool(deps.log, "send_telegram_file", params, () => handleSendFile(deps, params));
    },
  });

  pi.registerTool({
    name: "react_to_message",
    label: "React to message",
    description: REACT_DESCRIPTION,
    promptSnippet: "React to a Telegram message with an emoji",
    promptGuidelines: [
      "Use react_to_message for lightweight acknowledgements (e.g. 👍 on a quick confirmation) instead of a full reply.",
    ],
    parameters: ReactParams,
    async execute(_toolCallId, params) {
      return runTool(deps.log, "react_to_message", params, () =>
        handleReactToMessage(deps, params),
      );
    },
  });

  pi.registerTool({
    name: "pin_message",
    label: "Pin message",
    description: PIN_DESCRIPTION,
    promptSnippet: "Pin the most recent response message in the Telegram chat",
    promptGuidelines: [
      "Use pin_message when the user should be able to find the last response again easily (reminders, important info).",
    ],
    parameters: Type.Object({}),
    async execute() {
      return runTool(deps.log, "pin_message", {}, () => handlePinMessage(deps));
    },
  });

  pi.registerTool({
    name: "unpin_message",
    label: "Unpin message",
    description: UNPIN_DESCRIPTION,
    promptSnippet: "Unpin a previously pinned Telegram message",
    promptGuidelines: ["Use unpin_message when a pinned message is no longer relevant."],
    parameters: UnpinParams,
    async execute(_toolCallId, params) {
      return runTool(deps.log, "unpin_message", params, () => handleUnpinMessage(deps, params));
    },
  });

  pi.registerTool({
    name: "send_message_with_buttons",
    label: "Send message with buttons",
    description: BUTTONS_DESCRIPTION,
    promptSnippet: "Present tappable inline buttons to the user via Telegram",
    promptGuidelines: [
      "Use send_message_with_buttons for structured choices (yes/no, multiple-choice, confirm/cancel) instead of asking the user to type an option.",
    ],
    parameters: ButtonsParams,
    async execute(_toolCallId, params) {
      return runTool(deps.log, "send_message_with_buttons", params, () =>
        handleSendMessageWithButtons(deps, params),
      );
    },
  });
};
