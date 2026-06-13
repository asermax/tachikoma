import { mkdir } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";

export const expandHome = (path: string): string =>
  path === "~" || path.startsWith("~/") ? join(homedir(), path.slice(1)) : path;

export class Workspace {
  readonly root: string;

  constructor(configuredPath: string) {
    const expanded = expandHome(configuredPath);
    this.root = isAbsolute(expanded) ? expanded : resolve(expanded);
  }

  resolve(...parts: string[]): string {
    return join(this.root, ...parts);
  }

  /** Internal data (database, pi state) — never committed to the workspace repo. */
  get dataDir(): string {
    return this.resolve(".tachikoma");
  }

  /** Dedicated pi agent dir so Tachikoma never collides with a user's own pi install. */
  get piDir(): string {
    return join(this.dataDir, "pi");
  }

  get sessionsDir(): string {
    return join(this.piDir, "sessions");
  }

  get databaseFile(): string {
    return join(this.dataDir, "tachikoma.db");
  }

  /** Persisted diagnostic logs for daemon runs (rotated by pino-roll). */
  get logsDir(): string {
    return join(this.dataDir, "logs");
  }

  async ensure(): Promise<void> {
    await mkdir(this.sessionsDir, { recursive: true });
    await mkdir(this.logsDir, { recursive: true });
  }
}
