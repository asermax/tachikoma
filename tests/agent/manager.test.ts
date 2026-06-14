import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isolatedLoaderOptions, selectExtensionFactories } from "../../src/agent/manager.ts";

const h = vi.hoisted(() => {
  const fsState = { exists: true, size: 100 };
  const capturedLoaderOptions: Array<Record<string, unknown>> = [];
  const sessionManagerCalls: Array<{ kind: string; args: unknown[] }> = [];
  const tiersInstances: Array<Record<string, ReturnType<typeof import("vitest").vi.fn>>> = [];

  return {
    fsState,
    capturedLoaderOptions,
    sessionManagerCalls,
    tiersInstances,
    loaderReload: vi.fn(),
    createAgentSessionMock: vi.fn(),
    authStorageCreate: vi.fn((path?: string) => ({ kind: "auth", path })),
    getApiKeyMock: vi.fn(),
  };
});

const {
  fsState,
  capturedLoaderOptions,
  sessionManagerCalls,
  tiersInstances,
  loaderReload,
  createAgentSessionMock,
  authStorageCreate,
  getApiKeyMock,
} = h;

vi.mock("node:fs", () => ({
  existsSync: vi.fn(() => h.fsState.exists),
  statSync: vi.fn(() => ({ size: h.fsState.size })),
}));

vi.mock("@earendil-works/pi-coding-agent", () => {
  const fakeSessionManager = (kind: string) =>
    vi.fn((...args: unknown[]) => {
      h.sessionManagerCalls.push({ kind, args });
      return { kind, args };
    });

  return {
    AuthStorage: {
      create: (path?: string) => {
        const instance = h.authStorageCreate(path);
        return { ...instance, getApiKey: h.getApiKeyMock };
      },
    },
    ModelRegistry: {
      create: vi.fn((auth: unknown, path: string) => ({ kind: "registry", auth, path })),
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

const makeConfig = () => ({ agent: {} }) as unknown as import("../../src/config/schema.ts").Config;

const makeLog = () =>
  ({
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  }) as unknown as import("../../src/log.ts").Logger;

const makeSources = (
  overrides: Partial<import("../../src/agent/manager.ts").AgentSessionSources> = {},
) => ({
  piFactories: [{ id: "pi" }] as unknown as ExtensionFactory[],
  backgroundFactories: [{ id: "bg" }] as unknown as ExtensionFactory[],
  systemPromptBuilders: [] as (() => string)[],
  ...overrides,
});

beforeEach(() => {
  fsState.exists = true;
  fsState.size = 100;
  capturedLoaderOptions.length = 0;
  sessionManagerCalls.length = 0;
  tiersInstances.length = 0;
  loaderReload.mockClear();
  authStorageCreate.mockClear();
  getApiKeyMock.mockReset();
  createAgentSessionMock.mockReset();
  createAgentSessionMock.mockResolvedValue({
    session: { prompt: vi.fn(), dispose: vi.fn() },
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
  const all = [{ id: "a" }, { id: "b" }, { id: "c" }] as unknown as ExtensionFactory[];
  const background = [all[0], all[2]] as ExtensionFactory[];
  const sources = { piFactories: all, backgroundFactories: background };

  it("binds the background subset for background task runs", () => {
    expect(
      selectExtensionFactories({ bindBackgroundFactories: true, bare: true }, sources),
    ).toEqual(background);
  });

  it("binds nothing for other bare side runs", () => {
    expect(selectExtensionFactories({ bare: true }, sources)).toEqual([]);
  });

  it("binds every factory for a normal (non-bare) session", () => {
    expect(selectExtensionFactories({ bare: false }, sources)).toEqual(all);
  });
});

describe("AgentManager constructor", () => {
  it("uses workspace-local auth.json when it has content", () => {
    fsState.exists = true;
    fsState.size = 100;

    new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    expect(authStorageCreate).toHaveBeenCalledWith("/ws/root/.tachikoma/pi/auth.json");
  });

  it("falls back to the shared pi login when the local auth file is missing", () => {
    fsState.exists = false;

    new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    expect(authStorageCreate).toHaveBeenCalledWith(undefined);
  });

  it("falls back to the shared pi login when the local auth file is effectively empty", () => {
    fsState.exists = true;
    fsState.size = 2;

    new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    expect(authStorageCreate).toHaveBeenCalledWith(undefined);
  });
});

describe("AgentManager.apiKeyFor", () => {
  it("returns the stored api key for a provider", async () => {
    getApiKeyMock.mockResolvedValue("sk-123");
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await expect(manager.apiKeyFor("anthropic")).resolves.toBe("sk-123");
    expect(getApiKeyMock).toHaveBeenCalledWith("anthropic");
  });

  it("coerces a missing key to undefined", async () => {
    getApiKeyMock.mockResolvedValue(null);
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await expect(manager.apiKeyFor("anthropic")).resolves.toBeUndefined();
  });
});

describe("AgentManager.open", () => {
  it("composes the system prompt from builders for a non-bare session", async () => {
    const sources = makeSources({ systemPromptBuilders: [() => "alpha", () => "beta"] });
    const manager = new AgentManager(makeWorkspace(), makeConfig(), sources, makeLog());

    await manager.open();

    const loaderOptions = capturedLoaderOptions[0];
    expect(loaderOptions.systemPromptOverride).toBeTypeOf("function");
    expect((loaderOptions.systemPromptOverride as () => string)()).toBe("alpha\n\nbeta");
    expect(loaderReload).toHaveBeenCalledOnce();
  });

  it("omits the system prompt override when bare with no explicit prompt", async () => {
    const sources = makeSources({ systemPromptBuilders: [() => "alpha"] });
    const manager = new AgentManager(makeWorkspace(), makeConfig(), sources, makeLog());

    await manager.open({ bare: true });

    expect(capturedLoaderOptions[0]).not.toHaveProperty("systemPromptOverride");
  });

  it("omits the system prompt override when non-bare but no builders exist", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open();

    expect(capturedLoaderOptions[0]).not.toHaveProperty("systemPromptOverride");
  });

  it("uses an explicit system prompt over the composed one", async () => {
    const sources = makeSources({ systemPromptBuilders: [() => "alpha"] });
    const manager = new AgentManager(makeWorkspace(), makeConfig(), sources, makeLog());

    await manager.open({ systemPrompt: "explicit" });

    expect((capturedLoaderOptions[0].systemPromptOverride as () => string)()).toBe("explicit");
  });

  it("applies isolated loader options when isolatePrompt is set", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open({ isolatePrompt: true });

    expect(capturedLoaderOptions[0].noContextFiles).toBe(true);
    expect(capturedLoaderOptions[0].noSkills).toBe(true);
  });

  it("does not isolate the loader by default", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open();

    expect(capturedLoaderOptions[0]).not.toHaveProperty("noContextFiles");
  });

  it("creates a fresh session manager by default", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open();

    expect(sessionManagerCalls.at(-1)?.kind).toBe("create");
  });

  it("opens an in-memory session manager when inMemory is set", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open({ inMemory: true });

    expect(sessionManagerCalls.at(-1)?.kind).toBe("inMemory");
  });

  it("forks from a source file when forkFromFile is set", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open({ forkFromFile: "/sessions/source.json" });

    const call = sessionManagerCalls.at(-1);
    expect(call?.kind).toBe("forkFrom");
    expect(call?.args[0]).toBe("/sessions/source.json");
  });

  it("opens an existing session file when sessionFile is set", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open({ sessionFile: "/sessions/existing.json" });

    const call = sessionManagerCalls.at(-1);
    expect(call?.kind).toBe("open");
    expect(call?.args[0]).toBe("/sessions/existing.json");
  });

  it("pins the model from an explicit model reference", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());
    const tiers = currentTiers();
    tiers.resolveRef.mockReturnValue({ model: { id: "m1" }, thinkingLevel: "high" });

    await manager.open({ model: "anthropic/claude:high" });

    expect(tiers.resolveRef).toHaveBeenCalledWith("anthropic/claude:high");
    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs.model).toEqual({ id: "m1" });
    expect(sessionArgs.thinkingLevel).toBe("high");
  });

  it("resolves the configured tier when no explicit model and a tier is configured", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());
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
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());
    currentTiers().configuredRef.mockReturnValue(null);

    await manager.open();

    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs).not.toHaveProperty("model");
    expect(sessionArgs).not.toHaveProperty("thinkingLevel");
  });

  it("defaults the tier to main when unspecified", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open();

    expect(currentTiers().configuredRef).toHaveBeenCalledWith("main");
  });

  it("forwards tools and customTools when provided", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());
    const customTools = [{ name: "t" }] as never;

    await manager.open({ tools: ["read"], customTools });

    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs.tools).toEqual(["read"]);
    expect(sessionArgs.customTools).toBe(customTools);
  });

  it("omits tools and customTools when not provided", async () => {
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.open();

    const sessionArgs = createAgentSessionMock.mock.calls[0][0];
    expect(sessionArgs).not.toHaveProperty("tools");
    expect(sessionArgs).not.toHaveProperty("customTools");
  });

  it("logs a warning when pi reports a model fallback", async () => {
    const log = makeLog();
    createAgentSessionMock.mockResolvedValue({
      session: { prompt: vi.fn(), dispose: vi.fn() },
      modelFallbackMessage: "fell back to default",
    });
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), log);

    await manager.open();

    expect(log.warn).toHaveBeenCalledWith(
      { modelFallbackMessage: "fell back to default" },
      "model fallback on session open",
    );
  });

  it("does not warn when there is no model fallback", async () => {
    const log = makeLog();
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), log);

    await manager.open();

    expect(log.warn).not.toHaveBeenCalled();
  });

  it("returns the created session", async () => {
    const session = { prompt: vi.fn(), dispose: vi.fn() };
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await expect(manager.open()).resolves.toBe(session);
  });
});

describe("AgentManager.forkAndContinue", () => {
  it("forks, prompts, and disposes the session", async () => {
    const session = { prompt: vi.fn().mockResolvedValue(undefined), dispose: vi.fn() };
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await manager.forkAndContinue("/sessions/src.json", "continue", "processor", ["read"]);

    const call = sessionManagerCalls.at(-1);
    expect(call?.kind).toBe("forkFrom");
    expect(session.prompt).toHaveBeenCalledWith("continue");
    expect(session.dispose).toHaveBeenCalledOnce();
  });

  it("disposes the session even when the prompt throws", async () => {
    const session = {
      prompt: vi.fn().mockRejectedValue(new Error("boom")),
      dispose: vi.fn(),
    };
    createAgentSessionMock.mockResolvedValue({ session, modelFallbackMessage: null });
    const manager = new AgentManager(makeWorkspace(), makeConfig(), makeSources(), makeLog());

    await expect(
      manager.forkAndContinue("/sessions/src.json", "continue", "processor"),
    ).rejects.toThrow("boom");
    expect(session.dispose).toHaveBeenCalledOnce();
  });
});
