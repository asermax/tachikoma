import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

import {
  createGitGuardrailFactory,
  findDeniedSubcommand,
  splitCompoundCommands,
} from "../../src/extensions/git/guardrail.ts";
import { fakeLogger } from "./helpers.ts";

describe("splitCompoundCommands", () => {
  it("splits on &&, ||, |, and ;", () => {
    expect(splitCompoundCommands("git status && git push || echo done; ls | wc")).toEqual([
      "git status",
      "git push",
      "echo done",
      "ls",
      "wc",
    ]);
  });

  it("does not split on operators inside quotes", () => {
    expect(splitCompoundCommands('grep -E "a|b;c" file')).toEqual(['grep -E "a|b;c" file']);
    expect(splitCompoundCommands("echo 'a && b'")).toEqual(["echo 'a && b'"]);
  });

  it("does not split on escaped operators", () => {
    expect(splitCompoundCommands("echo a\\;b")).toEqual(["echo a\\;b"]);
  });

  it("trims whitespace and drops empty parts", () => {
    expect(splitCompoundCommands("  git status ;; git log  ")).toEqual(["git status", "git log"]);
  });
});

describe("findDeniedSubcommand", () => {
  const denied = [
    "git push origin main",
    "git push --force",
    "git reset --hard HEAD",
    "git checkout .",
    "git restore .",
    "git clean -fd",
    "git remote add origin url",
    "git remote set-url origin url",
    "git filter-repo --invert-paths --path a",
    "git rebase main",
  ];

  it.each(denied)("denies %s", (command) => {
    expect(findDeniedSubcommand(command)).not.toBeNull();
  });

  const allowed = [
    "git status",
    "git log --oneline",
    "git diff",
    "git add -A",
    "git commit -m wip",
    "git fetch origin",
    "git clone https://example.com/repo.git",
    "git remote -v",
    "git checkout main",
    "git restore --staged file.txt",
    "ls -la",
    "echo hello",
  ];

  it.each(allowed)("allows %s", (command) => {
    expect(findDeniedSubcommand(command)).toBeNull();
  });

  it("denies when a destructive sub-command hides in a compound command", () => {
    const match = findDeniedSubcommand("git status && git push --force");

    expect(match?.subcommand).toBe("git push --force");
  });

  it("does not flag a destructive operator hidden in a quoted argument", () => {
    expect(findDeniedSubcommand('git commit -m "reset and push later"')).toBeNull();
  });
});

describe("createGitGuardrailFactory", () => {
  const register = () => {
    let handler:
      | ((event: {
          type: string;
          toolName: string;
          input: Record<string, unknown>;
        }) => { block?: boolean; reason?: string } | undefined)
      | undefined;

    const pi = {
      on: (_event: string, fn: typeof handler) => {
        handler = fn;
      },
    };

    createGitGuardrailFactory(fakeLogger())(pi as unknown as Parameters<ExtensionFactory>[0]);

    return (input: Record<string, unknown>, toolName = "bash") =>
      handler?.({ type: "tool_call", toolName, input });
  };

  it("blocks a destructive git bash command with a steering reason", () => {
    const fire = register();

    const result = fire({ command: "git push --force origin main" });

    expect(result?.block).toBe(true);
    expect(result?.reason).toContain("scrub");
    expect(result?.reason).toContain("commit_workspace");
  });

  it("passes through a non-destructive bash command", () => {
    const fire = register();

    expect(fire({ command: "git status" })).toBeUndefined();
  });

  it("ignores non-bash tool calls", () => {
    const fire = register();

    expect(fire({ file_path: "/x", content: "git push" }, "write")).toBeUndefined();
  });
});
