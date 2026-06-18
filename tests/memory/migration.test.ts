import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { FILE_EDIT_TOOLS } from "../../src/agent/file-tools.ts";
import { fileExists, memoriesRoot } from "../../src/extensions/memory/layout.ts";
import { type MigrationDeps, migrateMemoryStores } from "../../src/extensions/memory/migration.ts";

const tempDirs: string[] = [];

afterEach(async () => {
  while (tempDirs.length > 0) {
    await rm(tempDirs.pop() as string, { recursive: true, force: true });
  }
});

const makeWorkspace = async (): Promise<string> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-memory-migration-"));
  tempDirs.push(dir);
  await mkdir(memoriesRoot(dir), { recursive: true });
  return dir;
};

const legacyDir = (workspace: string, store: string): string =>
  join(memoriesRoot(workspace), store);

const writeLegacyFile = async (workspace: string, store: string, name: string, content: string) => {
  await mkdir(legacyDir(workspace, store), { recursive: true });
  await writeFile(join(legacyDir(workspace, store), name), content);
};

const writeInDir = async (dir: string, name: string, content: string) => {
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, name), content);
};

const namesIn = async (dir: string): Promise<string[]> => {
  try {
    return await readdir(dir);
  } catch {
    return [];
  }
};

/** A mocked fold agent: it writes one representative topic file and leaves the legacy stores
 * untouched (matching the real prompt, which forbids the agent from touching them). The host is
 * responsible for removing the legacy stores after the fold commits. Returns the side-run shape. */
const createFoldSide = (workspace: string) => ({
  run: vi.fn(async () => {
    const topics = join(memoriesRoot(workspace), "topics");
    await mkdir(topics, { recursive: true });
    await writeFile(
      join(topics, "work-info.md"),
      "# Work Info\nfolded reference + preference detail\n",
    );
    return { text: "" };
  }),
});

const buildDeps = (
  workspace: string,
  side: MigrationDeps["side"],
  commitChanges: MigrationDeps["commitChanges"],
): MigrationDeps => ({
  side,
  workspaceRoot: workspace,
  log: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } as unknown as MigrationDeps["log"],
  commitChanges,
});

describe("migrateMemoryStores — detection", () => {
  it("is a no-op on a fresh install (no facts/ or preferences/)", async () => {
    const workspace = await makeWorkspace();
    const side = { run: vi.fn().mockResolvedValue({ text: "" }) };
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));

    expect(side.run).not.toHaveBeenCalled();
    expect(commitChanges).not.toHaveBeenCalled();
  });

  it("is a no-op when already migrated (topics/ present, legacy stores absent)", async () => {
    const workspace = await makeWorkspace();
    await writeInDir(join(memoriesRoot(workspace), "topics"), "MEMORY.md", "# Memory Index\n");

    const side = { run: vi.fn().mockResolvedValue({ text: "" }) };
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));

    expect(side.run).not.toHaveBeenCalled();
    expect(commitChanges).not.toHaveBeenCalled();
  });

  it("is a no-op when topics/ has files but the legacy stores are empty (KD6: detection keys on old content, not topics/ presence)", async () => {
    // This is the mid-fold-crash state that would FALSE-"done" if detection keyed on topics/ presence:
    // topics/ already exists (fold created files) but the old stores still hold content would re-run;
    // here the old stores are empty so it correctly no-ops. The presence of topics/ files must NOT gate.
    const workspace = await makeWorkspace();
    await writeInDir(
      join(memoriesRoot(workspace), "topics"),
      "work-info.md",
      "# Work Info\nalready folded\n",
    );
    await writeInDir(join(memoriesRoot(workspace), "topics"), "MEMORY.md", "# Memory Index\n");

    const side = { run: vi.fn().mockResolvedValue({ text: "" }) };
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));

    expect(side.run).not.toHaveBeenCalled();
    expect(commitChanges).not.toHaveBeenCalled();
  });

  it("skips a 0-byte / whitespace-only legacy file without triggering a fold or creating a topic", async () => {
    const workspace = await makeWorkspace();
    await writeLegacyFile(workspace, "facts", "blank.md", "   \n\t\n");
    await writeLegacyFile(workspace, "preferences", "empty.md", "");

    const side = { run: vi.fn().mockResolvedValue({ text: "" }) };
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));

    expect(side.run).not.toHaveBeenCalled();
    expect(commitChanges).not.toHaveBeenCalled();
    expect(await fileExists(join(memoriesRoot(workspace), "topics", "blank.md"))).toBe(false);
  });

  it("does not count a .txt file or an empty .md as content (detection specificity)", async () => {
    const workspace = await makeWorkspace();
    await writeLegacyFile(workspace, "facts", "notes.txt", "not markdown");
    await writeLegacyFile(workspace, "preferences", "placeholder.md", "");

    const side = { run: vi.fn().mockResolvedValue({ text: "" }) };
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));

    expect(side.run).not.toHaveBeenCalled();
    expect(commitChanges).not.toHaveBeenCalled();
  });
});

describe("migrateMemoryStores — happy path", () => {
  it("folds legacy facts+preferences into topics in one side.run, then removes the old stores", async () => {
    const workspace = await makeWorkspace();
    await writeLegacyFile(workspace, "facts", "work-info.md", "# Work Info\ncompany + role\n");
    await writeLegacyFile(
      workspace,
      "preferences",
      "comms.md",
      "# Comms\nprefers concise replies\n",
    );

    const side = createFoldSide(workspace);
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));

    // One fold run over all old files.
    expect(side.run).toHaveBeenCalledTimes(1);
    const call = side.run.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(call.tools).toEqual(FILE_EDIT_TOOLS);
    expect(call.tier).toBe("processor");
    const system = String(call.system);
    expect(system).toContain("memories/facts/");
    expect(system).toContain("memories/preferences/");
    expect(system).toContain("memories/topics/");

    // The host removed the legacy stores outright, even though the fold agent left them untouched.
    expect(await fileExists(legacyDir(workspace, "facts"))).toBe(false);
    expect(await fileExists(legacyDir(workspace, "preferences"))).toBe(false);

    // A topic file was produced.
    expect(await fileExists(join(memoriesRoot(workspace), "topics", "work-info.md"))).toBe(true);
  });

  it("makes exactly two commits in order — fold commit BEFORE the removal, removal commit after", async () => {
    const workspace = await makeWorkspace();
    await writeLegacyFile(workspace, "facts", "work-info.md", "# Work Info\ndetail\n");
    await writeLegacyFile(workspace, "preferences", "comms.md", "# Comms\ndetail\n");

    const side = createFoldSide(workspace);

    // Capture, at each commit, whether the legacy files are still on disk — proving whether the
    // removal has run yet. The fold commit must precede the removal so the fold is git-durable first.
    const traces: string[] = [];
    const legacyCountAtCommit: number[] = [];
    const commitChanges = vi.fn(async (message: string) => {
      const count =
        (await namesIn(legacyDir(workspace, "facts"))).length +
        (await namesIn(legacyDir(workspace, "preferences"))).length;
      legacyCountAtCommit.push(count);
      traces.push(message);
    });

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));

    expect(commitChanges).toHaveBeenCalledTimes(2);
    expect(traces[0]).toBe("chore(memory): migrate facts+preferences into topics");
    expect(traces[1]).toBe("chore(memory): remove legacy memory stores");
    // At the fold commit the legacy files were still on disk (removal had not run yet).
    expect(legacyCountAtCommit[0]).toBeGreaterThan(0);
    // At the removal commit they were gone.
    expect(legacyCountAtCommit[1]).toBe(0);
  });
});

describe("migrateMemoryStores — idempotency & failure", () => {
  it("is idempotent: a second call after a completed run is a no-op (legacy stores now empty)", async () => {
    const workspace = await makeWorkspace();
    await writeLegacyFile(workspace, "facts", "work-info.md", "# Work Info\ndetail\n");

    const side = createFoldSide(workspace);
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));
    expect(side.run).toHaveBeenCalledTimes(1);
    expect(commitChanges).toHaveBeenCalledTimes(2);

    side.run.mockClear();
    commitChanges.mockClear();

    await migrateMemoryStores(buildDeps(workspace, side, commitChanges));
    expect(side.run).not.toHaveBeenCalled();
    expect(commitChanges).not.toHaveBeenCalled();
  });

  it("re-runs after an interrupted fold: legacy content still present drives another side.run", async () => {
    const workspace = await makeWorkspace();
    await writeLegacyFile(workspace, "facts", "work-info.md", "# Work Info\ndetail\n");

    // Simulate a mid-fold crash: the first run does NOT empty the legacy files, and throws midway.
    const crashingSide = {
      run: vi.fn(async () => {
        throw new Error("agent crashed mid-fold");
      }),
    };
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await migrateMemoryStores(buildDeps(workspace, crashingSide, commitChanges));

    // Hard failure: aborted without sweeping or committing.
    expect(crashingSide.run).toHaveBeenCalledTimes(1);
    expect(commitChanges).not.toHaveBeenCalled();
    // Legacy content is untouched — the old file is still there with its content.
    expect(await fileExists(join(legacyDir(workspace, "facts"), "work-info.md"))).toBe(true);

    // On restart the check re-runs (old stores still hold content) and this time completes.
    const goodSide = createFoldSide(workspace);
    await migrateMemoryStores(buildDeps(workspace, goodSide, commitChanges));

    expect(goodSide.run).toHaveBeenCalledTimes(1);
    expect(commitChanges).toHaveBeenCalledTimes(2);
    expect(await fileExists(join(legacyDir(workspace, "facts"), "work-info.md"))).toBe(false);
  });

  it("on a hard side.run failure, sweeps nothing, commits nothing, leaves old files untouched, and does not throw", async () => {
    const workspace = await makeWorkspace();
    await writeLegacyFile(workspace, "facts", "work-info.md", "# Work Info\ndetail\n");
    await writeLegacyFile(workspace, "preferences", "comms.md", "# Comms\ndetail\n");

    const side = {
      run: vi.fn().mockRejectedValue(new Error("provider unavailable")),
    };
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await expect(
      migrateMemoryStores(buildDeps(workspace, side, commitChanges)),
    ).resolves.toBeUndefined();

    expect(side.run).toHaveBeenCalledTimes(1);
    expect(commitChanges).not.toHaveBeenCalled();
    // No sweep happened — old files survive with their content.
    expect(await fileExists(join(legacyDir(workspace, "facts"), "work-info.md"))).toBe(true);
    expect(await fileExists(join(legacyDir(workspace, "preferences"), "comms.md"))).toBe(true);
  });
});
