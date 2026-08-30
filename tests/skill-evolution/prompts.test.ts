import { describe, expect, it } from "vitest";

import {
  branchAnalysisInstruction,
  maintenanceSystemPrompt,
  proposalSystemPrompt,
} from "../../src/extensions/skill-evolution/prompts.ts";
import type { BranchRecord } from "../../src/sessions/trunk.ts";

const record = (branchId: string, summaryEntryId: string): BranchRecord => ({
  branchId,
  originalLeafId: `leaf-${summaryEntryId}`,
  baseId: null,
  summaryEntryId,
  lastExchange: null,
});

const WORKSPACE = "/tmp/tachi-workspace";
const DAY = "2026-08-30";

describe("branchAnalysisInstruction", () => {
  const instruction = branchAnalysisInstruction(WORKSPACE, record("topic-2", "s-2"), DAY);

  it("substitutes $WORKSPACE everywhere (no token survives)", () => {
    expect(instruction).toContain(`${WORKSPACE}/memories/skill-evolution/`);
    expect(instruction).toContain(`${WORKSPACE}/skills/`);
    expect(instruction).not.toContain("$WORKSPACE");
  });

  it("stamps the trunk day (no {date} token survives) and the branch id", () => {
    expect(instruction).toContain(`Today's date is ${DAY}.`);
    expect(instruction).toContain("`topic-2`");
    expect(instruction).not.toContain("{date}");
    // The per-branch suffix stamps both (memory's branchStoreInstruction idiom).
    expect(instruction).toContain(
      `This conversation is a single topic branch (\`topic-2\`) from the ${DAY} session. Focus only on this branch's own turns.`,
    );
  });

  it("carries the store-conventions section (R4/R6)", () => {
    expect(instruction).toContain("## Store Conventions");
    // One-line index entries in the PROBLEM — ROOT CAUSE — FIX form.
    expect(instruction).toContain("- [Title](./pattern-slug.md): PROBLEM — ROOT CAUSE — FIX");
    // Per-pattern pages with Problem / Root cause / Fix / dated Evidence.
    expect(instruction).toContain("## Problem");
    expect(instruction).toContain("## Root cause");
    expect(instruction).toContain("## Fix");
    expect(instruction).toContain("## Evidence");
    // Update-not-duplicate and the ~50-line cap.
    expect(instruction).toContain("Update, never duplicate");
    expect(instruction).toContain("~50 lines");
    // Writes confined to this store; the host-written ledger is untouchable.
    expect(instruction).toContain("Never read or write `skill-impact-log.md`");
  });

  it("carries the silent-background section (the fork keeps its persona — bar the behavior)", () => {
    expect(instruction).toContain("## Background Maintenance Step");
    expect(instruction).toContain("SILENT background skill-analysis step");
    expect(instruction).toContain("do NOT use any messaging");
  });

  it("collects the four skill-usage data-point kinds (R3) and allows recording nothing", () => {
    for (const kind of ["Invoked", "Failed", "Misapplied", "Workaround"]) {
      expect(instruction).toContain(`**${kind}**`);
    }
    expect(instruction).toContain("nothing to record");
  });
});

describe("maintenanceSystemPrompt", () => {
  const system = maintenanceSystemPrompt(WORKSPACE);

  it("substitutes $WORKSPACE (no token survives)", () => {
    expect(system).toContain(`${WORKSPACE}/memories/skill-evolution/`);
    expect(system).not.toContain("$WORKSPACE");
  });

  it("targets the maintenance duties: dedup near-duplicates, enforce caps, empty-then-sweep (R6b)", () => {
    expect(system).toContain("Near-duplicate patterns");
    expect(system).toContain("Size enforcement");
    expect(system).toContain("~50 lines");
    // No delete tool: empty the file, the host sweeps it afterwards.
    expect(system).toContain("no delete tool");
  });

  it("shares the store-conventions section and never carries a day/branch stamp", () => {
    expect(system).toContain("## Store Conventions");
    expect(system).toContain("skill-impact-log.md");
    expect(system).not.toContain(DAY);
    expect(system).not.toContain("topic-");
  });

  it("omits the silent-background section — headless runs have no persona or chat surface", () => {
    // Memory's maintenance prompts omit it too; only fork-continue instructions carry it.
    expect(system).not.toContain("## Background Maintenance Step");
  });
});

describe("proposalSystemPrompt", () => {
  const TMP_DIR = "/tmp/tachi-workspace/.tachikoma/tmp/skill-evolution";
  const DEFAULT_BRANCH = "main";
  const system = proposalSystemPrompt(WORKSPACE, TMP_DIR, DEFAULT_BRANCH);

  it("substitutes every token (none survives)", () => {
    expect(system).toContain(`${WORKSPACE}/skills/`);
    expect(system).toContain(TMP_DIR);
    expect(system).toContain(`refs/remotes/origin/${DEFAULT_BRANCH}`);
    expect(system).not.toContain("$WORKSPACE");
    expect(system).not.toContain("$TMPDIR");
    expect(system).not.toContain("$DEFAULT_BRANCH");
  });

  it("carries the worktree protocol (R8): cut from the remote default, edit, commit, push, report", () => {
    expect(system).toContain("## Worktree protocol");
    expect(system).toContain('"worktree", "add", "-b"');
    expect(system).toContain('"commit", "-m"');
    expect(system).toContain('"push", "origin", "<branch>"');
    expect(system).toContain("report_proposals");
  });

  it("carries the one-branch-per-pattern rule, the naming rule, and workspace-skills-only (R7/R9/R14)", () => {
    expect(system).toContain("One branch per pattern");
    expect(system).toContain("skill-evolution/<skill>-<slug>");
    expect(system).toContain("Workspace skills only");
    // Never the default branch, never force.
    expect(system).toContain("Never the default branch, never force");
    // An empty proposal list is a legitimate outcome.
    expect(system).toContain("report an empty list");
  });
});
