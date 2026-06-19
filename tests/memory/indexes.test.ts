import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildMemoryContext, formatMemoryIndex } from "../../src/extensions/memory/indexes.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

let workspace: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-memory-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("formatMemoryIndex", () => {
  it("formats well-formed entries and skips malformed ones", () => {
    const raw = [
      "# Memory Index",
      "",
      "[Work Info](./work-info.md): Job details and team structure",
      "[Broken](./broken.md) missing the colon separator",
      "[Tech Stack](./tech-stack.md): Languages and tools",
    ].join("\n");

    const formatted = formatMemoryIndex("topics", raw);

    expect(formatted).toContain("## Topics Index");
    expect(formatted).toContain("- [Work Info](./work-info.md): Job details and team structure");
    expect(formatted).toContain("- [Tech Stack](./tech-stack.md): Languages and tools");
    expect(formatted).not.toContain("Broken");
  });

  it("returns null when no entries parse", () => {
    expect(formatMemoryIndex("topics", "# Memory Index\n")).toBeNull();
  });

  it("uses the generic description for a store without a tailored one", () => {
    const formatted = formatMemoryIndex(
      "episodic",
      "[Summary](./2026-06-10.md): A day's summary\n",
    );

    expect(formatted).toContain("## Episodic Index");
    expect(formatted).toContain("Browse the entries below. When a file seems relevant");
    expect(formatted).not.toContain("Everything known about a subject");
  });

  it("formats the learnings index with its experience-notes description, skipping malformed lines", () => {
    const raw = [
      "# Memory Index",
      "",
      "[Test Suite](./test-suite.md): Flaky tests keep biting",
      "[Broken](./broken.md) missing the colon separator",
    ].join("\n");

    const formatted = formatMemoryIndex("learnings", raw);

    expect(formatted).toContain("## Learnings Index");
    expect(formatted).toContain("- [Test Suite](./test-suite.md): Flaky tests keep biting");
    expect(formatted).not.toContain("Broken");
    // tailored experience-notes description, not the topics or generic one
    expect(formatted).toContain("Experience notes");
    expect(formatted).toContain("recurring friction");
    expect(formatted).not.toContain("Everything known about a subject");
  });

  it("returns null for a header-only learnings index", () => {
    expect(formatMemoryIndex("learnings", "# Memory Index\n")).toBeNull();
  });
});

describe("buildMemoryContext", () => {
  it("returns empty content when the memories directory does not exist", async () => {
    expect(await buildMemoryContext(workspace, fakeLog)).toBe("");
  });

  it("includes the layout instructions plus parsed topics index", async () => {
    await mkdir(join(workspace, "memories", "topics"), { recursive: true });
    await writeFile(
      join(workspace, "memories", "topics", "MEMORY.md"),
      "# Memory Index\n\n[Work Info](./work-info.md): Job details\n",
      "utf8",
    );

    const content = await buildMemoryContext(workspace, fakeLog);

    expect(content).toContain("memories/episodic/");
    expect(content).toContain("grep or read");
    // The read-only / post-processing behavioral note.
    expect(content).toContain("do not write to");
    expect(content).toContain("## Topics Index");
    expect(content).toContain("- [Work Info](./work-info.md): Job details");
    // INDEXED_STORES yields a single topics loop — no separate Facts/Preferences sections.
    expect(content).not.toContain("## Facts Index");
    expect(content).not.toContain("## Preferences Index");
  });

  it("still includes the layout instructions when no index files exist", async () => {
    await mkdir(join(workspace, "memories"), { recursive: true });

    const content = await buildMemoryContext(workspace, fakeLog);

    expect(content).toContain("## Memory");
    expect(content).not.toContain("## Topics Index");
    expect(content).not.toContain("## Facts Index");
    expect(content).not.toContain("## Preferences Index");
  });

  it("injects a Learnings Index alongside the Topics Index when both have entries", async () => {
    await mkdir(join(workspace, "memories", "topics"), { recursive: true });
    await writeFile(
      join(workspace, "memories", "topics", "MEMORY.md"),
      "# Memory Index\n\n[Work Info](./work-info.md): Job details\n",
      "utf8",
    );
    await mkdir(join(workspace, "memories", "learnings"), { recursive: true });
    await writeFile(
      join(workspace, "memories", "learnings", "MEMORY.md"),
      "# Memory Index\n\n[Test Suite](./test-suite.md): Flaky tests\n",
      "utf8",
    );

    const content = await buildMemoryContext(workspace, fakeLog);

    expect(content).toContain("## Topics Index");
    expect(content).toContain("## Learnings Index");
    expect(content).toContain("- [Test Suite](./test-suite.md): Flaky tests");
  });

  it("omits the Learnings Index when it is header-only, but keeps the layout + topics sections", async () => {
    await mkdir(join(workspace, "memories", "topics"), { recursive: true });
    await writeFile(
      join(workspace, "memories", "topics", "MEMORY.md"),
      "# Memory Index\n\n[Work Info](./work-info.md): Job details\n",
      "utf8",
    );
    await mkdir(join(workspace, "memories", "learnings"), { recursive: true });
    await writeFile(
      join(workspace, "memories", "learnings", "MEMORY.md"),
      "# Memory Index\n",
      "utf8",
    );

    const content = await buildMemoryContext(workspace, fakeLog);

    // The layout section and topics index are still injected; a header-only learnings index
    // (or a missing one) yields no Learnings Index section.
    expect(content).toContain("## Memory");
    expect(content).toContain("## Topics Index");
    expect(content).not.toContain("## Learnings Index");
  });
});
