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
  it("targets the topics store, folds all durable signal types, and has no classification self-check", () => {
    const instruction = storeInstruction("topics", workspace);

    expect(instruction).toContain(join(workspace, "memories", "topics"));
    expect(instruction).toContain("SILENT background memory-maintenance step");
    expect(instruction).toContain("Do NOT");
    // The unified topics instruction folds every kind of durable signal into one store.
    expect(instruction).toContain("reference facts");
    expect(instruction).toContain("preferences");
    expect(instruction).toContain("memories/topics/");
    // The criteria are broadened beyond facts + preferences: insights, decisions, and patterns are in scope too.
    expect(instruction).toContain("Insights from content consumed");
    expect(instruction).toContain("Observed patterns");
    // It contains no facts-vs-preferences classification self-check.
    expect(instruction).not.toContain("Classification self-check");
    expect(instruction).not.toContain("classification self-check");
  });

  it("folds learnings into the topics fork under one shared, file-tool-limited instruction", () => {
    const instruction = storeInstruction("topics", workspace);

    // The learnings extraction section is folded into this fork (R4: one pass classifies each signal
    // inline — topic content to topics/, learning content to learnings/).
    expect(instruction).toContain("## Drafts");
    expect(instruction).toContain("## Confirmed");
    // Inline topic-vs-learning classification: one store wins, by primary aspect (the two never duplicate).
    expect(instruction).toContain("never both");
    expect(instruction).toContain("primary");
    // Read-before-promote: the agent reads existing learnings to match a recurring friction, and never
    // promotes a resolved friction (resolution is the opposite of recurrence).
    expect(instruction).toContain("Read existing learnings");
    expect(instruction).toContain("never promote");
    // One-time events are excluded from learnings (they stay episodic — only experience belongs here).
    expect(instruction).toContain("One-time events");
    expect(instruction).toContain("does NOT belong in learnings");
    // INDEX_UPDATE_SECTION is present — the fork writes both stores, so it keeps both indexes in sync.
    expect(instruction).toContain("## Memory Index");
    expect(instruction).toContain("MEMORY.md in the same directory");

    // The fork writes BOTH directories: both appear in the scope section and the silent-background clause.
    const bothDirs = `\`${join(workspace, "memories", "topics")}/\` and \`${join(workspace, "memories", "learnings")}/\``;
    expect(instruction).toContain(`Only create or modify files within ${bothDirs}.`);
    expect(instruction).toContain(`create or modify files\nunder ${bothDirs}.`);
  });

  it("leaves the episodic instruction single-store and untouched by the learnings fold", () => {
    const instruction = storeInstruction("episodic", workspace);

    // Episodic is its own fork — it never carries the learnings fold or a learnings write surface.
    expect(instruction).not.toContain("## Drafts");
    expect(instruction).not.toContain("## Confirmed");
    expect(instruction).not.toContain("memories/learnings/");
    expect(instruction).toContain("memories/episodic/");
  });

  it("stamps today's date and the conversation framing for episodic", () => {
    const instruction = storeInstruction("episodic", workspace);

    expect(instruction).toContain("We just finished the conversation above");
    expect(instruction).toContain("Today's date is");
    expect(instruction).toContain(join(workspace, "memories", "episodic"));
    // Episodic keeps a generic summary of the day's activity, not just notable one-time events.
    expect(instruction).toContain("summary of what happened");
  });

  it("stamps the given day (not wall-clock) so a late close files episodic under its real date", () => {
    const instruction = storeInstruction("episodic", workspace, "2026-06-13");

    expect(instruction).toContain("Today's date is 2026-06-13.");
    // The per-day filename instruction carries the same date.
    expect(instruction).toContain("2026-06-13.md");
  });
});
