import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import { defineExtension, type TachikomaExtension } from "../../src/extensions/api.ts";
import { ExtensionHost, type HostServices } from "../../src/extensions/host.ts";

const createFakeLog = () => {
  const log = {
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
  };

  return Object.assign(log, { child: () => log });
};

let db: AppDatabase;
let log: ReturnType<typeof createFakeLog>;

// buildContext only references these services in closures the test never invokes,
// so a structurally-typed stub is enough to exercise the load/isolation path.
const buildHost = (): ExtensionHost => {
  const services = {
    config: { extensions: {} },
    workspace: { dataDir: "/tmp", resolve: (p: string) => p },
    log,
    db,
    events: {},
    scheduler: {},
    agent: { tiers: {} },
    registry: {},
    coordinator: {},
    regs: {
      piFactories: [],
      systemPromptBuilders: [],
      contextProviders: [],
      exchangeProcessors: [],
      postProcessors: [],
      inboundMiddleware: [],
      sessionOpenHooks: [],
      channels: new Map(),
      bootstrapHooks: [],
    },
  } as unknown as HostServices;

  return new ExtensionHost(services);
};

beforeEach(async () => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-startup-isolation-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
  log = createFakeLog();
});

describe("ExtensionHost setup isolation", () => {
  it("isolates a throwing external setup and continues startup", async () => {
    const order: string[] = [];

    const throwingExternal = defineExtension({
      name: "bad-third-party",
      setup() {
        order.push("external-attempt");
        throw new Error("boom in external setup");
      },
    });

    // External extensions only ever enter the queue via app.registerExtension,
    // which a first-party extension (here, a stand-in for `external`) calls.
    const registrar = defineExtension({
      name: "registrar",
      setup(app) {
        order.push("registrar");
        app.registerExtension(throwingExternal as TachikomaExtension<never>);
      },
    });

    const after = defineExtension({
      name: "after-external",
      setup() {
        order.push("after-external");
      },
    });

    const host = buildHost();

    await expect(
      host.load([registrar, after] as TachikomaExtension<never>[]),
    ).resolves.toBeUndefined();

    expect(order).toEqual(["registrar", "after-external", "external-attempt"]);
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ extension: "bad-third-party" }),
      expect.stringContaining("skipping"),
    );
  });

  it("skips an external setup that hangs past its timeout", async () => {
    let resolved = false;

    const hangingExternal = defineExtension({
      name: "hanging-third-party",
      async setup() {
        await new Promise<void>((resolve) => setTimeout(resolve, 60_000));
        resolved = true;
      },
    });

    const registrar = defineExtension({
      name: "registrar",
      setup(app) {
        app.registerExtension(hangingExternal as TachikomaExtension<never>, { setupTimeoutMs: 20 });
      },
    });

    const host = buildHost();

    await expect(host.load([registrar] as TachikomaExtension<never>[])).resolves.toBeUndefined();

    expect(resolved).toBe(false);
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ extension: "hanging-third-party" }),
      expect.stringContaining("skipping"),
    );
  });

  it("propagates a throwing first-party setup (no isolation)", async () => {
    const throwingFirstParty = defineExtension({
      name: "core-bug",
      setup() {
        throw new Error("core bug must surface");
      },
    });

    const host = buildHost();

    await expect(host.load([throwingFirstParty] as TachikomaExtension<never>[])).rejects.toThrow(
      "core bug must surface",
    );
  });

  it("isolates a throwing external bootstrap hook and continues", async () => {
    const registrar = defineExtension({
      name: "registrar",
      setup(app) {
        app.registerExtension(
          defineExtension({
            name: "bad-bootstrap-third-party",
            setup(externalApp) {
              externalApp.bootstrap("init", () => {
                throw new Error("boom in external bootstrap");
              });
            },
          }) as TachikomaExtension<never>,
        );
      },
    });

    const host = buildHost();

    await host.load([registrar] as TachikomaExtension<never>[]);

    await expect(host.bootstrap()).resolves.toBeUndefined();
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ hook: "bad-bootstrap-third-party:init" }),
      expect.stringContaining("skipping"),
    );
  });

  it("propagates a throwing first-party bootstrap hook (no isolation)", async () => {
    const firstParty = defineExtension({
      name: "core",
      setup(app) {
        app.bootstrap("init", () => {
          throw new Error("core bootstrap bug must surface");
        });
      },
    });

    const host = buildHost();
    await host.load([firstParty] as TachikomaExtension<never>[]);

    await expect(host.bootstrap()).rejects.toThrow("core bootstrap bug must surface");
  });
});
