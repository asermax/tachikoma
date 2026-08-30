import type { GitResult } from "./git.ts";
import { runGitCapture } from "./git.ts";

/** The remote these helpers talk to — the workspace's shared origin. */
const REMOTE = "origin";

/**
 * Fetch origin with `--prune` so remote deletions reach the local tracking
 * refs — that is what makes "branch still on the remote" a meaningful signal
 * after overnight merges and proposal-branch deletions. Returns the git result
 * instead of throwing: a non-zero code is a soft-abort signal for the caller,
 * not an exceptional state (a hung or unreachable remote must not crash a run).
 */
export const fetchRemote = async (cwd: string): Promise<GitResult> =>
  runGitCapture(cwd, ["fetch", REMOTE, "--prune"]);

/**
 * Resolve origin's default branch name (e.g. "main"). Offline first: the local
 * `refs/remotes/origin/HEAD` symbolic ref (present in ordinary clones; absent
 * when the repo was `git init`ed rather than cloned, or cloned while origin was
 * still empty). Online fallback: `ls-remote --symref`, whose first line is
 * `ref: refs/heads/<name>`. Both failing throws — the caller soft-aborts rather
 * than classify against an unresolved ref.
 */
export const resolveRemoteDefaultBranch = async (cwd: string): Promise<string> => {
  const symbolic = await runGitCapture(cwd, [
    "symbolic-ref",
    "--short",
    `refs/remotes/${REMOTE}/HEAD`,
  ]);
  const prefix = `${REMOTE}/`;

  if (symbolic.code === 0 && symbolic.stdout.startsWith(prefix)) {
    const name = symbolic.stdout.slice(prefix.length);

    if (name !== "") return name;
  }

  const remote = await runGitCapture(cwd, ["ls-remote", "--symref", REMOTE, "HEAD"]);
  const symrefPrefix = "ref: refs/heads/";

  if (remote.code === 0) {
    // Line shape: `ref: refs/heads/<name>\tHEAD`. Branch names contain no
    // whitespace, so the first whitespace-delimited token after the prefix is
    // the whole name.
    const symrefLine = remote.stdout.split("\n").find((line) => line.startsWith(symrefPrefix));
    const name = symrefLine?.slice(symrefPrefix.length).trim().split(/\s+/)[0];

    if (name != null && name !== "") return name;
  }

  throw new Error(
    `could not resolve ${REMOTE}'s default branch (symbolic-ref: ${symbolic.stderr || `exit ${symbolic.code}`}; ls-remote: ${remote.stderr || `exit ${remote.code}`})`,
  );
};

/**
 * List origin's branch tips matching `pattern` (a ref tail pattern such as
 * `"skill-evolution/*"`), as branch name → tip SHA with `refs/heads/` stripped.
 * `ls-remote` is the authoritative remote view — local tracking refs predate
 * anything this process pushed. Throws when the listing itself fails; an empty
 * result is a legitimate answer (nothing matches).
 */
export const listRemoteBranchTips = async (
  cwd: string,
  pattern: string,
): Promise<Map<string, string>> => {
  const result = await runGitCapture(cwd, ["ls-remote", "--heads", REMOTE, pattern]);

  if (result.code !== 0) {
    throw new Error(
      `git ls-remote --heads ${REMOTE} ${pattern} failed: ${result.stderr || `exit code ${result.code}`}`,
    );
  }

  const refPrefix = "refs/heads/";
  const tips = new Map<string, string>();

  for (const line of result.stdout.split("\n")) {
    const [sha, ref] = line.split("\t");

    if (ref == null || !ref.startsWith(refPrefix)) continue;

    tips.set(ref.slice(refPrefix.length), sha ?? "");
  }

  return tips;
};
