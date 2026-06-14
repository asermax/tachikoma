import { describe, expect, it, vi } from "vitest";

import { persistentContextSection } from "../src/agent/system-prompt-section.ts";
import {
  defineExtension,
  type SessionScope,
  type TachikomaExtension,
} from "../src/extensions/api.ts";
import { ExtensionHost, factoryBindingTargets, type HostServices } from "../src/extensions/host.ts";
import { createRegistrations } from "../src/extensions/registrations.ts";

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
        app.agent.use(persistentContextSection("both", { provide: "B" }), {
          sessionScopes: ["main", "background"],
        });
        app.agent.use(persistentContextSection("main-only", { provide: "M" }));
        app.agent.use(persistentContextSection("bg-only", { provide: "G" }), {
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
