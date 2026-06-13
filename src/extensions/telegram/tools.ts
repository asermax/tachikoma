import { stat } from "node:fs/promises";
import { basename, extname, isAbsolute, join, resolve, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { InputFile } from "grammy";
import type { InlineKeyboardMarkup, ReactionTypeEmoji } from "grammy/types";
import { type Static, Type } from "typebox";

import { buildInlineKeyboard, validateButtons } from "./buttons.ts";
import type { ChannelMessageStore } from "./channel.ts";

/** Narrow grammY API surface the tools call — fakeable in tests. */
export interface ToolApi {
  sendMessage(
    chatId: number,
    text: string,
    other?: { reply_markup?: InlineKeyboardMarkup },
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
  chatId: number;
  workspaceRoot: string;
  /** Resolved, deduplicated roots that send_telegram_file accepts. */
  allowedRoots: string[];
  getLastInboundMessageId: () => number | null;
  getLastOutboundMessageId: () => number | null;
  /** Record/lookup message↔session mappings for reply-to routing. */
  store: ChannelMessageStore;
  /** Id of the session currently receiving messages, for outbound recording. */
  currentSessionId: () => number | null;
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

const SendFileParams = Type.Object({
  filePath: Type.String({
    description:
      "Path to the file — workspace-relative, or absolute under the workspace, " +
      "the system temporary directory, or a configured extra root",
  }),
  caption: Type.Optional(
    Type.String({ maxLength: 1024, description: "Brief description of the file" }),
  ),
});

export const handleSendFile = async (
  deps: Pick<ToolDeps, "api" | "chatId" | "workspaceRoot" | "allowedRoots">,
  params: Static<typeof SendFileParams>,
): Promise<string> => {
  const resolved = await validateFilePath(params.filePath, deps.workspaceRoot, deps.allowedRoots);
  const file = new InputFile(resolved);
  const other = params.caption != null ? { caption: params.caption } : {};

  switch (detectMediaType(resolved)) {
    case "photo":
      await deps.api.sendPhoto(deps.chatId, file, other);
      break;
    case "audio":
      await deps.api.sendAudio(deps.chatId, file, other);
      break;
    case "video":
      await deps.api.sendVideo(deps.chatId, file, other);
      break;
    default:
      await deps.api.sendDocument(deps.chatId, file, other);
      break;
  }

  return `File sent: ${basename(resolved)}`;
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
  deps: Pick<ToolDeps, "api" | "chatId" | "getLastInboundMessageId">,
  params: Static<typeof ReactParams>,
): Promise<string> => {
  const messageId = params.messageId ?? deps.getLastInboundMessageId();

  if (messageId == null) throw new Error("No message available to react to");

  // Telegram restricts reactions to a fixed emoji set; rather than hardcoding
  // that evolving list, pass the string through and surface the API rejection.
  await deps.api.setMessageReaction(deps.chatId, messageId, [
    { type: "emoji", emoji: params.emoji as ReactionTypeEmoji["emoji"] },
  ]);

  return `Reacted to message ${messageId} with ${params.emoji}`;
};

// ---- pin_message / unpin_message ------------------------------------------------

export const handlePinMessage = async (
  deps: Pick<ToolDeps, "api" | "chatId" | "getLastOutboundMessageId">,
): Promise<string> => {
  const messageId = deps.getLastOutboundMessageId();

  if (messageId == null) throw new Error("No message available to pin");

  // An audible pin is the point: it delivers the push notification.
  await deps.api.pinChatMessage(deps.chatId, messageId, { disable_notification: false });

  return `Message pinned (ID: ${messageId})`;
};

const UnpinParams = Type.Object({
  messageId: Type.Number({ description: "The Telegram message ID to unpin" }),
});

export const handleUnpinMessage = async (
  deps: Pick<ToolDeps, "api" | "chatId">,
  params: Static<typeof UnpinParams>,
): Promise<string> => {
  await deps.api.unpinChatMessage(deps.chatId, params.messageId);

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
  deps: Pick<ToolDeps, "api" | "chatId" | "store" | "currentSessionId">,
  params: Static<typeof ButtonsParams>,
): Promise<string> => {
  validateButtons(params.buttons);

  const sent = await deps.api.sendMessage(deps.chatId, params.prompt, {
    reply_markup: buildInlineKeyboard(params.buttons, params.singleUse ?? true),
  });

  // Map the prompt to the current session so a reply (or tap) routes back to
  // the session that asked the question, mirroring regular outbound recording.
  const sessionId = deps.currentSessionId();
  if (sessionId != null) deps.store.record(String(sent.message_id), sessionId, "outgoing");

  return `Buttons sent (message_id: ${sent.message_id})`;
};

// ---- registration -----------------------------------------------------------------

const SEND_FILE_DESCRIPTION = `Send a file to the user via Telegram.

Supported media types (auto-detected from extension):
- Images (.png, .jpg, .jpeg, .gif, .webp) → sent as photo
- Audio (.mp3, .ogg, .wav, .flac) → sent as audio
- Video (.mp4, .avi, .mov, .webm) → sent as video
- All other files → sent as document

The file must exist on disk and be a regular file under one of the allowed roots
(the workspace, the system temporary directory, or a configured extra root).
Allowed roots are enumerated in any rejection error. Telegram enforces a 50MB
upload limit.`;

const REACT_DESCRIPTION = `React to a Telegram message with an emoji.

Defaults to the user's most recent message when messageId is omitted. Telegram
supports a fixed set of reaction emoji (e.g. 👍 ❤️ 🔥 🎉 🤔 👀); unsupported emoji
are rejected by the API.`;

const PIN_DESCRIPTION = `Pin the most recent response message in the Telegram chat.

The pin triggers a push notification so the user sees the pinned message promptly.
Returns the pinned message's Telegram ID on success. Fails when no response has
been sent yet or when pinning fails. Idempotent: pinning an already-pinned message
succeeds.`;

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

export const registerTelegramTools = (pi: ExtensionAPI, deps: ToolDeps): void => {
  pi.registerTool({
    name: "send_telegram_file",
    label: "Send Telegram file",
    description: SEND_FILE_DESCRIPTION,
    promptSnippet: "Send a file from disk to the user via Telegram",
    promptGuidelines: [
      "Use send_telegram_file to deliver files (images, audio, video, documents) to the user instead of pasting their contents.",
    ],
    parameters: SendFileParams,
    async execute(_toolCallId, params) {
      return textResult(await handleSendFile(deps, params));
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
      return textResult(await handleReactToMessage(deps, params));
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
      return textResult(await handlePinMessage(deps));
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
      return textResult(await handleUnpinMessage(deps, params));
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
      return textResult(await handleSendMessageWithButtons(deps, params));
    },
  });
};
