import type { ExtensionAPI, ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isolatedLoaderOptions, selectExtensionFactories } from "../../src/agent/manager.ts";
import type { AgentExtensionFactory } from "../../src/extensions/api.ts";

const h = vi.hoisted(() => {
  const fsState = { exists: true, size: 100 };
  const capturedLoaderOptions: Array<Record<string, unknown>> = [];
  const sessionManagerCalls: Array<{ kind: string; args: unknown[] }> = [];
  const tiersInstances: Array<Record<string, ReturnType<typeof import("vitest").vi.fn>>> = [];
  // Drives the SessionManager fakes for shadowFork: what getLeafId/createBranchedSession return.
  const shadowState: { leafId: string | null; forkedFile: string | undefined } = {
    leafId: "leaf-1",
    forkedFile: "/ws/root/.tachikoma/pi/sessions/forked.jsonl",
  };

  return {
    fsState,
    capturedLoaderOptions,
    sessionManagerCalls,
    tiersInstances,
    shadowState,
    loaderReload: vi.fn(),
    createAgentSessionMock: vi.fn(),
    modelRuntimeCreate: vi.fn((options?: Record<string, unknown>) => ({
      kind: "runtime",
      options,
    })),
    getApiKeyMock: vi.fn(),
    createBranchedSessionMock: vi.fn(),
    rmMock: vi.fn(),
  };
});

const {
  fsState,
  capturedLoaderOptions,
  sessionManagerCalls,
  tiersInstances,
  shadowState,
  loaderReload,
  createAgentSessionMock,
  modelRuntimeCreate,
  getApiKeyMock,
  createBranchedSessionMock,
  rmMock,
} = h;

vi.mock("node:fs", () => ({
  existsSync: vi.fn(() => h.fsState.exists),
  statSync: vi.fn(() => ({ size: h.fsState.size })),
}));

vi.mock("node:fs/promises", () => ({
  rm: (...args: unknown[]) => h.rmMock(...args),
}));

vi.mock("@earendil-works/pi-coding-agent", () => {
  const fakeSessionManager = (kind: string) =>
    vi.fn((...args: unknown[]) => {
      h.sessionManagerCalls.push({ kind, args });
      return {
        kind,
        args,
        getLeafId: () => h.shadowState.leafId,
        createBranchedSession: (leafId: string) => {
          h.createBranchedSessionMock(leafId);
          return h.shadowState.forkedFile;
        },
      };
    });

  return {
    ModelRuntime: {
      create: (options?: Record<string, unknown>) => h.modelRuntimeCreate(options),
    },
    ModelRegistry: class {
      readonly runtime: unknown;
      getApiKeyForProvider = h.getApiKeyMock;
      constructor(runtime: unknown) {
        this.runtime = runtime;
      }
    },
    SettingsManager: {
      create: vi.fn((root: string, piDir: string) => ({ kind: "settings", root, piDir })),
    },
    SessionManager: {
      inMemory: fakeSessionManager("inMemory"),
      forkFrom: fakeSessionManager("forkFrom"),
      open: fakeSessionManager("open"),
      create: fakeSessionManager("create"),
    },
    DefaultResourceLoader: class {
      constructor(options: Record<string, unknown>) {
        h.capturedLoaderOptions.push(options);
      }
      reload = h.loaderReload;
    },
    createAgentSession: (...args: unknown[]) => h.createAgentSessionMock(...args),
  };
});

vi.mock("../../src/agent/models.ts", async () => {
  const actual = await vi.importActual<typeof import("../../src/agent/models.ts")>(
    "../../src/agent/models.ts",
  );

  return {
    ...actual,
    ModelTiers: class {
      resolveRef = vi.fn();
      configuredRef = vi.fn(() => null);
      resolve = vi.fn();

      constructor() {
        h.tiersInstances.push(this as unknown as Record<string, ReturnType<typeof vi.fn>>);
      }
    },
  };
});

const { AgentManager } = await import("../../src/agent/manager.ts");

const makeWorkspace = () =>
  ({
    root: "/ws/root",
    piDir: "/ws/root/.tachikoma/pi",
    sessionsDir: "/ws/root/.tachikoma/pi/sessions",
  }) as unknown as import("../../src/workspace.ts").Workspace;

const makeConfig = () =>
  ({ agent: {}, scheduler: {} }) as unknown as import("../../src/config/schema.ts").Config;

const makeLog = () =>
  ({
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
  }) as unknown as import("../../src/log.ts").Logger;

const makeSources = (
  overrides: Partial<import("../../src/agent/manager.ts").AgentSessionSources> = {},
) => ({
  piFactories: [{ id: "pi" }] as unknown as ExtensionFactory[],
  backgroundFactories: [{ id: "bg" }] as unknown as ExtensionFactory[],
  subagentFactories: [{ id: "sub" }] as unknown as ExtensionFactory[],
  ...overrides,
});

const makeSession = (
  overrides: Partial<{
    prompt: ReturnType<typeof vi.fn>;
    dispose: ReturnType<typeof vi.fn>;
    messages: Array<{ role: string; content: Array<{ type: string; text: string }> }>;
  }> = {},
) => ({
  prompt: vi.fn().mockResolvedValue(undefined),
  dispose: vi.fn(),
  // open() awaits session.bindExtensions({}) so pi fires resources_discover and the skills
  // extension can contribute the workspace skills/ dir; the fake session must expose it too.
  bindExtensions: vi.fn().mockResolvedValue(undefined),
  ...overrides,
});

beforeEach(() => {
  fsState.exists = true;
  fsState.size = 100;
  capturedLoaderOptions.length = 0;
  sessionManagerCalls.length = 0;
  tiersInstances.length = 0;
  loaderReload.mockClear();
  modelRuntimeCreate.mockClear();
  getApiKeyMock.mockReset();
  createBranchedSessionMock.mockClear();
  rmMock.mockReset();
  rmMock.mockResolvedValue(undefined);
  shadowState.leafId = "leaf-1";
  shadowState.forkedFile = "/ws/root/.tachikoma/pi/sessions/forked.jsonl";
  createAgentSessionMock.mockReset();
  createAgentSessionMock.mockResolvedValue({
    session: makeSession(),
    modelFallbackMessage: null,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

const currentTiers = () => tiersInstances[tiersInstances.length - 1];

describe("isolatedLoaderOptions", () => {
  it("suppresses pi's append, project context files, and skills catalog (AC4)", () => {
    const options = isolatedLoaderOptions();

    expect(options.appendSystemPromptOverride()).toEqual([]);
    expect(options.noContextFiles).toBe(true);
    expect(options.noSkills).toBe(true);
  });
});

describe("selectExtensionFactories", () => {
  // A named spy factory: when the wrapped form pi calls is invoked, it records the session context it
  // was handed, so the tests assert the scope each selected factory is bound with.
  const factory = (id: string): AgentExtensionFactory =>
    Object.assign(vi.fn(), { id }) as unknown as AgentExtensionFactory;
  const invoke = (selected: ExtensionFactory[]): void => {
    for (const wrapped of selected) wrapped({} as ExtensionAPI);
  };

  it("binds the background subset, each with the background scope, for background task runs", () => {
    const a = factory("a");
    const b = factory("b");
    const c = factory("c");
    const sources = {
      piFactories: [a, b, c],
      backgroundFactories: [a, c],
      subagentFactories: [a, c],
    };

    const selected = selectExtensionFactories(
      { bindBackgroundFactories: true, bare: true },
      sources,
    );

    expect(selected).toHaveLength(2);
    invoke(selected);
    expect(a).toHaveBeenCalledWith(expect.anything(), { scope: "background" });
    expect(c).toHaveBeenCalledWith(expect.anything(), { scope: "background" });
    expect(b).not.toHaveBeenCalled();
  });

  it("binds the subagent subset, each with the subagent scope, for a delegated subagent run", () => {
    const a = factory("a");
    const b = factory("b");
    const c = factory("c");
    const sources = {
      piFactories: [a, b, c],
      backgroundFactories: [a, c],
      subagentFactories: [a, c],
    };

    const selected = selectExtensionFactories({ bindSubagentFactories: true, bare: true }, sources);

    expect(selected).toHaveLength(2);
    invoke(selected);
    expect(a).toHaveBeenCalledWith(expect.anything(), { scope: "subagent" });
    expect(c).toHaveBeenCalledWith(expect.anything(), { scope: "subagent" });
    expect(b).not.toHaveBeenCalled();
  });

  it("gives background precedence over subagent when both flags are set", () => {
    const a = factory("a");
    const b = factory("b");
    const sources = {
      piFactories: [a, b],
      backgroundFactories: [a],
      subagentFactories: [b],
    };

    const selected = selectExtensionFactories(
      { bindBackgroundFactories: true, bindSubagentFactories: true, bare: true },
      sources,
    );

    // background wins: the background subset (a) binds, the subagent subset (b) does not.
    expect(selected).toHaveLength(1);
    invoke(selected);
    expect(a).toHaveBeenCalledWith(expect.anything(), { scope: "background" });
    expect(b).not.toHaveBeenCalled();
  });

  it("binds nothing for other bare side runs", () => {
    const a = factory("a");
    const sources = { piFactories: [a], backgroundFactories: [a], subagentFactories: [a] };

    expect(selectExtensionFactories({ bare: true }, sources)).toEqual([]);
  });

  it("binds every factory, each with the main scope, for a normal (non-bare) session", () => {
    const a = factory("a");
    const b = factory("b");
    const sources = { piFactories: [a, b], backgroundFactories: [], subagentFactories: [] };

    const selected = selectExtensionFactories({ bare: false }, sources);

    expect(selected).toHaveLength(2);
    invoke(selected);
    expect(a).toHaveBeenCalledWith(expect.anything(), { scope: "main" });
    expect(b).toHaveBeenCalledWith(expect.anything(), { scope: "main" });
  });
});

describe("AgentManager.create", () => {
  it("uses workspace-local auth.json when it has content", async () => {
    fsState.exists = true;
    fsState.size = 100;

    await AgentManager.create(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    expect(modelRuntimeCreate).toHaveBeenCalledWith({
      authPath: "/ws/root/.tachikoma/pi/auth.json",
      modelsPath: "/ws/root/.tachikoma/pi/models.json",
    });
  });

  it("falls back to the shared pi login when the local auth file is missing", async () => {
    fsState.exists = false;

    await AgentManager.create(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    expect(modelRuntimeCreate).toHaveBeenCalledWith({
      modelsPath: "/ws/root/.tachikoma/pi/models.json",
    });
  });

  it("falls back to the shared pi login when the local auth file is effectively empty", async () => {
    fsState.exists = true;
    fsState.size = 2;

    await AgentManager.create(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    expect(modelRuntimeCreate).toHaveBeenCalledWith({
      modelsPath: "/ws/root/.tachikoma/pi/models.json",
    });
  });
});

describe("AgentManager.apiKeyFor", () => {
  it("returns the stored api key for a provider", async () => {
    getApiKeyMock.mockResolvedValue("sk-123");
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await expect(manager.apiKeyFor("anthropic")).resolves.toBe("sk-123");
    expect(getApiKeyMock).toHaveBeenCalledWith("anthropic");
  });

  it("coerces a missing key to undefined", async () => {
    getApiKeyMock.mockResolvedValue(null);
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await expect(manager.apiKeyFor("anthropic")).resolves.toBeUndefined();
  });
});

describe("AgentManager.open", () => {
  it("applies the core main base prompt for a non-bare session (AC4)", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open();

    const loaderOptions = capturedLoaderOptions[0];
    expect(loaderOptions.systemPromptOverride).toBeTypeOf("function");
    const prompt = (loaderOptions.systemPromptOverride as () => string)();
    expect(prompt).toContain("personal assistant");
    expect(prompt).toContain("Workspace root: /ws/root");
    expect(loaderReload).toHaveBeenCalledOnce();
  });

  it("omits the system prompt override when bare with no explicit prompt (AC4)", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ bare: true });

    expect(capturedLoaderOptions[0]).not.toHaveProperty("systemPromptOverride");
  });

  it("uses an explicit system prompt over the core base prompt (AC4)", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ systemPrompt: "explicit" });

    expect((capturedLoaderOptions[0].systemPromptOverride as () => string)()).toBe("explicit");
  });

  it("applies isolated loader options when isolatePrompt is set", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ isolatePrompt: true });

    expect(capturedLoaderOptions[0].noContextFiles).toBe(true);
    expect(capturedLoaderOptions[0].noSkills).toBe(true);
  });

  it("forwards skillPaths to the loader as additionalSkillPaths", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ skillPaths: ["/guides"] });

    expect(capturedLoaderOptions[0].additionalSkillPaths).toEqual(["/guides"]);
  });

  it("composes skillPaths with isolation (noSkills still admits the added paths)", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ isolatePrompt: true, skillPaths: ["/guides"] });

    expect(capturedLoaderOptions[0].noSkills).toBe(true);
    expect(capturedLoaderOptions[0].additionalSkillPaths).toEqual(["/guides"]);
  });

  it("binds the force-load injection factory alongside the bare-path selection", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ bare: true, forceLoadSkills: ["skill-authoring"] });

    // Bare binds nothing; the injection factory is the run's grounding, so exactly one factory.
    const factories = capturedLoaderOptions[0].extensionFactories as ExtensionFactory[];
    expect(factories).toHaveLength(1);

    // Registration shape only (this file namespace-mocks the SDK, so the handler's body must not
    // run here — behavior lives in tests/agent/force-load-skills.test.ts): one before_agent_start
    // handler, no session_compact re-evaluation (the single-prompt contract).
    const registered: string[] = [];
    const fakePi = {
      on: (event: string) => {
        registered.push(event);
      },
    };
    factories[0]?.(fakePi as never);
    expect(registered).toEqual(["before_agent_start"]);
  });

  it("adds no skill loading when neither option is set (negative path)", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ bare: true });

    expect(capturedLoaderOptions[0]).not.toHaveProperty("additionalSkillPaths");
    expect(capturedLoaderOptions[0].extensionFactories).toHaveLength(0);
  });

  it("does not isolate the loader by default", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open();

    expect(capturedLoaderOptions[0]).not.toHaveProperty("noContextFiles");
  });

  it("creates a fresh session manager by default", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open();

    expect(sessionManagerCalls.at(-1)?.kind).toBe("create");
  });

  it("opens an in-memory session manager when inMemory is set", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ inMemory: true });

    expect(sessionManagerCalls.at(-1)?.kind).toBe("inMemory");
  });

  it("forks from a source file when forkFromFile is set", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ forkFromFile: "/sessions/source.json" });

    const call = sessionManagerCalls.at(-1);
    expect(call?.kind).toBe("forkFrom");
    expect(call?.args[0]).toBe("/sessions/source.json");
  });

  it("opens an existing session file when sessionFile is set", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open({ sessionFile: "/sessions/existing.json" });

    const call = sessionManagerCalls.at(-1);
    expect(call?.kind).toBe("open");
    expect(call?.args[0]).toBe("/sessions/existing.json");
  });

  it("pins the model from an explicit model reference", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );
    const tiers = currentTiers();
    tiers.resolveRef.mockReturnValue({ model: { id: "m1" }, thinkingLevel: "high" });

    await manager.open({ model: "anthropic/claude:high" });

    expect(tiers.resolveRef).toHaveBeenCalledWith("anthropic/claude:high");
    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs.model).toEqual({ id: "m1" });
    expect(sessionArgs.thinkingLevel).toBe("high");
  });

  it("resolves the configured tier when no explicit model and a tier is configured", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );
    const tiers = currentTiers();
    tiers.configuredRef.mockReturnValue({ provider: "anthropic", id: "claude" });
    tiers.resolve.mockReturnValue({ model: { id: "m2" } });

    await manager.open({ tier: "searcher" });

    expect(tiers.configuredRef).toHaveBeenCalledWith("searcher");
    expect(tiers.resolve).toHaveBeenCalledWith("searcher");
    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs.model).toEqual({ id: "m2" });
    expect(sessionArgs).not.toHaveProperty("thinkingLevel");
  });

  it("omits the model entirely when nothing is configured", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );
    currentTiers().configuredRef.mockReturnValue(null);

    await manager.open();

    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs).not.toHaveProperty("model");
    expect(sessionArgs).not.toHaveProperty("thinkingLevel");
  });

  it("defaults the tier to main when unspecified", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open();

    expect(currentTiers().configuredRef).toHaveBeenCalledWith("main");
  });

  it("forwards tools and customTools when provided", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );
    const customTools = [{ name: "t" }] as never;

    await manager.open({ tools: ["read"], customTools });

    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs.tools).toEqual(["read"]);
    expect(sessionArgs.customTools).toBe(customTools);
  });

  it("omits tools and customTools when not provided", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.open();

    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs).not.toHaveProperty("tools");
    expect(sessionArgs).not.toHaveProperty("customTools");
  });

  it("logs a warning when pi reports a model fallback", async () => {
    const log = makeLog();
    createAgentSessionMock.mockResolvedValue({
      session: makeSession(),
      modelFallbackMessage: "fell back to default",
    });
    const manager = await AgentManager.create(makeWorkspace(), makeConfig(), makeSources(), log);

    await manager.open();

    expect(log.warn).toHaveBeenCalledWith(
      { modelFallbackMessage: "fell back to default" },
      "model fallback on session open",
    );
  });

  it("does not warn when there is no model fallback", async () => {
    const log = makeLog();
    const manager = await AgentManager.create(makeWorkspace(), makeConfig(), makeSources(), log);

    await manager.open();

    expect(log.warn).not.toHaveBeenCalled();
  });

  it("returns the created session", async () => {
    const session = makeSession();
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await expect(manager.open()).resolves.toBe(session);
  });
});

describe("AgentManager.forkAndContinue", () => {
  it("forks, prompts, and disposes the session", async () => {
    const session = makeSession();
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await manager.forkAndContinue("/sessions/src.json", "continue", "processor", ["read"]);

    const call = sessionManagerCalls.at(-1);
    expect(call?.kind).toBe("forkFrom");
    expect(session.prompt).toHaveBeenCalledWith("continue");
    expect(session.dispose).toHaveBeenCalledOnce();
  });

  it("disposes the session even when the prompt throws", async () => {
    const session = makeSession({
      prompt: vi.fn().mockRejectedValue(new Error("boom")),
    });
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await expect(
      manager.forkAndContinue("/sessions/src.json", "continue", "processor"),
    ).rejects.toThrow("boom");
    expect(session.dispose).toHaveBeenCalledOnce();
  });
});

describe("AgentManager.shadowFork", () => {
  const makeForkSession = (text: string) =>
    makeSession({ messages: [{ role: "assistant", content: [{ type: "text", text }] }] });

  it("forks the source branch into a bare, tool-free headless session (R6, S2)", async () => {
    const session = makeForkSession('{"decision":"shift"}');
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    const fork = await manager.shadowFork("/sessions/live.jsonl", { systemPrompt: "live-prompt" });

    // opened the source file, then branched its current leaf into a new file
    expect(
      sessionManagerCalls.some((c) => c.kind === "open" && c.args[0] === "/sessions/live.jsonl"),
    ).toBe(true);
    expect(createBranchedSessionMock).toHaveBeenCalledWith("leaf-1");
    // the headless session opened the forked file, bare, with no tools and the live prompt
    expect(sessionManagerCalls.at(-1)?.args[0]).toBe(
      "/ws/root/.tachikoma/pi/sessions/forked.jsonl",
    );
    const sessionArgs = createAgentSessionMock.mock.calls.at(-1)?.[0];
    expect(sessionArgs.tools).toEqual([]);
    const systemPromptOverride = capturedLoaderOptions.at(-1)?.systemPromptOverride as () => string;
    expect(systemPromptOverride()).toBe("live-prompt");

    const reply = await fork.prompt("classify");
    expect(session.prompt).toHaveBeenCalledWith("classify");
    expect(reply).toBe('{"decision":"shift"}');
  });

  it("deletes the forked file and disposes the session on dispose", async () => {
    const session = makeForkSession("ok");
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    const fork = await manager.shadowFork("/sessions/live.jsonl");
    await fork.dispose();

    expect(session.dispose).toHaveBeenCalledOnce();
    expect(rmMock).toHaveBeenCalledWith("/ws/root/.tachikoma/pi/sessions/forked.jsonl", {
      force: true,
    });
  });

  it("throws when the source session has no entries to fork", async () => {
    shadowState.leafId = null;
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    await expect(manager.shadowFork("/sessions/empty.jsonl")).rejects.toThrow(/no entries/);
    expect(createAgentSessionMock).not.toHaveBeenCalled();
  });
});

describe("AgentManager.branchFile", () => {
  it("cuts the branch from a manager loaded fresh off disk, never a live session", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );

    const file = manager.branchFile("/sessions/trunk.jsonl", "leaf-7");

    // The source file was opened in its OWN SessionManager (the destructive createBranchedSession
    // must not run on a live session's manager), and that detached manager produced the branch file.
    expect(
      sessionManagerCalls.some((c) => c.kind === "open" && c.args[0] === "/sessions/trunk.jsonl"),
    ).toBe(true);
    expect(createBranchedSessionMock).toHaveBeenCalledWith("leaf-7");
    expect(file).toBe("/ws/root/.tachikoma/pi/sessions/forked.jsonl");
  });
});

describe("AgentManager.isForking", () => {
  it("is true only while a fork run is in flight (AC15)", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );
    let duringPrompt: boolean | undefined;
    const session = makeSession({
      prompt: vi.fn(async () => {
        duringPrompt = manager.isForking();
      }),
    });
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });

    expect(manager.isForking()).toBe(false);
    await manager.forkAndContinue("/sessions/src.json", "go", "processor");

    expect(duringPrompt).toBe(true);
    expect(manager.isForking()).toBe(false);
  });

  it("stays true across a nested fork and nets back to false (AC15)", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );
    const observed: boolean[] = [];
    let depth = 0;
    createAgentSessionMock.mockImplementation(async () => ({
      session: makeSession({
        prompt: vi.fn(async () => {
          if (depth++ === 0) await manager.forkAndContinue("/inner.json", "go", "processor");
          observed.push(manager.isForking());
        }),
      }),
      modelFallbackMessage: null,
    }));

    await manager.forkAndContinue("/outer.json", "go", "processor");

    expect(observed).toEqual([true, true]);
    expect(manager.isForking()).toBe(false);
  });

  it("nets back to false after two parallel forks resolve (AC15)", async () => {
    const manager = await AgentManager.create(
      makeWorkspace(),
      makeConfig(),
      makeSources(),
      makeLog(),
    );
    const gates: Array<() => void> = [];
    createAgentSessionMock.mockImplementation(async () => ({
      session: makeSession({
        prompt: vi.fn(() => new Promise<void>((resolve) => gates.push(resolve))),
      }),
      modelFallbackMessage: null,
    }));

    const first = manager.forkAndContinue("/a.json", "go", "processor");
    const second = manager.forkAndContinue("/b.json", "go", "processor");

    expect(manager.isForking()).toBe(true);

    // Let both opens + prompts reach the gate, then release them.
    await vi.waitFor(() => expect(gates).toHaveLength(2));
    for (const release of gates) release();
    await Promise.all([first, second]);

    expect(manager.isForking()).toBe(false);
  });
});
