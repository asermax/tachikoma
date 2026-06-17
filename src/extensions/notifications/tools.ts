import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { Logger } from "../../log.ts";
import { NOTIFY_EVENT, type NotifyPayload, SEVERITIES } from "./payload.ts";

export type NotifyEmitter = (event: string, payload: NotifyPayload) => void;

export const NotifyUserParams = Type.Object({
  text: Type.String({ description: "The notification message to deliver to the user" }),
  title: Type.Optional(Type.String({ description: "Optional short headline" })),
  severity: Type.Optional(
    StringEnum(["info", "warning", "urgent"] as const, {
      description:
        "Sets delivery priority: 'urgent' jumps the queue with the shortest wait, 'warning' is mid, 'info' (default) is lowest. All wait for a conversation pause and may be batched into a digest",
    }),
  ),
});

export const handleNotifyUser = (
  emit: NotifyEmitter,
  args: Static<typeof NotifyUserParams>,
  log: Logger,
): string => {
  const severity = args.severity ?? SEVERITIES.info;

  log.info({ severity, hasTitle: args.title != null }, "notify_user tool invoked");

  if (args.text.trim() === "") {
    log.warn({ severity }, "notify_user rejected — empty notification text");
    throw new Error("Notification text cannot be empty.");
  }

  emit(NOTIFY_EVENT, {
    title: args.title,
    text: args.text,
    severity,
    source: "agent",
  });

  return "Notification sent.";
};

/** pi extension factory exposing the notify_user tool to the agent. */
export const createNotifyToolFactory =
  (emit: NotifyEmitter, log: Logger): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "notify_user",
      label: "Notify User",
      description:
        "Send a notification to the user outside the normal reply flow. Notifications wait for a conversation pause and may be combined into a digest; urgent ones jump the queue with the shortest wait.",
      promptSnippet: "Notify the user proactively (use sparingly; urgent severity leads the queue)",
      promptGuidelines: [
        "Use notify_user only for information the user should see outside your direct reply; reserve severity 'urgent' for genuinely time-sensitive matters.",
      ],
      parameters: NotifyUserParams,
      async execute(_toolCallId, params) {
        return {
          content: [{ type: "text", text: handleNotifyUser(emit, params, log) }],
          details: undefined,
        };
      },
    });
  };
