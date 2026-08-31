import { Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { defineExtension } from "../api.ts";
import { NOTIFY_EVENT } from "./payload.ts";
import { NotificationRouter } from "./router.ts";
import { createNotifyToolFactory } from "./tools.ts";
import { NOTIFICATIONS_USAGE } from "./usage.ts";

export type { NotifyPayload, Severity } from "./payload.ts";

interface NotificationsConfig {
  flushWindowSeconds: number;
  dedupTtlSeconds: number;
}

/**
 * Notification delivery: routes `"notify"` app events into the conversation by
 * severity → delivery tier (urgent leads), batched into a digest when several
 * accumulate. Also exposes a notify_user tool — bound only into background task
 * runs — so an autonomous task can emit notifications.
 */
export default defineExtension<NotificationsConfig>({
  name: "notifications",

  configSchema: Type.Object({
    flushWindowSeconds: Type.Number({ default: 30 }),
    /** Window in which an identical (source + text) notice is dropped, guarding against re-emit/retry storms. */
    dedupTtlSeconds: Type.Number({ default: 60 }),
  }),

  setup(app) {
    const router = new NotificationRouter({
      deliver: (delivery) => app.channels.deliver(delivery),
      flushWindowSeconds: app.extensionConfig.flushWindowSeconds,
      dedupTtlSeconds: app.extensionConfig.dedupTtlSeconds,
      timezone: app.config.scheduler.timezone,
      log: app.log,
    });

    app.events.on(NOTIFY_EVENT, (payload) => router.handle(payload));

    app.onShutdown("flush", () => router.flushNow());

    // Receiving-side guidance for the main conversation (relay/act on what arrives). Main only,
    // mirroring the tool scoping below: background runs are producers and get their notify_user
    // guidance from their own base prompt.
    app.agent.use(provideContext(NOTIFICATIONS_USAGE, "notifications-usage"), {
      sessionScopes: ["main"],
    });

    app.agent.use(
      createNotifyToolFactory((event, payload) => app.events.emit(event, payload), app.log),
      {
        // Background runs only: in the main conversation the agent replies directly,
        // so out-of-band notification is meaningless there.
        sessionScopes: ["background"],
      },
    );
  },
});
