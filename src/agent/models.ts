import type { ModelRegistry } from "@earendil-works/pi-coding-agent";

import type { Config } from "../config/schema.ts";

export const MODEL_TIERS = {
  /** Main conversational agent. */
  agent: "agent",
  /** Retrieval and routing work. */
  searcher: "searcher",
  /** Extraction and mechanical transformation. */
  processor: "processor",
  /** Discrete classification with structured output. */
  classifier: "classifier",
} as const;

export type ModelTier = keyof typeof MODEL_TIERS;

export interface ModelRef {
  provider: string;
  id: string;
}

export const parseModelRef = (ref: string): ModelRef => {
  const slash = ref.indexOf("/");

  if (slash <= 0 || slash === ref.length - 1) {
    throw new Error(`Invalid model reference "${ref}" — expected "provider/model-id"`);
  }

  return { provider: ref.slice(0, slash), id: ref.slice(slash + 1) };
};

export class ModelTiers {
  private readonly agentConfig: Config["agent"];
  private readonly registry: ModelRegistry;

  constructor(agentConfig: Config["agent"], registry: ModelRegistry) {
    this.agentConfig = agentConfig;
    this.registry = registry;
  }

  ref(tier: ModelTier): ModelRef {
    const raw = {
      agent: this.agentConfig.model,
      searcher: this.agentConfig.searcherModel,
      processor: this.agentConfig.processorModel,
      classifier: this.agentConfig.classifierModel,
    }[tier];

    return parseModelRef(raw);
  }

  resolve(tier: ModelTier) {
    const { provider, id } = this.ref(tier);
    const model = this.registry.find(provider, id);

    if (model == null) {
      throw new Error(`Model "${provider}/${id}" (${tier} tier) not found in pi's model registry`);
    }

    return model;
  }
}
