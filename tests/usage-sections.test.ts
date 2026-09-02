import { readFile } from "node:fs/promises";

import { describe, expect, it, vi } from "vitest";

import type { AppContext } from "../src/extensions/api.ts";
import boundary from "../src/extensions/boundary/index.ts";
import external from "../src/extensions/external/index.ts";
import { firstPartyExtensions } from "../src/extensions/index.ts";
import notifications from "../src/extensions/notifications/index.ts";
import skills from "../src/extensions/skills/index.ts";
import telegram from "../src/extensions/telegram/index.ts";
import { pointersOf, USAGE_SECTIONS } from "./agent/extension-surfaces.ts";

/**
 * issue-445 section-content and scoping guards. For every slimmed section: the critical rule
 * stays inline and the demoted detail exists only in the reference file. For every NEW
 * registration: the section reaches exactly the session scopes that can use what it
 * documents (main-only features are never described to background sessions).
 */

// ---- content: inline keeps the critical rule, reference keeps the detail ----------------------

interface SectionCase {
  /** What the section must keep inline, as the it-title phrases it. */
  rule: string;
  /** Critical rules that stay in the usage section itself. */
  inline: string[];
  /** Detail that must exist only in the reference file. */
  notInline?: string[];
  /** Asserted against the reference file the section's own pointer names. */
  reference?: string[];
}

/**
 * Per-section content claims, keyed by usage module (USAGE_SECTIONS). The reference side
 * resolves through the section's pointer line, so this table never re-encodes the
 * `references/<topic>.md` layout.
 */
const CASES: { [K in keyof typeof USAGE_SECTIONS]?: SectionCase } = {
  "git/usage.ts": {
    rule: "dedicated-tools rule inline; the bash deny list only in the reference",
    inline: ["commit_workspace", "You do not need to commit or push by hand"],
    notInline: ["git push", "git rebase"],
    reference: ["git push", "git filter-repo", "scrub"],
  },
  "tasks/usage.ts": {
    rule: "type choice and goal structure inline; run mechanics only in the reference",
    inline: ["**session**", "**background**", "end state"],
    notInline: ["respond_to_task", "backgroundMaxIterations"],
    reference: ["respond_to_task", "📋 Scheduled task", "backgroundMaxIterations"],
  },
  "memory/usage.ts": {
    rule: "layout and the no-direct-writes rule inline; store detail in the reference",
    inline: ["memories/episodic/", "do not write to"],
    notInline: ["rollup", "transcriptRetentionDays"],
    reference: ["YYYY-WNN.md", "transcriptRetentionDays"],
  },
  "detached-processes/usage.ts": {
    rule: "the no-bash-backgrounding rule inline; capture detail in the reference",
    inline: ["nohup", "query_process"],
    notInline: ["stderr"],
    reference: ["stderr", "defaultMemoryLimitMb"],
  },
  "skills/usage.ts": {
    // Delegation guidance was absorbed from the core prompt — the tool is the skills
    // extension's own (tests/agent/prompts.test.ts guards the core side).
    rule: "carries the delegation guidance absorbed from the core prompt",
    inline: ["delegate_to_agent", "extensionTools"],
  },
  "projects/usage.ts": {
    rule: "registration and auto-persistence inline; auth guidance in the reference",
    inline: ["register_project", "snapshot from session start"],
    notInline: ["SSH"],
    reference: ["SSH", "commitDebounceMinutes"],
  },
  "self-update/usage.ts": {
    rule: "the dedicated-tools-only rule stays inline",
    inline: ["upgrade_self", "never run"],
  },
  "workflows/usage.ts": {
    rule: "the top-level-id routing rule stays inline; the stale-instance recovery procedure in the reference",
    inline: ["top-level"],
    reference: [
      "Stale instances",
      "query_workflow(workflow_id=...)",
      "tears down its whole nested stack",
      "tell the user what the interrupted run had done",
      "staleHours",
    ],
  },
  "boundary/usage.ts": {
    rule: "names its feature's key surfaces",
    inline: ["branch_summary", "ask_branch", "/checkpoint", "/rollback"],
  },
  "notifications/usage.ts": {
    rule: "names its feature's key surfaces",
    inline: ["digest"],
  },
  "telegram/usage.ts": {
    rule: "names its feature's key surfaces",
    inline: ["buttons"],
  },
  "external/usage.ts": {
    rule: "names its feature's key surfaces",
    inline: ["restart"],
  },
  "skill-evolution/usage.ts": {
    rule: "names its feature's key surfaces",
    inline: ["proposal", "memories/skill-evolution/"],
  },
};

describe("slimmed sections keep critical rules inline and demote detail to references", () => {
  for (const [name, testCase] of Object.entries(CASES as Record<string, SectionCase>)) {
    it(`${name}: ${testCase.rule}`, async () => {
      const usage = USAGE_SECTIONS[name];
      expect(usage, `${name} is missing from the shared usage enumeration`).toBeDefined();

      for (const inline of testCase.inline) {
        expect(usage, `${name} lost its critical rule`).toContain(inline);
      }
      for (const demoted of testCase.notInline ?? []) {
        expect(usage, `${name} still carries reference detail inline`).not.toContain(demoted);
      }

      if (testCase.reference) {
        const [pointer] = pointersOf(usage);
        expect(pointer, `${name} points at no reference file`).toBeDefined();
        const reference = await readFile(pointer, "utf8");

        for (const detail of testCase.reference) {
          expect(reference, `${name}'s reference lost detail`).toContain(detail);
        }
      }
    });
  }
});

// ---- scoping: a section reaches exactly the agents that can use it ---------------------------

interface AgentUseCall {
  factory: unknown;
  options: { sessionScopes?: string[] } | undefined;
}

/**
 * Minimal setup-driving fake: captures `app.agent.use` registrations (factory + scope
 * options) and merges whatever extra surface the driven extension's setup touches.
 */
const fakeApp = (extensionConfig: object, extra: Record<string, unknown> = {}) => {
  const agentUseCalls: AgentUseCall[] = [];
  const log = { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() };

  const app = {
    extensionConfig,
    log,
    ...extra,
    agent: {
      use: (factory: unknown, options?: { sessionScopes?: string[] }) => {
        agentUseCalls.push({ factory, options });
      },
      side: { complete: vi.fn(), classify: vi.fn(), run: vi.fn() },
      shadowFork: vi.fn(),
      branchFile: vi.fn(),
      isForking: vi.fn(() => false),
      ...(extra.agent as Record<string, unknown>),
    },
  };

  return { app: app as unknown as AppContext, agentUseCalls };
};

const scopesOf = (calls: AgentUseCall[]): string[][] =>
  calls.map((call) => call.options?.sessionScopes ?? ["main"]);

describe("usage section scoping (issue-445)", () => {
  it("boundary contributes its section to main only — everything it registers is main-scoped", () => {
    const { app, agentUseCalls } = fakeApp(
      { enabled: true, autoSetCheckpoint: true, autoSummarizeToCheckpoint: true },
      { inbound: { use: vi.fn() }, sessions: { activeTrunkSession: vi.fn(() => null) } },
    );

    boundary.setup(app);

    expect(agentUseCalls.length).toBe(2); // ask_branch + the usage section
    for (const scopes of scopesOf(agentUseCalls)) {
      expect(scopes).toEqual(["main"]);
    }
  });

  it("notifications: the tool is background-only while the section is main-only", () => {
    const { app, agentUseCalls } = fakeApp(
      { flushWindowSeconds: 30, dedupTtlSeconds: 60 },
      {
        config: { scheduler: { timezone: "UTC" } },
        channels: { deliver: vi.fn() },
        events: { on: vi.fn() },
        onShutdown: vi.fn(),
      },
    );

    notifications.setup(app);

    const scopes = scopesOf(agentUseCalls);
    expect(scopes).toContainEqual(["background"]); // notify_user tool
    expect(scopes).toContainEqual(["main"]); // usage section
    expect(scopes).not.toContainEqual(["main", "background"]);
  });

  it("external contributes tools and section main-only (default scope)", async () => {
    const { app, agentUseCalls } = fakeApp(
      { sources: [], setupTimeoutMs: 30_000 },
      {
        workspace: { dataDir: "/tmp/x", root: "/tmp/x", resolve: (...p: string[]) => p.join("/") },
        state: { get: () => undefined },
        registerExtension: vi.fn(),
      },
    );

    await external.setup(app);

    expect(agentUseCalls.length).toBe(2);
    for (const scopes of scopesOf(agentUseCalls)) {
      expect(scopes).toEqual(["main"]);
    }
  });

  it("skills reaches main and background with both its factory and its section", () => {
    const { app, agentUseCalls } = fakeApp(
      { enabled: true, proactiveLoading: false },
      {
        workspace: { resolve: (...p: string[]) => `/ws/${p.join("/")}` },
        bootstrap: vi.fn(),
        events: { on: vi.fn() },
        status: vi.fn(),
      },
    );

    skills.setup(app);

    expect(agentUseCalls.length).toBe(2);
    for (const scopes of scopesOf(agentUseCalls)) {
      expect(scopes).toEqual(["main", "background"]);
    }
  });

  it("telegram registers nothing (tools or section) when unconfigured", () => {
    const { app, agentUseCalls } = fakeApp({
      botToken: "",
      chatId: 0,
      allowMedia: true,
      pushNotifications: true,
      pushNotificationMinSeconds: 10,
      collapseIntensiveWork: true,
      intensiveWorkThreshold: 4,
      extraFileRoots: [],
    });

    telegram.setup(app);
    expect(agentUseCalls).toHaveLength(0);
  });
});

// ---- wiring: skill-evolution is a loaded first-party extension -------------------------------

describe("firstPartyExtensions wiring (issue-445)", () => {
  it("loads skill-evolution, right after skills", () => {
    const names = firstPartyExtensions.map((extension) => extension.name);

    expect(names).toContain("skill-evolution");
    expect(names.indexOf("skill-evolution")).toBe(names.indexOf("skills") + 1);
  });
});
