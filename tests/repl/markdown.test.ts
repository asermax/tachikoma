import { describe, expect, it } from "vitest";

import { renderMarkdown } from "../../src/extensions/repl/markdown.ts";

const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const ITALIC = "\x1b[3m";
const UNDERLINE = "\x1b[4m";
const CYAN = "\x1b[36m";
const YELLOW = "\x1b[33m";

describe("renderMarkdown", () => {
  it("styles headings as bold + underline without the hashes", () => {
    expect(renderMarkdown("## Title")).toBe(`${BOLD}${UNDERLINE}Title${RESET}`);
  });

  it("renders bold spans for both ** and __", () => {
    expect(renderMarkdown("a **b** __c__")).toBe(`a ${BOLD}b${RESET} ${BOLD}c${RESET}`);
  });

  it("renders italic spans without consuming bold", () => {
    expect(renderMarkdown("*x*")).toBe(`${ITALIC}x${RESET}`);
    expect(renderMarkdown("**y**")).toBe(`${BOLD}y${RESET}`);
  });

  it("renders inline code", () => {
    expect(renderMarkdown("call `foo()` now")).toBe(`call ${YELLOW}foo()${RESET} now`);
  });

  it("renders list items with a bullet, preserving indentation", () => {
    expect(renderMarkdown("- one\n  - two")).toBe(`${CYAN}•${RESET} one\n  ${CYAN}•${RESET} two`);
  });

  it("renders ordered list items as bullets too", () => {
    expect(renderMarkdown("1. first")).toBe(`${CYAN}•${RESET} first`);
  });

  it("dims fenced code lines and drops the fence markers", () => {
    expect(renderMarkdown("```ts\nconst a = 1;\n```")).toBe(`${DIM}const a = 1;${RESET}`);
  });

  it("does not apply inline styling inside code fences", () => {
    expect(renderMarkdown("```\n**not bold**\n```")).toBe(`${DIM}**not bold**${RESET}`);
  });

  it("passes plain text through unchanged", () => {
    expect(renderMarkdown("just a sentence.")).toBe("just a sentence.");
  });
});
