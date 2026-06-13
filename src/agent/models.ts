import type { ModelRegistry, SettingsManager } from "@earendil-works/pi-coding-agent";

import type { Config, ThinkingLevel } from "../config/schema.ts";

export const MODEL_TIERS = {
  /** Main conversational agent. */
  main: "main",
  /** Retrieval and routing work. */
  searcher: "searcher",
  /** Extraction and mechanical transformation. */
  processor: "processor",
  /** Discrete classification with structured output. */
  classifier: "classifier",
} as const;

export type ModelTier = keyof typeof MODEL_TIERS;

const THINKING_LEVELS = new Set(["off", "minimal", "low", "medium", "high", "xhigh"]);

export interface ModelRef {
  provider: string;
  id: string;
  thinkingLevel?: ThinkingLevel;
}

/**
 * Parse "provider/model-id[:thinkingLevel]". The suffix is only treated as a
 * thinking level when it names one — model ids may legitimately contain colons
 * (e.g. ollama's "llama3:8b").
 */
export const parseModelRef = (ref: string): ModelRef => {
  const slash = ref.indexOf("/");

  if (slash <= 0 || slash === ref.length - 1) {
    throw new Error(
      `Invalid model reference "${ref}" — expected "provider/model-id[:thinkingLevel]"`,
    );
  }

  const provider = ref.slice(0, slash);
  let id = ref.slice(slash + 1);
  let thinkingLevel: ThinkingLevel | undefined;

  const colon = id.lastIndexOf(":");
  if (colon > 0 && THINKING_LEVELS.has(id.slice(colon + 1))) {
    thinkingLevel = id.slice(colon + 1) as ThinkingLevel;
    id = id.slice(0, colon);
  }

  return { provider, id, ...(thinkingLevel != null ? { thinkingLevel } : {}) };
};

export interface ResolvedTier {
  model: NonNullable<ReturnType<ModelRegistry["find"]>>;
  thinkingLevel?: ThinkingLevel;
  /** True when nothing was configured and pi's own default resolution applied. */
  fromPiDefaults: boolean;
}

const FALLBACK_CHAIN: Record<ModelTier, ModelTier[]> = {
  main: ["main"],
  searcher: ["searcher", "main"],
  processor: ["processor", "main"],
  classifier: ["classifier", "processor", "main"],
};

/**
 * Per-role model selection. Roles left unset in `[agent]` fall back along
 * classifier → processor → main (searcher → main); a fully unset chain defers
 * to pi's own resolution: settings defaultProvider/defaultModel, else the
 * first credentialed model.
 */
export class ModelTiers {
  private readonly agentConfig: Config["agent"];
  private readonly registry: ModelRegistry;
  private readonly settings: SettingsManager;

  constructor(agentConfig: Config["agent"], registry: ModelRegistry, settings: SettingsManager) {
    this.agentConfig = agentConfig;
    this.registry = registry;
    this.settings = settings;
  }

  /** The configured reference for a tier after applying the fallback chain, if any. */
  configuredRef(tier: ModelTier): ModelRef | null {
    for (const candidate of FALLBACK_CHAIN[tier]) {
      const raw = this.agentConfig[candidate];
      if (raw != null) return parseModelRef(raw);
    }

    return null;
  }

  resolve(tier: ModelTier): ResolvedTier {
    const ref = this.configuredRef(tier);

    if (ref != null) {
      const model = this.registry.find(ref.provider, ref.id);

      if (model == null) {
        throw new Error(
          `Model "${ref.provider}/${ref.id}" (${tier} tier) not found in pi's model registry`,
        );
      }

      return {
        model,
        ...(ref.thinkingLevel != null ? { thinkingLevel: ref.thinkingLevel } : {}),
        fromPiDefaults: false,
      };
    }

    return { ...this.resolvePiDefault(tier), fromPiDefaults: true };
  }

  private resolvePiDefault(tier: ModelTier): Omit<ResolvedTier, "fromPiDefaults"> {
    const provider = this.settings.getDefaultProvider();
    const id = this.settings.getDefaultModel();
    const thinkingLevel = this.settings.getDefaultThinkingLevel();

    if (provider != null && id != null) {
      const model = this.registry.find(provider, id);

      if (model != null) {
        return { model, ...(thinkingLevel != null ? { thinkingLevel } : {}) };
      }
    }

    const available = this.registry.getAvailable();
    const first = available[0];

    if (first == null) {
      throw new Error(
        `No model available for the ${tier} tier: set [agent].${tier} in config.toml, ` +
          "a default model in pi's settings.json, or provider credentials",
      );
    }

    return { model: first, ...(thinkingLevel != null ? { thinkingLevel } : {}) };
  }
}
