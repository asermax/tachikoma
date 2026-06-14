import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { eq } from "drizzle-orm";
import { beforeEach, describe, expect, it } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import {
  type NewWorkflowState,
  WorkflowStateRepository,
} from "../../src/extensions/workflows/repository.ts";
import { workflowStates } from "../../src/extensions/workflows/schema.ts";
import { createTestDatabase } from "./helpers.ts";

let db: AppDatabase;
let repository: WorkflowStateRepository;
let scratchpadDir: string;

beforeEach(async () => {
  db = await createTestDatabase();
  repository = new WorkflowStateRepository(db);
  scratchpadDir = await mkdtemp(join(tmpdir(), "tachi-workflows-repo-"));
});

const newState = (overrides: Partial<NewWorkflowState> = {}): NewWorkflowState => ({
  id: "wf-1",
  skillName: "writing",
  workflowName: "draft",
  stepStates: { "01-plan": "pending" },
  definitionSnapshot: [{ id: "01-plan", title: "Plan", required: true, path: "/tmp/none" }],
  scratchpadPath: join(scratchpadDir, "wf-1.md"),
  ...overrides,
});

const backdate = (id: string, hours: number): void => {
  db.update(workflowStates)
    .set({ updatedAt: new Date(Date.now() - hours * 60 * 60 * 1000) })
    .where(eq(workflowStates.id, id))
    .run();
};

describe("WorkflowStateRepository.create", () => {
  it("rejects a second active top-level instance of the same skill+workflow", () => {
    repository.create(newState());

    expect(() => repository.create(newState({ id: "wf-2" }))).toThrow(
      /Active workflow state already exists for writing\/draft/,
    );
  });

  it("allows composed children to bypass the one-active-instance rule", () => {
    const root = repository.create(newState());

    const child = repository.create(
      newState({
        id: "child-1",
        parentWorkflowId: root.id,
        parentStepId: "01-plan",
        scratchpadPath: join(scratchpadDir, "child-1.md"),
      }),
    );

    expect(child.parentWorkflowId).toBe(root.id);
    expect(child.parentStepId).toBe("01-plan");
  });
});

describe("WorkflowStateRepository.softDelete", () => {
  it("soft-deletes an existing record and reports success", () => {
    const state = repository.create(newState());

    expect(repository.softDelete(state.id)).toBe(true);
    expect(repository.get(state.id)).toBeNull();
  });

  it("returns false when the record is missing or already deleted", () => {
    const state = repository.create(newState());
    repository.softDelete(state.id);

    expect(repository.softDelete(state.id)).toBe(false);
    expect(repository.softDelete("never-existed")).toBe(false);
  });
});

describe("WorkflowStateRepository.listStale", () => {
  it("keeps a root alive when a descendant is fresher than the cutoff", async () => {
    const root = repository.create(newState());
    await writeFile(join(scratchpadDir, "child.md"), "# c\n");
    repository.create(
      newState({
        id: "child-1",
        parentWorkflowId: root.id,
        parentStepId: "01-plan",
        scratchpadPath: join(scratchpadDir, "child.md"),
      }),
    );

    backdate(root.id, 25);
    // Child stays recent, so its updatedAt > the root's older timestamp keeps the
    // subtree above the cutoff (exercises the reduce's max-pick branch).

    expect(repository.listStale(24 * 60 * 60 * 1000)).toEqual([]);
  });

  it("returns a root whose entire subtree is older than the threshold", () => {
    const root = repository.create(newState());
    backdate(root.id, 25);

    expect(repository.listStale(24 * 60 * 60 * 1000).map((r) => r.id)).toEqual([root.id]);
  });
});

describe("WorkflowStateRepository.abortCascade", () => {
  it("returns an empty list when the root is already gone", () => {
    expect(repository.abortCascade("never-existed")).toEqual([]);
  });

  it("soft-deletes the root and its active descendants", () => {
    const root = repository.create(newState());
    repository.create(
      newState({
        id: "child-1",
        parentWorkflowId: root.id,
        parentStepId: "01-plan",
        scratchpadPath: join(scratchpadDir, "child-1.md"),
      }),
    );

    expect(repository.abortCascade(root.id).sort()).toEqual([root.id, "child-1"].sort());
    expect(repository.get(root.id)).toBeNull();
    expect(repository.getActiveChild(root.id)).toBeNull();
  });
});
