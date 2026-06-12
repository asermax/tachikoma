import { execFile } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { promisify } from "node:util";

import type { KeyValueState } from "../../db/state.ts";
import type { Logger } from "../../log.ts";
import { expandHome } from "../../workspace.ts";
import { loadExtensionModule } from "./loader.ts";

const execFileAsync = promisify(execFile);

export const ALIAS_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

const GIT_SOURCE_PATTERN = /^(https?:\/\/|git@|ssh:\/\/|file:\/\/)|\.git$/;

/** Git sources are cloned and updatable; anything else is a local path loaded in place. */
export const isGitSource = (source: string): boolean => GIT_SOURCE_PATTERN.test(source);

export interface InstallRecord {
  source: string;
  installedAt: string;
  path: string;
}

export interface UpdateResult {
  status: "updated" | "skipped";
  detail: string;
}

const INSTALLS_KEY = "installs";

export type InstallsState = Pick<KeyValueState, "get" | "set">;

export interface InstallManagerDeps {
  state: InstallsState;
  /** Where git sources are cloned: `{dataDir}/extensions/<alias>`. */
  extensionsDir: string;
  log: Logger;
  now?: () => Date;
}

/**
 * Agent-driven external extension installs. Git sources are cloned under the data dir and
 * updated with `git pull`; local paths are recorded as-is (always current).
 * Records live in the extension's KV state; loading happens on the next startup.
 */
export class InstallManager {
  private readonly state: InstallsState;
  private readonly extensionsDir: string;
  private readonly log: Logger;
  private readonly now: () => Date;

  constructor({ state, extensionsDir, log, now }: InstallManagerDeps) {
    this.state = state;
    this.extensionsDir = extensionsDir;
    this.log = log;
    this.now = now ?? (() => new Date());
  }

  list(): Record<string, InstallRecord> {
    return this.state.get<Record<string, InstallRecord>>(INSTALLS_KEY) ?? {};
  }

  async install(source: string, alias: string): Promise<InstallRecord> {
    if (!ALIAS_PATTERN.test(alias)) {
      throw new Error(`Invalid alias '${alias}': must match [a-z0-9][a-z0-9-]*`);
    }

    const records = this.list();

    if (records[alias] != null) {
      throw new Error(
        `ExternalExtension '${alias}' is already installed. Uninstall it or pick another alias.`,
      );
    }

    const fromGit = isGitSource(source);
    const path = fromGit ? await this.cloneGitSource(source, alias) : resolve(expandHome(source));

    if ((await loadExtensionModule(path, this.log)) == null) {
      if (fromGit) await rm(path, { recursive: true, force: true });

      throw new Error(
        `Source '${source}' does not contain a valid Tachikoma extension module ` +
          "(expected a .ts/.js file or an index.ts/index.js whose default export has 'name' and 'setup').",
      );
    }

    const record: InstallRecord = { source, installedAt: this.now().toISOString(), path };

    this.state.set(INSTALLS_KEY, { ...records, [alias]: record });
    this.log.info({ alias, source, path }, "external extension installed");

    return record;
  }

  private async cloneGitSource(source: string, alias: string): Promise<string> {
    const target = join(this.extensionsDir, alias);

    await mkdir(this.extensionsDir, { recursive: true });
    await rm(target, { recursive: true, force: true });

    try {
      await execFileAsync("git", ["clone", source, target]);
    } catch (error) {
      throw new Error(`git clone failed for '${source}': ${error}`);
    }

    return target;
  }

  async update(alias: string): Promise<UpdateResult> {
    const record = this.list()[alias];

    if (record == null) throw new Error(`ExternalExtension '${alias}' is not installed.`);

    if (!isGitSource(record.source)) {
      return {
        status: "skipped",
        detail:
          "Local external extensions load directly from their source path and are always current.",
      };
    }

    try {
      const { stdout } = await execFileAsync("git", ["pull", "--ff-only"], { cwd: record.path });

      this.log.info({ alias }, "external extension updated");
      return { status: "updated", detail: stdout.trim() };
    } catch (error) {
      throw new Error(`git pull failed for '${alias}': ${error}`);
    }
  }

  async uninstall(alias: string): Promise<InstallRecord> {
    const records = this.list();
    const record = records[alias];

    if (record == null) throw new Error(`ExternalExtension '${alias}' is not installed.`);

    // Only remove directories we cloned ourselves; local sources stay untouched.
    if (isGitSource(record.source)) {
      await rm(record.path, { recursive: true, force: true });
    }

    this.state.set(
      INSTALLS_KEY,
      Object.fromEntries(Object.entries(records).filter(([key]) => key !== alias)),
    );
    this.log.info({ alias }, "external extension uninstalled");

    return record;
  }
}
