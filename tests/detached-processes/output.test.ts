import { randomUUID } from "node:crypto";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  readOutputTail,
  readOutputWindow,
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
