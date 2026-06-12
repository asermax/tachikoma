import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ensureMemoryLayout, sweepEmptyMarkdown } from "../../src/extensions/memory/layout.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

let workspace: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-memory-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
});

describe("ensureMemoryLayout", () => {
  it("creates the store directories and header-only indexes", async () => {
    await ensureMemoryLayout(workspace, fakeLog);

    expect((await readdir(join(workspace, "memories"))).sort()).toEqual([
      "episodic",
      "facts",
      "preferences",
      "transcripts",
    ]);
    expect(await readFile(join(workspace, "memories", "facts", "MEMORY.md"), "utf8")).toBe(
      "# Memory Index\n",
    );
  });

  it("seeds placeholder entries for pre-existing files", async () => {
    await mkdir(join(workspace, "memories", "facts"), { recursive: true });
    await writeFile(join(workspace, "memories", "facts", "work-info.md"), "stuff", "utf8");

    await ensureMemoryLayout(workspace, fakeLog);

    expect(await readFile(join(workspace, "memories", "facts", "MEMORY.md"), "utf8")).toBe(
      "# Memory Index\n\n[Work Info](./work-info.md): Description pending update\n",
    );
  });

  it("leaves an existing index untouched", async () => {
    await mkdir(join(workspace, "memories", "facts"), { recursive: true });
    await writeFile(
      join(workspace, "memories", "facts", "MEMORY.md"),
      "# Memory Index\n\n[Custom](./custom.md): Hand-written entry\n",
      "utf8",
    );

    await ensureMemoryLayout(workspace, fakeLog);

    expect(await readFile(join(workspace, "memories", "facts", "MEMORY.md"), "utf8")).toContain(
      "Hand-written entry",
    );
  });
});

describe("sweepEmptyMarkdown", () => {
  it("removes empty and whitespace-only markdown files, keeping the rest", async () => {
    const dir = join(workspace, "memories", "facts");
    await mkdir(dir, { recursive: true });
    await writeFile(join(dir, "emptied.md"), "", "utf8");
    await writeFile(join(dir, "blank.md"), "  \n\n", "utf8");
    await writeFile(join(dir, "keep.md"), "real content", "utf8");
    await writeFile(join(dir, "notes.txt"), "", "utf8");

    await sweepEmptyMarkdown(dir, fakeLog);

    expect((await readdir(dir)).sort()).toEqual(["keep.md", "notes.txt"]);
  });

  it("ignores missing directories", async () => {
    await expect(sweepEmptyMarkdown(join(workspace, "nope"), fakeLog)).resolves.toBeUndefined();
  });
});
