import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ensureSkillEvolutionLayout,
  impactLogPath,
  skillEvolutionDir,
} from "../../src/extensions/skill-evolution/layout.ts";
import { IMPACT_LOG_FILENAME, readImpactLog } from "../../src/extensions/skill-evolution/store.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

let workspace: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-skill-evo-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
  vi.clearAllMocks();
});

describe("path helpers", () => {
  it("places the store under memories/skill-evolution (disjoint from memory's stores)", () => {
    expect(skillEvolutionDir(workspace)).toBe(join(workspace, "memories", "skill-evolution"));
    expect(impactLogPath(workspace)).toBe(
      join(workspace, "memories", "skill-evolution", IMPACT_LOG_FILENAME),
    );
  });
});

describe("ensureSkillEvolutionLayout", () => {
  it("seeds the index and the header-only impact ledger on first run", async () => {
    await ensureSkillEvolutionLayout(workspace, fakeLog);

    const dir = skillEvolutionDir(workspace);

    // R4: the one-line-per-pattern convention is documented in the seeded body.
    expect(await readFile(join(dir, "MEMORY.md"), "utf8")).toBe(
      `${[
        "# Skill Evolution Index",
        "",
        "One line per pattern page, in the form:",
        "`- [Title](./pattern-slug.md): PROBLEM — ROOT CAUSE — FIX`",
      ].join("\n")}\n`,
    );

    // S6: the ledger header carries the seven columns.
    expect(await readFile(impactLogPath(workspace), "utf8")).toContain(
      "| Date | Skill | Pattern | Branch | Tip | Description | Status |",
    );
  });

  it("seeds a ledger that parses as zero rows (seed and parser share one shape)", async () => {
    await ensureSkillEvolutionLayout(workspace, fakeLog);

    await expect(readImpactLog(impactLogPath(workspace), fakeLog)).resolves.toEqual([]);
    expect(fakeLog.warn).not.toHaveBeenCalled();
  });

  it("is idempotent — a second run leaves the seeds byte-identical", async () => {
    await ensureSkillEvolutionLayout(workspace, fakeLog);

    const dir = skillEvolutionDir(workspace);
    const firstIndex = await readFile(join(dir, "MEMORY.md"), "utf8");
    const firstLedger = await readFile(impactLogPath(workspace), "utf8");

    await ensureSkillEvolutionLayout(workspace, fakeLog);

    expect(await readFile(join(dir, "MEMORY.md"), "utf8")).toBe(firstIndex);
    expect(await readFile(impactLogPath(workspace), "utf8")).toBe(firstLedger);
    // Only the first run logged creations (one per seeded file).
    expect(fakeLog.info).toHaveBeenCalledTimes(2);
  });

  it("leaves user-edited files byte-identical (seed-only-when-absent)", async () => {
    const dir = skillEvolutionDir(workspace);
    await mkdir(dir, { recursive: true });
    await writeFile(join(dir, "MEMORY.md"), "# My own index\n", "utf8");
    await writeFile(join(dir, IMPACT_LOG_FILENAME), "# custom ledger\n", "utf8");

    await ensureSkillEvolutionLayout(workspace, fakeLog);

    expect(await readFile(join(dir, "MEMORY.md"), "utf8")).toBe("# My own index\n");
    expect(await readFile(join(dir, IMPACT_LOG_FILENAME), "utf8")).toBe("# custom ledger\n");
    expect(fakeLog.info).not.toHaveBeenCalled();
  });
});
