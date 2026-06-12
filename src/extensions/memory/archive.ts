import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";

import type { PostProcessor } from "../api.ts";
import { transcriptsDir } from "./layout.ts";

const headerSessionId = (raw: string): string | null => {
  const newline = raw.indexOf("\n");
  const firstLine = newline === -1 ? raw : raw.slice(0, newline);

  try {
    const header = JSON.parse(firstLine) as { type?: string; id?: unknown };

    if (header.type === "session" && typeof header.id === "string" && header.id !== "") {
      return header.id;
    }
  } catch {
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
      const sessionId = headerSessionId(raw);
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
