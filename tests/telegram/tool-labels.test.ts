import { describe, expect, it } from "vitest";

import {
  formatToolActivity,
  formatToolName,
  summarizeToolActivities,
} from "../../src/extensions/telegram/tool-labels.ts";

describe("formatToolActivity", () => {
  it("renders friendly present-progressive labels per known tool", () => {
    expect(formatToolActivity("read", { path: "/etc/hosts" })).toBe("Reading /etc/hosts");
    expect(formatToolActivity("bash", { command: "ls -la" })).toBe("Running: ls -la");
    expect(formatToolActivity("grep", { pattern: "TODO" })).toBe("Searching for 'TODO'");
    expect(formatToolActivity("find", { pattern: "**/*.ts" })).toBe("Finding files: **/*.ts");
    expect(formatToolActivity("edit", { path: "src/app.ts" })).toBe("Editing src/app.ts");
    expect(formatToolActivity("write", { path: "out.txt" })).toBe("Writing out.txt");
    expect(formatToolActivity("ls", { path: "/tmp" })).toBe("Listing /tmp");
    expect(formatToolActivity("delegate_to_agent", { agent: "explorer" })).toBe(
      "Delegating to explorer",
    );
  });

  it("uses sensible placeholders when args are missing", () => {
    expect(formatToolActivity("read", {})).toBe("Reading ...");
    expect(formatToolActivity("bash", {})).toBe("Running: ...");
    expect(formatToolActivity("delegate_to_agent", {})).toBe("Delegating to an agent");
  });

  it("prefers the Bash description over the command in the live label", () => {
    expect(
      formatToolActivity("bash", { description: "Run the test suite", command: "npm test" }),
    ).toBe("Run the test suite");
    // Missing description still falls back to the command, as before.
    expect(formatToolActivity("bash", { command: "ls -la" })).toBe("Running: ls -la");
  });

  it("appends a truncated description to the delegate live label", () => {
    expect(
      formatToolActivity("delegate_to_agent", {
        agent: "general-purpose",
        description: "find refs",
      }),
    ).toBe("Delegating to general-purpose: find refs");

    const long = "x".repeat(80);
    expect(
      formatToolActivity("delegate_to_agent", { agent: "general-purpose", description: long }),
    ).toBe(`Delegating to general-purpose: ${"x".repeat(60)}...`);

    // An empty description falls back to the agent-only label.
    expect(
      formatToolActivity("delegate_to_agent", { agent: "general-purpose", description: "" }),
    ).toBe("Delegating to general-purpose");

    // A missing agent still surfaces the description.
    expect(formatToolActivity("delegate_to_agent", { description: "explore" })).toBe(
      "Delegating to an agent: explore",
    );
  });

  it("truncates a long Bash command in the live label", () => {
    expect(formatToolActivity("bash", { command: "a".repeat(50) })).toBe(
      `Running: ${"a".repeat(40)}...`,
    );
  });

  it("shows a long Bash description verbatim in the live label (no truncation)", () => {
    const long = "a".repeat(50);
    expect(formatToolActivity("bash", { description: long, command: "ls" })).toBe(long);
  });

  it("falls back to the humanized name for unknown tools", () => {
    expect(formatToolActivity("run_task_now", {})).toBe("Run Task Now");
    expect(formatToolActivity("mcp__projects__list_projects", {})).toBe("List Projects");
  });
});

describe("formatToolName", () => {
  it("humanizes MCP tool names by stripping the server prefix", () => {
    expect(formatToolName("mcp__projects__list_projects")).toBe("List Projects");
    expect(formatToolName("mcp__server__tool")).toBe("Tool");
    expect(formatToolName("mcp__a__b__create_new_item")).toBe("Create New Item");
  });

  it("humanizes snake_case tool names", () => {
    expect(formatToolName("run_task_now")).toBe("Run Task Now");
    expect(formatToolName("send_telegram_file")).toBe("Send Telegram File");
    expect(formatToolName("compact")).toBe("Compact");
  });
});

describe("summarizeToolActivities", () => {
  it("returns an empty string for no activities", () => {
    expect(summarizeToolActivities([])).toBe("");
  });

  it("summarizes a single tool with its argument, capitalized", () => {
    expect(summarizeToolActivities([{ toolName: "read", args: { path: "/a/b/file.ts" } }])).toBe(
      "Reading `file.ts`",
    );
  });

  it("falls back to generic phrasings when known-tool args are missing", () => {
    expect(summarizeToolActivities([{ toolName: "read", args: {} }])).toBe("Reading a file");
    expect(summarizeToolActivities([{ toolName: "grep", args: {} }])).toBe(
      "Searching for a pattern",
    );
    expect(summarizeToolActivities([{ toolName: "find", args: {} }])).toBe("Finding files");
    expect(summarizeToolActivities([{ toolName: "edit", args: {} }])).toBe("Editing a file");
    expect(summarizeToolActivities([{ toolName: "write", args: {} }])).toBe("Writing a file");
    expect(summarizeToolActivities([{ toolName: "delegate_to_agent", args: {} }])).toBe(
      "Delegating to an agent",
    );
  });

  it("renders the argument-aware summaries for known tools", () => {
    expect(summarizeToolActivities([{ toolName: "grep", args: { pattern: "TODO" } }])).toBe(
      "Searching for `TODO`",
    );
    expect(summarizeToolActivities([{ toolName: "find", args: { pattern: "**/*.ts" } }])).toBe(
      "Finding `**/*.ts`",
    );
    expect(summarizeToolActivities([{ toolName: "edit", args: { path: "/x/y.ts" } }])).toBe(
      "Editing `y.ts`",
    );
    expect(summarizeToolActivities([{ toolName: "write", args: { path: "/x/z.ts" } }])).toBe(
      "Writing `z.ts`",
    );
    expect(
      summarizeToolActivities([{ toolName: "delegate_to_agent", args: { agent: "explorer" } }]),
    ).toBe("Delegating to explorer");
  });

  it("appends a truncated description to the delegate summary", () => {
    expect(
      summarizeToolActivities([
        {
          toolName: "delegate_to_agent",
          args: { agent: "general-purpose", description: "find refs" },
        },
      ]),
    ).toBe("Delegating to general-purpose: find refs");

    const long = "x".repeat(80);
    expect(
      summarizeToolActivities([
        { toolName: "delegate_to_agent", args: { agent: "general-purpose", description: long } },
      ]),
    ).toBe(`Delegating to general-purpose: ${"x".repeat(60)}...`);

    // An empty description falls back to the agent-only summary.
    expect(
      summarizeToolActivities([
        { toolName: "delegate_to_agent", args: { agent: "general-purpose", description: "" } },
      ]),
    ).toBe("Delegating to general-purpose");
  });

  it("prefers a Bash description over the command and lowercases its first letter", () => {
    expect(
      summarizeToolActivities([{ toolName: "bash", args: { description: "Run the tests" } }]),
    ).toBe("Run the tests");
  });

  it("shows a long Bash description verbatim in the baked summary (no truncation)", () => {
    // bashSummary lowercases the first letter; the outer pass recapitalizes it.
    expect(
      summarizeToolActivities([{ toolName: "bash", args: { description: "x".repeat(50) } }]),
    ).toBe(`X${"x".repeat(49)}`);
  });

  it("uses the Bash command when no description is present, truncating long ones", () => {
    expect(summarizeToolActivities([{ toolName: "bash", args: { command: "ls -la" } }])).toBe(
      "Running: `ls -la`",
    );

    const long = "a".repeat(50);
    expect(summarizeToolActivities([{ toolName: "bash", args: { command: long } }])).toBe(
      `Running: \`${"a".repeat(40)}...\``,
    );
  });

  it("truncates a Bash command at the 40-char boundary in the baked summary", () => {
    // Exactly 40 chars renders verbatim; 41 truncates to 40 + "...".
    expect(summarizeToolActivities([{ toolName: "bash", args: { command: "a".repeat(40) } }])).toBe(
      `Running: \`${"a".repeat(40)}\``,
    );
    expect(summarizeToolActivities([{ toolName: "bash", args: { command: "a".repeat(41) } }])).toBe(
      `Running: \`${"a".repeat(40)}...\``,
    );
  });

  it("falls back to a generic Bash phrase when neither description nor command is set", () => {
    expect(summarizeToolActivities([{ toolName: "bash", args: {} }])).toBe("Running a command");
  });

  it("escapes backticks in the inline-code wrapper (CommonMark 6.1 double-backtick)", () => {
    // Bash command containing a backtick — single backticks would collide.
    expect(
      summarizeToolActivities([{ toolName: "bash", args: { command: "echo `whoami`" } }]),
    ).toBe("Running: `` echo `whoami` ``");
    // The same handling applies to other inline-code summaries (e.g. a pattern).
    expect(summarizeToolActivities([{ toolName: "grep", args: { pattern: "a`b" } }])).toBe(
      "Searching for `` a`b ``",
    );
  });

  it("escapes backticks in a truncated long command (truncation precedes the code() wrap)", () => {
    // 54-char command with a backtick inside the first 40 chars.
    const cmd = `echo \`whoami\` ${"a".repeat(40)}`;
    expect(summarizeToolActivities([{ toolName: "bash", args: { command: cmd } }])).toBe(
      `Running: \`\` echo \`whoami\` ${"a".repeat(26)}... \`\``,
    );
  });

  it("summarizes unknown tools by their humanized name", () => {
    expect(summarizeToolActivities([{ toolName: "run_task_now", args: {} }])).toBe(
      "Used Run Task Now",
    );
    expect(summarizeToolActivities([{ toolName: "mcp__projects__list_projects", args: {} }])).toBe(
      "Used List Projects",
    );
  });

  it("aggregates a known tool used more than twice", () => {
    expect(
      summarizeToolActivities([
        { toolName: "read", args: { path: "/a.ts" } },
        { toolName: "read", args: { path: "/b.ts" } },
        { toolName: "read", args: { path: "/c.ts" } },
      ]),
    ).toBe("Reading 3 files");
  });

  it("uses the per-tool aggregate phrasing for each known tool", () => {
    const triple = (toolName: string) => [
      { toolName, args: {} },
      { toolName, args: {} },
      { toolName, args: {} },
    ];

    expect(summarizeToolActivities(triple("grep"))).toBe("Running 3 searches");
    expect(summarizeToolActivities(triple("find"))).toBe("Running 3 file searches");
    expect(summarizeToolActivities(triple("bash"))).toBe("Running 3 commands");
    expect(summarizeToolActivities(triple("edit"))).toBe("Editing 3 files");
    expect(summarizeToolActivities(triple("write"))).toBe("Writing 3 files");
    expect(summarizeToolActivities(triple("delegate_to_agent"))).toBe("Delegating to 3 agents");
  });

  it("aggregates an unknown tool used more than twice with a count phrase", () => {
    expect(
      summarizeToolActivities([
        { toolName: "run_task_now", args: {} },
        { toolName: "run_task_now", args: {} },
        { toolName: "run_task_now", args: {} },
      ]),
    ).toBe("Used Run Task Now 3 times");
  });

  it("joins two phrases with 'and'", () => {
    expect(
      summarizeToolActivities([
        { toolName: "read", args: { path: "/a.ts" } },
        { toolName: "bash", args: { command: "ls" } },
      ]),
    ).toBe("Reading `a.ts` and running: `ls`");
  });

  it("joins three-plus phrases with commas and a trailing 'and'", () => {
    expect(
      summarizeToolActivities([
        { toolName: "read", args: { path: "/a.ts" } },
        { toolName: "edit", args: { path: "/b.ts" } },
        { toolName: "write", args: { path: "/c.ts" } },
      ]),
    ).toBe("Reading `a.ts`, editing `b.ts`, and writing `c.ts`");
  });

  it("keeps only the last five phrases and folds the rest into 'more'", () => {
    expect(
      summarizeToolActivities([
        { toolName: "read", args: { path: "/a.ts" } },
        { toolName: "edit", args: { path: "/b.ts" } },
        { toolName: "write", args: { path: "/c.ts" } },
        { toolName: "grep", args: { pattern: "x" } },
        { toolName: "find", args: { pattern: "y" } },
        { toolName: "bash", args: { command: "ls" } },
      ]),
    ).toBe(
      "Editing `b.ts`, writing `c.ts`, searching for `x`, finding `y`, running: `ls`, and more",
    );
  });
});
