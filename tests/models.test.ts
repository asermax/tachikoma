import type { ModelRegistry, SettingsManager } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

import { ModelTiers, parseModelRef } from "../src/agent/models.ts";
import type { Config } from "../src/config/schema.ts";

describe("parseModelRef", () => {
  it("parses provider and id", () => {
    expect(parseModelRef("anthropic/claude-opus-4-5")).toEqual({
      provider: "anthropic",
      id: "claude-opus-4-5",
    });
  });

  it("parses a thinking level suffix", () => {
    expect(parseModelRef("anthropic/claude-opus-4-5:high")).toEqual({
      provider: "anthropic",
      id: "claude-opus-4-5",
      thinkingLevel: "high",
    });
  });

  it("keeps colons that are part of the model id", () => {
    expect(parseModelRef("ollama/llama3:8b")).toEqual({ provider: "ollama", id: "llama3:8b" });
  });

  it("rejects references without a provider", () => {
    expect(() => parseModelRef("claude-opus-4-5")).toThrow(/expected/);
  });
});

const fakeRegistry = (known: Record<string, string[]>, available: string[] = []) =>
  ({
    find: (provider: string, id: string) =>
      known[provider]?.includes(id) ? { provider, id } : undefined,
    getAvailable: () =>
      available.map((ref) => {
        const [provider, id] = ref.split("/");
        return { provider, id };
      }),
  }) as unknown as ModelRegistry;

const fakeSettings = (defaults: { provider?: string; model?: string } = {}) =>
  ({
    getDefaultProvider: () => defaults.provider,
    getDefaultModel: () => defaults.model,
    getDefaultThinkingLevel: () => undefined,
  }) as unknown as SettingsManager;

const agentConfig = (overrides: Partial<Config["agent"]> = {}): Config["agent"] => overrides;

describe("ModelTiers", () => {
  it("resolves a configured role with its thinking suffix", () => {
    const tiers = new ModelTiers(
      agentConfig({ main: "anthropic/claude-opus-4-5:high" }),
      fakeRegistry({ anthropic: ["claude-opus-4-5"] }),
      fakeSettings(),
    );

    const resolved = tiers.resolve("main");

    expect(resolved.model.id).toBe("claude-opus-4-5");
    expect(resolved.thinkingLevel).toBe("high");
    expect(resolved.fromPiDefaults).toBe(false);
  });

  it("falls back along classifier → processor → main", () => {
    const tiers = new ModelTiers(
      agentConfig({ main: "google/gemini-2.5-pro", processor: "google/gemini-2.5-flash" }),
      fakeRegistry({ google: ["gemini-2.5-pro", "gemini-2.5-flash"] }),
      fakeSettings(),
    );

    expect(tiers.resolve("classifier").model.id).toBe("gemini-2.5-flash");
    expect(tiers.resolve("searcher").model.id).toBe("gemini-2.5-pro");
  });

  it("uses pi's settings default when no role in the chain is set", () => {
    const tiers = new ModelTiers(
      agentConfig(),
      fakeRegistry({ deepseek: ["deepseek-v4-pro"] }),
      fakeSettings({ provider: "deepseek", model: "deepseek-v4-pro" }),
    );

    const resolved = tiers.resolve("processor");

    expect(resolved.model.id).toBe("deepseek-v4-pro");
    expect(resolved.fromPiDefaults).toBe(true);
  });

  it("falls back to the first credentialed model when settings are empty", () => {
    const tiers = new ModelTiers(agentConfig(), fakeRegistry({}, ["zai/glm-5"]), fakeSettings());

    expect(tiers.resolve("main").model.id).toBe("glm-5");
  });

  it("throws a configuration error when nothing can resolve", () => {
    const tiers = new ModelTiers(agentConfig(), fakeRegistry({}), fakeSettings());

    expect(() => tiers.resolve("classifier")).toThrow(/No model available/);
  });

  it("throws when a configured model is unknown to the registry", () => {
    const tiers = new ModelTiers(
      agentConfig({ main: "anthropic/claude-nonexistent" }),
      fakeRegistry({ anthropic: [] }),
      fakeSettings(),
    );

    expect(() => tiers.resolve("main")).toThrow(/not found/);
  });
});
