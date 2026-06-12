import { StringEnum } from "@earendil-works/pi-ai";
import { type Static, Type } from "typebox";

export const ThinkingLevelSchema = StringEnum(
  ["off", "minimal", "low", "medium", "high", "xhigh"] as const,
  { default: "medium" },
);

export const ConfigSchema = Type.Object({
  workspace: Type.Object(
    {
      path: Type.String({ default: "~/tachikoma" }),
    },
    { default: {} },
  ),

  agent: Type.Object(
    {
      // Model references use "provider/model-id" as known to pi's model registry.
      model: Type.String({ default: "anthropic/claude-opus-4-5" }),
      thinkingLevel: ThinkingLevelSchema,
      searcherModel: Type.String({ default: "anthropic/claude-opus-4-5" }),
      processorModel: Type.String({ default: "anthropic/claude-haiku-4-5" }),
      classifierModel: Type.String({ default: "anthropic/claude-haiku-4-5" }),
    },
    { default: {} },
  ),

  logging: Type.Object(
    {
      level: Type.String({ default: "info" }),
      pretty: Type.Boolean({ default: true }),
    },
    { default: {} },
  ),

  channels: Type.Object(
    {
      default: Type.String({ default: "repl" }),
    },
    { default: {} },
  ),

  sessions: Type.Object(
    {
      idleCloseSeconds: Type.Number({ default: 900 }),
      resumeWindowSeconds: Type.Number({ default: 86400 }),
    },
    { default: {} },
  ),

  scheduler: Type.Object(
    {
      timezone: Type.Optional(Type.String()),
    },
    { default: {} },
  ),

  // Per-extension sections, validated by each extension's own configSchema.
  extensions: Type.Record(Type.String(), Type.Unknown(), { default: {} }),
});

export type Config = Static<typeof ConfigSchema>;
export type ThinkingLevel = Static<typeof ThinkingLevelSchema>;
