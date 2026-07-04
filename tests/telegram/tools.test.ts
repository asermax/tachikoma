import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
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
    setMessageReaction: vi.fn().mockResolvedValue(true),
    pinChatMessage: vi.fn().mockResolvedValue(true),
    unpinChatMessage: vi.fn().mockResolvedValue(true),
  }) satisfies ToolApi;

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
    await writeFile(join(workspace, "notes.txt"), "fake notes");
    await writeFile(join(workspace, "song.mp3"), "fake audio");
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
  });

  it("sends images as photos with the caption", async () => {
    const api = fakeApi();

    const result = await handleSendFile(deps(api), {
      filePath: join(workspace, "pic.png"),
      caption: "a chart",
    });

    expect(result).toBe("File sent: pic.png");
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
    expect(sent?.content[0].text).toBe("File sent: pic.png");
    expect(api.sendPhoto).toHaveBeenCalled();

    await rm(workspace, { recursive: true, force: true });
  });
});
