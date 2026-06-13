import { defineExtension } from "../api.ts";

/**
 * Channel-agnostic conversation commands handled before the agent sees them.
 * (/queue is handled at submit time by the coordinator, since steering happens
 * before middleware runs; /stop is channel-level because it must act mid-stream.)
 */
export default defineExtension({
  name: "commands",

  setup(app) {
    app.inbound.use(async (message, context, next) => {
      if (message.text.trim() === "/new") {
        message.metadata.handled = true;

        await context.closeSession();
        app.channels.deliver({ text: "🆕 Started a fresh session.", gate: "immediate" });
        return;
      }

      return next();
    });
  },
});
