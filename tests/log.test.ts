import { mkdtemp, readdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { describe, expect, it } from "vitest";

import { createRootLogger, retainedFiles } from "../src/log.ts";

const tempDir = () => mkdtemp(join(tmpdir(), "tachi-log-"));

const rolledLogs = async (dir: string): Promise<string[]> =>
  (await readdir(dir)).filter((name) => name.endsWith(".log") && name !== "current.log");

describe("createRootLogger", () => {
  it("normalizes an uppercase level to lowercase", async () => {
    const logger = await createRootLogger({ level: "INFO", pretty: false });

    expect(logger.level).toBe("info");
  });

  it("writes a parseable JSON line to a rolled file", async () => {
    const dir = await tempDir();

    const logger = await createRootLogger({
      level: "info",
      pretty: false,
      file: { path: join(dir, "tachikoma"), frequency: "daily", retainedFiles: 7 },
    });
    logger.info({ marker: "abc" }, "hello from the file sink");
    logger.flush();
    await delay(50);

    const files = await rolledLogs(dir);
    expect(files.length).toBeGreaterThanOrEqual(1);

    const contents = await readFile(join(dir, files[0] as string), "utf8");
    const line = contents
      .trim()
      .split("\n")
      .find((l) => l.includes("hello from the file sink"));
    expect(line).toBeDefined();

    const parsed = JSON.parse(line as string);
    expect(parsed.msg).toBe("hello from the file sink");
    expect(parsed.marker).toBe("abc");
  });

  it("rotates to a new file while running on a short frequency", async () => {
    const dir = await tempDir();

    // Numeric frequency (ms) drives pino-roll's real timer — no fake timers.
    const logger = await createRootLogger({
      level: "info",
      pretty: false,
      file: { path: join(dir, "tachikoma"), frequency: 60, retainedFiles: 5 },
    });

    logger.info("before rotation");
    logger.flush();
    await delay(200);

    logger.info("after rotation");
    logger.flush();
    await delay(50);

    expect((await rolledLogs(dir)).length).toBeGreaterThanOrEqual(2);
  });

  // Guards the schema↔pino-roll contract: every config-valid frequency must build
  // a stream (pino-roll throws on unsupported strings, e.g. "weekly").
  it.each(["hourly", "daily"])("builds a file sink for the %s frequency", async (frequency) => {
    const dir = await tempDir();

    await expect(
      createRootLogger({
        level: "info",
        pretty: false,
        file: { path: join(dir, "tachikoma"), frequency, retainedFiles: 7 },
      }),
    ).resolves.toBeDefined();
  });
});

describe("retainedFiles", () => {
  it("keeps retentionDays of files at any frequency", () => {
    expect(retainedFiles(7, "daily")).toBe(7);
    expect(retainedFiles(7, "hourly")).toBe(168);
  });

  it("falls back to one-file-per-day for unmapped frequencies", () => {
    expect(retainedFiles(5, "weekly")).toBe(5);
  });

  it("never returns less than one", () => {
    expect(retainedFiles(0, "daily")).toBe(1);
  });
});
