import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { storeInstruction } from "../../src/extensions/memory/extraction.ts";

let workspace: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-memory-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("storeInstruction", () => {
  it("targets the store directory and instructs a silent background step", () => {
    const instruction = storeInstruction("facts", workspace);

    expect(instruction).toContain(join(workspace, "memories", "facts"));
    expect(instruction).toContain("SILENT background memory-maintenance step");
    expect(instruction).toContain("Do NOT");
  });

  it("stamps today's date and the conversation framing for episodic", () => {
    const instruction = storeInstruction("episodic", workspace);

    expect(instruction).toContain("We just finished the conversation above");
    expect(instruction).toContain("Today's date is");
    expect(instruction).toContain(join(workspace, "memories", "episodic"));
  });
});
