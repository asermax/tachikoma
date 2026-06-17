import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";
import {
  createNotifyToolFactory,
  handleNotifyUser,
} from "../../src/extensions/notifications/tools.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { info: vi.fn(), warn: vi.fn() } as unknown as Logger;

describe("handleNotifyUser", () => {
  it("emits a notify event with the agent as source", () => {
    const emit = vi.fn();

    const message = handleNotifyUser(
      emit,
      {
        title: "Heads up",
        text: "the deploy finished",
        severity: "urgent",
      },
      fakeLog,
    );

    expect(message).toBe("Notification sent.");
    expect(emit).toHaveBeenCalledExactlyOnceWith("notify", {
      title: "Heads up",
      text: "the deploy finished",
      severity: "urgent",
      source: "agent",
    });
  });

  it("defaults severity to info", () => {
    const emit = vi.fn();

    handleNotifyUser(emit, { text: "fyi" }, fakeLog);

    expect(emit.mock.calls[0]?.[1]).toMatchObject({ severity: "info" });
  });

  it("rejects empty text", () => {
    const emit = vi.fn();

    expect(() => handleNotifyUser(emit, { text: "   " }, fakeLog)).toThrow(/cannot be empty/);
    expect(emit).not.toHaveBeenCalled();
  });
});

interface RegisteredTool {
  name: string;
  execute: (toolCallId: string, params: unknown) => Promise<{ content: { text: string }[] }>;
}

describe("createNotifyToolFactory", () => {
  it("registers notify_user and emits through the tool's execute", async () => {
    const emit = vi.fn();
    const tools: RegisteredTool[] = [];
    const pi = { registerTool: (tool: RegisteredTool) => tools.push(tool) };

    createNotifyToolFactory(emit, fakeLog)(pi as unknown as Parameters<ExtensionFactory>[0]);

    expect(tools.map((tool) => tool.name)).toEqual(["notify_user"]);

    const result = await tools[0]?.execute("call-1", { text: "ping" });

    expect(result?.content[0]?.text).toBe("Notification sent.");
    expect(emit.mock.calls[0]?.[1]).toMatchObject({ text: "ping", source: "agent" });
  });
});
