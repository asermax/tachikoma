import { existsSync } from "node:fs";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { eq } from "drizzle-orm";
import { beforeEach, describe, expect, it } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import {
  createStaleWorkflowCleanup,
  type StaleRepository,
} from "../../src/extensions/workflows/cleanup.ts";
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

const createWorkflow = async (
  id: string,
  workflowName: string,
  parentWorkflowId: string | null = null,
) => {
  const scratchpadPath = join(scratchpadDir, `workflow-${id}.md`);
  await writeFile(scratchpadPath, "# Workflow\n");

  return repository.create({
    id,
    skillName: "writing",
    workflowName,
    stepStates: { "01-plan": "pending" },
    definitionSnapshot: [{ id: "01-plan", title: "Plan", required: true, path: "/tmp/none" }],
    scratchpadPath,
    parentWorkflowId,
  });
};

const backdate = (id: string, hours: number): void => {
  db.update(workflowStates)
    .set({ updatedAt: new Date(Date.now() - hours * 60 * 60 * 1000) })
    .where(eq(workflowStates.id, id))
    .run();
};

const processorContext = () => ({
  trunk: null,
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

  it("cascade soft-deletes the entire subtree of a stale workflow", async () => {
    const root = await createWorkflow("root-1", "draft");
    const child = await createWorkflow("child-1", "compose", root.id);
    backdate(root.id, 25);
    backdate(child.id, 25);

    await createStaleWorkflowCleanup(repository, 24).process(processorContext());

    const rootRow = db.select().from(workflowStates).where(eq(workflowStates.id, root.id)).get();
    const childRow = db.select().from(workflowStates).where(eq(workflowStates.id, child.id)).get();

    expect(rootRow?.deletedAt).not.toBeNull();
    expect(childRow?.deletedAt).not.toBeNull();
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
      abortCascade: () => [],
    };

    await expect(
      createStaleWorkflowCleanup(broken, 24).process(processorContext()),
    ).resolves.toBeUndefined();
  });

  it("leaves the scratchpad alone when abortCascade deletes nothing", async () => {
    const stale = await createWorkflow("stale-noop", "draft");
    backdate(stale.id, 25);

    const repo: StaleRepository = {
      listStale: () => [stale],
      abortCascade: () => [],
    };

    await createStaleWorkflowCleanup(repo, 24).process(processorContext());

    expect(existsSync(stale.scratchpadPath)).toBe(true);
  });

  it("isolates a per-workflow failure and continues with the rest", async () => {
    const failing = await createWorkflow("fail-1", "draft");
    const good = await createWorkflow("good-1", "publish");
    backdate(failing.id, 25);
    backdate(good.id, 25);

    const log = createFakeLog();
    const repo: StaleRepository = {
      listStale: () => [failing, good],
      abortCascade: (id) => {
        if (id === failing.id) {
          throw new Error("cascade blew up");
        }

        return repository.abortCascade(id);
      },
    };

    await createStaleWorkflowCleanup(repo, 24).process({
      trunk: null,
      transcriptPath: null,
      log,
    });

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ workflowId: failing.id }),
      "failed to clean up stale workflow",
    );
    expect(existsSync(good.scratchpadPath)).toBe(false);
    expect(repository.get(good.id)).toBeNull();
  });
});
