import { existsSync } from "node:fs";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { eq } from "drizzle-orm";
import { beforeEach, describe, expect, it } from "vitest";

import type { SessionRecord } from "../../src/db/core-schema.ts";
import type { AppDatabase } from "../../src/db/index.ts";
import { createStaleWorkflowCleanup } from "../../src/extensions/workflows/cleanup.ts";
import { WorkflowStateRepository } from "../../src/extensions/workflows/repository.ts";
import { workflowStates } from "../../src/extensions/workflows/schema.ts";
import { createFakeLog, createTestDatabase } from "./helpers.ts";

let db: AppDatabase;
let repository: WorkflowStateRepository;
let scratchpadDir: string;

beforeEach(async () => {
  db = await createTestDatabase();
  repository = new WorkflowStateRepository(db);
  scratchpadDir = await mkdtemp(join(tmpdir(), "tachi-workflows-cleanup-"));
});

const createWorkflow = async (id: string, workflowName: string) => {
  const scratchpadPath = join(scratchpadDir, `workflow-${id}.md`);
  await writeFile(scratchpadPath, "# Workflow\n");

  return repository.create({
    id,
    skillName: "writing",
    workflowName,
    stepStates: { "01-plan": "pending" },
    definitionSnapshot: [{ id: "01-plan", title: "Plan", required: true, path: "/tmp/none" }],
    scratchpadPath,
  });
};

const backdate = (id: string, hours: number): void => {
  db.update(workflowStates)
    .set({ updatedAt: new Date(Date.now() - hours * 60 * 60 * 1000) })
    .where(eq(workflowStates.id, id))
    .run();
};

const processorContext = () => ({
  session: { id: 1 } as SessionRecord,
  transcriptPath: null,
  log: createFakeLog(),
});

describe("stale workflow cleanup", () => {
  it("soft-deletes workflows past the threshold and removes their scratchpads", async () => {
    const stale = await createWorkflow("stale-1", "draft");
    const fresh = await createWorkflow("fresh-1", "publish");
    backdate(stale.id, 25);

    await createStaleWorkflowCleanup(repository, 24).process(processorContext());

    expect(repository.get(stale.id)).toBeNull();
    expect(existsSync(stale.scratchpadPath)).toBe(false);

    expect(repository.get(fresh.id)).not.toBeNull();
    expect(existsSync(fresh.scratchpadPath)).toBe(true);
  });

  it("does nothing when no workflows are stale", async () => {
    const fresh = await createWorkflow("fresh-2", "draft");

    await createStaleWorkflowCleanup(repository, 24).process(processorContext());

    expect(repository.get(fresh.id)).not.toBeNull();
  });

  it("survives repository failures without throwing", async () => {
    const broken = {
      listStale: () => {
        throw new Error("db gone");
      },
      softDelete: () => true,
    };

    await expect(
      createStaleWorkflowCleanup(broken, 24).process(processorContext()),
    ).resolves.toBeUndefined();
  });
});
