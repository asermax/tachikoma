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
  checkpointActive: false,
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

  it("returns set-checkpoint when the classifier detects a short side task beginning (no checkpoint active)", async () => {
    const fork = forkReturning('{"decision":"set-checkpoint","reason":"short side task"}');
    const deps = makeDeps({ shadowFork: vi.fn().mockResolvedValue(fork) });

    await expect(classifyShift(deps, input)).resolves.toBe("set-checkpoint");
    expect(fork.dispose).toHaveBeenCalledOnce();
  });

  it("returns summarize-to-checkpoint when the classifier detects the side task ending (checkpoint active)", async () => {
    const fork = forkReturning('{"decision":"summarize-to-checkpoint","reason":"back to main"}');
    const deps = makeDeps({ shadowFork: vi.fn().mockResolvedValue(fork) });

    await expect(classifyShift(deps, { ...input, checkpointActive: true })).resolves.toBe(
      "summarize-to-checkpoint",
    );
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

  it("fails open to continue on an unrecognized decision value", async () => {
    const fork = forkReturning('{"decision":"teleport","reason":"???"}');
    const deps = makeDeps({ shadowFork: vi.fn().mockResolvedValue(fork) });

    await expect(classifyShift(deps, input)).resolves.toBe("continue");
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

  it("offers set-checkpoint (not summarize) in the prompt when no checkpoint is active", async () => {
    const fork = forkReturning('{"decision":"continue"}');
    const shadowFork = vi.fn().mockResolvedValue(fork);
    const deps = makeDeps({ shadowFork });

    await classifyShift(deps, { ...input, checkpointActive: false });

    expect(fork.prompt).toHaveBeenCalledOnce();
    const prompt = fork.prompt.mock.calls[0]?.[0] as string;
    // The set-checkpoint conditional block is present; the summarize-to-checkpoint block is not. (The
    // shared JSON-enum line lists all four values, so assert on the block's distinctive phrasing.)
    expect(prompt).toContain("self-contained side task");
    // The broadened recognition examples (issue-411): a side task is distinct + self-contained, with
    // note/reminder captures and ceremonies as named cases — not gated on "clearly short (1-2 turns)".
    expect(prompt).toContain("note or reminder");
    expect(prompt).toContain("daily ceremony");
    expect(prompt).not.toContain("a checkpoint is currently active");
  });

  it("offers summarize-to-checkpoint (not set-checkpoint) in the prompt when a checkpoint is active", async () => {
    const fork = forkReturning('{"decision":"continue"}');
    const shadowFork = vi.fn().mockResolvedValue(fork);
    const deps = makeDeps({ shadowFork });

    await classifyShift(deps, { ...input, checkpointActive: true });

    expect(fork.prompt).toHaveBeenCalledOnce();
    const prompt = fork.prompt.mock.calls[0]?.[0] as string;
    expect(prompt).toContain("a checkpoint is currently active");
    expect(prompt).not.toContain("self-contained side task");
  });

  it("recognizes a return to the main line in the summarize-to-checkpoint prompt (explicit, topic-match, or orphan that fits the main line)", async () => {
    const fork = forkReturning('{"decision":"continue"}');
    const shadowFork = vi.fn().mockResolvedValue(fork);
    const deps = makeDeps({ shadowFork });

    await classifyShift(deps, { ...input, checkpointActive: true });

    const prompt = fork.prompt.mock.calls[0]?.[0] as string;
    // The return signal covers three cases (issue-419): an explicit "going back" reference, a clear
    // main-line topic match, and — the common case the broadening adds — a message that does not
    // continue the side task but fits the main line, even without naming it. The prompt leans toward
    // summarize-to-checkpoint over continue in that case, while still preferring continue when unsure.
    expect(prompt).toContain("returns to that main line");
    expect(prompt).toContain("going back to the report");
    expect(prompt).toContain("matches the main-line topic");
    expect(prompt).toContain("does NOT follow on from the side task's last turn");
    expect(prompt).toContain("fits the main line");
    expect(prompt).toContain("lean toward");
  });
});
