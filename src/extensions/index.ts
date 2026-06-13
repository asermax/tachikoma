import type { TachikomaExtension } from "./api.ts";
import boundary from "./boundary/index.ts";
import commands from "./commands/index.ts";
import context from "./context/index.ts";
import detachedProcesses from "./detached-processes/index.ts";
import external from "./external/index.ts";
import git from "./git/index.ts";
import memory from "./memory/index.ts";
import notifications from "./notifications/index.ts";
import projects from "./projects/index.ts";
import repl from "./repl/index.ts";
import skills from "./skills/index.ts";
import tasks from "./tasks/index.ts";
import telegram from "./telegram/index.ts";
import workflows from "./workflows/index.ts";

/** First-party extensions, in load order. */
export const firstPartyExtensions = [
  commands,
  context,
  memory,
  projects,
  git,
  boundary,
  skills,
  workflows,
  tasks,
  detachedProcesses,
  notifications,
  repl,
  telegram,
  external,
] as TachikomaExtension<never>[];
