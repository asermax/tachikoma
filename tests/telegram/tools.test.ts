import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import {
  packCallbackData,
  unpackCallbackData,
  validateButtons,
} from "../../src/extensions/telegram/buttons.ts";
import {
  detectMediaType,
  handlePinMessage,
  handleReactToMessage,
  handleSendFile,
  handleSendMessageWithButtons,
  handleUnpinMessage,
  type ToolApi,
} from "../../src/extensions/telegram/tools.ts";

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
    await writeFile(join(outside, "secret.txt"), "nope");
  });

  afterAll(async () => {
    await rm(workspace, { recursive: true, force: true });
    await rm(outside, { recursive: true, force: true });
  });

  const deps = (api: ToolApi) => ({
    api,
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

  it("rejects missing files", async () => {
    await expect(handleSendFile(deps(fakeApi()), { filePath: "missing.txt" })).rejects.toThrow(
      /File not found/,
    );
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
      { api, chatId: 42, getLastInboundMessageId: () => 5 },
      { emoji: "👍", messageId: 9 },
    );

    expect(result).toBe("Reacted to message 9 with 👍");
    expect(api.setMessageReaction).toHaveBeenCalledWith(42, 9, [{ type: "emoji", emoji: "👍" }]);
  });

  it("defaults to the user's last message", async () => {
    const api = fakeApi();

    await handleReactToMessage(
      { api, chatId: 42, getLastInboundMessageId: () => 5 },
      { emoji: "🔥" },
    );

    expect(api.setMessageReaction).toHaveBeenCalledWith(42, 5, [{ type: "emoji", emoji: "🔥" }]);
  });

  it("fails when no message is available", async () => {
    await expect(
      handleReactToMessage(
        { api: fakeApi(), chatId: 42, getLastInboundMessageId: () => null },
        { emoji: "👍" },
      ),
    ).rejects.toThrow("No message available to react to");
  });
});

describe("pinning", () => {
  it("pins the last outbound message audibly", async () => {
    const api = fakeApi();

    const result = await handlePinMessage({ api, chatId: 42, getLastOutboundMessageId: () => 7 });

    expect(result).toBe("Message pinned (ID: 7)");
    expect(api.pinChatMessage).toHaveBeenCalledWith(42, 7, { disable_notification: false });
  });

  it("fails to pin when nothing was sent yet", async () => {
    await expect(
      handlePinMessage({ api: fakeApi(), chatId: 42, getLastOutboundMessageId: () => null }),
    ).rejects.toThrow("No message available to pin");
  });

  it("unpins by message id", async () => {
    const api = fakeApi();

    const result = await handleUnpinMessage({ api, chatId: 42 }, { messageId: 7 });

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
    chatId: 42,
    store: { record: vi.fn(), findSessionId: vi.fn(() => null) },
    currentSessionId: () => 100 as number | null,
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
    expect(api.sendMessage).toHaveBeenCalledWith(42, "Proceed?", {
      reply_markup: {
        inline_keyboard: [
          [
            { text: "Yes", callback_data: "btn1:yes" },
            { text: "No", callback_data: "btn1:no" },
          ],
          [{ text: "Cancel", callback_data: "btn1:cancel" }],
        ],
      },
    });
  });

  it("packs multi-use callbacks when singleUse is false", async () => {
    const api = fakeApi();

    await handleSendMessageWithButtons(buttonDeps(api), {
      prompt: "Pick",
      buttons: [[{ label: "A", value: "a" }]],
      singleUse: false,
    });

    expect(api.sendMessage).toHaveBeenCalledWith(42, "Pick", {
      reply_markup: { inline_keyboard: [[{ text: "A", callback_data: "btnN:a" }]] },
    });
  });

  it("records the prompt as an outgoing message for the current session", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendMessageWithButtons(
      buttonDeps(api, { store: { record, findSessionId: vi.fn(() => null) } }),
      { prompt: "Proceed?", buttons: [[{ label: "Yes", value: "yes" }]] },
    );

    expect(record).toHaveBeenCalledWith("11", 100, "outgoing");
  });

  it("skips recording when no session is active", async () => {
    const api = fakeApi();
    const record = vi.fn();

    await handleSendMessageWithButtons(
      buttonDeps(api, {
        store: { record, findSessionId: vi.fn(() => null) },
        currentSessionId: () => null,
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
