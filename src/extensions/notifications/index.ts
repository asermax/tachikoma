import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { NOTIFY_EVENT } from "./payload.ts";
import { NotificationRouter } from "./router.ts";
import { createNotifyToolFactory } from "./tools.ts";

export type { NotifyPayload, Severity } from "./payload.ts";

interface NotificationsConfig {
  flushWindowSeconds: number;
  maxHoldSeconds: number;
  dedupTtlSeconds: number;
}

/**
 * Notification delivery: routes `"notify"` app events to the user by severity —
 * urgent immediately, the rest idle-gated with a max hold, batched into a digest
 * when several accumulate. Also exposes a notify_user tool so the agent itself
 * can emit notifications.
 */
export default defineExtension<NotificationsConfig>({
  name: "notifications",

  configSchema: Type.Object({
    flushWindowSeconds: Type.Number({ default: 30 }),
    maxHoldSeconds: Type.Number({ default: 900 }),
    /** Window in which an identical (source + text) notice is dropped, guarding against re-emit/retry storms. */
    dedupTtlSeconds: Type.Number({ default: 60 }),
  }),

  setup(app) {
    const router = new NotificationRouter({
      deliver: (delivery) => app.channels.deliver(delivery),
      flushWindowSeconds: app.extensionConfig.flushWindowSeconds,
      maxHoldSeconds: app.extensionConfig.maxHoldSeconds,
      dedupTtlSeconds: app.extensionConfig.dedupTtlSeconds,
      log: app.log,
    });

    app.events.on(NOTIFY_EVENT, (payload) => router.handle(payload));

    app.agent.use(
      createNotifyToolFactory((event, payload) => app.events.emit(event, payload)),
      {
        sessionScopes: ["main", "background"],
      },
    );
  },
});
