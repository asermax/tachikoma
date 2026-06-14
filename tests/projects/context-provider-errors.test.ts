import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeLogger } from "./helpers.ts";

const listSubmodules = vi.fn();
const projectState = vi.fn();
const describeProjectState = vi.fn(
  (state: { name: string; branch: string | null }) => `- ${state.name}: ${state.branch}`,
);

vi.mock("../../src/extensions/projects/git.ts", () => ({
  listSubmodules: (...args: unknown[]) => listSubmodules(...args),
  projectState: (...args: unknown[]) => projectState(...args),
  describeProjectState: (...args: unknown[]) => describeProjectState(...args),
}));

const { buildProjectsContext } = await import("../../src/extensions/projects/context-provider.ts");

beforeEach(() => {
  listSubmodules.mockReset();
  projectState.mockReset();
});

describe("buildProjectsContext error paths", () => {
  it("warns and treats the workspace as empty when listing submodules throws", async () => {
    const log = fakeLogger();
    listSubmodules.mockRejectedValue(new Error("git exploded"));

    const content = await buildProjectsContext("/ws", log);

    expect(content).toContain("No projects are currently registered.");
    expect(log.warn).toHaveBeenCalledWith(expect.anything(), "failed to list submodules");
  });

  it("skips and warns for submodules whose state lookup rejects", async () => {
    const log = fakeLogger();
    listSubmodules.mockResolvedValue(["projects/ok", "projects/broken"]);
    projectState.mockImplementation(async (_root: string, path: string) => {
      if (path === "projects/broken") throw new Error("state failed");

      return { name: "ok", branch: "main", commit: "abc", dirtyFiles: 0 };
    });

    const content = await buildProjectsContext("/ws", log);

    expect(content).toContain("## Registered Projects");
    expect(content).toContain("- ok: main");
    expect(log.warn).toHaveBeenCalledWith(expect.anything(), "failed to get project info");
  });
});
