import { beforeEach, describe, expect, it, vi } from "vitest";

const smartPush = vi.hoisted(() => vi.fn());
const listSubmodules = vi.hoisted(() => vi.fn());
const hasRemote = vi.hoisted(() => vi.fn());
const commitAll = vi.hoisted(() => vi.fn());

vi.mock("../../src/git/sync.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/git/sync.ts")>()),
  smartPush,
}));

vi.mock("../../src/git/git.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/git/git.ts")>()),
  listSubmodules,
  hasRemote,
}));

vi.mock("../../src/git/commit.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/git/commit.ts")>()),
  commitAll,
}));

const { handleCommitWorkspace } = await import("../../src/extensions/git/tools.ts");
const { PUSH_RESULT } = await import("../../src/git/sync.ts");

const resolver = vi.fn();

const deps = {
  workspaceRoot: "/ws",
  agent: async () => {},
  log: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  resolver,
};

beforeEach(() => {
  vi.clearAllMocks();
  hasRemote.mockResolvedValue(true);
});

describe("handleCommitWorkspace — push orchestration", () => {
  it("forwards the resolver to smartPush", async () => {
    commitAll.mockResolvedValue([]);
    listSubmodules.mockResolvedValue([]);
    smartPush.mockResolvedValue(PUSH_RESULT.nothingToPush);

    await handleCommitWorkspace(deps, {});

    expect(smartPush).toHaveBeenCalledWith("/ws", "origin", "HEAD", expect.anything(), resolver);
  });

  it("does not push when push is false", async () => {
    commitAll.mockResolvedValue(["Save changes"]);
    listSubmodules.mockResolvedValue(["projects/app"]);

    const output = await handleCommitWorkspace(deps, { push: false });

    expect(smartPush).not.toHaveBeenCalled();
    expect(output).toBe("Committed workspace changes: Save changes");
  });

  it("skips repos without an origin remote", async () => {
    commitAll.mockResolvedValue(["Save changes"]);
    hasRemote.mockResolvedValue(false);
    listSubmodules.mockResolvedValue(["projects/app"]);

    const output = await handleCommitWorkspace(deps, {});

    expect(smartPush).not.toHaveBeenCalled();
    expect(output).toBe("Committed workspace changes: Save changes");
  });

  it("omits push lines when every repo is up to date", async () => {
    commitAll.mockResolvedValue([]);
    listSubmodules.mockResolvedValue(["projects/app"]);
    smartPush.mockResolvedValue(PUSH_RESULT.nothingToPush);

    const output = await handleCommitWorkspace(deps, {});

    expect(output).toBe("Nothing to commit — the working tree is clean.");
  });

  it("reports each submodule's outcome in listSubmodules order and never throws", async () => {
    commitAll.mockResolvedValue([]);
    listSubmodules.mockResolvedValue(["projects/alpha", "projects/beta"]);
    smartPush.mockImplementation(async (cwd: string) => {
      if (cwd.endsWith("alpha")) return PUSH_RESULT.pushed;
      if (cwd.endsWith("beta")) return PUSH_RESULT.pushFailed;
      return PUSH_RESULT.nothingToPush; // workspace (/ws)
    });

    const output = await handleCommitWorkspace(deps, {});

    expect(output).toBe(
      "Nothing to commit — the working tree is clean.\n" +
        "Pushed project 'alpha' to origin.\n" +
        "Project 'beta' push failed — changes remain committed locally.",
    );
    expect(smartPush).toHaveBeenCalledTimes(3);
  });

  it("surfaces a failure line (and keeps pushing siblings) when a submodule push throws", async () => {
    commitAll.mockResolvedValue([]);
    listSubmodules.mockResolvedValue(["projects/broken", "projects/ok"]);
    smartPush.mockImplementation(async (cwd: string) => {
      if (cwd.endsWith("broken")) throw new Error("network down");
      if (cwd.endsWith("ok")) return PUSH_RESULT.pushed;
      return PUSH_RESULT.nothingToPush;
    });

    const output = await handleCommitWorkspace(deps, {});

    // The throwing repo surfaces a failure line (never silently dropped), its
    // sibling still pushes in listSubmodules order, and the tool did not throw.
    expect(output).toBe(
      "Nothing to commit — the working tree is clean.\n" +
        "Project 'broken' push failed — changes remain committed locally.\n" +
        "Pushed project 'ok' to origin.",
    );
  });
});
