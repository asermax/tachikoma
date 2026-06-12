import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { sql } from "drizzle-orm";
import { vi } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import type { Logger } from "../../src/log.ts";

export const createFakeLog = (): Logger =>
  ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }) as unknown as Logger;

// DDL mirror of schema.ts — remove once central migrations include these tables
const createWorkflowTables = (db: AppDatabase): void => {
  db.run(sql`
    CREATE TABLE IF NOT EXISTS \`workflow_states\` (
      \`id\` text PRIMARY KEY NOT NULL,
      \`skill_name\` text NOT NULL,
      \`workflow_name\` text NOT NULL,
      \`current_step\` text,
      \`step_states\` text NOT NULL,
      \`definition_snapshot\` text NOT NULL,
      \`scratchpad_path\` text NOT NULL,
      \`deleted_at\` integer,
      \`created_at\` integer NOT NULL,
      \`updated_at\` integer NOT NULL
    )
  `);
  db.run(
    sql`CREATE INDEX IF NOT EXISTS \`ix_workflow_states_skill_name\` ON \`workflow_states\` (\`skill_name\`)`,
  );
  db.run(
    sql`CREATE INDEX IF NOT EXISTS \`ix_workflow_states_workflow_name\` ON \`workflow_states\` (\`workflow_name\`)`,
  );
  db.run(
    sql`CREATE INDEX IF NOT EXISTS \`ix_workflow_states_active_lookup\` ON \`workflow_states\` (\`skill_name\`,\`workflow_name\`)`,
  );
};

export const createTestDatabase = async (): Promise<AppDatabase> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-workflows-db-"));
  const db = createDatabase(join(dir, "test.db"));

  runMigrations(db);
  createWorkflowTables(db);

  return db;
};

export interface StepFixture {
  id: string;
  frontmatter: string;
  body?: string;
}

export const writeWorkflowFixture = async (
  skillsRoot: string,
  skill: string,
  workflow: string,
  steps: StepFixture[],
): Promise<void> => {
  for (const step of steps) {
    const stepDir = join(skillsRoot, skill, "workflows", workflow, step.id);

    await mkdir(stepDir, { recursive: true });
    await writeFile(
      join(stepDir, "instructions.md"),
      `---\n${step.frontmatter}\n---\n\n${step.body ?? `Instructions for ${step.id}.`}\n`,
    );
  }
};
