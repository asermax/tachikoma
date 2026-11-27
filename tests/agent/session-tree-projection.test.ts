import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { Api, AssistantMessage, Model } from "@earendil-works/pi-ai";
import {
  createAssistantMessageEventStream,
  getCurrentSystemMessage,
  getCurrentTools,
} from "@earendil-works/pi-ai";
import type { AgentSession, FileEntry } from "@earendil-works/pi-coding-agent";
import {
  buildSessionProjection,
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  parseSessionEntries,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentManager } from "../../src/agent/manager.ts";
import { appendInContextEntry, getLeafId, reseatLeaf } from "../../src/agent/session-tree.ts";
import type { SideRunner } from "../../src/agent/side-run.ts";
import { provideContext } from "../../src/agent/system-prompt-section.ts";
import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import { KeyValueState } from "../../src/db/state.ts";
import { collapseCurrentTopic } from "../../src/extensions/boundary/collapse.ts";
import { openOrCreateTrunk, readBoomerangState, TrunkState } from "../../src/sessions/trunk.ts";
import { fakeLogger } from "../git/helpers.ts";

/**
 * The session-tree seam against pi's real session machinery.
 *
 * pi builds each request from the session file's leaf path, tools and prompt sections included, but
 * decides what to declare by diffing against the agent's in-memory transcript. These cases run a real
 * `AgentSession` through the production leaf moves (the topic collapse, the same-day reopen, a
 * rollback-style rewind) and read what a provider would have received.
 *
 * No model is called and nothing touches the network: the session's stream function is replaced with
 * a recorder, and the credential on disk only satisfies `prompt()`'s auth preflight.
 */

const TOOLS = ["read", "bash", "edit", "write", "grep", "find", "ls"];
/** A model from pi's built-in catalog. The version is pinned, so the entry is stable. */
const MODEL = { provider: "anthropic", id: "claude-sonnet-4-5" } as const;
const DAY = "2026-11-27";
/**
 * How the request gets its system prompt. Tachikoma's main session forces one through
 * `before_agent_start`, which renders the prompt sections into text; the plain shape leaves the
 * path's own system message in place, so its sections can be compared directly.
 */
const SHAPES = ["forced-prompt", "plain"] as const;
type Shape = (typeof SHAPES)[number];

/** What a provider would have received, read the way pi reads a request. */
interface RequestContext {
  tools: number;
  sections: string[];
}

interface Trunk {
  session: AgentSession;
  file: () => string;
  /** Send one user message and return the request it produced. */
  ask: (text: string) => Promise<RequestContext>;
  /** The production topic shift: collapse the live branch, then inject a related-branch pointer. */
  shift: (branchId: string) => Promise<string>;
  dispose: () => void;
}

interface LeafReplay {
  systemMessages: number;
  tools: number;
}

let runtimeDir: string;
let agentDir: string;
let modelRuntime: ModelRuntime;
let model: Model<Api>;
let workDir: string;
let db: AppDatabase;
let trunkState: TrunkState;
const openSessions: AgentSession[] = [];

const log = fakeLogger();

const completer = (): Pick<SideRunner, "complete"> =>
  ({
    complete: vi.fn().mockResolvedValue("A summary of the abandoned branch."),
  }) as unknown as Pick<SideRunner, "complete">;

const reply = (answeringModel: Model<Api>): AssistantMessage => ({
  role: "assistant",
  content: [{ type: "text", text: "Done." }],
  api: answeringModel.api,
  provider: answeringModel.provider,
  model: answeringModel.id,
  usage: {
    input: 1,
    output: 1,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 2,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  },
  stopReason: "stop",
  timestamp: Date.now(),
});

/** Stand in for the provider: record the request's declarations, answer with one text block. */
const recordingStream =
  (requests: RequestContext[]): AgentSession["agent"]["streamFunction"] =>
  (streamModel, context) => {
    const system = getCurrentSystemMessage(context.messages);
    requests.push({
      tools: getCurrentTools(context.messages).length,
      sections: Object.keys(system?.sections ?? {}).sort(),
    });

    const message = reply(streamModel);
    const stream = createAssistantMessageEventStream();
    stream.push({ type: "start", partial: message });
    stream.push({ type: "done", reason: "stop", message });
    return stream;
  };

const openSession =
  (requests: RequestContext[], shape: Shape): AgentManager["open"] =>
  async (options) => {
    const sessionFile = options?.sessionFile;
    const loader = new DefaultResourceLoader({
      cwd: workDir,
      agentDir,
      ...(shape === "forced-prompt"
        ? { extensionFactories: [provideContext("[tachikoma context]")] }
        : {}),
      noExtensions: true,
      noSkills: true,
      noContextFiles: true,
      noPromptTemplates: true,
      noThemes: true,
      systemPromptOverride: () => "You are a test assistant.",
    });
    await loader.reload();

    const { session } = await createAgentSession({
      cwd: workDir,
      agentDir,
      model,
      tools: TOOLS,
      resourceLoader: loader,
      sessionManager:
        sessionFile == null
          ? SessionManager.create(workDir, workDir)
          : SessionManager.open(sessionFile, workDir),
      settingsManager: SettingsManager.inMemory(),
      modelRuntime,
    });
    await session.bindExtensions({});
    session.agent.streamFunction = recordingStream(requests);
    openSessions.push(session);
    return session;
  };

/** The production topic collapse: summarize the live branch onto its base and record the new base. */
const collapseLiveBranch = async (session: AgentSession, branchId: string): Promise<string> => {
  const collapsed = await collapseCurrentTopic(
    { side: completer(), log },
    {
      session,
      currentBaseId: readBoomerangState(session)?.currentTopicBaseId ?? null,
      branchId,
    },
  );
  if (collapsed == null) throw new Error("the topic collapse failed");
  return collapsed.newBaseId;
};

/** The session file, read with pi's own parser: the header followed by every entry. */
const fileEntries = async (file: string): Promise<FileEntry[]> =>
  parseSessionEntries(await readFile(file, "utf8"));

/** Open today's trunk the way the coordinator does: fresh, or reopened and re-seated onto its base. */
const openTrunk = async (shape: Shape): Promise<Trunk> => {
  const requests: RequestContext[] = [];

  const { session } = await openOrCreateTrunk(
    {
      agent: { open: openSession(requests, shape) },
      trunk: trunkState,
      now: () => new Date(`${DAY}T12:00:00.000Z`),
      timezone: "UTC",
    },
    DAY,
  );

  return {
    session,
    file: () => {
      const file = session.sessionFile;
      if (file == null) throw new Error("the trunk session has no file");
      return file;
    },
    ask: async (text) => {
      await session.prompt(text);
      const request = requests.at(-1);
      if (request == null) throw new Error("the exchange sent no request");
      return request;
    },
    shift: async (branchId) => {
      const base = await collapseLiveBranch(session, branchId);
      appendInContextEntry(
        session,
        "tachikoma-related-branch",
        "This new branch may continue a previous one.",
      );
      return base;
    },
    dispose: () => session.dispose(),
  };
};

/** Replay pi's own projection at every leaf of a session file. */
const leafReplays = async (file: string): Promise<LeafReplay[]> => {
  const entries = (await fileEntries(file)).filter((entry) => entry.type !== "session");
  const parents = new Set(entries.map((entry) => entry.parentId));

  return entries
    .filter((entry) => !parents.has(entry.id))
    .map((leaf) => {
      const { messages } = buildSessionProjection(entries, leaf.id);
      return {
        systemMessages: messages.filter((message) => message.role === "system").length,
        tools: getCurrentTools(messages).length,
      };
    });
};

/** How many system-message entries the file holds. A declaration is one such entry. */
const countDeclarations = async (file: string): Promise<number> =>
  (await fileEntries(file)).filter(
    (entry) => entry.type === "message" && entry.message.role === "system",
  ).length;

const expectTranscriptOnLeaf = (session: AgentSession): void => {
  expect(session.agent.state.messages).toEqual(
    session.sessionManager.buildSessionProjection().messages,
  );
};

describe("session-tree seam against pi's request projection", () => {
  beforeAll(async () => {
    runtimeDir = await mkdtemp(join(tmpdir(), "tachi-projection-runtime-"));
    agentDir = join(runtimeDir, "agent");
    await mkdir(agentDir);
    await writeFile(
      join(agentDir, "auth.json"),
      JSON.stringify({ anthropic: { type: "api_key", key: "test" } }),
    );
    modelRuntime = await ModelRuntime.create({
      authPath: join(agentDir, "auth.json"),
      modelsPath: null,
      refreshOnCreate: false,
    });
    const resolved = modelRuntime.getModel(MODEL.provider, MODEL.id);
    if (resolved == null) {
      throw new Error(`pi's built-in catalog has no ${MODEL.provider}/${MODEL.id}`);
    }
    model = resolved;
  });

  afterAll(async () => {
    await rm(runtimeDir, { recursive: true, force: true });
  });

  beforeEach(async () => {
    workDir = await mkdtemp(join(tmpdir(), "tachi-projection-trunk-"));
    // The `app_state` store behind the trunk pointer outlives a process, so a test's reopens share it.
    db = createDatabase(join(workDir, "test.db"));
    runMigrations(db);
    trunkState = new TrunkState(new KeyValueState(db, "trunk"));
  });

  afterEach(async () => {
    for (const session of openSessions.splice(0)) session.dispose();
    db.$client.close();
    await rm(workDir, { recursive: true, force: true });
  });

  it.each(SHAPES)(
    "carries the full tool set on every request after each of three collapses in one run (%s)",
    async (shape) => {
      const trunk = await openTrunk(shape);
      const first = await trunk.ask("topic 1");

      await trunk.shift("topic-1");
      const second = await trunk.ask("topic 2");
      await trunk.shift("topic-2");
      const third = await trunk.ask("topic 3");
      await trunk.shift("topic-3");
      const fourth = await trunk.ask("topic 4");

      for (const request of [first, second, third, fourth]) {
        expect(request.tools).toBe(TOOLS.length);
      }
      if (shape === "plain") {
        for (const request of [second, third, fourth]) {
          expect(request.sections).toEqual(first.sections);
        }
      }
    },
    20_000,
  );

  it.each(SHAPES)(
    "carries the full tool set on the first request after a same-day reopen (%s)",
    async (shape) => {
      const first = await openTrunk(shape);
      await first.ask("topic 1");
      await first.shift("topic-1");
      const afterCollapse = await first.ask("topic 2");
      await first.shift("topic-2");
      const file = first.file();
      first.dispose();

      // A restart right after a collapse: the file's last entry sits on a path with no system message.
      const second = await openTrunk(shape);
      const afterRestart = await second.ask("topic 3");
      second.dispose();

      // A restart after a turn: the file's last entry now sits on a path that carries one.
      const third = await openTrunk(shape);
      const afterLaterRestart = await third.ask("topic 3, continued");
      expect(third.file()).toBe(file);

      expect(afterRestart.tools).toBe(TOOLS.length);
      expect(afterLaterRestart.tools).toBe(TOOLS.length);
      if (shape === "plain") {
        // Same prompt sections as the first request after a collapse in a running process.
        expect(afterRestart.sections).toEqual(afterCollapse.sections);
        expect(afterLaterRestart.sections).toEqual(afterCollapse.sections);
      }
    },
    20_000,
  );

  it("writes no second declaration when a rollback-style rewind lands on a path that has one", async () => {
    const first = await openTrunk("plain");
    await first.ask("topic 1");
    await first.ask("topic 1, again");
    // The tip `/rollback` rewinds to: the leaf before the exchange that triggered the decision.
    const preDecisionLeaf = getLeafId(first.session);
    if (preDecisionLeaf == null) throw new Error("the trunk has no leaf");
    await first.shift("topic-1");
    await first.ask("topic 2");
    first.dispose();

    const reopened = await openTrunk("plain");
    const before = await countDeclarations(reopened.file());

    reseatLeaf(reopened.session, preDecisionLeaf);
    const request = await reopened.ask("topic 1, picked up again");

    expect(await countDeclarations(reopened.file())).toBe(before);
    expect(request.tools).toBe(TOOLS.length);
  });

  it("keeps the in-memory transcript equal to the file projection after each leaf move", async () => {
    const trunk = await openTrunk("plain");
    await trunk.ask("topic 1");

    await collapseLiveBranch(trunk.session, "topic-1");
    expectTranscriptOnLeaf(trunk.session);
    trunk.dispose();

    const reopened = await openTrunk("plain");
    expectTranscriptOnLeaf(reopened.session);
  });

  it("replays a system message and the full tool set at every leaf of a twice-collapsed trunk", async () => {
    const trunk = await openTrunk("plain");
    await trunk.ask("topic 1");
    await trunk.shift("topic-1");
    await trunk.ask("topic 2");
    await trunk.shift("topic-2");
    await trunk.ask("topic 3");

    const leaves = await leafReplays(trunk.file());

    // Each finished topic ends on its own leaf: the abandoned tip, then the next one.
    expect(leaves).toHaveLength(3);
    for (const leaf of leaves) {
      expect(leaf.systemMessages).toBeGreaterThan(0);
      expect(leaf.tools).toBe(TOOLS.length);
    }
  });
});
