import type { TachikomaExtension } from "./api.ts";
import boundary from "./boundary/index.ts";
import context from "./context/index.ts";
import memory from "./memory/index.ts";
import repl from "./repl/index.ts";
import skills from "./skills/index.ts";
import tasks from "./tasks/index.ts";
import telegram from "./telegram/index.ts";
import workflows from "./workflows/index.ts";

/** First-party extensions, in load order. */
export const firstPartyExtensions = [
  context,
  memory,
  boundary,
  skills,
  workflows,
  tasks,
  repl,
  telegram,
] as TachikomaExtension<never>[];
