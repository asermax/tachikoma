import { describe, expect, it } from "vitest";

import {
  AUTHORING_GUIDE_SKILLS,
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

  it("collects the seven skill-usage data-point kinds (R3) and allows recording nothing", () => {
    for (const kind of [
      "Invoked",
      "Failed",
      "Misapplied",
      "Workaround",
      "Stale",
      "Redundant",
      "Obsolete",
    ]) {
      expect(instruction).toContain(`**${kind}**`);
    }
    expect(instruction).toContain("nothing to record");
  });

  it("frames the fix as any edit type, not only additions (R1/R2)", () => {
    // The full spectrum: outlived guidance in the collection framing, and every edit type up
    // to retirement in the Fix convention the instruction composes.
    expect(instruction).toContain("wrong, omitted, or outlived");
    expect(instruction).toContain("up to retiring the skill entirely");
  });

  it("treats bundled-tooling gaps as first-class skill gaps, not doc-only friction", () => {
    // Root cause and Fix both span guidance and bundled tooling, so analysis can name a
    // missing or broken CLI command instead of forcing a documentation workaround. Both
    // convention phrases wrap across prompt lines — assert against unwrapped text.
    const unwrapped = instruction.replaceAll(/\s+/g, " ");
    expect(unwrapped).toContain("missing or broken bundled tooling");
    expect(unwrapped).toContain("or fixing or extending the skill's bundled tooling");
    expect(instruction).toContain("a missing or broken CLI command is a skill gap like any other");
    // Reading the skill covers its bundled executables when the friction involves them.
    expect(instruction).toContain("starting from its `SKILL.md`");
    expect(instruction).toContain(
      "including any bundled CLI or scripts when the friction involves them",
    );
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
    // A retired skill's pages are superseded too — the skill directory no longer exists.
    expect(system).toContain("or the skill no longer exists");
    // Supersession also reads the skill's bundled files — a tooling fix a merged proposal
    // landed retires the pattern page it resolved.
    expect(system).toContain("or its bundled files");
    expect(system).toContain(
      "the CLI command or script a merged proposal added, repaired, or removed",
    );
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
  // The production shape: a stable per-workspace dir under the OS temp dir (proposalTmpDir).
  const TMP_DIR = "/tmp/tachikoma-skill-evolution/1a2b3c4d5e6f7a8b";
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
    // Reading before writing spans guidance and bundled files alike.
    expect(system).toContain("considered change to the skill's actual guidance and bundled files");
  });

  it("carries the one-branch-per-pattern rule, the naming rule, and workspace-skills-only (R7/R9/R14)", () => {
    expect(system).toContain("One branch per pattern");
    expect(system).toContain("skill-evolution/<skill>-<slug>");
    expect(system).toContain("Workspace skills only");
    // A workspace skill is its whole directory — the CLI or scripts it bundles are in scope.
    expect(system).toContain("executable content such as a CLI or scripts");
    // Never the default branch, never force.
    expect(system).toContain("Never the default branch, never force");
    // An empty proposal list is a legitimate outcome.
    expect(system).toContain("report an empty list");
  });

  it("names the full report payload with its reasoning fields, sourced from the pattern page (R10)", () => {
    // All seven fields in one report tuple — reasoning included, not just the one-line description.
    expect(system).toContain("{branch, skill, pattern, description, problem, rootCause, evidence}");
    // The reasoning fields restate the acted-on pattern page for the authored change.
    expect(system).toContain("reasoning for review");
    expect(system).toContain("restated from the acted-on pattern page");
    expect(system).toContain("`problem` = the observable problem it fixes");
    expect(system).toContain(
      "`rootCause` = the gap in the skill's guidance or bundled tooling that produced it",
    );
    // (`evidence`'s definition wraps across prompt lines — assert its unwrapped phrase.)
    expect(system).toContain("dated observations backing the pattern");
  });

  it("covers the full edit spectrum: modify, delete/consolidate, retire a whole skill (R7)", () => {
    // A proposal may change existing guidance, not only add to it.
    expect(system).toContain("adding, correcting, removing, or consolidating its guidance");
    // The spectrum also spans the skill's bundled tooling — fixing or adding a CLI command.
    expect(system).toContain(
      "or changing its bundled tooling — fixing or adding a CLI command, correcting a script",
    );
    expect(system).toContain("deletion of the skill's entire directory");
    // A pattern whose skill is already gone in the worktree is declined, not improvised —
    // "existed and is gone", so a new-skill pattern (no skill ever existed) is never declined.
    expect(system).toContain("existed and is gone from the worktree");
    expect(system).toContain("decline that pattern");
    // The worktree protocol documents the deletion tool and that add stages removals.
    expect(system).toContain("`delete_path` for removals");
    expect(system).toContain("`git add` records removals");
    // Removals keep the surviving skill coherent.
    expect(system).toContain("removals leave the surviving skill coherent");
  });

  it("fixes the root cause where it lives — the tooling change itself, not a doc workaround", () => {
    expect(system).toContain("Fix the root cause where it lives");
    expect(system).toContain("not a documentation workaround");
    // Guidance stays in sync with the tooling change it accompanies.
    expect(system).toContain("Keep the skill's guidance in sync with it");
    // Tests ship with the change: this run authors them and states how they run, but cannot
    // execute them (no shell tool) — running them belongs to the branch's review.
    expect(system).toContain("author the tests and state how they run");
    expect(system).toContain("this run cannot execute them (no shell tool)");
    expect(system).toContain("running them belongs to the review of your branch");
  });

  it("carries the authoring-conventions rule grounded in the force-loaded guides (R17)", () => {
    // Both guide names, asserted against the shared constant so the rule and the run wiring
    // (forceLoadSkills) cannot drift apart on a rename.
    for (const name of AUTHORING_GUIDE_SKILLS) {
      expect(system).toContain(`\`${name}\``);
    }
    // Conditional on the guides' presence (fail-soft loading and policy never disagree).
    expect(system).toContain("when their skill content is present in your input");
    // New skills follow the full conventions; new workflows follow the step format and are
    // documented in their skill's SKILL.md; edits preserve established structure.
    expect(system).toContain("must conform to them");
    expect(system).toContain("trigger-rich description");
    expect(system).toContain("`title`-frontmattered `instructions.md`");
    expect(system).toContain("documented in its skill's `SKILL.md`");
    expect(system).toContain("preserve the skill's established structure");
    // The guides' workspace vocabulary maps to the current proposal worktree.
    expect(system).toContain("they mean the current proposal worktree's `skills/`");
    // The guides' runtime tools are conventions only — never part of this run's surface; the
    // testing guidance the guides now carry is authoring material, not a tool to run.
    expect(system).toContain("executing tests");
    expect(system).toContain("NOT part of your tool surface");
    expect(system).toContain("authoring conventions only");
    // Bundled reference material is never a proposal target (qualifies the R14 rule above it).
    expect(system).toContain("never proposal targets");
  });
});
