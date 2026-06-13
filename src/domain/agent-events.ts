import type { Usage } from "@earendil-works/pi-ai";

export const ERROR_KINDS = {
  auth: "auth",
  billing: "billing",
  encoding: "encoding",
  provider: "provider",
  unknown: "unknown",
} as const;

export type ErrorKind = keyof typeof ERROR_KINDS;

/** Cost and token totals for a completed exchange, as reported by the provider. */
export interface ResultUsage {
  /** Total USD cost of the exchange's final assistant turn. */
  costUsd: number;
  usage: Usage;
}

export type AgentEvent =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool-start"; toolCallId: string; toolName: string; args: Record<string, unknown> }
  | { kind: "tool-end"; toolCallId: string; toolName: string; isError: boolean }
  | { kind: "status"; text: string }
  | {
      kind: "result";
      stopReason: "done" | "aborted" | "error";
      sessionId?: string;
      result?: ResultUsage;
    }
  | { kind: "error"; message: string; recoverable: boolean; errorKind: ErrorKind };
