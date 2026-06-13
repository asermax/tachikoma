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

  // Per-role model selection only — everything else about the agent (default model,
  // thinking budgets, compaction, retry, custom providers) belongs to pi's own
  // settings.json/models.json under {workspace}/.tachikoma/pi/.
  agent: Type.Object(
    {
      // "provider/model-id[:thinkingLevel]". Unset roles fall back along
      // classifier → processor → main (searcher → main), then to pi's resolution.
      main: Type.Optional(Type.String()),
      searcher: Type.Optional(Type.String()),
      processor: Type.Optional(Type.String()),
      classifier: Type.Optional(Type.String()),
    },
    { default: {} },
  ),

  logging: Type.Object(
    {
      level: Type.String({ default: "info" }),
      pretty: Type.Boolean({ default: true }),
      toFile: Type.Boolean({ default: true }),
      rotateFrequency: StringEnum(["hourly", "daily"] as const, { default: "daily" }),
      retentionDays: Type.Number({ default: 7 }),
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
