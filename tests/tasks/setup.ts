import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";

export const createTasksTestDb = async (): Promise<AppDatabase> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-tasks-"));
  const db = createDatabase(join(dir, "test.db"));

  runMigrations(db);

  return db;
};
