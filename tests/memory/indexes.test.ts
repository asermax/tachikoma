import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ContextProviderInput } from "../../src/extensions/api.ts";
import {
  createMemoryIndexProvider,
  formatMemoryIndex,
} from "../../src/extensions/memory/indexes.ts";

const input = {} as ContextProviderInput;

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
});

describe("memory index provider", () => {
  it("returns null when the memories directory does not exist", async () => {
    const block = await createMemoryIndexProvider(workspace).provide(input);

    expect(block).toBeNull();
  });

  it("injects the layout instructions plus parsed indexes", async () => {
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

    const block = await createMemoryIndexProvider(workspace).provide(input);

    expect(block?.tag).toBe("memories");
    expect(block?.content).toContain("memories/episodic/");
    expect(block?.content).toContain("grep or read");
    expect(block?.content).toContain("## Facts Index");
    expect(block?.content).toContain("- [Work Info](./work-info.md): Job details");
    // Header-only preferences index has no entries, so its section is omitted.
    expect(block?.content).not.toContain("## Preferences Index");
  });

  it("still injects the layout instructions when no index files exist", async () => {
    await mkdir(join(workspace, "memories"), { recursive: true });

    const block = await createMemoryIndexProvider(workspace).provide(input);

    expect(block?.content).toContain("## Memory");
    expect(block?.content).not.toContain("## Facts Index");
    expect(block?.content).not.toContain("## Preferences Index");
  });
});
