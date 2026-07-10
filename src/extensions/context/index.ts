import { readFileSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";

import { provideContext } from "../../agent/system-prompt-section.ts";
import type { Logger } from "../../log.ts";
import { defineExtension } from "../api.ts";
import { createCoreContextProcessor } from "./processor.ts";

const SOUL_TEMPLATE = `# Soul

You are Tachikoma, a proactive personal assistant. You maintain persistent memory
across conversations, learn continuously about the person you assist, and handle
background work during quiet moments.

You are curious, direct, and warm. You take initiative when it helps and stay out
of the way when it doesn't.
`;

const USER_TEMPLATE = `# User

Nothing is known about the user yet. This file accumulates durable knowledge about
who they are, extracted from conversations.
`;

const readOrCreate = async (path: string, template: string, log: Logger): Promise<string> => {
  try {
    return await readFile(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;

    await writeFile(path, template, "utf8");
    log.info({ file: path }, "created context file from template");

    return template;
  }
};

/**
 * Foundational context: SOUL.md (personality) and USER.md (durable user knowledge) are appended to
 * the agent's system prompt — layered on top of the core base prompt (identity + hygiene + workspace
 * root, owned by AgentManager). AGENTS.md is discovered natively by pi from the workspace root, so it
 * needs no handling here.
 */
export default defineExtension({
  name: "context",

  async setup(app) {
    // Updates SOUL/USER/AGENTS from the just-ended conversation. Runs in phase:"preFinalize"
    // (set by the processor) so it lands after the parallel memory-store extractions and before
    // the finalize phase commits the workspace.
    app.sessions.registerProcessor(
      createCoreContextProcessor({
        agent: app.agent,
        workspaceRoot: app.workspace.root,
        dataDir: app.workspace.dataDir,
        timezone: app.config.scheduler.timezone,
      }),
    );

    let soul = "";
    let user = "";

    app.bootstrap("load-context-files", async () => {
      soul = await readOrCreate(app.workspace.resolve("SOUL.md"), SOUL_TEMPLATE, app.log);
      user = await readOrCreate(app.workspace.resolve("USER.md"), USER_TEMPLATE, app.log);
    });

    // Re-read on every session build so core-context updates from the previous
    // session's post-processing apply without a restart; the bootstrap snapshot
    // is only the fallback when a read fails mid-flight.
    const fresh = (path: string, fallback: string): string => {
      try {
        return readFileSync(path, "utf8");
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
          app.log.warn(
            { err: error, file: path },
            "context file read failed, using cached snapshot",
          );
        }

        return fallback;
      }
    };

    app.agent.use(provideContext(() => fresh(app.workspace.resolve("SOUL.md"), soul)));
    app.agent.use(provideContext(() => fresh(app.workspace.resolve("USER.md"), user)));
  },
});
