import { describe, expect, it, vi } from "vitest";

import { classifyShift, type ShiftDeps } from "../../src/extensions/boundary/classifier.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { error: vi.fn(), warn: vi.fn() } as unknown as Logger;

const forkReturning = (reply: string) => ({
  prompt: vi.fn().mockResolvedValue(reply),
  dispose: vi.fn().mockResolvedValue(undefined),
});

const makeDeps = (overrides: Partial<ShiftDeps> = {}): ShiftDeps => ({
  shadowFork: vi.fn(),
  getSystemPrompt: () => "live-prompt",
  log: fakeLog,
  ...overrides,
});

const input = {
  sessionFile: "/sessions/live.jsonl",
  currentBranchHasAssistantTurn: true,
  message: "let's talk about something else entirely",
};

describe("classifyShift", () => {
  it("returns continue on a short follow-up (classifier says continue)", async () => {
    const fork = forkReturning('{"decision":"continue","reason":"follow-up"}');
    const deps = makeDeps({ shadowFork: vi.fn().mockResolvedValue(fork) });

    await expect(classifyShift(deps, { ...input, message: "ok thanks" })).resolves.toBe("continue");
    expect(fork.dispose).toHaveBeenCalledOnce();
  });

  it("returns shift on a clear topic change", async () => {
    const fork = forkReturning('{"decision":"shift","reason":"new topic"}');
    const deps = makeDeps({ shadowFork: vi.fn().mockResolvedValue(fork) });

    await expect(classifyShift(deps, input)).resolves.toBe("shift");
    expect(fork.prompt).toHaveBeenCalledOnce();
    expect(fork.dispose).toHaveBeenCalledOnce();
  });

  it("fails open to continue when the shadow fork throws", async () => {
    const deps = makeDeps({ shadowFork: vi.fn().mockRejectedValue(new Error("fork down")) });

    await expect(classifyShift(deps, input)).resolves.toBe("continue");
  });

  it("fails open to continue on garbage classifier output", async () => {
    const fork = forkReturning("not json at all");
    const deps = makeDeps({ shadowFork: vi.fn().mockResolvedValue(fork) });

    await expect(classifyShift(deps, input)).resolves.toBe("continue");
    expect(fork.dispose).toHaveBeenCalledOnce();
  });

  it("skips classification (no fork) when the current branch has no assistant turn", async () => {
    const shadowFork = vi.fn();
    const deps = makeDeps({ shadowFork });

    await expect(
      classifyShift(deps, { ...input, currentBranchHasAssistantTurn: false }),
    ).resolves.toBe("continue");
    expect(shadowFork).not.toHaveBeenCalled();
  });

  it("inherits the live system prompt and the classifier tier", async () => {
    const fork = forkReturning('{"decision":"continue"}');
    const shadowFork = vi.fn().mockResolvedValue(fork);
    const deps = makeDeps({ shadowFork });

    await classifyShift(deps, input);

    expect(shadowFork).toHaveBeenCalledWith("/sessions/live.jsonl", {
      systemPrompt: "live-prompt",
      tier: "classifier",
    });
  });
});
