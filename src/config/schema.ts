import { StringEnum } from "@earendil-works/pi-ai";
import { type Static, Type } from "typebox";

export const ThinkingLevelSchema = StringEnum(
  ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const,
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
      default: Type.String({ default: "telegram" }),
    },
    { default: {} },
  ),

  coordinator: Type.Object(
    {
      // Milliseconds a bare arg-taking command (/new, /queue, /skill) waits for its argument before
      // the pending-input prompt expires (R9). Default 2 minutes; kept short so a stale prompt can't
      // capture a later, unrelated message. Transient (in-memory) — never survives a restart.
      pendingInputTtlMs: Type.Number({ default: 120_000 }),
    },
    { default: {} },
  ),

  scheduler: Type.Object(
    {
      timezone: Type.Optional(Type.String()),
      // Hour (0–23, scheduler tz) the nightly trunk-close cron fires. 04:00 matches the slot the
      // former core-context maintenance cron used; close only happens when no exchange is in flight.
      nightlyCloseHour: Type.Number({ default: 4 }),
      // Minutes of exchange quiet before pending workspace + registered-project changes are
      // committed and pushed in the background (trailing-edge debounce — every exchange resets the
      // timer, so an active conversation defers persistence until this long after the last exchange).
      // 0 disables mid-session auto commit-push entirely; only the nightly trunk close persists.
      commitDebounceMinutes: Type.Number({ default: 5 }),
    },
    { default: {} },
  ),

  // Environment variables applied to process.env at startup, before any runtime
  // service or pi session is constructed — so they are visible app-wide and to
  // anything that inherits the process environment (sessions, spawned tools,
  // detached processes). Config-defined values overwrite existing same-named vars.
  env: Type.Record(Type.String(), Type.String(), { default: {} }),

  // Per-extension sections, validated by each extension's own configSchema.
  extensions: Type.Record(Type.String(), Type.Unknown(), { default: {} }),
});

export type Config = Static<typeof ConfigSchema>;
export type ThinkingLevel = Static<typeof ThinkingLevelSchema>;
