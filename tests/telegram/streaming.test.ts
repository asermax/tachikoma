import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TELEGRAM_MAX_MESSAGE_LENGTH } from "../../src/extensions/telegram/chunking.ts";
import { toTelegramEntities } from "../../src/extensions/telegram/entities.ts";
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
}

const fakeApi = () => {
  const calls: ApiCall[] = [];
  let next = 0;

  return {
    calls,
    sendMessage: vi.fn(async (_chat: number, text: string) => {
      calls.push({ type: "send", text });
      next += 1;
      return { message_id: next };
    }),
    editMessageText: vi.fn(async (_chat: number, messageId: number, text: string) => {
      calls.push({ type: "edit", messageId, text });
      return true;
    }),
    deleteMessage: vi.fn(async (_chat: number, messageId: number) => {
      calls.push({ type: "delete", messageId });
      return true;
    }),
  };
};

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
    expect(api.calls.at(-1)).toEqual({
      type: "send",
      text: rendered("Let me search.\n\n_🔧 Searching for 'skippable'_"),
    });

    // Text after the tool bakes the summary marker, separated by blank lines.
    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("Found it.\n\n");
    expect(api.calls.at(-1)).toEqual({
      type: "edit",
      messageId: 1,
      text: rendered("Let me search.\n\n_🔧 Searching for `skippable`_\n\nFound it."),
    });
  });

  it("shows a marker-only message before any text arrives", async () => {
    const api = fakeApi();
    const renderer = new StreamRenderer(api, 42, fakeLog);

    await renderer.showTransient("Compacting…");

    expect(api.calls).toEqual([{ type: "send", text: rendered("_Compacting…_") }]);
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
    expect(api.calls.at(-1)).toEqual({
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
    expect(api.calls[0]).toEqual({
      type: "send",
      text: rendered("_📌 Checkpoint set — main line parked_\n\nFirst."),
    });

    vi.advanceTimersByTime(EDIT_THROTTLE_MS);
    await renderer.appendText("Second.\n\n");
    expect(api.calls.at(-1)).toEqual({
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
