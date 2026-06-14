import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeLogger } from "./helpers.ts";

const listSubmodules = vi.fn();
const initSubmodule = vi.fn();
const resolveDefaultBranch = vi.fn();
const checkoutBranch = vi.fn();
const smartPull = vi.fn();
const mkdir = vi.fn();

vi.mock("node:fs/promises", () => ({
  mkdir: (...args: unknown[]) => mkdir(...args),
}));

vi.mock("../../src/extensions/git/sync.ts", () => ({
  smartPull: (...args: unknown[]) => smartPull(...args),
}));

vi.mock("../../src/extensions/projects/git.ts", () => ({
  listSubmodules: (...args: unknown[]) => listSubmodules(...args),
  initSubmodule: (...args: unknown[]) => initSubmodule(...args),
  resolveDefaultBranch: (...args: unknown[]) => resolveDefaultBranch(...args),
  checkoutBranch: (...args: unknown[]) => checkoutBranch(...args),
}));

const { syncProjects } = await import("../../src/extensions/projects/hooks.ts");

beforeEach(() => {
  listSubmodules.mockReset();
  initSubmodule.mockReset().mockResolvedValue(undefined);
  resolveDefaultBranch.mockReset().mockResolvedValue("main");
  checkoutBranch.mockReset().mockResolvedValue(undefined);
  smartPull.mockReset().mockResolvedValue("up-to-date");
  mkdir.mockReset().mockResolvedValue(undefined);
});

describe("syncProjects retry behavior", () => {
  it("skips syncing when no submodules are registered", async () => {
    const log = fakeLogger();
    listSubmodules.mockResolvedValue([]);

    await syncProjects("/ws", log);

    expect(initSubmodule).not.toHaveBeenCalled();
    expect(log.debug).toHaveBeenCalledWith("no submodules found — skipping sync");
  });

  it("retries a submodule sync once after a transient failure", async () => {
    const log = fakeLogger();
    listSubmodules.mockResolvedValue(["projects/app"]);
    initSubmodule.mockRejectedValueOnce(new Error("init flaked")).mockResolvedValue(undefined);

    await syncProjects("/ws", log);

    expect(initSubmodule).toHaveBeenCalledTimes(2);
    expect(log.debug).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/app" }),
      "submodule sync failed — retrying",
    );
    expect(log.warn).not.toHaveBeenCalled();
  });

  it("warns when a submodule still fails after the retry", async () => {
    const log = fakeLogger();
    listSubmodules.mockResolvedValue(["projects/app"]);
    initSubmodule.mockRejectedValue(new Error("init dead"));

    await syncProjects("/ws", log);

    expect(initSubmodule).toHaveBeenCalledTimes(2);
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/app" }),
      "submodule sync failed after retry",
    );
  });
});
