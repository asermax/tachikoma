import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { beforeEach, describe, expect, it, vi } from "vitest";

import { InstallManager, isGitSource } from "../../src/extensions/plugins/installs.ts";
import {
  handleInstallPlugin,
  handleListInstalledPlugins,
  handleUninstallPlugin,
  handleUpdatePlugin,
} from "../../src/extensions/plugins/tools.ts";
import type { Logger } from "../../src/log.ts";

const execFileAsync = promisify(execFile);

const fakeLog = {
  error: vi.fn(),
  warn: vi.fn(),
  info: vi.fn(),
  debug: vi.fn(),
} as unknown as Logger;

const VALID_MODULE = `export default {
  name: "demo-plugin",
  setup() {},
};
`;

const createFakeState = () => {
  const store = new Map<string, unknown>();

  return {
    get: <T>(key: string): T | null => (store.has(key) ? (store.get(key) as T) : null),
    set: <T>(key: string, value: T): void => {
      store.set(key, value);
    },
  };
};

const gitCommitAll = async (repo: string, message: string): Promise<void> => {
  await execFileAsync("git", ["add", "."], { cwd: repo });
  await execFileAsync(
    "git",
    [
      "-c",
      "user.email=test@test",
      "-c",
      "user.name=test",
      "-c",
      "commit.gpgsign=false",
      "commit",
      "-m",
      message,
    ],
    { cwd: repo },
  );
};

const createPluginRepo = async (): Promise<string> => {
  const repo = await mkdtemp(join(tmpdir(), "tachi-plugin-repo-"));

  await writeFile(join(repo, "index.ts"), VALID_MODULE);
  await execFileAsync("git", ["init"], { cwd: repo });
  await gitCommitAll(repo, "init");

  return repo;
};

let pluginsDir: string;
let manager: InstallManager;

beforeEach(async () => {
  pluginsDir = await mkdtemp(join(tmpdir(), "tachi-plugins-dir-"));
  manager = new InstallManager({
    state: createFakeState(),
    pluginsDir,
    log: fakeLog,
    now: () => new Date("2026-06-12T10:00:00Z"),
  });
});

describe("isGitSource", () => {
  it("recognizes git URLs and treats plain paths as local", () => {
    expect(isGitSource("https://github.com/owner/repo.git")).toBe(true);
    expect(isGitSource("git@github.com:owner/repo.git")).toBe(true);
    expect(isGitSource("file:///tmp/repo")).toBe(true);
    expect(isGitSource("/tmp/repo.git")).toBe(true);
    expect(isGitSource("/tmp/local-plugin")).toBe(false);
  });
});

describe("install_plugin", () => {
  it("clones a git source into the plugins dir and records the install", async () => {
    const repo = await createPluginRepo();

    const message = await handleInstallPlugin(manager, {
      source: pathToFileURL(repo).href,
      alias: "demo",
    });

    expect(message).toContain("Plugin 'demo' installed successfully.");
    expect(message).toContain("restart");
    expect(existsSync(join(pluginsDir, "demo", "index.ts"))).toBe(true);
    expect(manager.list()).toEqual({
      demo: {
        source: pathToFileURL(repo).href,
        installedAt: "2026-06-12T10:00:00.000Z",
        path: join(pluginsDir, "demo"),
      },
    });
  });

  it("records a local path install in place without cloning", async () => {
    const local = await mkdtemp(join(tmpdir(), "tachi-plugin-local-"));
    await writeFile(join(local, "index.ts"), VALID_MODULE);

    await handleInstallPlugin(manager, { source: local, alias: "local-demo" });

    expect(existsSync(join(pluginsDir, "local-demo"))).toBe(false);
    expect(manager.list()["local-demo"]).toMatchObject({ source: local, path: local });
  });

  it("rejects invalid aliases and alias collisions", async () => {
    const local = await mkdtemp(join(tmpdir(), "tachi-plugin-local-"));
    await writeFile(join(local, "index.ts"), VALID_MODULE);

    await expect(
      handleInstallPlugin(manager, { source: local, alias: "Bad Alias" }),
    ).rejects.toThrow(/Invalid alias/);

    await handleInstallPlugin(manager, { source: local, alias: "demo" });
    await expect(handleInstallPlugin(manager, { source: local, alias: "demo" })).rejects.toThrow(
      /already installed/,
    );
  });

  it("cleans up the clone when the repo holds no valid extension module", async () => {
    const repo = await mkdtemp(join(tmpdir(), "tachi-plugin-repo-"));
    await writeFile(join(repo, "readme.md"), "not a plugin");
    await execFileAsync("git", ["init"], { cwd: repo });
    await gitCommitAll(repo, "init");

    await expect(
      handleInstallPlugin(manager, { source: pathToFileURL(repo).href, alias: "junk" }),
    ).rejects.toThrow(/does not contain a valid Tachikoma extension/);

    expect(existsSync(join(pluginsDir, "junk"))).toBe(false);
    expect(manager.list()).toEqual({});
  });
});

describe("update_plugin", () => {
  it("pulls new commits for a git install", async () => {
    const repo = await createPluginRepo();
    await handleInstallPlugin(manager, { source: pathToFileURL(repo).href, alias: "demo" });

    const updated = VALID_MODULE.replace("demo-plugin", "demo-plugin-v2");
    await writeFile(join(repo, "index.ts"), updated);
    await gitCommitAll(repo, "update");

    const message = await handleUpdatePlugin(manager, { alias: "demo" });

    expect(message).toContain("Plugin 'demo' updated.");
    expect(message).toContain("restart");
    expect(await readFile(join(pluginsDir, "demo", "index.ts"), "utf8")).toBe(updated);
  });

  it("skips local installs as always current", async () => {
    const local = await mkdtemp(join(tmpdir(), "tachi-plugin-local-"));
    await writeFile(join(local, "index.ts"), VALID_MODULE);
    await handleInstallPlugin(manager, { source: local, alias: "local-demo" });

    const message = await handleUpdatePlugin(manager, { alias: "local-demo" });

    expect(message).toContain("always current");
  });

  it("rejects unknown aliases", async () => {
    await expect(handleUpdatePlugin(manager, { alias: "ghost" })).rejects.toThrow(/not installed/);
  });
});

describe("uninstall_plugin", () => {
  it("removes the record and the cloned directory for git installs", async () => {
    const repo = await createPluginRepo();
    await handleInstallPlugin(manager, { source: pathToFileURL(repo).href, alias: "demo" });

    const message = await handleUninstallPlugin(manager, { alias: "demo" });

    expect(message).toContain("Plugin 'demo' uninstalled.");
    expect(existsSync(join(pluginsDir, "demo"))).toBe(false);
    expect(manager.list()).toEqual({});
  });

  it("keeps local source directories untouched", async () => {
    const local = await mkdtemp(join(tmpdir(), "tachi-plugin-local-"));
    await writeFile(join(local, "index.ts"), VALID_MODULE);
    await handleInstallPlugin(manager, { source: local, alias: "local-demo" });

    await handleUninstallPlugin(manager, { alias: "local-demo" });

    expect(existsSync(join(local, "index.ts"))).toBe(true);
    expect(manager.list()).toEqual({});
  });

  it("rejects unknown aliases", async () => {
    await expect(handleUninstallPlugin(manager, { alias: "ghost" })).rejects.toThrow(
      /not installed/,
    );
  });
});

describe("list_installed_plugins", () => {
  it("reports an empty install set", () => {
    expect(handleListInstalledPlugins(manager)).toBe("No plugins installed.");
  });

  it("lists installs with source kind and path", async () => {
    const local = await mkdtemp(join(tmpdir(), "tachi-plugin-local-"));
    await writeFile(join(local, "index.ts"), VALID_MODULE);
    await handleInstallPlugin(manager, { source: local, alias: "local-demo" });

    const message = handleListInstalledPlugins(manager);

    expect(message).toContain("**local-demo** (local)");
    expect(message).toContain(`Source: ${local}`);
    expect(message).toContain("Installed: 2026-06-12T10:00:00.000Z");
  });
});
