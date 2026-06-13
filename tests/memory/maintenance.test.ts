import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Runner } from "../../src/extensions/memory/extraction.ts";
import {
  buildCrossStoreManifest,
  buildStoreManifestForContext,
  type MaintenanceDeps,
  runContextMaintenanceTick,
  runMaintenanceTick,
} from "../../src/extensions/memory/maintenance.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const settings = { recentDays: 15, weeklyThresholdMonths: 3, monthlyThresholdMonths: 12 };

// 2026-06-08 is a Monday, 2026-06-14 is a Sunday.
const monday = () => new Date(2026, 5, 8, 3, 0, 0);
const sunday = () => new Date(2026, 5, 14, 3, 0, 0);

const fakeRunner = () => {
  const run = vi.fn().mockResolvedValue({ text: "done" });
  const side: Runner = { run };
  return { side, run };
};

let workspace: string;

const deps = (side: Runner, now: () => Date): MaintenanceDeps => ({
  side,
  workspaceRoot: workspace,
  settings,
  log: fakeLog,
  now,
});

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-memory-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("runMaintenanceTick", () => {
  it("runs the episodic consolidation prompt with the configured thresholds", async () => {
    const { side, run } = fakeRunner();

    await runMaintenanceTick("episodic", deps(side, monday));

    const options = run.mock.calls[0]?.[0];
    expect(options.tier).toBe("processor");
    expect(options.system).toContain(join(workspace, "memories", "episodic"));
    expect(options.system).toContain("last 15 days");
    expect(options.system).toContain("Older than 12 months");
    expect(options.system).not.toContain("Memory Index");
  });

  it("uses the light index consistency section on weekdays", async () => {
    const { side, run } = fakeRunner();

    await runMaintenanceTick("facts", deps(side, monday));

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain("## Memory Index Consistency");
    expect(system).not.toContain("## Memory Index Rebuild (full)");
  });

  it("commits the pass with a store-specific message after sweeping", async () => {
    const { side } = fakeRunner();
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await runMaintenanceTick("facts", { ...deps(side, monday), commitChanges });

    expect(commitChanges).toHaveBeenCalledWith("chore(memory): scheduled facts maintenance");
  });

  it("uses the full index rebuild section on Sundays", async () => {
    const { side, run } = fakeRunner();

    await runMaintenanceTick("preferences", deps(side, sunday));

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain("## Memory Index Rebuild (full)");
    expect(system).toContain(join(workspace, "memories", "preferences"));
  });

  it("sweeps files the maintenance agent emptied", async () => {
    const factsDir = join(workspace, "memories", "facts");
    await mkdir(factsDir, { recursive: true });
    await writeFile(join(factsDir, "keep.md"), "durable fact", "utf8");
    await writeFile(join(factsDir, "obsolete.md"), "", "utf8");

    const { side } = fakeRunner();
    await runMaintenanceTick("facts", deps(side, monday));

    expect((await readdir(factsDir)).sort()).toEqual(["keep.md"]);
  });
});

describe("buildCrossStoreManifest", () => {
  it("lists the other stores and root context files", async () => {
    await mkdir(join(workspace, "memories", "episodic"), { recursive: true });
    await writeFile(join(workspace, "memories", "episodic", "2026-06-10.md"), "day", "utf8");
    await writeFile(join(workspace, "USER.md"), "# User", "utf8");

    const manifest = await buildCrossStoreManifest(workspace, "facts");

    expect(manifest).toContain("### Episodic Files");
    expect(manifest).toContain("- `memories/episodic/2026-06-10.md`");
    expect(manifest).toContain("### Context Files");
    expect(manifest).toContain("- `USER.md` (workspace root)");
    expect(manifest).not.toContain("### Facts Files");
  });

  it("returns null when nothing else exists", async () => {
    expect(await buildCrossStoreManifest(workspace, "facts")).toBeNull();
  });
});

describe("runContextMaintenanceTick", () => {
  it("reviews the three foundational context files conservatively", async () => {
    const { side, run } = fakeRunner();

    await runContextMaintenanceTick({ side, workspaceRoot: workspace, log: fakeLog });

    const options = run.mock.calls[0]?.[0];
    expect(options.tier).toBe("processor");
    expect(options.system).toContain(join(workspace, "SOUL.md"));
    expect(options.system).toContain(join(workspace, "USER.md"));
    expect(options.system).toContain(join(workspace, "AGENTS.md"));

    // Cleanup-only and conservative — no new content, no destructive scope beyond the three files.
    expect(options.system).toContain("Cleanup-only");
    expect(options.system).toContain("Do NOT add new content");
    expect(options.system).toContain("especially conservative");
    expect(options.system).not.toContain("Memory Index");
  });

  it("includes a names-only memory store manifest so context can defer to facts", async () => {
    await mkdir(join(workspace, "memories", "facts"), { recursive: true });
    await writeFile(join(workspace, "memories", "facts", "tech-stack.md"), "stack", "utf8");

    const { side, run } = fakeRunner();
    await runContextMaintenanceTick({ side, workspaceRoot: workspace, log: fakeLog });

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain("## Memory Store Visibility");
    expect(system).toContain("- `memories/facts/tech-stack.md`");
  });

  it("does not write or delete files itself — edits are left to the side agent", async () => {
    await writeFile(join(workspace, "USER.md"), "# User\n", "utf8");

    const { side } = fakeRunner();
    await runContextMaintenanceTick({ side, workspaceRoot: workspace, log: fakeLog });

    expect((await readdir(workspace)).includes("USER.md")).toBe(true);
  });
});

describe("buildStoreManifestForContext", () => {
  it("lists the memory stores by name only", async () => {
    await mkdir(join(workspace, "memories", "preferences"), { recursive: true });
    await writeFile(join(workspace, "memories", "preferences", "style.md"), "x", "utf8");

    const manifest = await buildStoreManifestForContext(workspace);

    expect(manifest).toContain("### Preferences Files");
    expect(manifest).toContain("- `memories/preferences/style.md`");
  });

  it("returns null when no stores have files", async () => {
    expect(await buildStoreManifestForContext(workspace)).toBeNull();
  });
});
