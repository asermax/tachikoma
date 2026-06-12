export type AgentEvent =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool-start"; toolCallId: string; toolName: string; args: Record<string, unknown> }
  | { kind: "tool-end"; toolCallId: string; toolName: string; isError: boolean }
  | { kind: "status"; text: string }
  | { kind: "result"; stopReason: "done" | "aborted" | "error" }
  | { kind: "error"; message: string };
