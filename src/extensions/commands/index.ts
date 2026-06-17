import { defineExtension } from "../api.ts";
import { collapseCurrentTopic } from "../boundary/collapse.ts";

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

        app.log.debug({ command: "/new" }, "handling slash command");

        // Daily-trunk model: a bare "/new" forces a topic shift, collapsing the current branch into a
        // summary on the trunk so a fresh topic starts. The empty-branch guard skips collapse when the
        // live branch has no assistant turn yet.
        const trunk = context.trunk;

        if (trunk?.hasAssistantTurnSinceBase === true) {
          await collapseCurrentTopic(
            { side: app.agent.side, log: app.log },
            {
              session: trunk.session,
              currentBaseId: trunk.currentBaseId,
              branchId: trunk.liveBranchId,
              reason: "user forced a new topic",
            },
          );
        }

        app.channels.deliver({ text: "🆕 Started a fresh topic.", immediate: true });
        return;
      }

      return next();
    });
  },
});
