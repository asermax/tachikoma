import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createProjectsExchangeProcessor } from "../../src/extensions/projects/processor.ts";
import { handleRegisterProject } from "../../src/extensions/projects/tools.ts";
import { runGit } from "../../src/git/git.ts";
import type { DebouncedTask } from "../../src/util/debouncer.ts";
import {
  configureIdentity,
  createProjectOrigin,
  createWorkspace,
  fakeLogger,
  makeTempDir,
} from "./helpers.ts";

/** A `DebouncedTask` stand-in that records `touch()` calls and otherwise no-ops. */
const recordingDebouncer = (): DebouncedTask =>
  ({
    touch: vi.fn(),
    clear: vi.fn(),
    whenIdle: vi.fn().mockResolvedValue(undefined),
  }) as unknown as DebouncedTask;

let base: string;
let workspace: string;
let origin: string;
let projectPath: string;

beforeEach(async () => {
  base = await makeTempDir();
  origin = await createProjectOrigin(base, "app");
  workspace = await createWorkspace(base);

  await handleRegisterProject(
    { workspaceRoot: workspace, log: fakeLogger() },
    { name: "app", url: origin },
  );

  projectPath = join(workspace, "projects", "app");
  await configureIdentity(projectPath);
});

afterEach(async () => {
  await rm(base, { recursive: true, force: true });
});

describe("projects exchange processor", () => {
  it("resets the debounce timer on each exchange and does not commit", async () => {
    await writeFile(join(projectPath, "feature.ts"), "export const x = 1;\n", "utf8");
    const debouncer = recordingDebouncer();

    await createProjectsExchangeProcessor({ debouncer, log: fakeLogger() }).process({
      userText: "hi",
    });

    expect(debouncer.touch).toHaveBeenCalledTimes(1);
    // Nothing is committed on the exchange path — the file stays uncommitted.
    expect(await runGit(projectPath, ["status", "--porcelain"])).not.toBe("");
  });

  it("resets the timer on every exchange, not just the first", async () => {
    const debouncer = recordingDebouncer();

    const processor = createProjectsExchangeProcessor({ debouncer, log: fakeLogger() });
    await processor.process({ userText: "one" });
    await processor.process({ userText: "two" });

    expect(debouncer.touch).toHaveBeenCalledTimes(2);
  });
});
