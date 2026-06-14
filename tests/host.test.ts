import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { Type } from "typebox";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { provideContext } from "../src/agent/system-prompt-section.ts";
import {
  type AppContext,
  defineExtension,
  type PostProcessor,
  type PostProcessorContext,
  type SessionScope,
  type TachikomaExtension,
} from "../src/extensions/api.ts";
import { ExtensionHost, factoryBindingTargets, type HostServices } from "../src/extensions/host.ts";
import { createRegistrations } from "../src/extensions/registrations.ts";
import { initRepo } from "./git/helpers.ts";

type MockLog = {
  warn: ReturnType<typeof vi.fn>;
  info: ReturnType<typeof vi.fn>;
  debug: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
  child: () => MockLog;
};

const createLog = (): MockLog => {
  const log: MockLog = {
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
    child: () => log,
  };

  return log;
};

const createServices = (overrides: Partial<HostServices> = {}): HostServices => {
  const log = createLog();
  const regs = createRegistrations();

  return {
    config: { extensions: {}, sessions: { resumeWindowSeconds: 60 } },
    workspace: { dataDir: "/tmp", resolve: (p: string) => p },
    log,
    db: {},
    events: {},
    scheduler: {},
    agent: { tiers: {} },
    registry: {},
    coordinator: {},
    regs,
    ...overrides,
  } as unknown as HostServices;
};

describe("factoryBindingTargets", () => {
  it("binds the main session only when scopes are omitted (AC1)", () => {
    expect(factoryBindingTargets()).toEqual({ main: true, background: false });
    expect(factoryBindingTargets({})).toEqual({ main: true, background: false });
  });

  it('treats explicit ["main"] like the omitted default (AC5)', () => {
    expect(factoryBindingTargets({ sessionScopes: ["main"] })).toEqual({
      main: true,
      background: false,
    });
  });

  it("binds both lists when both scopes are present (AC2)", () => {
    expect(factoryBindingTargets({ sessionScopes: ["main", "background"] })).toEqual({
      main: true,
      background: true,
    });
  });

  it("binds background only when main is absent (AC3)", () => {
    expect(factoryBindingTargets({ sessionScopes: ["background"] })).toEqual({
      main: false,
      background: true,
    });
  });

  it("binds neither list for an empty scope array, without throwing (AC4)", () => {
    expect(factoryBindingTargets({ sessionScopes: [] })).toEqual({
      main: false,
      background: false,
    });
  });

  it("ignores out-of-union scopes rather than throwing (AC6)", () => {
    expect(factoryBindingTargets({ sessionScopes: ["main", "unknown"] as SessionScope[] })).toEqual(
      { main: true, background: false },
    );
  });
});

describe("agent.use context-section registration", () => {
  it("routes a context section into main and/or background by its scope", async () => {
    const regs = createRegistrations();
    const log = Object.assign(
      { warn: vi.fn(), info: vi.fn(), debug: vi.fn(), error: vi.fn() },
      { child() {} },
    );
    log.child = () => log;

    const services = {
      config: { extensions: {} },
      workspace: { dataDir: "/tmp", resolve: (p: string) => p },
      log,
      db: {},
      events: {},
      scheduler: {},
      agent: { tiers: {} },
      registry: {},
      coordinator: {},
      regs,
    } as unknown as HostServices;

    const ext = defineExtension({
      name: "ctx-test",
      setup(app) {
        app.agent.use(provideContext("B", "both"), {
          sessionScopes: ["main", "background"],
        });
        app.agent.use(provideContext("M", "main-only"));
        app.agent.use(provideContext("G", "bg-only"), {
          sessionScopes: ["background"],
        });
      },
    });

    await new ExtensionHost(services).load([ext] as TachikomaExtension<never>[]);

    // "both" + "main-only" bind main; "both" + "bg-only" bind background.
    expect(regs.piFactories).toHaveLength(2);
    expect(regs.backgroundFactories).toHaveLength(2);
  });
});

describe("ExtensionHost.load", () => {
  it("validates extension config against its schema before setup", async () => {
    const services = createServices({
      config: {
        extensions: { schemaed: { count: "7" } },
        sessions: { resumeWindowSeconds: 60 },
      },
    } as unknown as Partial<HostServices>);

    let seen: unknown;

    const ext = defineExtension({
      name: "schemaed",
      configSchema: Type.Object({ count: Type.Number({ default: 1 }) }),
      setup(app) {
        seen = app.extensionConfig;
      },
    });

    await new ExtensionHost(services).load([ext] as TachikomaExtension<never>[]);

    expect(seen).toEqual({ count: 7 });
  });

  it("falls back to the raw section when no schema is declared", async () => {
    const services = createServices({
      config: {
        extensions: { plain: { anything: true } },
        sessions: { resumeWindowSeconds: 60 },
      },
    } as unknown as Partial<HostServices>);

    let seen: unknown;

    const ext = defineExtension({
      name: "plain",
      setup(app) {
        seen = app.extensionConfig;
      },
    });

    await new ExtensionHost(services).load([ext] as TachikomaExtension<never>[]);

    expect(seen).toEqual({ anything: true });
  });

  it("defaults extensionConfig to an empty object when the section is absent", async () => {
    const services = createServices();
    let seen: unknown;

    const ext = defineExtension({
      name: "missing-section",
      setup(app) {
        seen = app.extensionConfig;
      },
    });

    await new ExtensionHost(services).load([ext] as TachikomaExtension<never>[]);

    expect(seen).toEqual({});
  });

  it("propagates a first-party setup failure (fail hard)", async () => {
    const services = createServices();

    const ext = defineExtension({
      name: "boom",
      setup() {
        throw new Error("first-party blew up");
      },
    });

    await expect(
      new ExtensionHost(services).load([ext] as TachikomaExtension<never>[]),
    ).rejects.toThrow("first-party blew up");
  });

  it("isolates a throwing external setup and logs a warning", async () => {
    const services = createServices();
    const log = services.log as unknown as MockLog;

    const external = defineExtension({
      name: "ext-bad",
      setup() {
        throw new Error("third-party blew up");
      },
    });

    const host = new ExtensionHost(services);

    const enqueuer = defineExtension({
      name: "enqueuer",
      setup(app) {
        app.registerExtension(external as TachikomaExtension<never>);
      },
    });

    await host.load([enqueuer] as TachikomaExtension<never>[]);

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ extension: "ext-bad" }),
      expect.stringContaining("external extension setup failed"),
    );
  });

  it("isolates a hanging external setup via the setup timeout", async () => {
    vi.useFakeTimers();

    const services = createServices();
    const log = services.log as unknown as MockLog;

    const hanging = defineExtension({
      name: "ext-hang",
      setup() {
        return new Promise<void>(() => {});
      },
    });

    const enqueuer = defineExtension({
      name: "enqueuer",
      setup(app) {
        app.registerExtension(hanging as TachikomaExtension<never>, { setupTimeoutMs: 50 });
      },
    });

    const loaded = new ExtensionHost(services).load([enqueuer] as TachikomaExtension<never>[]);

    await vi.advanceTimersByTimeAsync(60);
    await loaded;

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ extension: "ext-hang" }),
      expect.stringContaining("external extension setup failed"),
    );
  });

  it("loads a successful external setup and logs at debug", async () => {
    const services = createServices();
    const log = services.log as unknown as MockLog;
    const setup = vi.fn();

    const external = defineExtension({ name: "ext-ok", setup });

    const enqueuer = defineExtension({
      name: "enqueuer",
      setup(app) {
        app.registerExtension(external as TachikomaExtension<never>);
      },
    });

    await new ExtensionHost(services).load([enqueuer] as TachikomaExtension<never>[]);

    expect(setup).toHaveBeenCalledTimes(1);
    expect(log.debug).toHaveBeenCalledWith(
      expect.objectContaining({ extension: "ext-ok" }),
      "external extension loaded",
    );
  });
});

describe("ExtensionHost context API delegation", () => {
  const captureApp = async (services: HostServices): Promise<AppContext<unknown>> => {
    let captured!: AppContext<unknown>;

    const ext = defineExtension({
      name: "capture",
      setup(app) {
        captured = app as AppContext<unknown>;
      },
    });

    await new ExtensionHost(services).load([ext] as TachikomaExtension<never>[]);

    return captured;
  };

  it("wires the sessions API onto the coordinator and registry", async () => {
    const coordinator = {
      current: vi.fn(() => ({ id: 1 })),
      closeActiveSession: vi.fn(async () => {}),
      closeActiveSessionIfIdle: vi.fn(async () => true),
      abortExchange: vi.fn(async () => {}),
    };

    const registry = {
      get: vi.fn(() => ({ id: 2 })),
      update: vi.fn(() => ({ id: 2 })),
      listResumable: vi.fn(() => [{ id: 3 }]),
    };

    const services = createServices({
      coordinator: coordinator as unknown as HostServices["coordinator"],
      registry: registry as unknown as HostServices["registry"],
    });

    const app = await captureApp(services);

    expect(app.sessions.current()).toEqual({ id: 1 });
    expect(app.sessions.get(2)).toEqual({ id: 2 });

    app.sessions.update(2, { summary: "s" });
    expect(registry.update).toHaveBeenCalledWith(2, { summary: "s" });

    app.sessions.listResumable();
    expect(registry.listResumable).toHaveBeenCalledWith(60);

    await app.sessions.close();
    expect(coordinator.closeActiveSession).toHaveBeenCalled();

    expect(await app.sessions.closeIfIdle()).toBe(true);
    await app.sessions.abortExchange();
    expect(coordinator.abortExchange).toHaveBeenCalled();
  });

  it("registers session lifecycle hooks and processors", async () => {
    const services = createServices();
    const app = await captureApp(services);

    const openHook = vi.fn();
    const exchangeProcessor = { name: "ex", process: vi.fn() };
    const postProcessor: PostProcessor = { name: "pp", process: vi.fn(async () => {}) };

    app.sessions.onOpen(openHook);
    app.sessions.onExchange(exchangeProcessor);
    app.sessions.registerProcessor(postProcessor);

    expect(services.regs.sessionOpenHooks).toContain(openHook);
    expect(services.regs.exchangeProcessors).toContain(exchangeProcessor);
    expect(services.regs.postProcessors).toContain(postProcessor);
  });

  it("wires the channels API onto registry and coordinator", async () => {
    const deliver = vi.fn();
    const services = createServices({
      coordinator: { deliver } as unknown as HostServices["coordinator"],
    });

    const app = await captureApp(services);
    const channel = { name: "repl" } as never;

    app.channels.register(channel);
    expect(services.regs.channels.get("repl")).toBe(channel);

    const delivery = { text: "hi" } as never;
    app.channels.deliver(delivery);
    expect(deliver).toHaveBeenCalledWith(delivery);
  });

  it("exposes agent helpers and routes systemPrompt builders", async () => {
    const forkAndContinue = vi.fn(async () => {});
    const tiers = { fast: {} };

    const services = createServices({
      agent: { tiers, forkAndContinue } as unknown as HostServices["agent"],
    });

    const app = await captureApp(services);

    expect(app.agent.models).toBe(tiers);
    expect(app.agent.side).toBeDefined();

    await app.agent.forkAndContinue("/sess.jsonl", "go", "fast");
    expect(forkAndContinue).toHaveBeenCalledWith("/sess.jsonl", "go", "fast", undefined);
  });

  it("delegates the git API to the core helpers over a real repo", async () => {
    const base = await mkdtemp(join(tmpdir(), "tachi-host-git-"));

    try {
      await initRepo(base);

      const services = createServices();
      const app = await captureApp(services);

      expect(typeof app.git.smartPush).toBe("function");
      expect(typeof app.git.smartPull).toBe("function");

      // A clean tree has nothing to commit — proves the call reaches the core helper.
      await expect(app.git.commitAll({ cwd: base, fallbackMessage: "noop" })).resolves.toBeNull();
    } finally {
      await rm(base, { recursive: true, force: true });
    }
  });

  it("registers inbound middleware, status, shutdown and bootstrap hooks", async () => {
    const status = vi.fn();
    const services = createServices({
      coordinator: { status } as unknown as HostServices["coordinator"],
    });

    const app = await captureApp(services);

    const middleware = vi.fn();
    app.inbound.use(middleware);
    expect(services.regs.inboundMiddleware).toContain(middleware);

    app.status("working");
    expect(status).toHaveBeenCalledWith("working");

    const shutdown = vi.fn();
    app.onShutdown("flush", shutdown);
    expect(services.regs.shutdownHooks).toEqual([
      expect.objectContaining({ name: "capture:flush", hook: shutdown }),
    ]);

    const bootstrapHook = vi.fn();
    app.bootstrap("warm", bootstrapHook);
    expect(services.regs.bootstrapHooks).toEqual([
      expect.objectContaining({ name: "capture:warm", hook: bootstrapHook, external: false }),
    ]);
  });

  it("runs registered post-processors through the sessions API", async () => {
    const services = createServices();
    const log = services.log as unknown as MockLog;
    const app = await captureApp(services);

    const order: string[] = [];

    app.sessions.registerProcessor({
      name: "finalizer",
      phase: "finalize",
      process: async () => {
        order.push("finalize");
      },
    });
    app.sessions.registerProcessor({
      name: "mainer",
      process: async () => {
        order.push("main");
      },
    });

    await app.sessions.runPostProcessors({
      session: null,
      transcriptPath: null,
      log: log as unknown as PostProcessorContext["log"],
    });

    expect(order).toEqual(["main", "finalize"]);
  });
});

describe("runPostProcessorsOnce error isolation", () => {
  it("logs a rejected processor and still runs the rest", async () => {
    const services = createServices();
    const log = services.log as unknown as MockLog;

    let captured!: AppContext<unknown>;
    const ext = defineExtension({
      name: "capture",
      setup(app) {
        captured = app as AppContext<unknown>;
      },
    });
    await new ExtensionHost(services).load([ext] as TachikomaExtension<never>[]);

    const survivor = vi.fn(async () => {});

    captured.sessions.registerProcessor({
      name: "exploder",
      process: async () => {
        throw new Error("processor failed");
      },
    });
    captured.sessions.registerProcessor({ name: "survivor", process: survivor });

    await captured.sessions.runPostProcessors({
      session: null,
      transcriptPath: null,
      log: log as unknown as PostProcessorContext["log"],
    });

    expect(survivor).toHaveBeenCalledTimes(1);
    expect(log.error).toHaveBeenCalledWith(
      expect.objectContaining({ processor: "exploder" }),
      "post-processor failed",
    );
  });
});

describe("ExtensionHost.bootstrap", () => {
  it("runs first-party hooks and propagates their failures", async () => {
    const services = createServices();
    const ran: string[] = [];

    services.regs.bootstrapHooks.push({
      name: "core:ok",
      hook: () => {
        ran.push("ok");
      },
    });
    services.regs.bootstrapHooks.push({
      name: "core:bad",
      hook: () => {
        throw new Error("bootstrap blew up");
      },
    });

    await expect(new ExtensionHost(services).bootstrap()).rejects.toThrow("bootstrap blew up");
    expect(ran).toEqual(["ok"]);
  });

  it("isolates a failing external hook and continues", async () => {
    const services = createServices();
    const log = services.log as unknown as MockLog;
    const after = vi.fn();

    services.regs.bootstrapHooks.push({
      name: "ext:bad",
      external: true,
      hook: () => {
        throw new Error("external bootstrap failed");
      },
    });
    services.regs.bootstrapHooks.push({ name: "ext:after", external: true, hook: after });

    await new ExtensionHost(services).bootstrap();

    expect(after).toHaveBeenCalledTimes(1);
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ hook: "ext:bad" }),
      expect.stringContaining("external bootstrap hook failed"),
    );
  });
});

describe("withTimeout (via successful external setup)", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("clears the timer when setup resolves before the timeout", async () => {
    const services = createServices();
    const clearSpy = vi.spyOn(globalThis, "clearTimeout");

    const external = defineExtension({ name: "ext-fast", setup: vi.fn() });
    const enqueuer = defineExtension({
      name: "enqueuer",
      setup(app) {
        app.registerExtension(external as TachikomaExtension<never>, { setupTimeoutMs: 1000 });
      },
    });

    await new ExtensionHost(services).load([enqueuer] as TachikomaExtension<never>[]);

    expect(clearSpy).toHaveBeenCalled();
  });
});
