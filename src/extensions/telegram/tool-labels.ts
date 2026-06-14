type ToolArgs = Record<string, unknown>;

/** One tool invocation observed during a tool→text segment. */
export interface ToolActivity {
  toolName: string;
  args: ToolArgs;
}

const asString = (value: unknown, fallback = "..."): string =>
  typeof value === "string" && value.length > 0 ? value : fallback;

const basename = (path: string): string => path.split("/").filter(Boolean).at(-1) ?? path;

/** Wrap a value in Telegram inline-code markdown for visual grouping. */
const code = (value: string): string => `\`${value}\``;

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

const bashSummary = (args: ToolArgs): string => {
  if (typeof args.description === "string" && args.description.length > 0) {
    const desc = args.description;
    return desc.charAt(0).toLowerCase() + desc.slice(1);
  }

  if (typeof args.command === "string" && args.command.length > 0) {
    const cmd = args.command.length > 40 ? `${args.command.slice(0, 40)}...` : args.command;
    return `running: ${code(cmd)}`;
  }

  return "running a command";
};

// Past-segment summary phrasings (lowercase verb phrases, basenames for brevity).
// Inline code groups the argument visually, so quotes are omitted around it.
const TOOL_SUMMARY: Record<string, (args: ToolArgs) => string> = {
  Read: (args) =>
    typeof args.file_path === "string"
      ? `reading ${code(basename(args.file_path))}`
      : "reading a file",
  Grep: (args) =>
    typeof args.pattern === "string"
      ? `searching for ${code(args.pattern)}`
      : "searching for a pattern",
  Glob: (args) =>
    typeof args.pattern === "string" ? `globbing ${code(args.pattern)}` : "globbing a pattern",
  Bash: bashSummary,
  Edit: (args) =>
    typeof args.file_path === "string"
      ? `editing ${code(basename(args.file_path))}`
      : "editing a file",
  Write: (args) =>
    typeof args.file_path === "string"
      ? `writing ${code(basename(args.file_path))}`
      : "writing a file",
  Agent: (args) =>
    typeof args.description === "string" ? `agent: ${args.description}` : "dispatched an agent",
  ToolSearch: () => "searching tools",
};

// Aggregated phrasing for a tool used more than twice in one segment.
const TOOL_AGGREGATE: Record<string, (count: number) => string> = {
  Read: (count) => `reading ${count} files`,
  Grep: (count) => `running ${count} searches`,
  Glob: (count) => `running ${count} glob searches`,
  Bash: (count) => `running ${count} commands`,
  Edit: (count) => `editing ${count} files`,
  Write: (count) => `writing ${count} files`,
  Agent: (count) => `dispatching ${count} agents`,
  ToolSearch: (count) => `running ${count} tool searches`,
};

const summaryPhrase = (toolName: string, args: ToolArgs): string => {
  const format = TOOL_SUMMARY[toolName];

  return format != null ? format(args) : `used ${formatToolName(toolName)}`;
};

const joinPhrases = (phrases: string[]): string => {
  if (phrases.length <= 1) return phrases.join("");
  if (phrases.length === 2) return phrases.join(" and ");

  return `${phrases.slice(0, -1).join(", ")}, and ${phrases.at(-1)}`;
};

/**
 * Collapse the tools that ran in one tool→text segment into a single
 * capitalized verb phrase. Same-typed tools aggregate past two uses; up to
 * five phrases survive, the rest fold into a trailing "more". Mirrors the
 * legacy summary so the baked-in marker reads naturally top-to-bottom.
 */
export const summarizeToolActivities = (activities: ToolActivity[]): string => {
  if (activities.length === 0) return "";

  const groups = new Map<string, ToolActivity[]>();
  for (const activity of activities) {
    const group = groups.get(activity.toolName) ?? [];
    group.push(activity);
    groups.set(activity.toolName, group);
  }

  const phrases: string[] = [];
  for (const [toolName, group] of groups) {
    if (group.length > 2) {
      phrases.push(
        TOOL_AGGREGATE[toolName]?.(group.length) ??
          `used ${formatToolName(toolName)} ${group.length} times`,
      );
    } else {
      for (const activity of group) phrases.push(summaryPhrase(toolName, activity.args));
    }
  }

  if (phrases.length > 5) {
    phrases.splice(0, phrases.length - 5);
    phrases.push("more");
  }

  const result = joinPhrases(phrases);

  return result.charAt(0).toUpperCase() + result.slice(1);
};
