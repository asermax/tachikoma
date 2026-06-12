import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { detectBoundary } from "./detector.ts";
import { createSummaryProcessor } from "./summary.ts";

interface BoundaryConfig {
  enabled: boolean;
}

/**
 * Conversation boundary detection: keeps a rolling per-session summary after every
 * exchange, and classifies each incoming message as continuing the active session,
 * starting a fresh one, or resuming a recently closed one.
 */
export default defineExtension<BoundaryConfig>({
  name: "boundary",

  configSchema: Type.Object({
    enabled: Type.Boolean({ default: true }),
  }),

  setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.info("boundary detection disabled by configuration");
      return;
    }

    app.sessions.onExchange(createSummaryProcessor(app.agent.side, app.sessions, app.log));

    app.inbound.use(async (message, context, next) => {
      const active = context.session;
      const candidates = app.sessions
        .listResumable()
        .filter((session) => session.summary != null && session.id !== active?.id)
        .map((session) => ({ id: session.id, summary: session.summary as string }));

      // Nothing to compare against: first-ever message, or an active session that
      // has not produced a summary yet and no resumable history.
      if ((active == null || active.summary == null) && candidates.length === 0) {
        return next();
      }

      app.status("Checking conversation topic…");

      const decision = await detectBoundary(
        app.agent.side,
        {
          message: message.text,
          activeSummary: active?.summary ?? null,
          lastExchange: active?.lastExchange ?? null,
          candidates,
        },
        app.log,
      );

      if (decision.decision === "new" && active != null) {
        app.status("Topic shift — closing the previous session");
        await context.closeSession();
      } else if (decision.decision === "resume" && decision.resumeSessionId != null) {
        const target = app.sessions.get(decision.resumeSessionId);

        if (target != null) {
          app.status("Resuming a previous conversation");
          await context.resumeSession(target);
        }
      }

      return next();
    });
  },
});
