import { mkdir, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { runGit } from "../../src/extensions/git/git.ts";
import { commitFile, configureIdentity, initRepo } from "../git/helpers.ts";

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
