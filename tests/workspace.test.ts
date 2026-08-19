import { mkdtemp, stat } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { expandHome, Workspace } from "../src/workspace.ts";

describe("expandHome", () => {
  it("expands a bare tilde to the home directory", () => {
    expect(expandHome("~")).toBe(homedir());
  });

  it("expands a tilde-prefixed path", () => {
    expect(expandHome("~/projects")).toBe(join(homedir(), "/projects"));
  });

  it("leaves a path without a leading tilde untouched", () => {
    expect(expandHome("/etc/config")).toBe("/etc/config");
  });

  it("does not expand a tilde embedded mid-path", () => {
    expect(expandHome("foo/~/bar")).toBe("foo/~/bar");
  });
});

describe("Workspace", () => {
  it("keeps an absolute configured path as the root", () => {
    const workspace = new Workspace("/srv/tachikoma");

    expect(workspace.root).toBe("/srv/tachikoma");
  });

  it("resolves a relative configured path against the cwd", () => {
    const workspace = new Workspace("relative/dir");

    expect(workspace.root).toBe(resolve("relative/dir"));
    expect(isAbsolute(workspace.root)).toBe(true);
  });

  it("expands a tilde configured path to an absolute root", () => {
    const workspace = new Workspace("~/tachi");

    expect(workspace.root).toBe(join(homedir(), "/tachi"));
  });

  it("derives data, pi, sessions, database, instance-lock and logs paths from the root", () => {
    const workspace = new Workspace("/srv/tachikoma");

    expect(workspace.resolve("a", "b")).toBe("/srv/tachikoma/a/b");
    expect(workspace.dataDir).toBe("/srv/tachikoma/.tachikoma");
    expect(workspace.piDir).toBe("/srv/tachikoma/.tachikoma/pi");
    expect(workspace.sessionsDir).toBe("/srv/tachikoma/.tachikoma/pi/sessions");
    expect(workspace.databaseFile).toBe("/srv/tachikoma/.tachikoma/tachikoma.db");
    expect(workspace.instanceLockFile).toBe("/srv/tachikoma/.tachikoma/instance.json");
    expect(workspace.logsDir).toBe("/srv/tachikoma/.tachikoma/logs");
  });

  describe("ensure", () => {
    let dir: string;

    beforeEach(async () => {
      dir = await mkdtemp(join(tmpdir(), "tachi-ws-"));
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("creates the sessions and logs directories recursively", async () => {
      const workspace = new Workspace(dir);

      await workspace.ensure();

      expect((await stat(workspace.sessionsDir)).isDirectory()).toBe(true);
      expect((await stat(workspace.logsDir)).isDirectory()).toBe(true);
    });
  });
});
