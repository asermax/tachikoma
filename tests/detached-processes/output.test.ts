import { randomUUID } from "node:crypto";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  readOutputTail,
  readOutputTailMerged,
  readOutputWindow,
  readOutputWindowMerged,
} from "../../src/extensions/detached-processes/output.ts";

let dir: string;

const writeLog = async (content: string): Promise<string> => {
  const path = join(dir, `${randomUUID()}.log`);
  await writeFile(path, content, "utf-8");
  return path;
};

beforeAll(async () => {
  dir = await mkdtemp(join(tmpdir(), "output-test-"));
});

afterAll(() => {});

describe("readOutputTail", () => {
  it("returns null when the file does not exist", async () => {
    expect(await readOutputTail(join(dir, "missing.log"))).toBeNull();
  });

  it("returns an empty string for an empty file", async () => {
    expect(await readOutputTail(await writeLog(""))).toBe("");
  });

  it("reads the full content of a small file", async () => {
    expect(await readOutputTail(await writeLog("line one\nline two\n"))).toBe(
      "line one\nline two\n",
    );
  });

  it("reads only the trailing window for a large file", async () => {
    const big = `${"x".repeat(300 * 1024)}TAIL`;
    const tail = await readOutputTail(await writeLog(big));

    expect(tail?.endsWith("TAIL")).toBe(true);
    expect(tail?.length).toBe(256 * 1024);
  });
});

describe("readOutputWindow", () => {
  it("returns null when the file does not exist", async () => {
    expect(await readOutputWindow(join(dir, "missing.log"), 0, 10)).toBeNull();
  });

  it("reports an empty window with pastEnd false at offset zero for an empty file", async () => {
    expect(await readOutputWindow(await writeLog(""), 0, 10)).toEqual({
      content: "",
      totalLines: 0,
      pastEnd: false,
    });
  });

  it("reports pastEnd true for an empty file read beyond the start", async () => {
    expect(await readOutputWindow(await writeLog(""), 5, 10)).toEqual({
      content: "",
      totalLines: 0,
      pastEnd: true,
    });
  });

  it("reads a window in the middle of the file", async () => {
    const path = await writeLog("a\nb\nc\nd\ne\n");

    expect(await readOutputWindow(path, 1, 2)).toEqual({
      content: "b\nc",
      totalLines: 5,
      pastEnd: false,
    });
  });

  it("treats a trailing newline as a terminator, not an extra line", async () => {
    expect(await readOutputWindow(await writeLog("a\nb\n"), 0, 10)).toEqual({
      content: "a\nb",
      totalLines: 2,
      pastEnd: false,
    });
  });

  it("counts a missing trailing newline as a final line", async () => {
    expect(await readOutputWindow(await writeLog("a\nb"), 0, 10)).toEqual({
      content: "a\nb",
      totalLines: 2,
      pastEnd: false,
    });
  });

  it("flags pastEnd when the offset is at or beyond the last line", async () => {
    expect(await readOutputWindow(await writeLog("a\nb\nc\n"), 3, 5)).toEqual({
      content: "",
      totalLines: 3,
      pastEnd: true,
    });
  });
});

describe("readOutputTailMerged", () => {
  const missing = (name: string): string => join(dir, `${name}-${randomUUID()}.log`);

  it("returns null when all streams are missing", async () => {
    expect(
      await readOutputTailMerged([
        { label: "stdout", path: missing("out") },
        { label: "stderr", path: missing("err") },
      ]),
    ).toBeNull();
  });

  it("returns null when all streams are empty files", async () => {
    expect(
      await readOutputTailMerged([
        { label: "stdout", path: await writeLog("") },
        { label: "stderr", path: await writeLog("") },
      ]),
    ).toBeNull();
  });

  it("presents both streams as separated labeled sections, stdout first", async () => {
    expect(
      await readOutputTailMerged([
        { label: "stdout", path: await writeLog("out1\nout2\n") },
        { label: "stderr", path: await writeLog("err1\n") },
      ]),
    ).toBe("[stdout]\nout1\nout2\n\n[stderr]\nerr1");
  });

  it("omits a missing/empty stdout and shows stderr only", async () => {
    expect(
      await readOutputTailMerged([
        { label: "stdout", path: missing("out") },
        { label: "stderr", path: await writeLog("only-err\n") },
      ]),
    ).toBe("[stderr]\nonly-err");
  });

  it("omits an empty stderr and shows stdout only", async () => {
    expect(
      await readOutputTailMerged([
        { label: "stdout", path: await writeLog("only-out\n") },
        { label: "stderr", path: await writeLog("") },
      ]),
    ).toBe("[stdout]\nonly-out");
  });
});

describe("readOutputWindowMerged", () => {
  const missing = (name: string): string => join(dir, `${name}-${randomUUID()}.log`);

  it("applies the window to each stream and separates them", async () => {
    expect(
      await readOutputWindowMerged(
        [
          { label: "stdout", path: await writeLog("a\nb\nc\n") },
          { label: "stderr", path: await writeLog("x\ny\nz\n") },
        ],
        0,
        2,
      ),
    ).toEqual({
      content: "[stdout]\na\nb\n\n[stderr]\nx\ny",
      empty: false,
      pastEnd: false,
      totalLines: 3,
    });
  });

  it("omits a stream past EOF while keeping the other", async () => {
    const result = await readOutputWindowMerged(
      [
        { label: "stdout", path: await writeLog("only-line\n") },
        { label: "stderr", path: await writeLog("x\ny\nz\nw\nv\n") },
      ],
      0,
      2,
    );

    expect(result.content).toBe("[stdout]\nonly-line\n\n[stderr]\nx\ny");
    expect(result.empty).toBe(false);
    expect(result.pastEnd).toBe(false);
    expect(result.totalLines).toBe(5);
  });

  it("flags pastEnd and reports the longest log when every window is past EOF", async () => {
    expect(
      await readOutputWindowMerged(
        [
          { label: "stdout", path: await writeLog("a\nb\nc\n") },
          { label: "stderr", path: await writeLog("x\n") },
        ],
        50,
        10,
      ),
    ).toEqual({
      content: "",
      empty: false,
      pastEnd: true,
      totalLines: 3,
    });
  });

  it("reports empty when all streams are missing or empty", async () => {
    expect(
      await readOutputWindowMerged(
        [
          { label: "stdout", path: missing("out") },
          { label: "stderr", path: await writeLog("") },
        ],
        0,
        10,
      ),
    ).toEqual({
      content: "",
      empty: true,
      pastEnd: false,
      totalLines: 0,
    });
  });
});
