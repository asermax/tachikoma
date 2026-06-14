import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { PostProcessorContext } from "../../src/extensions/api.ts";
import type { Completer } from "../../src/extensions/git/commit.ts";
import { PUSH_RESULT } from "../../src/extensions/git/sync.ts";
import { fakeLogger } from "./helpers.ts";

const commitAll = vi.fn();
const smartPush = vi.fn();
const isDirty = vi.fn();
const listSubmodules = vi.fn();

vi.mock("../../src/extensions/git/commit.ts", () => ({
  commitAll: (...args: unknown[]) => commitAll(...args),
}));

vi.mock("../../src/extensions/git/sync.ts", async () => {
  const actual = await vi.importActual<typeof import("../../src/extensions/git/sync.ts")>(
    "../../src/extensions/git/sync.ts",
  );

  return {
    ...actual,
    smartPush: (...args: unknown[]) => smartPush(...args),
  };
});

vi.mock("../../src/extensions/projects/git.ts", () => ({
  isDirty: (...args: unknown[]) => isDirty(...args),
  listSubmodules: (...args: unknown[]) => listSubmodules(...args),
}));

const { createProjectsProcessor } = await import("../../src/extensions/projects/processor.ts");

const log = fakeLogger();

const context = (): PostProcessorContext => ({
  session: {} as SessionRecord,
  transcriptPath: null,
  log,
});

const side: Completer = { complete: vi.fn() };

beforeEach(() => {
  vi.clearAllMocks();
});

describe("projects processor (mocked git)", () => {
  it("skips entirely when there are no submodules", async () => {
    listSubmodules.mockResolvedValue([]);

    await createProjectsProcessor({ workspaceRoot: "/ws", side }).process(context());

    expect(isDirty).not.toHaveBeenCalled();
    expect(commitAll).not.toHaveBeenCalled();
    expect(log.debug).toHaveBeenCalledWith("no submodules found — skipping project processing");
  });

  it("skips committing when no submodule is dirty", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(false);

    await createProjectsProcessor({ workspaceRoot: "/ws", side }).process(context());

    expect(commitAll).not.toHaveBeenCalled();
    expect(log.debug).toHaveBeenCalledWith("no dirty submodules — skipping commit");
  });

  it("warns and skips a submodule whose dirty check rejected", async () => {
    listSubmodules.mockResolvedValue(["projects/broken", "projects/app"]);
    isDirty.mockImplementation(async (repoPath: string) => {
      if (repoPath.endsWith("broken")) throw new Error("not a git repo");
      return true;
    });
    commitAll.mockResolvedValue("Update app");
    smartPush.mockResolvedValue(PUSH_RESULT.pushed);

    await createProjectsProcessor({ workspaceRoot: "/ws", side }).process(context());

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/broken" }),
      "failed to check submodule status",
    );
    expect(commitAll).toHaveBeenCalledTimes(1);
  });

  it("warns when a push fails but leaves the commit in place", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(true);
    commitAll.mockResolvedValue("Update app");
    smartPush.mockResolvedValue(PUSH_RESULT.pushFailed);

    await createProjectsProcessor({ workspaceRoot: "/ws", side }).process(context());

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/app", result: PUSH_RESULT.pushFailed }),
      "push failed — changes remain committed locally",
    );
  });

  it("does not log a commit message when nothing was committed", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(true);
    commitAll.mockResolvedValue(null);
    smartPush.mockResolvedValue(PUSH_RESULT.nothingToPush);

    await createProjectsProcessor({ workspaceRoot: "/ws", side }).process(context());

    expect(log.info).not.toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.anything() }),
      "committed project changes",
    );
  });

  it("derives the project name from a path with no separator", async () => {
    listSubmodules.mockResolvedValue(["flat"]);
    isDirty.mockResolvedValue(true);
    commitAll.mockResolvedValue(null);
    smartPush.mockResolvedValue(PUSH_RESULT.pushed);

    await createProjectsProcessor({ workspaceRoot: "/ws", side }).process(context());

    expect(commitAll).toHaveBeenCalledWith(
      expect.objectContaining({ fallbackMessage: expect.stringContaining("flat") }),
    );
  });

  it("warns when commit-and-push for a submodule rejects", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(true);
    commitAll.mockRejectedValue(new Error("disk full"));

    await createProjectsProcessor({ workspaceRoot: "/ws", side }).process(context());

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/app" }),
      "failed to process submodule",
    );
  });
});
