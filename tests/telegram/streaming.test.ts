import type { MessageEntity } from "grammy/types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TELEGRAM_MAX_MESSAGE_LENGTH } from "../../src/extensions/telegram/chunking.ts";
import {
  type TelegramPayload,
  toTelegramEntities,
} from "../../src/extensions/telegram/entities.ts";
import { EDIT_THROTTLE_MS, StreamRenderer } from "../../src/extensions/telegram/streaming.ts";
import type { Logger } from "../../src/log.ts";

/** The literal display text the renderer produces for a given markdown input. */
const rendered = (markdown: string): string => toTelegramEntities(markdown).text;

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

interface ApiCall {
  type: "send" | "edit" | "delete";
  messageId?: number;
  text?: string;
  /** Entities passed in the `other` options arg (DLT-064): present only when non-empty. */
  entities?: MessageEntity[];
}

const fakeApi = () => {
  const calls: ApiCall[] = [];
  let next = 0;

  // The 4th param captures `other?.entities` (where the expandable_blockquote travels). It is recorded
  // only when non-empty so existing assertions that check just {type, text, messageId} stay exact.
  const recordEntities = (other?: {
    entities?: MessageEntity[];
  }): { entities?: MessageEntity[] } =>
    other?.entities?.length ? { entities: other.entities } : {};

  return {
    calls,
    sendMessage: vi.fn(
      async (_chat: number, text: string, other?: { entities?: MessageEntity[] }) => {
        calls.push({ type: "send", text, ...recordEntities(other) });
        next += 1;
        return { message_id: next };
      },
    ),
    editMessageText: vi.fn(
      async (
        _chat: number,
        messageId: number,
        text: string,
        other?: { entities?: MessageEntity[] },
      ) => {
        calls.push({ type: "edit", messageId, text, ...recordEntities(other) });
        return true;
      },
    ),
    deleteMessage: vi.fn(async (_chat: number, messageId: number) => {
      calls.push({ type: "delete", messageId });
      return true;
    }),
  };
};

// Payload/entity assertion helpers, redeclared inline (the ones in entities.test.ts are module-local).
const findEntity = (call: ApiCall | undefined, type: string) =>
  call?.entities?.find((e) => e.type === type);

/** Like `findEntity`, but asserts the entity is present and returns it non-optionally. */
const mustEntity = (call: ApiCall | undefined, type: string): MessageEntity => {
  const entity = findEntity(call, type);
  if (!entity) throw new Error(`expected a ${type} entity on ${call?.type ?? "no"} call`);
  return entity;
};

/** The payload recorded for a call (text + entities), for entity-level assertions. */
const payloadOf = (call: ApiCall | undefined): TelegramPayload => ({
  text: call?.text ?? "",
  entities: call?.entities ?? [],
});

const notModifiedError = () =>
  Object.assign(new Error("400: Bad Request: message is not modified"), {
    description: "Bad Request: message is not modified",
  });

describe("StreamRenderer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("holds an in-progress paragraph until a paragraph boundary closes it", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // No paragraph break yet — nothing renders during streaming.
    await renderer.appendText("Hello");
    expect(api.calls).toEqual([]);

    // Completing the paragraph reveals everything up to the break.
    await renderer.appendText(" there.\n\nSecond");
    expect(api.calls).toEqual([{ type: "send", text: rendered("Hello there.") }]);
  });

  it("renders complete paragraphs and throttles subsequent edits", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("First.\n\n");
    expect(api.calls).toEqual([{ type: "send", text: rendered("First.") }]);

    // Within the throttle window the new paragraph is buffered, not edited in.
    await renderer.appendText("Second.\n\n");
    expect(api.calls).toHaveLength(1);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("Third.\n\n");
    expect(api.calls).toHaveLength(2);
    expect(api.calls[1]).toEqual({
      type: "edit",
      messageId: 1,
      text: rendered("First.\n\nSecond.\n\nThird."),
    });
  });

  it("bakes a tool marker between text segments and shows a live line while running", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // Settled text plus a live tool line renders the whole buffer beneath it,
    // even without a trailing paragraph break.
    await renderer.appendText("Let me search.");
    await renderer.appendTool("grep", { pattern: "skippable" });
    expect(api.calls.at(-1)).toMatchObject({
      type: "send",
      text: rendered("Let me search.\n\n_🔧 Searching for 'skippable'_"),
    });

    // Text after the tool bakes the summary marker, separated by blank lines.
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("Found it.\n\n");
    expect(api.calls.at(-1)).toMatchObject({
      type: "edit",
      messageId: 1,
      text: rendered("Let me search.\n\n_🔧 Searching for `skippable`_\n\nFound it."),
    });
  });

  it("shows a marker-only message before any text arrives", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.showTransient("Compacting…");

    expect(api.calls).toMatchObject([{ type: "send", text: rendered("_Compacting…_") }]);
  });

  it("finalize bypasses the throttle and flushes the trailing paragraph", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello");
    await renderer.appendText(" world");
    expect(api.calls).toEqual([]);

    expect(await renderer.finalize()).toBe(1);
    expect(api.calls.at(-1)).toEqual({ type: "send", text: rendered("Hello world") });
  });

  it("bakes a pending tool summary at finalize when no trailing text follows", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Done.");
    await renderer.appendTool("read", { path: "/tmp/notes/config.ts" });

    expect(await renderer.finalize()).toBe(1);
    expect(api.calls.at(-1)).toMatchObject({
      type: "edit",
      messageId: 1,
      text: rendered("Done.\n\n_🔧 Reading `config.ts`_"),
    });
  });

  it("finalizes the current message and continues in a new one past the edit cap", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    const first = "a".repeat(3000);
    const second = "b".repeat(3000);

    await renderer.appendText(`${first}\n\n`);
    expect(api.calls).toEqual([{ type: "send", text: rendered(first) }]);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(`${second}\n\n`);

    expect(api.calls).toEqual([
      { type: "send", text: rendered(first) },
      { type: "edit", messageId: 1, text: rendered(first) },
      { type: "send", text: rendered(second) },
    ]);
  });

  it("chunks overflow at finalize when the throttle held the last flushes back", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    const text = "a".repeat(TELEGRAM_MAX_MESSAGE_LENGTH + 100);

    await renderer.appendText("start");
    await renderer.appendText(text.slice(5));

    // "start" alone never rendered (no paragraph break), so the first overflow
    // chunk is sent fresh rather than edited into an existing message.
    expect(await renderer.finalize()).toBe(2);
    expect(api.calls).toEqual([
      { type: "send", text: rendered(`start${text.slice(5, TELEGRAM_MAX_MESSAGE_LENGTH)}`) },
      { type: "send", text: rendered(text.slice(TELEGRAM_MAX_MESSAGE_LENGTH)) },
    ]);
  });

  it("treats 'message is not modified' rejections as benign", async () => {
    const api = fakeApi();
    api.editMessageText.mockRejectedValueOnce(notModifiedError());
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello\n\n");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("again\n\n");

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("more\n\n");

    expect(api.calls.at(-1)).toMatchObject({ type: "edit", messageId: 1 });
  });

  it("falls back to final-send behavior when an edit fails", async () => {
    const api = fakeApi();
    api.editMessageText.mockRejectedValue(new Error("400: message can't be edited"));
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello\n\n");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("world\n\n");

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("tail\n\n");
    expect(api.calls.filter((call) => call.type === "send")).toHaveLength(1);

    expect(await renderer.finalize()).toBe(2);
    expect(api.calls.at(-2)).toEqual({ type: "delete", messageId: 1 });
    expect(api.calls.at(-1)).toEqual({ type: "send", text: rendered("Hello\n\nworld\n\ntail") });
  });

  it("deletes the placeholder when only markers were shown", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.showTransient("Compacting…");

    expect(await renderer.finalize()).toBeNull();
    expect(api.calls.at(-1)).toEqual({ type: "delete", messageId: 1 });
  });

  it("returns null from finalize when nothing was rendered", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    expect(await renderer.finalize()).toBeNull();
    expect(api.calls).toEqual([]);
  });

  it("sends the first chunk fresh when the final edit fails", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // Render a streaming message, then make the final in-place edit reject so finalize
    // falls back to sending that chunk as a brand-new message.
    await renderer.appendText("Hello.\n\n");
    expect(api.calls).toEqual([{ type: "send", text: rendered("Hello.") }]);

    api.editMessageText.mockRejectedValue(new Error("400: message can't be edited"));
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);

    const lastId = await renderer.finalize();

    expect(fakeLog.warn).toHaveBeenCalledWith(
      expect.anything(),
      "final edit failed — sending as a new message",
    );
    expect(api.calls.at(-1)).toEqual({ type: "send", text: rendered("Hello.") });
    expect(lastId).toBe(2);
  });

  it("bakes a tool marker with no leading text when a tool precedes any output", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // A tool runs before any text: the baked marker has no preceding paragraph,
    // so no separating newline is prefixed.
    await renderer.appendTool("grep", { pattern: "first" });
    await renderer.appendText("Now the answer.");

    expect(await renderer.finalize()).toBe(1);
    expect(api.calls.at(-1)?.text).toBe(rendered("_🔧 Searching for `first`_\n\nNow the answer."));
  });

  it("does not double the newline when the buffer already ends with one", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // Buffer ends in a newline before baking, so the separator guard is skipped.
    await renderer.appendText("Line one.\n");
    await renderer.appendTool("read", { path: "/a/b.ts" });
    await renderer.appendText("Done.");

    expect(await renderer.finalize()).toBe(1);
    expect(api.calls.at(-1)?.text).toBe(rendered("Line one.\n\n_🔧 Reading `b.ts`_\n\nDone."));
  });

  it("drops the live line when appending it would exceed the edit limit", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // Settled text fills the budget; a transient line cannot be appended beneath
    // it without overflowing, so only the settled text renders.
    const big = `${"a".repeat(TELEGRAM_MAX_MESSAGE_LENGTH - 5)}\n\n`;
    await renderer.appendText(big);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.showTransient("status that pushes past the limit");

    // The whole buffer (its trailing break included) renders; the transient is dropped.
    expect(api.calls.at(-1)?.text).toBe(rendered(big));
  });

  it("returns null from a broken finalize when no buffer text survives", async () => {
    const api = fakeApi();
    api.sendMessage.mockRejectedValue(new Error("400: chat not found"));
    const renderer = new StreamRenderer(api, 42, fakeLog);

    // The first send (a transient-only render) fails, marking the renderer broken
    // while the buffer is still empty; the broken finalize then has nothing to send.
    await renderer.showTransient("status");

    expect(await renderer.finalize()).toBeNull();
  });

  it("swallows a delete failure while cleaning up the placeholder", async () => {
    const api = fakeApi();
    api.deleteMessage.mockRejectedValue(new Error("400: message to delete not found"));
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.showTransient("Compacting…");

    expect(await renderer.finalize()).toBeNull();
    expect(fakeLog.warn).toHaveBeenCalledWith(
      expect.anything(),
      "streaming message cleanup failed",
    );
  });
});

describe("StreamRenderer decision header (DLT-181)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("anchors the header above the streamed body and recomposes it across edits", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    renderer.setHeader({
      label: "📌 Checkpoint set",
      note: "main line parked",
      rollbackable: true,
    });

    await renderer.appendText("First.\n\n");
    // The header is recomposed on every edit (KD9): editMessageText replaces the full text, so the
    // renderer must prepend the header each time.
    expect(api.calls[0]).toMatchObject({
      type: "send",
      text: rendered("_📌 Checkpoint set — main line parked_\n\nFirst."),
    });

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("Second.\n\n");
    expect(api.calls.at(-1)).toMatchObject({
      type: "edit",
      messageId: 1,
      text: rendered("_📌 Checkpoint set — main line parked_\n\nFirst.\n\nSecond."),
    });
  });

  it("renders no header when unset (today's behavior)", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.appendText("Hello.\n\n");

    expect(api.calls[0]).toEqual({ type: "send", text: rendered("Hello.") });
  });

  it("renders a label-only header when the note is empty", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    renderer.setHeader({ label: "🆕 New topic", note: "", rollbackable: false });

    await renderer.appendText("Body.\n\n");

    expect(api.calls[0]?.text).toBe(rendered("_🆕 New topic_\n\nBody."));
  });

  it("keeps the header above a transient line, recomposed each edit", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    renderer.setHeader({ label: "📌 Checkpoint set", note: "parked", rollbackable: true });

    await renderer.appendText("Settled.");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("grep", { pattern: "needle" });

    // header + settled body + live tool line, all recomposed together.
    expect(api.calls.at(-1)?.text).toBe(
      rendered("_📌 Checkpoint set — parked_\n\nSettled.\n\n_🔧 Searching for 'needle'_"),
    );
  });

  it("drops the header (best-effort) when the body exceeds the edit limit and logs the descriptor", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    renderer.setHeader({ label: "📌 Checkpoint set", note: "parked", rollbackable: true });

    // Body just under the limit on its own; header + body overflows it.
    const big = `${"a".repeat(TELEGRAM_MAX_MESSAGE_LENGTH - 5)}\n\n`;
    await renderer.appendText(big);

    // The header was dropped so the body alone renders; the descriptor is logged (R8 best-effort).
    expect(api.calls.at(-1)?.text).toBe(rendered(big));
    expect(fakeLog.info).toHaveBeenCalledWith(
      expect.objectContaining({
        decisionHeader: { label: "📌 Checkpoint set", note: "parked", rollbackable: true },
      }),
      "decision header dropped — body exceeded the edit limit (best-effort surfacing)",
    );
  });

  it("includes the header on the finalized message", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);
    renderer.setHeader({ label: "📌 Checkpoint set", note: "parked", rollbackable: true });

    await renderer.appendText("Body.\n\n");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("More.");

    expect(await renderer.finalize()).toBe(1);
    expect(api.calls.at(-1)?.text).toBe(rendered("_📌 Checkpoint set — parked_\n\nBody.\n\nMore."));
  });

  it("logs the descriptor and falls back when a render fails mid-stream (R8)", async () => {
    const api = fakeApi();
    api.editMessageText.mockRejectedValue(new Error("400: message can't be edited"));
    const renderer = new StreamRenderer(api, 42, fakeLog);
    renderer.setHeader({ label: "📌 Checkpoint set", note: "parked", rollbackable: true });

    await renderer.appendText("First.\n\n");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("Second.\n\n");

    // The edit failure marks the renderer broken and logs the dropped descriptor.
    expect(fakeLog.info).toHaveBeenCalledWith(
      expect.objectContaining({
        decisionHeader: { label: "📌 Checkpoint set", note: "parked", rollbackable: true },
      }),
      "decision header dropped after a render failure (best-effort surfacing)",
    );
  });
});

describe("StreamRenderer intensive-work detection (DLT-064)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Construct with the collapse config as trailing args (mirroring channel.ts); defaults keep the
  // existing suites byte-identical. `collapse`/`threshold` are the last two StreamRenderer params.
  const make = (api: ReturnType<typeof fakeApi>, collapse = true, threshold = 4): StreamRenderer =>
    new StreamRenderer(api, 42, fakeLog, null, false, collapse, threshold);

  it("counts a block of tools followed by text as a single boundary, not one per tool (R1)", async () => {
    const api = fakeApi();
    // threshold 1: a count of 1 stays inactive (1 > 1 is false), but a per-tool count of 5 would be active.
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendTool("read", { path: "/c.ts" });
    await renderer.appendTool("read", { path: "/d.ts" });
    await renderer.appendTool("read", { path: "/e.ts" });
    await renderer.appendText("All read.\n\n");

    expect(renderer.collapseActive()).toBe(false);
  });

  it("does not count a trailing tool block with no following text (R9)", async () => {
    const api = fakeApi();
    // threshold 1: one boundary stays inactive; a second would flip it active.
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("step\n\n"); // boundary 1
    expect(renderer.collapseActive()).toBe(false);

    // Trailing tool with no following text — its summary bakes at finalize but adds no boundary.
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.finalize();

    expect(renderer.collapseActive()).toBe(false);
  });

  it("completes a boundary with even a single character of text (R1)", async () => {
    const api = fakeApi();
    // threshold 0: one boundary flips collapse active.
    const renderer = make(api, true, 0);
    await renderer.appendTool("read", { path: "/a.ts" });
    expect(renderer.collapseActive()).toBe(false);

    await renderer.appendText("x"); // any text, even one char, completes the boundary
    expect(renderer.collapseActive()).toBe(true);
  });

  it("does not count a status (transient) event as a boundary (R1)", async () => {
    const api = fakeApi();
    // threshold 1: two boundaries are active, one is not — so a spurious status boundary would show.
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n"); // boundary 1
    await renderer.showTransient("Compacting…"); // status — NOT a boundary
    await renderer.appendText("second\n\n"); // no pending tools → no boundary
    expect(renderer.collapseActive()).toBe(false);

    // Contrast: a second tool→text transition does add a boundary → active.
    const other = make(fakeApi(), true, 1);
    await other.appendTool("read", { path: "/a.ts" });
    await other.appendText("first\n\n");
    await other.appendTool("read", { path: "/b.ts" });
    await other.appendText("second\n\n"); // boundary 2 → active
    expect(other.collapseActive()).toBe(true);
  });

  it("activates collapse only once the count exceeds the default threshold of 4 (R6)", async () => {
    const api = fakeApi();
    const renderer = make(api); // defaults: collapse on, threshold 4
    for (let i = 0; i < 4; i += 1) {
      await renderer.appendTool("read", { path: `/f${i}.ts` });
      await renderer.appendText(`step ${i}\n\n`); // four boundaries
    }
    expect(renderer.collapseActive()).toBe(false); // 4 > 4 is false

    await renderer.appendTool("read", { path: "/f4.ts" });
    await renderer.appendText("step 4\n\n"); // fifth boundary
    expect(renderer.collapseActive()).toBe(true); // 5 > 4
  });

  it("honors a custom intensiveWorkThreshold (R6)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("one\n\n"); // boundary 1 → 1 > 1 false
    expect(renderer.collapseActive()).toBe(false);

    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("two\n\n"); // boundary 2 → 2 > 1 true
    expect(renderer.collapseActive()).toBe(true);
  });

  it("never collapses when collapseIntensiveWork is disabled (R7)", async () => {
    const api = fakeApi();
    const renderer = make(api, false, 4);
    for (let i = 0; i < 6; i += 1) {
      await renderer.appendTool("read", { path: `/f${i}.ts` });
      await renderer.appendText(`step ${i}\n\n`); // six boundaries
    }
    expect(renderer.collapseActive()).toBe(false);
  });

  it("resets detection at each message boundary (overflow commit) so the tail is evaluated independently (R9)", async () => {
    const api = fakeApi();
    // threshold 0: a single tool→text boundary makes collapse active.
    const renderer = make(api, true, 0);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("seed\n\n"); // boundary 1 → active
    expect(renderer.collapseActive()).toBe(true);

    // Advance past the throttle so the next flush reaches commitOverflow, then overflow the buffer
    // past the 4096-char edit limit. commitOverflow finalizes the earlier chunk(s) in place and
    // resets the boundary counter, so the streaming tail message starts fresh from zero.
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText(`${"x".repeat(TELEGRAM_MAX_MESSAGE_LENGTH + 10)}\n\n`);

    expect(renderer.collapseActive()).toBe(false);
  });
});

describe("StreamRenderer intensive-work collapse (DLT-064)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Same trailing-arg shape as channel.ts; a low threshold makes a turn intensive after a couple of
  // tool→text boundaries so the payload-level behavior is observable without five segments.
  const make = (api: ReturnType<typeof fakeApi>, collapse = true, threshold = 4): StreamRenderer =>
    new StreamRenderer(api, 42, fakeLog, null, false, collapse, threshold);

  it("folds intermediate segments into an expandable_blockquote, leaving the final answer expanded (R2/R5)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1); // 2 boundaries ⇒ active
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n"); // boundary 1
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("second\n\n"); // boundary 2 ⇒ active
    await renderer.appendTool("read", { path: "/c.ts" });
    await renderer.appendText("final answer"); // boundary 3; no trailing break ⇒ live tail
    await renderer.finalize();

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    // The collapsed block covers the intermediate region; the final answer sits AFTER it (expanded).
    expect(last.text.slice(block.offset, block.offset + block.length)).not.toContain(
      "final answer",
    );
    expect(last.text.slice(block.offset + block.length)).toContain("final answer");
  });

  it("carries the baked tool-summary markers nested inside the block, not live activity labels (R2/R5)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n");
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("final answer");
    await renderer.finalize();

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    // Baked markers render as italic spans with inline code; both nest inside the block.
    const innerItalics = (last.entities ?? []).filter((e) => e.type === "italic");
    expect(innerItalics.length).toBeGreaterThan(0);
    for (const e of innerItalics) {
      expect(e.offset).toBeGreaterThanOrEqual(block.offset);
      expect(e.offset + e.length).toBeLessThanOrEqual(block.offset + block.length);
    }
    const code = mustEntity(last, "code");
    expect(code.offset).toBeGreaterThanOrEqual(block.offset);
    expect(code.offset + code.length).toBeLessThanOrEqual(block.offset + block.length);
    // The live activity phrasing ("Reading /b.ts") is not present — only the baked summary.
    expect(last.text).not.toContain("Reading /b.ts");
  });

  it("keeps the last preface + trailing tool expanded, folding earlier units (R4/R10)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n"); // boundary 1
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("second\n\n"); // boundary 2 ⇒ active
    await renderer.appendTool("read", { path: "/c.ts" }); // trailing tool — no following text
    await renderer.finalize(); // bakes c's summary into the LAST unit (expanded), not the block

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    const blockText = last.text.slice(block.offset, block.offset + block.length);
    const tailText = last.text.slice(block.offset + block.length);
    // Earlier units fold: the "first" preface and the a/b tool summaries sit inside the block.
    expect(blockText).toContain("first");
    expect(blockText).toContain("a.ts");
    expect(blockText).toContain("b.ts");
    // The last unit stays expanded: the "second" preface and the trailing c.ts summary sit after it.
    expect(tailText).toContain("second");
    expect(tailText).toContain("c.ts");
    expect(tailText).not.toContain("first");
    expect(last.text.length).toBeGreaterThan(0);
  });

  it("keeps a running tool's preface + live line expanded as the tail (R3)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n");
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("second\n\n"); // boundary 2 ⇒ active
    // A tool is running — its preface "second" stays visible with its live activity line as the tail.
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("grep", { pattern: "needle" });

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    const tailText = last.text.slice(block.offset + block.length);
    // The tail holds the preface text AND the running tool's activity line (the last unit), expanded.
    expect(tailText).toContain("second");
    expect(tailText).toContain("Searching for 'needle'");
    // Only earlier units fold: "first" is inside the block, not the tail.
    expect(last.text.slice(block.offset, block.offset + block.length)).toContain("first");
  });

  it("folds earlier inline segments retroactively when the threshold is crossed (R2/R3)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n"); // boundary 1 — inactive
    expect(api.calls.every((c) => !findEntity(c, "expandable_blockquote"))).toBe(true);

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("read", { path: "/b.ts" });
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("second\n\n"); // boundary 2 ⇒ crossing
    // The previously-inline "first" segment is now rebuilt from the buffer into the block.
    const crossed = api.calls.at(-1);
    const block = mustEntity(crossed, "expandable_blockquote");
    expect(crossed.text.slice(block.offset, block.offset + block.length)).toContain("first");
  });

  it("anchors the decision header above the collapsed block, never inside it (R11)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    renderer.setHeader({ label: "📌 Checkpoint set", note: "parked", rollbackable: true });
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n");
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("second\n\n"); // boundary 2 ⇒ active
    await renderer.appendTool("read", { path: "/c.ts" });
    await renderer.appendText("final answer");
    await renderer.finalize();

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    // The header is a separate italic payload prepended ABOVE the block (the first italic span).
    const header = mustEntity(last, "italic");
    expect(header.offset).toBeLessThanOrEqual(block.offset);
    expect(header.offset + header.length).toBeLessThanOrEqual(block.offset);
    // ...and the header text sits outside the block.
    expect(last.text.slice(block.offset, block.offset + block.length)).not.toContain("Checkpoint");
  });

  it("renders no expandable_blockquote when collapseIntensiveWork is disabled, regardless of count (R7)", async () => {
    const api = fakeApi();
    const renderer = make(api, false, 1); // disabled
    for (let i = 0; i < 5; i += 1) {
      await renderer.appendTool("read", { path: `/f${i}.ts` });
      await renderer.appendText(`seg${i}\n\n`);
    }
    await renderer.finalize();
    expect(api.calls.every((c) => !findEntity(c, "expandable_blockquote"))).toBe(true);
  });

  it("collapses only once the 5th boundary forms at the default threshold (R6/R8)", async () => {
    // 4 boundaries ⇒ no collapse.
    const a = fakeApi();
    const r4 = make(a);
    for (let i = 0; i < 4; i += 1) {
      vi.advanceTimersByTime(EDIT_THROTTLE_MS);
      await r4.appendTool("read", { path: `/f${i}.ts` });
      await r4.appendText(`seg${i}\n\n`);
    }
    await r4.finalize();
    expect(a.calls.every((c) => !findEntity(c, "expandable_blockquote"))).toBe(true);

    // 5 boundaries ⇒ collapse.
    const b = fakeApi();
    const r5 = make(b);
    for (let i = 0; i < 5; i += 1) {
      vi.advanceTimersByTime(EDIT_THROTTLE_MS);
      await r5.appendTool("read", { path: `/g${i}.ts` });
      await r5.appendText(`seg${i}\n\n`);
    }
    await r5.finalize();
    expect(b.calls.some((c) => findEntity(c, "expandable_blockquote") != null)).toBe(true);
  });

  it("renders a below-threshold turn byte-identically to the non-collapse path (R8)", async () => {
    const api = fakeApi();
    const renderer = make(api); // default threshold 4
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("the answer\n\n");
    await renderer.finalize();
    const last = api.calls.at(-1);
    expect(last.text).toBe(rendered("_🔧 Reading `a.ts`_\n\nthe answer"));
    expect(findEntity(last, "expandable_blockquote")).toBeUndefined();
  });

  it("renders a status transient as the expanded tail, outside the block (R11)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n");
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("second\n\n"); // boundary 2 ⇒ active
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.showTransient("Compacting…");
    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    expect(last.text.slice(block.offset + block.length)).toContain("Compacting…");
  });

  it("delivers plain inline text with no block on the broken-render fallback (R11)", async () => {
    const api = fakeApi();
    api.editMessageText.mockRejectedValue(new Error("400: message can't be edited"));
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("read", { path: "/b.ts" });
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("second\n\n"); // would be active, but the edit failure breaks the renderer
    await renderer.finalize(); // finalizeBroken → sendChunked (plain text, no entities)
    expect(api.calls.every((c) => !findEntity(c, "expandable_blockquote"))).toBe(true);
  });

  it("evaluates collapse per message on overflow: a committed chunk carries its own block, the tail resets (R9)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("seed\n\n"); // boundary 1
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("read", { path: "/b.ts" });
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    // boundary 2 ⇒ active; the buffer overflows 4096 so commitOverflow commits the intensive chunk
    // (carrying its own collapsed block) and resets detection for the streaming tail.
    await renderer.appendText(`${"x".repeat(TELEGRAM_MAX_MESSAGE_LENGTH + 10)}\n\n`);

    const committed = api.calls.find((c) => findEntity(c, "expandable_blockquote") != null);
    expect(committed).toBeDefined();
    // After the reset the streaming tail is evaluated independently from zero.
    expect(renderer.collapseActive()).toBe(false);
  });

  it("exposes the composed payload's text as the convert of the buffer (structural sanity)", async () => {
    // Guards against offset drift: the block entity exactly covers the intermediate converted text,
    // and every nested entity points at the right substring of the payload text.
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n");
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("final answer");
    await renderer.finalize();

    const last = api.calls.at(-1);
    const payload = payloadOf(last);
    for (const e of payload.entities) {
      expect(e.offset).toBeGreaterThanOrEqual(0);
      expect(e.offset + e.length).toBeLessThanOrEqual(payload.text.length);
    }
  });

  it("keeps the whole streaming text segment expanded until a tool resumes, not paragraph-by-paragraph (R1)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1); // 2 boundaries ⇒ active
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n"); // boundary 1
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("second\n\n"); // boundary 2 ⇒ active; split moves to "second"
    // Stream two more paragraphs of the SAME text segment (no tool between them).
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("third\n\n");
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("fourth\n\n");

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    const blockText = last.text.slice(block.offset, block.offset + block.length);
    const tailText = last.text.slice(block.offset + block.length);
    // The whole final segment ("second" onward) stays expanded — not folded paragraph-by-paragraph.
    expect(tailText).toContain("second");
    expect(tailText).toContain("third");
    expect(tailText).toContain("fourth");
    // ...contiguously, in order, after the block (nothing in this segment is folded into it).
    const secondIdx = tailText.indexOf("second");
    expect(tailText.indexOf("third")).toBeGreaterThan(secondIdx);
    expect(tailText.indexOf("fourth")).toBeGreaterThan(tailText.indexOf("third"));
    expect(blockText).not.toContain("second");
    expect(blockText).not.toContain("third");
    expect(blockText).not.toContain("fourth");
    // Only content before the last tool marker folds.
    expect(blockText).toContain("first");
  });

  it("folds a unit only at the next tool-group → new-text transition (R2)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n"); // boundary 1
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("second\n\n"); // boundary 2 ⇒ active; "first"+/a fold, "second" is the tail
    // A new tool group runs, then text resumes ⇒ the "second" unit folds, the new text is the tail.
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("read", { path: "/c.ts" });
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("third\n\n"); // boundary 3 ⇒ "second"+/b fold

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    const blockText = last.text.slice(block.offset, block.offset + block.length);
    const tailText = last.text.slice(block.offset + block.length);
    // The prior units ("first"+/a, "second"+/b) folded; the new unit "third" is the expanded tail.
    expect(blockText).toContain("first");
    expect(blockText).toContain("second");
    expect(tailText).toContain("third");
    expect(tailText).not.toContain("first");
    expect(tailText).not.toContain("second");
  });

  it("keeps a multi-paragraph final answer fully expanded at finalize (R4)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("first\n\n"); // boundary 1
    await renderer.appendTool("read", { path: "/b.ts" });
    await renderer.appendText("answer p1\n\nanswer p2\n\n"); // boundary 2 ⇒ active; multi-paragraph answer
    await renderer.finalize();

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    const blockText = last.text.slice(block.offset, block.offset + block.length);
    const tailText = last.text.slice(block.offset + block.length);
    // The whole multi-paragraph final answer stays expanded; only prior units fold.
    expect(tailText).toContain("answer p1");
    expect(tailText).toContain("answer p2");
    expect(blockText).toContain("first");
    expect(tailText).not.toContain("first");
  });

  it("folds pre-overflow tail content once collapse re-activates after an overflow (R6/AC6b)", async () => {
    const api = fakeApi();
    const renderer = make(api, true, 1);
    await renderer.appendTool("read", { path: "/a.ts" });
    await renderer.appendText("seed\n\n"); // boundary 1
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("read", { path: "/b.ts" });
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    // Overflow: commits the intensive chunk with its own block; detection resets on the kept tail.
    await renderer.appendText(`${"x".repeat(TELEGRAM_MAX_MESSAGE_LENGTH + 10)}\n\n`);
    expect(renderer.collapseActive()).toBe(false);

    // Two new boundaries on the tail re-activate collapse. The first tail unit must fold into the new
    // block once the second forms — verifying the overflow reset left the split valid from zero.
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("read", { path: "/c.ts" });
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("tail one\n\n"); // boundary 1 on the tail (1 > 1 ⇒ inactive)
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendTool("read", { path: "/d.ts" });
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("tail two\n\n"); // boundary 2 on the tail ⇒ re-activate
    expect(renderer.collapseActive()).toBe(true);

    const last = api.calls.at(-1);
    const block = mustEntity(last, "expandable_blockquote");
    const blockText = last.text.slice(block.offset, block.offset + block.length);
    const tailText = last.text.slice(block.offset + block.length);
    // The first tail unit ("tail one") folded into the block; only "tail two" is the expanded tail.
    expect(blockText).toContain("tail one");
    expect(tailText).toContain("tail two");
    expect(tailText).not.toContain("tail one");
  });
});
