import { mkdir, readdir, readFile, stat, unlink, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";

import type { Logger } from "../../log.ts";
import type { PostProcessor } from "../api.ts";
import { transcriptsDir } from "./layout.ts";

const DAY_MS = 24 * 60 * 60 * 1000;

const headerSessionId = (raw: string, log: Logger): string | null => {
  const newline = raw.indexOf("\n");
  const firstLine = newline === -1 ? raw : raw.slice(0, newline);

  try {
    const header = JSON.parse(firstLine) as { type?: string; id?: unknown };

    if (header.type === "session" && typeof header.id === "string" && header.id !== "") {
      return header.id;
    }
  } catch (error) {
    log.debug({ err: error }, "transcript header unparseable — using source filename");

    return null;
  }

  return null;
};

/**
 * Copies the pi session JSONL into the workspace so transcripts are
 * git-versioned alongside the memories extracted from them. Named after the
 * pi session id from the JSONL header, falling back to the source filename.
 * Never throws — a failed archive must not block the rest of finalization.
 */
export const createTranscriptArchiveProcessor = (workspaceRoot: string): PostProcessor => ({
  name: "transcript-archive",
  phase: "finalize",

  async process({ transcriptPath, log }) {
    if (transcriptPath == null) {
      log.debug("no transcript — skipping archive");
      return;
    }

    try {
      const raw = await readFile(transcriptPath, "utf8");
      const sessionId = headerSessionId(raw, log);
      const name = sessionId != null ? `${sessionId}.jsonl` : basename(transcriptPath);
      const dest = join(transcriptsDir(workspaceRoot), name);

      await mkdir(transcriptsDir(workspaceRoot), { recursive: true });
      await writeFile(dest, raw, "utf8");

      log.info({ dest }, "archived transcript");
    } catch (error) {
      log.warn({ err: error, src: transcriptPath }, "transcript archive failed");
    }
  },
});

/**
 * Deletes archived transcripts older than the retention window. Filenames are
 * pi session ids, not dates, so age is read from each file's mtime (stamped at
 * archive write = session close). Deterministic host-side deletion — unlike the
 * store maintenance ticks, no headless agent runs. Never throws; a retention of
 * 0 (or less) disables pruning and keeps transcripts forever.
 */
export const pruneTranscripts = async (
  workspaceRoot: string,
  retentionDays: number,
  log: Logger,
  now: () => Date = () => new Date(),
): Promise<void> => {
  if (retentionDays <= 0) return;

  const dir = transcriptsDir(workspaceRoot);

  let names: string[];

  try {
    names = await readdir(dir);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      log.warn({ err: error, dir }, "failed to read transcripts dir for pruning");
    }

    return;
  }

  const cutoff = now().getTime() - retentionDays * DAY_MS;
  let removed = 0;

  for (const name of names) {
    if (!name.endsWith(".jsonl")) continue;

    const path = join(dir, name);

    try {
      if ((await stat(path)).mtimeMs >= cutoff) continue;

      await unlink(path);
      removed++;
    } catch (error) {
      log.warn({ path, err: error }, "failed to prune transcript");
    }
  }

  if (removed > 0) log.info({ removed, retentionDays }, "pruned old transcripts");
};
