import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export interface GitResult {
  code: number;
  stdout: string;
  stderr: string;
}

/** Run a git command and return exit code + trimmed output, never throwing on non-zero exit. */
export const runGitCapture = async (cwd: string, args: string[]): Promise<GitResult> => {
  try {
    const { stdout, stderr } = await execFileAsync("git", args, { cwd });
    return { code: 0, stdout: stdout.trim(), stderr: stderr.trim() };
  } catch (error) {
    const failure = error as { code?: number | string; stdout?: string; stderr?: string };

    return {
      code: typeof failure.code === "number" ? failure.code : 1,
      stdout: (failure.stdout ?? "").trim(),
      stderr: (failure.stderr ?? "").trim(),
    };
  }
};

/** Run a git command and throw on failure. Returns trimmed stdout. */
export const runGit = async (cwd: string, args: string[]): Promise<string> => {
  const result = await runGitCapture(cwd, args);

  if (result.code !== 0) {
    throw new Error(`git ${args.join(" ")} failed: ${result.stderr || `exit code ${result.code}`}`);
  }

  return result.stdout;
};

export const hasUncommittedChanges = async (cwd: string): Promise<boolean> =>
  (await runGitCapture(cwd, ["status", "--porcelain"])).stdout !== "";

export const hasRemote = async (cwd: string, remote: string): Promise<boolean> =>
  (await runGitCapture(cwd, ["remote", "get-url", remote])).code === 0;

/** List all registered submodule paths (e.g. ["projects/my-app"]) from git state. */
export const listSubmodules = async (workspaceRoot: string): Promise<string[]> => {
  const result = await runGitCapture(workspaceRoot, ["submodule", "status", "--recursive"]);

  if (result.code !== 0) return [];

  // Each line is like " abc1234 projects/my-app (heads/main)" — the first
  // character is a status indicator (space, +, -, or U) fused to the hash.
  return result.stdout
    .split("\n")
    .map((line) => line.trim().split(/\s+/))
    .filter((parts) => parts.length >= 2)
    .map((parts) => parts[1] as string);
};
