type ToolArgs = Record<string, unknown>;

const asString = (value: unknown, fallback = "..."): string =>
  typeof value === "string" && value.length > 0 ? value : fallback;

// Present-progressive live-activity labels keyed by tool name. Each entry maps
// a tool's args to a friendly line shown while the tool runs.
const TOOL_DISPLAY: Record<string, (args: ToolArgs) => string> = {
  Read: (args) => `Reading ${asString(args.file_path)}`,
  Grep: (args) => `Searching for '${asString(args.pattern)}'`,
  Glob: (args) => `Globbing ${asString(args.pattern)}`,
  Bash: (args) => `Running: ${asString(args.command)}`,
  Edit: (args) => `Editing ${asString(args.file_path)}`,
  Write: (args) => `Writing ${asString(args.file_path)}`,
  LS: (args) => `Listing ${asString(args.path)}`,
  Agent: (args) =>
    typeof args.description === "string" ? `Agent: ${args.description}` : "Agent...",
  ToolSearch: (args) => `Searching tools: ${asString(args.query)}`,
};

/**
 * Turn a raw tool name into a human-readable label. MCP tool names follow the
 * pattern `mcp__<server>__<tool>`; we keep only the trailing tool segment,
 * humanize the underscores, and title-case it. Non-MCP names pass through.
 */
export const formatToolName = (name: string): string => {
  if (!name.startsWith("mcp__")) return name;

  const lastSegment = name.split("__").at(-1) ?? name;

  return lastSegment
    .split("_")
    .filter((word) => word.length > 0)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
};

/** Friendly live-activity label for a tool invocation, args-aware. */
export const formatToolActivity = (toolName: string, args: ToolArgs): string => {
  const format = TOOL_DISPLAY[toolName];

  if (format != null) return format(args);

  return formatToolName(toolName);
};
