type ToolArgs = Record<string, unknown>;

/** One tool invocation observed during a tool→text segment. */
export interface ToolActivity {
  toolName: string;
  args: ToolArgs;
}

/** True for a non-empty string — the shared "has a usable value" predicate. */
const nonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0;

const asString = (value: unknown, fallback = "..."): string =>
  nonEmptyString(value) ? value : fallback;

const basename = (path: string): string => path.split("/").filter(Boolean).at(-1) ?? path;

/**
 * Wrap a value in Telegram inline-code markdown for visual grouping. When the
 * content itself contains a backtick, single backticks would collide and break
 * the span, so we switch to a double-backtick span with space padding per
 * CommonMark 6.1 (mirrors the legacy `code_wrap`).
 */
const code = (value: string): string =>
  value.includes("`") ? `\`\` ${value} \`\`` : `\`${value}\``;

/** Max length for a delegation `description` shown in tool-activity labels. */
const DELEGATE_DESC_MAX = 60;

/** The delegation `description` arg, truncated for display, or `null` when empty/missing. */
const delegateDescription = (args: ToolArgs): string | null => {
  const desc = asString(args.description, "");
  if (desc === "") return null;
  return desc.length > DELEGATE_DESC_MAX ? `${desc.slice(0, DELEGATE_DESC_MAX)}...` : desc;
};

/** Shared delegate phrasing for the live label (`Delegating`) and summary (`delegating`). */
const delegateLabel = (args: ToolArgs, verb: string): string => {
  const agent = typeof args.agent === "string" ? args.agent : "an agent";
  const desc = delegateDescription(args);
  return `${verb} to ${agent}${desc != null ? `: ${desc}` : ""}`;
};

/** Truncate a value to `max` chars, appending an ellipsis when it overflows. */
const truncate = (value: string, max = 40): string =>
  value.length > max ? `${value.slice(0, max)}...` : value;

// Present-progressive live-activity labels keyed by pi's tool name. Each entry
// maps a tool's args to a friendly line shown while the tool runs. pi's built-in
// tools are lowercase (`read`, `grep`, …) and take a `path`/`pattern`/`command`
// arg shape — these keys must match what pi emits, not Claude Code's names.
const TOOL_DISPLAY: Record<string, (args: ToolArgs) => string> = {
  read: (args) => `Reading ${asString(args.path)}`,
  grep: (args) => `Searching for '${asString(args.pattern)}'`,
  find: (args) => `Finding files: ${asString(args.pattern)}`,
  bash: (args) =>
    nonEmptyString(args.description)
      ? args.description
      : `Running: ${truncate(asString(args.command))}`,
  edit: (args) => `Editing ${asString(args.path)}`,
  write: (args) => `Writing ${asString(args.path)}`,
  ls: (args) => `Listing ${asString(args.path)}`,
  delegate_to_agent: (args) => delegateLabel(args, "Delegating"),
};

/**
 * Turn a raw tool name into a human-readable label. MCP tool names follow the
 * pattern `mcp__<server>__<tool>`, so we keep only the trailing tool segment;
 * every name is then split on its underscores and title-cased. Humanizing the
 * underscores away also keeps the label safe inside Telegram's `_italic_`
 * markdown, where a raw `snake_case` name would be mis-parsed as emphasis.
 */
export const formatToolName = (name: string): string => {
  const base = name.startsWith("mcp__") ? (name.split("__").at(-1) ?? name) : name;

  return base
    .split("_")
    .filter((word) => word.length > 0)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
};

/** Friendly live-activity label for a tool invocation, args-aware. */
export const formatToolActivity = (toolName: string, args: ToolArgs): string => {
  const format = TOOL_DISPLAY[toolName];

  if (format != null) return format(args);

  return formatToolName(toolName);
};

const bashSummary = (args: ToolArgs): string => {
  if (nonEmptyString(args.description)) {
    const desc = args.description;
    return desc.charAt(0).toLowerCase() + desc.slice(1);
  }

  if (nonEmptyString(args.command)) {
    return `running: ${code(truncate(args.command))}`;
  }

  return "running a command";
};

// Past-segment summary phrasings (lowercase verb phrases, basenames for brevity).
// Inline code groups the argument visually, so quotes are omitted around it.
const TOOL_SUMMARY: Record<string, (args: ToolArgs) => string> = {
  read: (args) =>
    typeof args.path === "string" ? `reading ${code(basename(args.path))}` : "reading a file",
  grep: (args) =>
    typeof args.pattern === "string"
      ? `searching for ${code(args.pattern)}`
      : "searching for a pattern",
  find: (args) =>
    typeof args.pattern === "string" ? `finding ${code(args.pattern)}` : "finding files",
  bash: bashSummary,
  edit: (args) =>
    typeof args.path === "string" ? `editing ${code(basename(args.path))}` : "editing a file",
  write: (args) =>
    typeof args.path === "string" ? `writing ${code(basename(args.path))}` : "writing a file",
  delegate_to_agent: (args) => delegateLabel(args, "delegating"),
};

// Aggregated phrasing for a tool used more than twice in one segment.
const TOOL_AGGREGATE: Record<string, (count: number) => string> = {
  read: (count) => `reading ${count} files`,
  grep: (count) => `running ${count} searches`,
  find: (count) => `running ${count} file searches`,
  bash: (count) => `running ${count} commands`,
  edit: (count) => `editing ${count} files`,
  write: (count) => `writing ${count} files`,
  delegate_to_agent: (count) => `delegating to ${count} agents`,
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
