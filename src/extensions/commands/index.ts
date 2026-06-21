import { defineExtension } from "../api.ts";

/**
 * Channel-agnostic conversation commands.
 *
 * `/queue` is handled at submit time by the coordinator (steering happens before middleware runs).
 * `/stop` is channel-level because it must act mid-stream (Telegram's handleStop aborts the run).
 *
 * `/new` was handled here (an immediate topic collapse), but as of DLT-181 (R9) a *bare* `/new`
 * enters the coordinator's pending-input flow instead (it prompts for the new topic's first message),
 * and `/new <arg>` is prefix-stripped at submit time into `forceNew`, which the boundary extension
 * honors. Neither reaches inbound middleware, so this extension now passes every message through —
 * it remains registered as the home for future channel-agnostic commands.
 */
export default defineExtension({
  name: "commands",

  setup(app) {
    app.inbound.use(async (_message, _context, next) => next());
  },
});
