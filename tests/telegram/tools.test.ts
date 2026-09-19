import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { InputFile } from "grammy";
import type { Mock } from "vitest";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import {
  packCallbackData,
  unpackCallbackData,
  validateButtons,
} from "../../src/extensions/telegram/buttons.ts";
import { toTelegramEntities } from "../../src/extensions/telegram/entities.ts";
import {
  detectMediaType,
  handlePinMessage,
  handleReactToMessage,
  handleSendFile,
  handleSendMessageWithButtons,
  handleUnpinMessage,
  registerTelegramTools,
  type ToolApi,
  type ToolDeps,
} from "../../src/extensions/telegram/tools.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

const fakeApi = () =>
  ({
    sendMessage: vi.fn().mockResolvedValue({ message_id: 11 }),
    sendPhoto: vi.fn().mockResolvedValue({ message_id: 12 }),
    sendAudio: vi.fn().mockResolvedValue({ message_id: 13 }),
    sendVideo: vi.fn().mockResolvedValue({ message_id: 14 }),
    sendDocument: vi.fn().mockResolvedValue({ message_id: 15 }),
    sendMediaGroup: vi.fn().mockResolvedValue([{ message_id: 16 }, { message_id: 17 }]),
    setMessageReaction: vi.fn().mockResolvedValue(true),
    pinChatMessage: vi.fn().mockResolvedValue(true),
    unpinChatMessage: vi.fn().mockResolvedValue(true),
  }) satisfies ToolApi;

/** The album items one sendMediaGroup call received — type + InputFile-inferred filename, in order. */
const sentAlbumItems = (api: ToolApi) => {
  const media = (api.sendMediaGroup as Mock).mock.calls[0]?.[1] as
    | { type: string; media: InputFile; caption?: string }[]
    | undefined;
  return media ?? [];
};

describe("detectMediaType", () => {
  it("detects categories from the extension, case-insensitively", () => {
    expect(detectMediaType("/a/pic.PNG")).toBe("photo");
    expect(detectMediaType("/a/song.mp3")).toBe("audio");
    expect(detectMediaType("/a/clip.MOV")).toBe("video");
    expect(detectMediaType("/a/notes.txt")).toBe("document");
    expect(detectMediaType("/a/no-extension")).toBe("document");
  });
});

describe("handleSendFile", () => {
  let workspace: string;
  let outside: string;

  beforeAll(async () => {
    workspace = await mkdtemp(join(tmpdir(), "tachi-telegram-ws-"));
    outside = await mkdtemp(join(tmpdir(), "tachi-telegram-out-"));

    await writeFile(join(workspace, "pic.png"), "fake image");
    await writeFile(join(workspace, "pic2.png"), "fake image two");
    await writeFile(join(workspace, "notes.txt"), "fake notes");
    await writeFile(join(workspace, "notes2.txt"), "fake notes two");
    await writeFile(join(workspace, "song.mp3"), "fake audio");
    await writeFile(join(workspace, "song2.mp3"), "fake audio two");
    await writeFile(join(workspace, "clip.mp4"), "fake video");
    await writeFile(join(outside, "secret.txt"), "nope");
  });

  afterAll(async () => {
    await rm(workspace, { recursive: true, force: true });
    await rm(outside, { recursive: true, force: true });
  });

  const deps = (api: ToolApi) => ({
    api,
    log: fakeLog,
    chatId: 42,
    workspaceRoot: workspace,
    allowedRoots: [workspace],
    store: { record: vi.fn(), resolve: vi.fn(() => null) },
    // No active trunk by default so existing cases don't trigger recording.
    currentRouting: () => null,
  });

  it("sends images as photos with the caption", async () => {
    const api = fakeApi();

    const result = await handleSendFile(deps(api), {
      filePath: join(workspace, "pic.png"),
      caption: "a chart",
    });

    expect(result).toBe("File sent: pic.png (message_id: 12)");
    expect(api.sendPhoto).toHaveBeenCalledWith(42, expect.anything(), { caption: "a chart" });
    expect(api.sendDocument).not.toHaveBeenCalled();
  });

  it("resolves workspace-relative paths and falls back to document", async () => {
    const api = fakeApi();

    await handleSendFile(deps(api), { filePath: "notes.txt" });

    expect(api.sendDocument).toHaveBeenCalledWith(42, expect.anything(), {});
  });

  it("sends audio files as audio", async () => {
    const api = fakeApi();

    await handleSendFile(deps(api), { filePath: "song.mp3" });

    expect(api.sendAudio).toHaveBeenCalledWith(42, expect.anything(), {});
    expect(api.sendDocument).not.toHaveBeenCalled();
  });

  it("sends video files as video", async () => {
    const api = fakeApi();

    await handleSendFile(deps(api), { filePath: "clip.mp4" });

    expect(api.sendVideo).toHaveBeenCalledWith(42, expect.anything(), {});
    expect(api.sendDocument).not.toHaveBeenCalled();
  });

  it("rejects missing files", async () => {
    await expect(handleSendFile(deps(fakeApi()), { filePath: "missing.txt" })).rejects.toThrow(
      /File not found/,
    );
  });

  it("accepts an allowed root that already ends with a path separator", async () => {
    const api = fakeApi();

    await handleSendFile(
      {
        api,
        log: fakeLog,
        chatId: 42,
        workspaceRoot: workspace,
        allowedRoots: [`${workspace}${sep}`],
        store: { record: vi.fn(), resolve: vi.fn(() => null) },
        currentRouting: () => null,
      },
      { filePath: "notes.txt" },
    );

    expect(api.sendDocument).toHaveBeenCalled();
  });

  it("rejects directories that are not regular files", async () => {
    const api = fakeApi();

    await expect(handleSendFile(deps(api), { filePath: "." })).rejects.toThrow(
      /not a regular file/,
    );
    expect(api.sendDocument).not.toHaveBeenCalled();
  });

  it("rejects files outside the allowed roots", async () => {
    const api = fakeApi();

    await expect(
      handleSendFile(deps(api), { filePath: join(outside, "secret.txt") }),
    ).rejects.toThrow(/allowed roots/);
    expect(api.sendDocument).not.toHaveBeenCalled();
  });

  it("records the sent file message against the current branch routing", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendFile(
      {
        api,
        log: fakeLog,
        chatId: 42,
        workspaceRoot: workspace,
        allowedRoots: [workspace],
        store: { record, resolve: vi.fn(() => null) },
        currentRouting: () => ({ treeEntryId: "entry-1", branchId: "topic-1" }),
      },
      { filePath: "pic.png" },
    );

    // sendPhoto returns message_id: 12 in the fake api; the file message maps to the live branch
    // with a label naming what was sent (media type + filename) for reaction-quote recovery.
    expect(record).toHaveBeenCalledWith(
      "12",
      { treeEntryId: "entry-1", branchId: "topic-1" },
      "outgoing",
      { label: "photo pic.png" },
    );
  });

  it("includes the caption in the recorded label", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendFile(
      {
        api,
        log: fakeLog,
        chatId: 42,
        workspaceRoot: workspace,
        allowedRoots: [workspace],
        store: { record, resolve: vi.fn(() => null) },
        currentRouting: () => ({ treeEntryId: "entry-1", branchId: "topic-1" }),
      },
      { filePath: "pic.png", caption: "a chart" },
    );

    expect(record).toHaveBeenCalledWith(
      "12",
      { treeEntryId: "entry-1", branchId: "topic-1" },
      "outgoing",
      { label: "photo pic.png — a chart" },
    );
  });

  it("skips recording when no trunk is active", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendFile(
      {
        api,
        log: fakeLog,
        chatId: 42,
        workspaceRoot: workspace,
        allowedRoots: [workspace],
        store: { record, resolve: vi.fn(() => null) },
        currentRouting: () => null,
      },
      { filePath: "notes.txt" },
    );

    expect(record).not.toHaveBeenCalled();
  });

  it("dispatches a one-element array like a bare path", async () => {
    const api = fakeApi();

    const result = await handleSendFile(deps(api), { filePath: ["pic.png"], caption: "a chart" });

    expect(result).toBe("File sent: pic.png (message_id: 12)");
    expect(api.sendPhoto).toHaveBeenCalledWith(42, expect.anything(), { caption: "a chart" });
    expect(api.sendMediaGroup).not.toHaveBeenCalled();
  });

  it("sends multiple images as one album in input order, shared caption on the first item", async () => {
    const api = fakeApi();

    const result = await handleSendFile(deps(api), {
      filePath: ["pic.png", "pic2.png"],
      caption: "the diagrams",
    });

    expect(api.sendMediaGroup).toHaveBeenCalledTimes(1);
    const items = sentAlbumItems(api);
    expect(items.map((item) => [item.type, item.media.filename])).toEqual([
      ["photo", "pic.png"],
      ["photo", "pic2.png"],
    ]);
    expect(items[0].media).toBeInstanceOf(InputFile);
    expect(items[0].caption).toBe("the diagrams");
    expect(items[1].caption).toBeUndefined();
    expect(result).toBe("Files sent: pic.png, pic2.png (message_id: 16, 17)");
    expect(api.sendPhoto).not.toHaveBeenCalled();
  });

  it("groups photos and videos in one album", async () => {
    const api = fakeApi();

    await handleSendFile(deps(api), { filePath: ["pic.png", "clip.mp4"] });

    expect(api.sendMediaGroup).toHaveBeenCalledTimes(1);
    expect(sentAlbumItems(api).map((item) => item.type)).toEqual(["photo", "video"]);
  });

  it("maps a caption array positionally onto the album items", async () => {
    const api = fakeApi();

    await handleSendFile(deps(api), {
      filePath: ["pic.png", "pic2.png"],
      caption: ["first cap", ""],
    });

    const items = sentAlbumItems(api);
    expect(items[0].caption).toBe("first cap");
    // An empty caption behaves as no caption for that item.
    expect(items[1].caption).toBeUndefined();
  });

  it("sends duplicate paths as distinct album items", async () => {
    const api = fakeApi();

    await handleSendFile(deps(api), { filePath: ["pic.png", "pic.png"] });

    expect(api.sendMediaGroup).toHaveBeenCalledTimes(1);
    expect(sentAlbumItems(api).map((item) => item.media.filename)).toEqual(["pic.png", "pic.png"]);
  });

  it("groups documents and audio in same-type albums", async () => {
    const api = fakeApi();

    await handleSendFile(deps(api), { filePath: ["notes.txt", "notes2.txt"] });
    expect(sentAlbumItems(api).map((item) => item.type)).toEqual(["document", "document"]);

    await handleSendFile(deps(api), { filePath: ["song.mp3", "song2.mp3"] });
    expect(
      (api.sendMediaGroup as Mock).mock.calls[1]?.[1].map((item: { type: string }) => item.type),
    ).toEqual(["audio", "audio"]);
  });

  it("rejects more than 10 files before any send", async () => {
    const api = fakeApi();

    await expect(
      handleSendFile(deps(api), { filePath: Array.from({ length: 11 }, () => "pic.png") }),
    ).rejects.toThrow(/at most 10 files/);
    expect(api.sendMediaGroup).not.toHaveBeenCalled();
  });

  it("rejects media mixes Telegram cannot group", async () => {
    const api = fakeApi();

    await expect(handleSendFile(deps(api), { filePath: ["pic.png", "notes.txt"] })).rejects.toThrow(
      /cannot mix these media types/,
    );

    // The easily-missed pairing: neither documents nor audio group with anything but their own type.
    await expect(
      handleSendFile(deps(api), { filePath: ["song.mp3", "notes.txt"] }),
    ).rejects.toThrow(/cannot mix these media types/);

    expect(api.sendMediaGroup).not.toHaveBeenCalled();
  });

  it("aggregates every failing path into one error before any send", async () => {
    const api = fakeApi();

    const error = await handleSendFile(deps(api), {
      filePath: ["pic.png", "missing.txt", join(outside, "secret.txt")],
    }).catch((failure: Error) => failure);

    expect(error).toBeInstanceOf(Error);
    expect(error.message).toMatch(/File not found/);
    expect(error.message).toMatch(/allowed roots/);
    expect(api.sendMediaGroup).not.toHaveBeenCalled();
    expect(api.sendPhoto).not.toHaveBeenCalled();
  });

  it("rejects an empty file list and a caption length mismatch", async () => {
    const api = fakeApi();

    await expect(handleSendFile(deps(api), { filePath: [] })).rejects.toThrow(/at least one file/);

    await expect(
      handleSendFile(deps(api), { filePath: ["pic.png", "pic2.png"], caption: ["only one"] }),
    ).rejects.toThrow(/one caption per file/);

    expect(api.sendMediaGroup).not.toHaveBeenCalled();
  });

  it("rejects a sendMediaGroup response that miscounts the album items", async () => {
    const api = fakeApi();
    api.sendMediaGroup.mockResolvedValueOnce([{ message_id: 16 }]);
    const record = vi.fn();

    // Telegram returns one message per item; a mismatch would misalign the id→file ledger
    // labels, so the handler surfaces it rather than recording wrong labels.
    await expect(
      handleSendFile(
        { ...deps(api), store: { record, resolve: vi.fn(() => null) } },
        {
          filePath: ["pic.png", "pic2.png"],
        },
      ),
    ).rejects.toThrow(/returned 1 messages for 2 album items/);
    expect(record).not.toHaveBeenCalled();
  });

  it("records every album item id with its own label", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendFile(
      {
        api,
        log: fakeLog,
        chatId: 42,
        workspaceRoot: workspace,
        allowedRoots: [workspace],
        store: { record, resolve: vi.fn(() => null) },
        currentRouting: () => ({ treeEntryId: "entry-1", branchId: "topic-1" }),
      },
      { filePath: ["pic.png", "pic2.png"], caption: ["first cap", ""] },
    );

    expect(record).toHaveBeenCalledWith(
      "16",
      { treeEntryId: "entry-1", branchId: "topic-1" },
      "outgoing",
      { label: "photo pic.png — first cap" },
    );
    // The empty caption adds no suffix to the second item's label.
    expect(record).toHaveBeenCalledWith(
      "17",
      { treeEntryId: "entry-1", branchId: "topic-1" },
      "outgoing",
      { label: "photo pic2.png" },
    );
  });

  it("skips album recording when no trunk is active", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendFile(
      {
        api,
        log: fakeLog,
        chatId: 42,
        workspaceRoot: workspace,
        allowedRoots: [workspace],
        store: { record, resolve: vi.fn(() => null) },
        currentRouting: () => null,
      },
      { filePath: ["pic.png", "pic2.png"] },
    );

    expect(record).not.toHaveBeenCalled();
  });
});

describe("handleReactToMessage", () => {
  it("reacts to an explicit message id", async () => {
    const api = fakeApi();

    const result = await handleReactToMessage(
      { api, log: fakeLog, chatId: 42, getLastInboundMessageId: () => 5 },
      { emoji: "👍", messageId: 9 },
    );

    expect(result).toBe("Reacted to message 9 with 👍");
    expect(api.setMessageReaction).toHaveBeenCalledWith(42, 9, [{ type: "emoji", emoji: "👍" }]);
  });

  it("defaults to the user's last message", async () => {
    const api = fakeApi();

    await handleReactToMessage(
      { api, log: fakeLog, chatId: 42, getLastInboundMessageId: () => 5 },
      { emoji: "🔥" },
    );

    expect(api.setMessageReaction).toHaveBeenCalledWith(42, 5, [{ type: "emoji", emoji: "🔥" }]);
  });

  it("fails when no message is available", async () => {
    await expect(
      handleReactToMessage(
        { api: fakeApi(), log: fakeLog, chatId: 42, getLastInboundMessageId: () => null },
        { emoji: "👍" },
      ),
    ).rejects.toThrow("No message available to react to");
  });
});

describe("pinning", () => {
  it("returns the pinned message id resolved by the channel", async () => {
    // The channel pins the in-flight response inline (at the tool's tool-start) and resolves
    // requestPin with the message id — the tool hands that id back so a later turn can unpin it.
    const requestPin = vi.fn().mockResolvedValue(42);

    const result = await handlePinMessage({ log: fakeLog, requestPin });

    expect(requestPin).toHaveBeenCalledTimes(1);
    expect(result).toBe("Message pinned (ID: 42)");
  });

  it("throws when the channel reports no message available to pin", async () => {
    const requestPin = vi.fn().mockResolvedValue(null);

    await expect(handlePinMessage({ log: fakeLog, requestPin })).rejects.toThrow(
      "No message available to pin",
    );
  });

  it("propagates a pin failure from the channel", async () => {
    const requestPin = vi.fn().mockRejectedValue(new Error("not authorized to pin"));

    await expect(handlePinMessage({ log: fakeLog, requestPin })).rejects.toThrow(
      "not authorized to pin",
    );
  });

  it("unpins by message id", async () => {
    const api = fakeApi();

    const result = await handleUnpinMessage({ api, log: fakeLog, chatId: 42 }, { messageId: 7 });

    expect(result).toBe("Message unpinned (ID: 7)");
    expect(api.unpinChatMessage).toHaveBeenCalledWith(42, 7);
  });
});

describe("handleSendMessageWithButtons", () => {
  const buttonDeps = (
    api: ToolApi,
    overrides: Partial<Parameters<typeof handleSendMessageWithButtons>[0]> = {},
  ) => ({
    api,
    log: fakeLog,
    chatId: 42,
    store: { record: vi.fn(), resolve: vi.fn(() => null) },
    currentRouting: () =>
      ({ treeEntryId: "entry-1", branchId: "topic-1" }) as {
        treeEntryId: string;
        branchId: string;
      } | null,
    ...overrides,
  });

  it("sends the prompt with a packed inline keyboard", async () => {
    const api = fakeApi();

    const result = await handleSendMessageWithButtons(buttonDeps(api), {
      prompt: "Proceed?",
      buttons: [
        [
          { label: "Yes", value: "yes" },
          { label: "No", value: "no" },
        ],
        [{ label: "Cancel", value: "cancel" }],
      ],
    });

    expect(result).toBe("Buttons sent (message_id: 11)");
    const promptPayload = toTelegramEntities("Proceed?");
    expect(api.sendMessage).toHaveBeenCalledWith(42, promptPayload.text, {
      reply_markup: {
        inline_keyboard: [
          [
            { text: "Yes", callback_data: "btn1:yes" },
            { text: "No", callback_data: "btn1:no" },
          ],
          [{ text: "Cancel", callback_data: "btn1:cancel" }],
        ],
      },
      entities: promptPayload.entities,
    });
  });

  it("packs multi-use callbacks when singleUse is false", async () => {
    const api = fakeApi();

    await handleSendMessageWithButtons(buttonDeps(api), {
      prompt: "Pick",
      buttons: [[{ label: "A", value: "a" }]],
      singleUse: false,
    });

    const pickPayload = toTelegramEntities("Pick");
    expect(api.sendMessage).toHaveBeenCalledWith(42, pickPayload.text, {
      reply_markup: { inline_keyboard: [[{ text: "A", callback_data: "btnN:a" }]] },
      entities: pickPayload.entities,
    });
  });

  it("renders markdown in the prompt as MessageEntities", async () => {
    const api = fakeApi();
    const payload = toTelegramEntities("**Are you sure?**");

    await handleSendMessageWithButtons(buttonDeps(api), {
      prompt: "**Are you sure?**",
      buttons: [[{ label: "Yes", value: "yes" }]],
    });

    expect(payload.entities.some((e) => e.type === "bold")).toBe(true);
    expect(api.sendMessage).toHaveBeenCalledWith(42, payload.text, {
      reply_markup: { inline_keyboard: [[{ text: "Yes", callback_data: "btn1:yes" }]] },
      entities: payload.entities,
    });
  });

  it("resends the raw prompt with the keyboard on a render rejection", async () => {
    const parseErr = Object.assign(new Error("can't parse entities"), {
      description: "Bad Request: can't parse entities",
    });
    const api = fakeApi();
    api.sendMessage.mockImplementation(async (...args: unknown[]) => {
      const other = args.at(-1) as { entities?: unknown[] } | undefined;
      if (other?.entities?.length) throw parseErr;
      return { message_id: 11 };
    });
    const payload = toTelegramEntities("**bold**");

    const result = await handleSendMessageWithButtons(buttonDeps(api), {
      prompt: "**bold**",
      buttons: [[{ label: "Yes", value: "yes" }]],
    });

    expect(result).toBe("Buttons sent (message_id: 11)");
    expect(api.sendMessage).toHaveBeenCalledTimes(2);
    expect(api.sendMessage).toHaveBeenLastCalledWith(42, payload.text, {
      reply_markup: { inline_keyboard: [[{ text: "Yes", callback_data: "btn1:yes" }]] },
    });
  });

  it("records the button message against the current branch routing", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendMessageWithButtons(
      buttonDeps(api, { store: { record, resolve: vi.fn(() => null) } }),
      { prompt: "Proceed?", buttons: [[{ label: "Yes", value: "yes" }]] },
    );

    expect(record).toHaveBeenCalledWith(
      "11",
      { treeEntryId: "entry-1", branchId: "topic-1" },
      "outgoing",
      // The label carries the prompt and the choice labels, so a later tap/reaction on the
      // message recovers the question that was asked.
      { label: "Proceed? [Yes]" },
    );
  });

  it("skips recording when no trunk is active", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendMessageWithButtons(
      buttonDeps(api, {
        store: { record, resolve: vi.fn(() => null) },
        currentRouting: () => null,
      }),
      { prompt: "Proceed?", buttons: [[{ label: "Yes", value: "yes" }]] },
    );

    expect(record).not.toHaveBeenCalled();
  });

  it("rejects invalid button layouts before sending", async () => {
    const api = fakeApi();
    const base = buttonDeps(api);

    await expect(handleSendMessageWithButtons(base, { prompt: "x", buttons: [] })).rejects.toThrow(
      /at least one row/,
    );

    await expect(
      handleSendMessageWithButtons(base, {
        prompt: "x",
        buttons: [[{ label: " ", value: "a" }]],
      }),
    ).rejects.toThrow(/empty label/);

    await expect(
      handleSendMessageWithButtons(base, {
        prompt: "x",
        buttons: [[{ label: "A", value: "v".repeat(59) }]],
      }),
    ).rejects.toThrow(/58-byte limit/);

    expect(api.sendMessage).not.toHaveBeenCalled();
  });
});

describe("callback data packing", () => {
  it("round-trips values for both keyboard modes", () => {
    expect(unpackCallbackData(packCallbackData("yes", true))).toEqual({
      value: "yes",
      singleUse: true,
    });
    expect(unpackCallbackData(packCallbackData("no", false))).toEqual({
      value: "no",
      singleUse: false,
    });
  });

  it("rejects unknown prefixes", () => {
    expect(unpackCallbackData("other:yes")).toBeNull();
  });
});

describe("validateButtons", () => {
  it("rejects empty rows and oversized totals", () => {
    expect(() => validateButtons([[]])).toThrow(/row 0/);

    const row = Array.from({ length: 101 }, (_, index) => ({
      label: `b${index}`,
      value: `v${index}`,
    }));
    expect(() => validateButtons([row])).toThrow(/exceeds the cap/);
  });
});

describe("registerTelegramTools", () => {
  type RegisteredTool = {
    name: string;
    description: string;
    parameters?: {
      properties?: Record<
        string,
        {
          anyOf?: {
            maxLength?: number;
            description?: string;
            items?: { maxLength?: number };
          }[];
        }
      >;
    };
    execute: (toolCallId: string, params: unknown) => Promise<{ content: { text: string }[] }>;
  };

  const register = (deps: ToolDeps) => {
    const tools = new Map<string, RegisteredTool>();
    const pi = {
      registerTool: (definition: RegisteredTool) => {
        tools.set(definition.name, definition);
      },
    } as unknown as ExtensionAPI;

    registerTelegramTools(pi, deps);

    return tools;
  };

  const baseDeps = (api: ToolApi): ToolDeps => ({
    api,
    log: fakeLog,
    chatId: 42,
    workspaceRoot: "/tmp/ws",
    allowedRoots: ["/tmp/ws"],
    getLastInboundMessageId: () => 5,
    requestPin: vi.fn().mockResolvedValue(7),
    store: { record: vi.fn(), resolve: vi.fn(() => null) },
    currentRouting: () => ({ treeEntryId: "entry-1", branchId: "topic-1" }),
  });

  it("registers all five telegram tools", () => {
    const tools = register(baseDeps(fakeApi()));

    expect([...tools.keys()].sort()).toEqual([
      "pin_message",
      "react_to_message",
      "send_message_with_buttons",
      "send_telegram_file",
      "unpin_message",
    ]);
  });

  it("routes execute callbacks through the handlers and wraps their text results", async () => {
    const api = fakeApi();
    const tools = register(baseDeps(api));

    const react = await tools.get("react_to_message")?.execute("call-1", { emoji: "👍" });
    expect(react?.content[0].text).toBe("Reacted to message 5 with 👍");
    expect(api.setMessageReaction).toHaveBeenCalled();

    const pin = await tools.get("pin_message")?.execute("call-2", {});
    expect(pin?.content[0].text).toBe("Message pinned (ID: 7)");

    const unpin = await tools.get("unpin_message")?.execute("call-3", { messageId: 7 });
    expect(unpin?.content[0].text).toBe("Message unpinned (ID: 7)");

    const buttons = await tools
      .get("send_message_with_buttons")
      ?.execute("call-4", { prompt: "Proceed?", buttons: [[{ label: "Yes", value: "yes" }]] });
    expect(buttons?.content[0].text).toBe("Buttons sent (message_id: 11)");
  });

  it("delivers a file through the send_telegram_file execute callback", async () => {
    const workspace = await mkdtemp(join(tmpdir(), "tachi-telegram-reg-"));
    await writeFile(join(workspace, "pic.png"), "fake image");

    const api = fakeApi();
    const tools = register({
      ...baseDeps(api),
      workspaceRoot: workspace,
      allowedRoots: [workspace],
    });

    const sent = await tools.get("send_telegram_file")?.execute("call-5", { filePath: "pic.png" });
    expect(sent?.content[0].text).toBe("File sent: pic.png (message_id: 12)");
    expect(api.sendPhoto).toHaveBeenCalled();

    await rm(workspace, { recursive: true, force: true });
  });

  it("teaches the album form on the send_telegram_file surface", () => {
    const tools = register(baseDeps(fakeApi()));
    const tool = tools.get("send_telegram_file");

    // The model-visible surface must document the array form, the 2-10 cap, the grouping
    // constraints, and both caption modes — otherwise the agent keeps issuing one call
    // per file, which is the bug being fixed.
    expect(tool?.description).toMatch(/2-10 paths/);
    expect(tool?.description).toMatch(/documents group only with documents/);
    expect(tool?.description).toMatch(/audio only with audio/);
    expect(tool?.description).toMatch(/one grouped\s+album/);
    expect(tool?.description).toMatch(/a single string captions the whole album/);
    expect(tool?.description).toMatch(/an\s+array captions each file in order/);

    const filePath = tool?.parameters?.properties?.filePath;
    expect(filePath?.anyOf?.[1]?.items).toBeDefined();
    expect(filePath?.anyOf?.[1]?.description).toMatch(/2-10/);
    expect(filePath?.anyOf?.[1]?.description).toMatch(/documents only with documents/);

    // The 1024-caption bound survives on both union members, and the caption array member
    // documents the positional mapping.
    const caption = tool?.parameters?.properties?.caption;
    expect(caption?.anyOf?.[0]?.maxLength).toBe(1024);
    expect(caption?.anyOf?.[1]?.items?.maxLength).toBe(1024);
    expect(caption?.anyOf?.[1]?.description).toMatch(/positionally/);
  });
});
