import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for the receiving side of notifications, injected into the main session's
 * context. Scoped to main: only the main conversation relays to the person — background
 * runs are notification *producers* and get their `notify_user` guidance from their own base
 * prompt instead.
 */
export const NOTIFICATIONS_USAGE = `## Notifications

Background work — task runs, detached processes, updates, maintenance — surfaces through notifications rather than direct replies, reaching you as a turn when the person is next idle (urgent ones sooner; accumulated ones batched into one digest turn). You are the bridge to the person, who sees nothing until you relay it: pass on what matters, act where the notice asks, skip what went stale.

${referencePointer(import.meta.dirname, "notifications")}`;
