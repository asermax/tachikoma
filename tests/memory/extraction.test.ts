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
  it("targets the topics store, folds both signal types, and has no classification self-check", () => {
    const instruction = storeInstruction("topics", workspace);

    expect(instruction).toContain(join(workspace, "memories", "topics"));
    expect(instruction).toContain("SILENT background memory-maintenance step");
    expect(instruction).toContain("Do NOT");
    // The unified topics instruction folds both signal types into one store.
    expect(instruction).toContain("stable reference facts");
    expect(instruction).toContain("preferences");
    expect(instruction).toContain("memories/topics/");
    // It contains no facts-vs-preferences classification self-check.
    expect(instruction).not.toContain("Classification self-check");
    expect(instruction).not.toContain("classification self-check");
  });

  it("stamps today's date and the conversation framing for episodic", () => {
    const instruction = storeInstruction("episodic", workspace);

    expect(instruction).toContain("We just finished the conversation above");
    expect(instruction).toContain("Today's date is");
    expect(instruction).toContain(join(workspace, "memories", "episodic"));
  });
});
