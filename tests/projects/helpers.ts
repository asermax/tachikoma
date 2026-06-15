import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { runGit } from "../../src/git/git.ts";
import type { RebaseResolver } from "../../src/git/sync.ts";
import { commitFile, initRepo } from "../git/helpers.ts";

export { commitFile, configureIdentity, fakeLogger, headOf, lastSubject } from "../git/helpers.ts";

// Submodule clones from local paths are blocked by default since the
// CVE-2022-39253 hardening, and the spawned clone ignores the superproject's
// local config — the env allowlist is the only switch that reaches it.
process.env.GIT_ALLOW_PROTOCOL = "file";

export const makeTempDir = (): Promise<string> => mkdtemp(join(tmpdir(), "tachi-projects-"));

/** A bare "remote" project repo seeded with one commit on main. */
export const createProjectOrigin = async (base: string, name: string): Promise<string> => {
  const source = join(base, `${name}-source`);
  await mkdir(source);
  await initRepo(source);
  await commitFile(source, "README.md", `# ${name}\n`, "Initial commit");

  const origin = join(base, `${name}.git`);
  await runGit(base, ["clone", "--bare", source, origin]);

  return origin;
};

export const createWorkspace = async (base: string): Promise<string> => {
  const workspace = join(base, "workspace");
  await mkdir(workspace);
  await initRepo(workspace);
  await commitFile(workspace, ".gitignore", ".tachikoma/\n", "Initial commit");

  return workspace;
};

/**
 * Stand-in for the side agent: drives the in-progress rebase to completion the
 * way the real agent would — resolves every conflicted file to a merged body,
 * stages it, and continues — without spawning an LLM. Mirrors the fake in
 * tests/git/sync.test.ts.
 */
export const resolvingResolver: RebaseResolver = async (cwd) => {
  while (true) {
    const conflicted = await runGit(cwd, ["diff", "--name-only", "--diff-filter=U"]);

    if (conflicted === "") return;

    for (const file of conflicted.split("\n")) {
      await writeFile(join(cwd, file), "merged by agent\n", "utf8");
      await runGit(cwd, ["add", file]);
    }

    await runGit(cwd, ["-c", "core.editor=true", "rebase", "--continue"]);
  }
};
