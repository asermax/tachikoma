import { execFile, spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import type { Logger } from "../../log.ts";

const execFileAsync = promisify(execFile);

/**
 * The three dangerous operations of a self-modifying process, isolated behind
 * interfaces so the orchestration and decision logic can be unit-tested against
 * fakes. The real implementations below are deliberately thin.
 */
export interface RegistryClient {
  /** Latest published version of the package, or null when the lookup fails. */
  fetchLatest(): Promise<string | null>;
}

export interface Installer {
  /** Install a specific version globally. Resolves on success, rejects on failure. */
  install(version: string): Promise<void>;
}

export interface Restarter {
  /** Replace the current process with a fresh boot of the (now installed) version. */
  restart(): never;
}

export const PACKAGE_NAME = "@asermax/tachikoma";

const REGISTRY_URL = `https://registry.npmjs.org/${PACKAGE_NAME}`;
const REGISTRY_TIMEOUT_MS = 10_000;
const INSTALL_TIMEOUT_MS = 120_000;

/** Reads `dist-tags.latest` from the npm registry metadata. */
export class NpmRegistryClient implements RegistryClient {
  private readonly log: Logger;

  constructor(log: Logger) {
    this.log = log;
  }

  async fetchLatest(): Promise<string | null> {
    try {
      const response = await fetch(REGISTRY_URL, {
        headers: { Accept: "application/vnd.npm.install-v1+json" },
        signal: AbortSignal.timeout(REGISTRY_TIMEOUT_MS),
      });

      if (!response.ok) {
        this.log.warn({ status: response.status }, "registry lookup returned non-ok status");
        return null;
      }

      const body = (await response.json()) as { "dist-tags"?: { latest?: string } };
      const latest = body["dist-tags"]?.latest;

      return typeof latest === "string" ? latest : null;
    } catch (error) {
      this.log.warn({ err: error }, "failed to fetch latest version from registry");
      return null;
    }
  }
}

/**
 * Installs globally via the configured package-manager command. The command is a
 * template where `{version}` is substituted, e.g. `npm i -g @asermax/tachikoma@{version}`.
 */
export class CommandInstaller implements Installer {
  private readonly commandTemplate: string;
  private readonly log: Logger;

  constructor(commandTemplate: string, log: Logger) {
    this.commandTemplate = commandTemplate;
    this.log = log;
  }

  async install(version: string): Promise<void> {
    const [command, ...args] = this.commandTemplate
      .replace(/\{version\}/g, version)
      .split(/\s+/)
      .filter((token) => token !== "");

    if (command == null) throw new Error("install command is empty");

    this.log.info({ command, args }, "running install command");

    const { stderr } = await execFileAsync(command, args, { timeout: INSTALL_TIMEOUT_MS });

    if (stderr.trim() !== "") this.log.debug({ stderr: stderr.trim() }, "install command stderr");
  }
}

/** Re-execs the current Node process with the same entrypoint and argv. */
export class ProcessRestarter implements Restarter {
  private readonly log: Logger;

  constructor(log: Logger) {
    this.log = log;
  }

  restart(): never {
    this.log.warn("re-executing process to load the new version");

    // Node has no execv; spawnSync runs the replacement to completion in-line so
    // the parent never proceeds past this call, then we exit with the child's
    // code. stdio is inherited so the new process owns the same terminal/pipes.
    const result = spawnSync(process.execPath, process.argv.slice(1), { stdio: "inherit" });

    process.exit(result.status ?? 0);
  }
}

/**
 * Reads the running package's own version from its package.json, resolved
 * relative to this module so it reflects the installed copy, not the cwd.
 */
export const readInstalledVersion = async (): Promise<string> => {
  const here = dirname(fileURLToPath(import.meta.url));

  // src/extensions/self-update -> package root is three levels up in dist too.
  const candidates = [
    join(here, "..", "..", "..", "package.json"),
    join(here, "..", "..", "package.json"),
  ];

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(await readFile(candidate, "utf8")) as { version?: string };
      if (typeof parsed.version === "string") return parsed.version;
    } catch {
      // try the next candidate
    }
  }

  return "0.0.0";
};
