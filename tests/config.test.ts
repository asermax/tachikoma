import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parse as parseToml } from "smol-toml";
import { describe, expect, it } from "vitest";

import { DEFAULT_CONFIG_TEMPLATE } from "../src/config/default-template.ts";
import { loadConfig } from "../src/config/load.ts";
import { ConfigError, parseWithSchema } from "../src/config/parse.ts";
import { ConfigSchema } from "../src/config/schema.ts";

describe("config loading", () => {
  it("generates a commented default file on first run", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");

    const { config, created } = await loadConfig(path);

    expect(created).toBe(true);
    expect(config.agent.main).toBeUndefined();
    expect(config.sessions.resumeWindowSeconds).toBe(86400);
    await expect(readFile(path, "utf8")).resolves.toContain("[workspace]");
  });

  it("applies defaults for sections missing from the file", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, '[agent]\nmain = "anthropic/claude-fable-5"\n', "utf8");

    const { config, created } = await loadConfig(path);

    expect(created).toBe(false);
    expect(config.agent.main).toBe("anthropic/claude-fable-5");
    expect(config.agent.processor).toBeUndefined();
    expect(config.channels.default).toBe("repl");
  });

  it("rejects malformed TOML with a ConfigError carrying the line and column", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, "[sessions]\nresumeWindowSeconds = \n", "utf8");

    await expect(loadConfig(path)).rejects.toThrow(ConfigError);
    await expect(loadConfig(path)).rejects.toThrow(/line \d+, column \d+/);
  });

  it("parses and validates the default template cleanly", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");

    await expect(loadConfig(path)).resolves.toMatchObject({ created: true });
  });

  it("keeps the default template in sync with the schema defaults", async () => {
    const fromTemplate = parseWithSchema(
      ConfigSchema,
      parseToml(DEFAULT_CONFIG_TEMPLATE),
      "template",
    );
    const pureDefaults = parseWithSchema(ConfigSchema, {}, "defaults");

    expect(fromTemplate).toEqual(pureDefaults);
  });

  it("rejects invalid values with a readable error", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, '[sessions]\nresumeWindowSeconds = "soon"\n', "utf8");

    await expect(loadConfig(path)).rejects.toThrow(ConfigError);
  });

  it("accepts a valid IANA scheduler timezone", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, '[scheduler]\ntimezone = "America/Argentina/Buenos_Aires"\n', "utf8");

    const { config } = await loadConfig(path);

    expect(config.scheduler.timezone).toBe("America/Argentina/Buenos_Aires");
  });

  it("rejects an invalid scheduler timezone with a clear error", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, '[scheduler]\ntimezone = "Mars/Olympus_Mons"\n', "utf8");

    await expect(loadConfig(path)).rejects.toThrow(/not a valid IANA timezone/);
  });

  it("defaults an unset scheduler timezone to the detected system timezone", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");

    const { config } = await loadConfig(path);

    expect(config.scheduler.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });
});
