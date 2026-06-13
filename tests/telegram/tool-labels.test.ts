import { describe, expect, it } from "vitest";

import { formatToolActivity, formatToolName } from "../../src/extensions/telegram/tool-labels.ts";

describe("formatToolActivity", () => {
  it("renders friendly present-progressive labels per known tool", () => {
    expect(formatToolActivity("Read", { file_path: "/etc/hosts" })).toBe("Reading /etc/hosts");
    expect(formatToolActivity("Bash", { command: "ls -la" })).toBe("Running: ls -la");
    expect(formatToolActivity("Grep", { pattern: "TODO" })).toBe("Searching for 'TODO'");
    expect(formatToolActivity("Glob", { pattern: "**/*.ts" })).toBe("Globbing **/*.ts");
    expect(formatToolActivity("Edit", { file_path: "src/app.ts" })).toBe("Editing src/app.ts");
    expect(formatToolActivity("Write", { file_path: "out.txt" })).toBe("Writing out.txt");
    expect(formatToolActivity("LS", { path: "/tmp" })).toBe("Listing /tmp");
    expect(formatToolActivity("ToolSearch", { query: "slack" })).toBe("Searching tools: slack");
  });

  it("uses sensible placeholders when args are missing", () => {
    expect(formatToolActivity("Read", {})).toBe("Reading ...");
    expect(formatToolActivity("Bash", {})).toBe("Running: ...");
    expect(formatToolActivity("Agent", {})).toBe("Agent...");
    expect(formatToolActivity("Agent", { description: "explore the repo" })).toBe(
      "Agent: explore the repo",
    );
  });

  it("falls back to the prettified name for unknown tools", () => {
    expect(formatToolActivity("CustomThing", {})).toBe("CustomThing");
    expect(formatToolActivity("mcp__projects__list_projects", {})).toBe("List Projects");
  });
});

describe("formatToolName", () => {
  it("humanizes MCP tool names by stripping the server prefix", () => {
    expect(formatToolName("mcp__projects__list_projects")).toBe("List Projects");
    expect(formatToolName("mcp__server__tool")).toBe("Tool");
    expect(formatToolName("mcp__a__b__create_new_item")).toBe("Create New Item");
  });

  it("passes non-MCP names through unchanged", () => {
    expect(formatToolName("Read")).toBe("Read");
    expect(formatToolName("send_telegram_file")).toBe("send_telegram_file");
  });
});
