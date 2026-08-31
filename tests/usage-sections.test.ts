import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import type { AppContext } from "../src/extensions/api.ts";
import boundary from "../src/extensions/boundary/index.ts";
import { BOUNDARY_USAGE } from "../src/extensions/boundary/usage.ts";
import { DETACHED_PROCESSES_USAGE } from "../src/extensions/detached-processes/usage.ts";
import external from "../src/extensions/external/index.ts";
import { EXTERNAL_USAGE } from "../src/extensions/external/usage.ts";
import { GIT_USAGE } from "../src/extensions/git/usage.ts";
import { firstPartyExtensions } from "../src/extensions/index.ts";
import { MEMORY_LAYOUT_USAGE } from "../src/extensions/memory/usage.ts";
import notifications from "../src/extensions/notifications/index.ts";
import { NOTIFICATIONS_USAGE } from "../src/extensions/notifications/usage.ts";
import { PROJECTS_USAGE } from "../src/extensions/projects/usage.ts";
import { SELF_UPDATE_USAGE } from "../src/extensions/self-update/usage.ts";
import { SKILL_EVOLUTION_USAGE } from "../src/extensions/skill-evolution/usage.ts";
import skills from "../src/extensions/skills/index.ts";
import { SKILLS_USAGE } from "../src/extensions/skills/usage.ts";
import { buildTasksUsage } from "../src/extensions/tasks/usage.ts";
import telegram from "../src/extensions/telegram/index.ts";
import { TELEGRAM_USAGE } from "../src/extensions/telegram/usage.ts";
import { WORKFLOWS_USAGE } from "../src/extensions/workflows/usage.ts";

/**
 * issue-445 section-content and scoping guards. For every slimmed section: the critical rule
 * stays inline and the demoted detail exists only in the reference file. For every NEW
 * registration: the section reaches exactly the session scopes that can use what it
 * documents (main-only features are never described to background sessions).
 */

const SRC = join(import.meta.dirname, "..", "src");

const REFERENCE = (extension: string, topic: string): Promise<string> =>
  readFile(join(SRC, "extensions", extension, "references", `${topic}.md`), "utf8");

// ---- content: inline keeps the critical rule, reference keeps the detail ----------------------

describe("slimmed sections keep critical rules inline and demote detail to references", () => {
  it("git: dedicated-tools rule inline; the bash deny list only in the reference", async () => {
    const reference = await REFERENCE("git", "git");

    expect(GIT_USAGE).toContain("commit_workspace");
    expect(GIT_USAGE).toContain("You do not need to commit or push by hand");
    expect(GIT_USAGE).not.toContain("git push");
    expect(GIT_USAGE).not.toContain("git rebase");

    expect(reference).toContain("git push");
    expect(reference).toContain("git filter-repo");
    expect(reference).toContain("scrub");
  });

  it("tasks: type choice and goal structure inline; run mechanics only in the reference", async () => {
    const usage = buildTasksUsage("UTC");
    const reference = await REFERENCE("tasks", "tasks");

    expect(usage).toContain("**session**");
    expect(usage).toContain("**background**");
    expect(usage).toContain("end state");
    expect(usage).not.toContain("respond_to_task");
    expect(usage).not.toContain("backgroundMaxIterations");

    expect(reference).toContain("respond_to_task");
    expect(reference).toContain("📋 Scheduled task");
    expect(reference).toContain("backgroundMaxIterations");
  });

  it("memory: layout and the no-direct-writes rule inline; store detail in the reference", async () => {
    const reference = await REFERENCE("memory", "memory");

    expect(MEMORY_LAYOUT_USAGE).toContain("memories/episodic/");
    expect(MEMORY_LAYOUT_USAGE).toContain("do not write to");
    expect(MEMORY_LAYOUT_USAGE).not.toContain("rollup");
    expect(MEMORY_LAYOUT_USAGE).not.toContain("transcriptRetentionDays");

    expect(reference).toContain("YYYY-WNN.md");
    expect(reference).toContain("transcriptRetentionDays");
  });

  it("detached-processes: the no-bash-backgrounding rule inline; capture detail in the reference", async () => {
    const reference = await REFERENCE("detached-processes", "processes");

    expect(DETACHED_PROCESSES_USAGE).toContain("nohup");
    expect(DETACHED_PROCESSES_USAGE).toContain("query_process");
    expect(DETACHED_PROCESSES_USAGE).not.toContain("stderr");

    expect(reference).toContain("stderr");
    expect(reference).toContain("defaultMemoryLimitMb");
  });

  it("skills: delegation guidance lives here (absorbed from the core prompt)", () => {
    // The core base prompt's former delegate_to_agent bullet moved into this section — the
    // tool is the skills extension's own (tests/agent/prompts.test.ts guards the core side).
    expect(SKILLS_USAGE).toContain("delegate_to_agent");
    expect(SKILLS_USAGE).toContain("extensionTools");
  });

  it("projects: registration and auto-persistence inline; auth guidance in the reference", async () => {
    const reference = await REFERENCE("projects", "projects");

    expect(PROJECTS_USAGE).toContain("register_project");
    expect(PROJECTS_USAGE).toContain("snapshot from session start");
    expect(PROJECTS_USAGE).not.toContain("SSH");

    expect(reference).toContain("SSH");
    expect(reference).toContain("commitDebounceMinutes");
  });

  it("self-update: the dedicated-tools-only rule stays inline", () => {
    expect(SELF_UPDATE_USAGE).toContain("upgrade_self");
    expect(SELF_UPDATE_USAGE).toContain("never run");
  });

  it("workflows: the top-level-id routing rule stays inline", () => {
    expect(WORKFLOWS_USAGE).toContain("top-level");
  });

  it("new sections name their feature's key surfaces", () => {
    expect(BOUNDARY_USAGE).toContain("branch_summary");
    expect(BOUNDARY_USAGE).toContain("ask_branch");
    expect(BOUNDARY_USAGE).toContain("/checkpoint");
    expect(BOUNDARY_USAGE).toContain("/rollback");

    expect(NOTIFICATIONS_USAGE).toContain("digest");

    expect(TELEGRAM_USAGE).toContain("buttons");

    expect(EXTERNAL_USAGE).toContain("restart");

    expect(SKILL_EVOLUTION_USAGE).toContain("proposal");
    expect(SKILL_EVOLUTION_USAGE).toContain("memories/skill-evolution/");
  });
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
