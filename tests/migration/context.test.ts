import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import type { Logger } from "../../src/log.ts";
import { adaptContextFiles } from "../../src/migration/context.ts";
import { Workspace } from "../../src/workspace.ts";

const fakeLog = { info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const makeWorkspace = async (): Promise<Workspace> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-migration-ctx-"));
  return new Workspace(dir);
};

describe("adaptContextFiles", () => {
  it("moves context/ files to the workspace root and removes the empty dir", async () => {
    const workspace = await makeWorkspace();
    const oldDir = workspace.resolve("context");
    await mkdir(oldDir, { recursive: true });
    await writeFile(join(oldDir, "SOUL.md"), "# Soul\n", "utf8");
    await writeFile(join(oldDir, "USER.md"), "# User\n", "utf8");
    await writeFile(join(oldDir, "AGENTS.md"), "# Agents\n", "utf8");

    await adaptContextFiles(workspace, fakeLog);

    await expect(readFile(workspace.resolve("SOUL.md"), "utf8")).resolves.toBe("# Soul\n");
    await expect(readFile(workspace.resolve("USER.md"), "utf8")).resolves.toBe("# User\n");
    await expect(readFile(workspace.resolve("AGENTS.md"), "utf8")).resolves.toBe("# Agents\n");
    expect(existsSync(oldDir)).toBe(false);
  });

  it("keeps the root file when both locations exist", async () => {
    const workspace = await makeWorkspace();
    const oldDir = workspace.resolve("context");
    await mkdir(oldDir, { recursive: true });
    await writeFile(join(oldDir, "SOUL.md"), "old soul\n", "utf8");
    await writeFile(workspace.resolve("SOUL.md"), "new soul\n", "utf8");

    await adaptContextFiles(workspace, fakeLog);

    await expect(readFile(workspace.resolve("SOUL.md"), "utf8")).resolves.toBe("new soul\n");
    await expect(readFile(join(oldDir, "SOUL.md"), "utf8")).resolves.toBe("old soul\n");
  });

  it("is a no-op without a context/ directory", async () => {
    const workspace = await makeWorkspace();

    await expect(adaptContextFiles(workspace, fakeLog)).resolves.toBeUndefined();

    expect(existsSync(workspace.resolve("SOUL.md"))).toBe(false);
  });
});
