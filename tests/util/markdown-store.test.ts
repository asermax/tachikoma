import { mkdtemp, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Logger } from "../../src/log.ts";
import {
  fileExists,
  isBlankMarkdown,
  listMarkdown,
  MEMORY_INDEX_FILENAME,
  sweepEmptyMarkdown,
} from "../../src/util/markdown-store.ts";

const fakeLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn() } as unknown as Logger;

let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-mdstore-"));
});

afterEach(async () => {
  await rm(dir, { recursive: true, force: true });
  vi.clearAllMocks();
});

describe("fileExists", () => {
  it("reports true only for an existing path", async () => {
    const path = join(dir, "store.md");
    await writeFile(path, "content", "utf8");

    await expect(fileExists(path)).resolves.toBe(true);
    await expect(fileExists(join(dir, "absent.md"))).resolves.toBe(false);
  });
});

describe("listMarkdown", () => {
  it("lists .md files sorted, excluding the MEMORY.md index", async () => {
    await writeFile(join(dir, "beta.md"), "b", "utf8");
    await writeFile(join(dir, "alpha.md"), "a", "utf8");
    await writeFile(join(dir, MEMORY_INDEX_FILENAME), "# Index", "utf8");
    await writeFile(join(dir, "notes.txt"), "t", "utf8");

    await expect(listMarkdown(dir)).resolves.toEqual(["alpha.md", "beta.md"]);
  });

  it("reads a missing dir as empty (ENOENT-tolerant)", async () => {
    await expect(listMarkdown(join(dir, "never-created"))).resolves.toEqual([]);
  });
});

describe("isBlankMarkdown", () => {
  it("counts zero-byte and whitespace-only files as blank, content as not", async () => {
    const empty = join(dir, "empty.md");
    await writeFile(empty, "", "utf8");
    const blank = join(dir, "blank.md");
    await writeFile(blank, "  \n\n", "utf8");
    const content = join(dir, "content.md");
    await writeFile(content, "real content", "utf8");

    await expect(isBlankMarkdown(empty, await stat(empty))).resolves.toBe(true);
    await expect(isBlankMarkdown(blank, await stat(blank))).resolves.toBe(true);
    await expect(isBlankMarkdown(content, await stat(content))).resolves.toBe(false);
  });
});

describe("sweepEmptyMarkdown", () => {
  it("removes empty and whitespace-only markdown files, keeping the rest", async () => {
    await writeFile(join(dir, "emptied.md"), "", "utf8");
    await writeFile(join(dir, "blank.md"), "  \n\n", "utf8");
    await writeFile(join(dir, "keep.md"), "real content", "utf8");
    await writeFile(join(dir, "notes.txt"), "", "utf8");

    await sweepEmptyMarkdown(dir, fakeLog);

    expect((await readdir(dir)).sort()).toEqual(["keep.md", "notes.txt"]);
  });

  it("never sweeps the index, even when blank", async () => {
    await writeFile(join(dir, MEMORY_INDEX_FILENAME), "", "utf8");
    await writeFile(join(dir, "emptied.md"), "", "utf8");

    await sweepEmptyMarkdown(dir, fakeLog);

    expect(await readdir(dir)).toEqual([MEMORY_INDEX_FILENAME]);
  });

  it("preserves additional structural files passed by the caller", async () => {
    await writeFile(join(dir, "ledger.md"), "", "utf8");
    await writeFile(join(dir, "emptied.md"), "", "utf8");

    await sweepEmptyMarkdown(dir, fakeLog, ["ledger.md"]);

    expect(await readdir(dir)).toEqual(["ledger.md"]);
  });

  it("ignores missing directories", async () => {
    await expect(sweepEmptyMarkdown(join(dir, "nope"), fakeLog)).resolves.toBeUndefined();
  });
});
