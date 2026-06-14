import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parse as parseToml } from "smol-toml";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_CONFIG_TEMPLATE } from "../src/config/default-template.ts";
import { defaultConfigPath, loadConfig } from "../src/config/load.ts";
import { ConfigError, parseWithSchema } from "../src/config/parse.ts";
import { ConfigSchema } from "../src/config/schema.ts";
import { resolveTimezone } from "../src/config/timezone.ts";

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

  it("re-throws read errors that are not ENOENT", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));

    await expect(loadConfig(dir)).rejects.toMatchObject({ code: "EISDIR" });
  });

  it("uses the default config path when none is provided", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    vi.stubEnv("XDG_CONFIG_HOME", dir);

    try {
      const { path, created } = await loadConfig();

      expect(created).toBe(true);
      expect(path).toBe(join(dir, "tachikoma", "config.toml"));
    } finally {
      vi.unstubAllEnvs();
    }
  });
});

describe("defaultConfigPath", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("honors XDG_CONFIG_HOME when set", () => {
    vi.stubEnv("XDG_CONFIG_HOME", "/xdg/conf");

    expect(defaultConfigPath()).toBe(join("/xdg/conf", "tachikoma", "config.toml"));
  });

  it("falls back to ~/.config when XDG_CONFIG_HOME is unset", async () => {
    vi.stubEnv("XDG_CONFIG_HOME", undefined);
    const { homedir } = await import("node:os");

    expect(defaultConfigPath()).toBe(join(homedir(), ".config", "tachikoma", "config.toml"));
  });
});

describe("parseWithSchema", () => {
  it("renders root-level validation errors with a '/' instance path", () => {
    try {
      parseWithSchema(ConfigSchema, "not-an-object", "label");
      expect.unreachable("expected a ConfigError");
    } catch (error) {
      expect(error).toBeInstanceOf(ConfigError);
      expect((error as ConfigError).message).toContain("/:");
    }
  });
});

describe("resolveTimezone", () => {
  let dateTimeFormatSpy: ReturnType<typeof vi.spyOn> | undefined;

  beforeEach(() => {
    dateTimeFormatSpy = undefined;
  });

  afterEach(() => {
    dateTimeFormatSpy?.mockRestore();
  });

  it("re-throws non-RangeError failures from Intl.DateTimeFormat", () => {
    dateTimeFormatSpy = vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new TypeError("boom");
    });

    expect(() => resolveTimezone("America/New_York", "label")).toThrow(TypeError);
  });
});
