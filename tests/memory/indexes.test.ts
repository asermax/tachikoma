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

    const formatted = formatMemoryIndex("facts", raw);

    expect(formatted).toContain("## Facts Index");
    expect(formatted).toContain("- [Work Info](./work-info.md): Job details and team structure");
    expect(formatted).toContain("- [Tech Stack](./tech-stack.md): Languages and tools");
    expect(formatted).not.toContain("Broken");
  });

  it("returns null when no entries parse", () => {
    expect(formatMemoryIndex("facts", "# Memory Index\n")).toBeNull();
  });

  it("uses the generic description for a store without a tailored one", () => {
    const formatted = formatMemoryIndex(
      "episodic",
      "[Summary](./2026-06-10.md): A day's summary\n",
    );

    expect(formatted).toContain("## Episodic Index");
    expect(formatted).toContain("Browse the entries below. When a file seems relevant");
    expect(formatted).not.toContain("Stable reference information");
  });
});

describe("buildMemoryContext", () => {
  it("returns empty content when the memories directory does not exist", async () => {
    expect(await buildMemoryContext(workspace, fakeLog)).toBe("");
  });

  it("includes the layout instructions plus parsed indexes", async () => {
    await mkdir(join(workspace, "memories", "facts"), { recursive: true });
    await mkdir(join(workspace, "memories", "preferences"), { recursive: true });
    await writeFile(
      join(workspace, "memories", "facts", "MEMORY.md"),
      "# Memory Index\n\n[Work Info](./work-info.md): Job details\n",
      "utf8",
    );
    await writeFile(
      join(workspace, "memories", "preferences", "MEMORY.md"),
      "# Memory Index\n",
      "utf8",
    );

    const content = await buildMemoryContext(workspace, fakeLog);

    expect(content).toContain("memories/episodic/");
    expect(content).toContain("grep or read");
    // The read-only / post-processing behavioral note.
    expect(content).toContain("do not write to");
    expect(content).toContain("## Facts Index");
    expect(content).toContain("- [Work Info](./work-info.md): Job details");
    // Header-only preferences index has no entries, so its section is omitted.
    expect(content).not.toContain("## Preferences Index");
  });

  it("still includes the layout instructions when no index files exist", async () => {
    await mkdir(join(workspace, "memories"), { recursive: true });

    const content = await buildMemoryContext(workspace, fakeLog);

    expect(content).toContain("## Memory");
    expect(content).not.toContain("## Facts Index");
    expect(content).not.toContain("## Preferences Index");
  });
});
