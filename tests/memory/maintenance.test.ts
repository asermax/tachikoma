import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Runner } from "../../src/extensions/memory/extraction.ts";
import {
  buildCrossStoreManifest,
  buildStoreManifestForContext,
  type MaintenanceDeps,
  maintenanceSystemPrompt,
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

    await runMaintenanceTick("topics", deps(side, monday));

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain("## Memory Index Consistency");
    expect(system).not.toContain("## Memory Index Rebuild (full)");
  });

  it("commits the pass with a store-specific message after sweeping", async () => {
    const { side } = fakeRunner();
    const commitChanges = vi.fn().mockResolvedValue(undefined);

    await runMaintenanceTick("topics", { ...deps(side, monday), commitChanges });

    expect(commitChanges).toHaveBeenCalledWith("chore(memory): scheduled topics maintenance");
  });

  it("uses the full index rebuild section on Sundays", async () => {
    const { side, run } = fakeRunner();

    await runMaintenanceTick("topics", deps(side, sunday));

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain("## Memory Index Rebuild (full)");
    expect(system).toContain(join(workspace, "memories", "topics"));
  });

  it("sweeps files the maintenance agent emptied", async () => {
    const topicsDir = join(workspace, "memories", "topics");
    await mkdir(topicsDir, { recursive: true });
    await writeFile(join(topicsDir, "keep.md"), "durable topic", "utf8");
    await writeFile(join(topicsDir, "obsolete.md"), "", "utf8");

    const { side } = fakeRunner();
    await runMaintenanceTick("topics", deps(side, monday));

    expect((await readdir(topicsDir)).sort()).toEqual(["keep.md"]);
  });

  it("uses the unified topics maintenance prompt with no misclassification section", async () => {
    const { side, run } = fakeRunner();

    await runMaintenanceTick("topics", deps(side, monday));

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain("topics memory cleanup");
    // Folds both signal types into one store, consolidates clusters, ~50-line size guard.
    expect(system).toContain("Cluster Consolidation");
    expect(system).toContain("~50 lines");
    // No second store to misclassify into — that section is gone.
    expect(system).not.toContain("Misclassification");
  });
});

describe("buildCrossStoreManifest", () => {
  it("lists the other stores and root context files, never the old store names", async () => {
    await mkdir(join(workspace, "memories", "episodic"), { recursive: true });
    await writeFile(join(workspace, "memories", "episodic", "2026-06-10.md"), "day", "utf8");
    await writeFile(join(workspace, "USER.md"), "# User", "utf8");

    const manifest = await buildCrossStoreManifest(workspace, "topics");

    expect(manifest).toContain("### Episodic Files");
    expect(manifest).toContain("- `memories/episodic/2026-06-10.md`");
    expect(manifest).toContain("### Context Files");
    expect(manifest).toContain("- `USER.md` (workspace root)");
    // MEMORY_STORES is now episodic + topics only — the current store is excluded,
    // and the old facts/preferences store names never appear.
    expect(manifest).not.toContain("### Topics Files");
    expect(manifest).not.toContain("### Facts Files");
    expect(manifest).not.toContain("### Preferences Files");
  });

  it("skips other stores whose directories hold no markdown files", async () => {
    await mkdir(join(workspace, "memories", "episodic"), { recursive: true });
    await writeFile(join(workspace, "memories", "episodic", "notes.txt"), "not md", "utf8");
    await writeFile(join(workspace, "USER.md"), "# User", "utf8");

    const manifest = await buildCrossStoreManifest(workspace, "topics");

    // Episodic holds no .md files, so it is skipped; only the context file is listed.
    expect(manifest).toContain("### Context Files");
    expect(manifest).not.toContain("### Episodic Files");
  });

  it("returns null when nothing else exists", async () => {
    expect(await buildCrossStoreManifest(workspace, "topics")).toBeNull();
  });
});

describe("maintenanceSystemPrompt", () => {
  it("falls back to the system clock when no clock is injected", async () => {
    const prompt = await maintenanceSystemPrompt("topics", { workspaceRoot: workspace, settings });

    expect(prompt).toContain("topics memory cleanup");
  });

  it("appends the cross-store manifest when another store has files", async () => {
    await mkdir(join(workspace, "memories", "episodic"), { recursive: true });
    await writeFile(join(workspace, "memories", "episodic", "2026-06-10.md"), "day", "utf8");

    const prompt = await maintenanceSystemPrompt("topics", {
      workspaceRoot: workspace,
      settings,
      now: monday,
    });

    expect(prompt).toContain("## Cross-Store Visibility");
    expect(prompt).toContain("- `memories/episodic/2026-06-10.md`");
  });

  it("omits the cross-store manifest when no other stores have files", async () => {
    const prompt = await maintenanceSystemPrompt("topics", {
      workspaceRoot: workspace,
      settings,
      now: monday,
    });

    expect(prompt).not.toContain("## Cross-Store Visibility");
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

  it("includes a names-only memory store manifest so context can defer to topics", async () => {
    await mkdir(join(workspace, "memories", "topics"), { recursive: true });
    await writeFile(join(workspace, "memories", "topics", "tech-stack.md"), "stack", "utf8");

    const { side, run } = fakeRunner();
    await runContextMaintenanceTick({ side, workspaceRoot: workspace, log: fakeLog });

    const system = run.mock.calls[0]?.[0].system;
    expect(system).toContain("## Memory Store Visibility");
    expect(system).toContain("- `memories/topics/tech-stack.md`");
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
    await mkdir(join(workspace, "memories", "topics"), { recursive: true });
    await writeFile(join(workspace, "memories", "topics", "style.md"), "x", "utf8");

    const manifest = await buildStoreManifestForContext(workspace);

    expect(manifest).toContain("### Topics Files");
    expect(manifest).toContain("- `memories/topics/style.md`");
    expect(manifest).not.toContain("### Facts Files");
    expect(manifest).not.toContain("### Preferences Files");
  });

  it("skips store directories that hold no markdown files", async () => {
    await mkdir(join(workspace, "memories", "episodic"), { recursive: true });
    await writeFile(join(workspace, "memories", "episodic", "raw.txt"), "not md", "utf8");
    await mkdir(join(workspace, "memories", "topics"), { recursive: true });
    await writeFile(join(workspace, "memories", "topics", "tech.md"), "x", "utf8");

    const manifest = await buildStoreManifestForContext(workspace);

    expect(manifest).not.toContain("### Episodic Files");
    expect(manifest).toContain("### Topics Files");
  });

  it("returns null when no stores have files", async () => {
    expect(await buildStoreManifestForContext(workspace)).toBeNull();
  });
});
