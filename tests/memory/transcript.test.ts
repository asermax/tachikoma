import { describe, expect, it } from "vitest";

import { parseTranscript, renderConversation } from "../../src/extensions/memory/transcript.ts";

const line = (value: unknown): string => JSON.stringify(value);

const transcript = [
  line({ type: "session", version: 3, id: "sess-1", timestamp: "2026-06-12T10:00:00Z", cwd: "/w" }),
  line({
    type: "message",
    id: "1",
    parentId: null,
    message: { role: "user", content: "hello there", timestamp: 1 },
  }),
  line({
    type: "message",
    id: "2",
    parentId: "1",
    message: {
      role: "assistant",
      content: [
        { type: "thinking", thinking: "let me think" },
        { type: "text", text: "hi! let me check something" },
        { type: "toolCall", id: "t1", name: "read", arguments: { path: "/w/notes.md" } },
      ],
    },
  }),
  line({
    type: "message",
    id: "3",
    parentId: "2",
    message: {
      role: "toolResult",
      toolCallId: "t1",
      toolName: "read",
      content: [{ type: "text", text: "secret file contents" }],
      isError: false,
    },
  }),
  "this line is not json {{{",
  line({ type: "model_change", id: "4", parentId: "3", provider: "anthropic", modelId: "x" }),
  line({
    type: "message",
    id: "5",
    parentId: "4",
    message: { role: "user", content: [{ type: "text", text: "thanks!" }], timestamp: 2 },
  }),
].join("\n");

describe("parseTranscript", () => {
  it("keeps user and assistant text, skipping tool noise and malformed lines", () => {
    expect(parseTranscript(transcript)).toEqual([
      { role: "user", text: "hello there" },
      { role: "assistant", text: "hi! let me check something" },
      { role: "user", text: "thanks!" },
    ]);
  });

  it("skips messages that carry no text at all", () => {
    const toolOnly = line({
      type: "message",
      id: "1",
      parentId: null,
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "t1", name: "grep", arguments: {} }],
      },
    });

    expect(parseTranscript(toolOnly)).toEqual([]);
  });
});

describe("renderConversation", () => {
  it("renders role-prefixed turns separated by blank lines", () => {
    const rendered = renderConversation(parseTranscript(transcript), 10_000);

    expect(rendered).toBe(
      "user: hello there\n\nassistant: hi! let me check something\n\nuser: thanks!",
    );
  });

  it("keeps the newest turns when over the cap", () => {
    const turns = [
      { role: "user" as const, text: "a".repeat(50) },
      { role: "user" as const, text: "b".repeat(50) },
      { role: "assistant" as const, text: "c".repeat(50) },
    ];

    const rendered = renderConversation(turns, 130);

    expect(rendered).toContain("[earlier conversation truncated]");
    expect(rendered).toContain("c".repeat(50));
    expect(rendered).toContain("b".repeat(50));
    expect(rendered).not.toContain("a".repeat(50));
    expect(rendered.indexOf("[earlier conversation truncated]")).toBe(0);
  });

  it("clips the tail of a single overlong turn", () => {
    const rendered = renderConversation([{ role: "user", text: `${"x".repeat(100)}END` }], 40);

    expect(rendered).toContain("END");
    expect(rendered.length).toBeLessThan(120);
  });
});
