import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("smol-toml", async () => {
  const actual = await vi.importActual<typeof import("smol-toml")>("smol-toml");

  return {
    ...actual,
    parse: vi.fn(() => {
      throw new Error("non-toml failure");
    }),
  };
});

describe("loadConfig with a non-TomlError parser failure", () => {
  afterEach(() => {
    vi.resetModules();
  });

  it("re-throws errors that are not TomlError instances", async () => {
    const { loadConfig } = await import("../src/config/load.ts");

    const dir = await mkdtemp(join(tmpdir(), "tachi-config-"));
    const path = join(dir, "config.toml");
    await writeFile(path, "[agent]\n", "utf8");

    await expect(loadConfig(path)).rejects.toThrow("non-toml failure");
  });
});
