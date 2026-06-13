import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { vi } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import type { Logger } from "../../src/log.ts";

export const createFakeLog = (): Logger =>
  ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }) as unknown as Logger;

export const createTestDatabase = async (): Promise<AppDatabase> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-workflows-db-"));
  const db = createDatabase(join(dir, "test.db"));

  runMigrations(db);

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
