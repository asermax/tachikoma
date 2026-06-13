import { mkdtemp, readFile, stat, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { describe, expect, it } from "vitest";

import { createRootLogger, rotateLogs } from "../src/log.ts";

const tempDir = () => mkdtemp(join(tmpdir(), "tachi-log-"));

describe("createRootLogger", () => {
  it("normalizes an uppercase level to lowercase", () => {
    const logger = createRootLogger({ level: "INFO", pretty: false });

    expect(logger.level).toBe("info");
  });

  it("writes a parseable JSON line to the file destination", async () => {
    const dir = await tempDir();
    const file = join(dir, "tachikoma.log");

    const logger = createRootLogger({ level: "info", pretty: false, file });
    logger.info({ marker: "abc" }, "hello from the file sink");
    logger.flush();
    await delay(50);

    const contents = await readFile(file, "utf8");
    const line = contents
      .trim()
      .split("\n")
      .find((l) => l.includes("hello from the file sink"));
    expect(line).toBeDefined();

    const parsed = JSON.parse(line as string);
    expect(parsed.msg).toBe("hello from the file sink");
    expect(parsed.marker).toBe("abc");
  });
});

describe("rotateLogs", () => {
  it("archives the current log under a timestamped name", async () => {
    const dir = await tempDir();
    await writeFile(join(dir, "tachikoma.log"), "old contents\n", "utf8");

    const now = new Date("2026-06-13T14:05:09");
    await rotateLogs(dir, 7, now);

    const archived = join(dir, "tachikoma.2026-06-13_14-05-09.log");
    await expect(readFile(archived, "utf8")).resolves.toBe("old contents\n");
    await expect(stat(join(dir, "tachikoma.log"))).rejects.toThrow();
  });

  it("prunes archives older than retentionDays while keeping recent ones", async () => {
    const dir = await tempDir();
    const now = new Date("2026-06-13T12:00:00");

    const stale = join(dir, "tachikoma.2026-06-01_00-00-00.log");
    const fresh = join(dir, "tachikoma.2026-06-12_00-00-00.log");
    await writeFile(stale, "stale\n", "utf8");
    await writeFile(fresh, "fresh\n", "utf8");

    const staleTime = new Date("2026-06-01T00:00:00");
    const freshTime = new Date("2026-06-12T00:00:00");
    await utimes(stale, staleTime, staleTime);
    await utimes(fresh, freshTime, freshTime);

    await rotateLogs(dir, 7, now);

    await expect(stat(stale)).rejects.toThrow();
    await expect(readFile(fresh, "utf8")).resolves.toBe("fresh\n");
  });

  it("is a no-op when no current log exists", async () => {
    const dir = await tempDir();

    await expect(rotateLogs(dir, 7, new Date())).resolves.toBeUndefined();
  });
});
