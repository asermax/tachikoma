import { describe, expect, it, vi } from "vitest";

import { handleNotifyUser } from "../../src/extensions/notifications/tools.ts";

describe("handleNotifyUser", () => {
  it("emits a notify event with the agent as source", () => {
    const emit = vi.fn();

    const message = handleNotifyUser(emit, {
      title: "Heads up",
      text: "the deploy finished",
      severity: "urgent",
    });

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

    handleNotifyUser(emit, { text: "fyi" });

    expect(emit.mock.calls[0]?.[1]).toMatchObject({ severity: "info" });
  });

  it("rejects empty text", () => {
    const emit = vi.fn();

    expect(() => handleNotifyUser(emit, { text: "   " })).toThrow(/cannot be empty/);
    expect(emit).not.toHaveBeenCalled();
  });
});
