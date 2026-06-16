import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { GitApi, PostProcessorContext } from "../../src/extensions/api.ts";
import type { CommitAgent } from "../../src/git/commit-agent.ts";
import { PUSH_RESULT } from "../../src/git/sync.ts";
import { fakeLogger } from "./helpers.ts";

const commitAll = vi.fn();
const smartPush = vi.fn();
const isAhead = vi.fn();
const isDirty = vi.fn();
const listSubmodules = vi.fn();

vi.mock("../../src/extensions/projects/git.ts", () => ({
  isAhead: (...args: unknown[]) => isAhead(...args),
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

const agent: CommitAgent = async () => {};

const git = {
  commitAll: (...args: unknown[]) => commitAll(...args),
  smartPush: (...args: unknown[]) => smartPush(...args),
  smartPull: vi.fn(),
} as unknown as GitApi;

beforeEach(() => {
  vi.clearAllMocks();
  isAhead.mockResolvedValue(false);
});

describe("projects processor (mocked git)", () => {
  it("skips entirely when there are no submodules", async () => {
    listSubmodules.mockResolvedValue([]);

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(isDirty).not.toHaveBeenCalled();
    expect(commitAll).not.toHaveBeenCalled();
    expect(log.debug).toHaveBeenCalledWith("no submodules found — skipping project processing");
  });

  it("skips committing when no submodule is dirty or ahead", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(false);
    // isAhead defaults to false via beforeEach

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(commitAll).not.toHaveBeenCalled();
    expect(smartPush).not.toHaveBeenCalled();
    expect(log.debug).toHaveBeenCalledWith(
      "no dirty or ahead submodules — skipping project processing",
    );
  });

  it("pushes a clean submodule that is ahead of its remote without committing", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(false);
    isAhead.mockResolvedValue(true);
    smartPush.mockResolvedValue(PUSH_RESULT.pushed);

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(commitAll).not.toHaveBeenCalled();
    expect(smartPush).toHaveBeenCalledWith("/ws/projects/app", "origin", "HEAD", { log });
    expect(log.info).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/app", result: PUSH_RESULT.pushed }),
      "pushed ahead project changes",
    );
  });

  it("commits a dirty submodule and separately pushes a clean-ahead one", async () => {
    listSubmodules.mockResolvedValue(["projects/dirty", "projects/ahead"]);
    isDirty.mockImplementation(async (repoPath: string) => repoPath.endsWith("dirty"));
    isAhead.mockResolvedValue(true);
    commitAll.mockResolvedValue("Update dirty");
    smartPush.mockResolvedValue(PUSH_RESULT.pushed);

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(commitAll).toHaveBeenCalledTimes(1);
    expect(commitAll).toHaveBeenCalledWith(expect.objectContaining({ cwd: "/ws/projects/dirty" }));
    expect(smartPush).toHaveBeenCalledTimes(2);
    expect(smartPush).toHaveBeenCalledWith("/ws/projects/dirty", "origin", "HEAD", { log });
    expect(smartPush).toHaveBeenCalledWith("/ws/projects/ahead", "origin", "HEAD", { log });
  });

  it("warns and skips a clean submodule whose ahead check rejected", async () => {
    listSubmodules.mockResolvedValue(["projects/broken", "projects/ahead"]);
    isDirty.mockResolvedValue(false);
    isAhead.mockImplementation(async (repoPath: string) => {
      if (repoPath.endsWith("broken")) throw new Error("git rev-parse failed");
      return true;
    });
    smartPush.mockResolvedValue(PUSH_RESULT.pushed);

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/broken" }),
      "failed to check if submodule is ahead",
    );
    expect(smartPush).toHaveBeenCalledTimes(1);
    expect(smartPush).toHaveBeenCalledWith("/ws/projects/ahead", "origin", "HEAD", { log });
  });

  it("warns and skips a submodule whose dirty check rejected", async () => {
    listSubmodules.mockResolvedValue(["projects/broken", "projects/app"]);
    isDirty.mockImplementation(async (repoPath: string) => {
      if (repoPath.endsWith("broken")) throw new Error("not a git repo");
      return true;
    });
    commitAll.mockResolvedValue(["Update app"]);
    smartPush.mockResolvedValue(PUSH_RESULT.pushed);

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/broken" }),
      "failed to check submodule status",
    );
    expect(commitAll).toHaveBeenCalledTimes(1);
  });

  it("warns when a push fails but leaves the commit in place", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(true);
    commitAll.mockResolvedValue(["Update app"]);
    smartPush.mockResolvedValue(PUSH_RESULT.pushFailed);

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/app", result: PUSH_RESULT.pushFailed }),
      "push failed — changes remain committed locally",
    );
  });

  it("does not log a commit message when nothing was committed", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(true);
    commitAll.mockResolvedValue([]);
    smartPush.mockResolvedValue(PUSH_RESULT.nothingToPush);

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(log.info).not.toHaveBeenCalledWith(
      expect.objectContaining({ subjects: expect.anything() }),
      "committed project changes",
    );
  });

  it("derives the project name from a path with no separator", async () => {
    listSubmodules.mockResolvedValue(["flat"]);
    isDirty.mockResolvedValue(true);
    commitAll.mockResolvedValue([]);
    smartPush.mockResolvedValue(PUSH_RESULT.pushed);

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(commitAll).toHaveBeenCalledWith(
      expect.objectContaining({ fallbackMessage: expect.stringContaining("flat") }),
    );
  });

  it("warns when commit-and-push for a submodule rejects", async () => {
    listSubmodules.mockResolvedValue(["projects/app"]);
    isDirty.mockResolvedValue(true);
    commitAll.mockRejectedValue(new Error("disk full"));

    await createProjectsProcessor({ workspaceRoot: "/ws", agent, git }).process(context());

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path: "projects/app" }),
      "failed to process submodule",
    );
  });
});
