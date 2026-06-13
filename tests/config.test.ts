import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config/load.ts";
import { ConfigError } from "../src/config/parse.ts";

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

  it("rejects invalid values with a readable error", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path, '[sessions]\nresumeWindowSeconds = "soon"\n', "utf8");

    await expect(loadConfig(path)).rejects.toThrow(ConfigError);
  });
});
