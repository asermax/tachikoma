import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve, sep } from "node:path";

import { type ToolDefinition, truncateTail } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import type { SideRunner } from "../../agent/side-run.ts";
import { runGitCapture } from "../../git/git.ts";
import { listRemoteBranchTips } from "../../git/remote.ts";
import type { Logger } from "../../log.ts";
import { fileExists } from "../../util/markdown-store.ts";
import { skillEvolutionDir } from "./layout.ts";
import { proposalSystemPrompt } from "./prompts.ts";
import { formatImpactLog, type ImpactLogEntry } from "./store.ts";

/**
 * The proposal stage (S7/S8, R7–R9): one scoped agent authors one branch per eligible pattern in
 * temp worktrees under the tmp dir, and the host — never the agent — decides what gets logged.
 *
 * The agent drives git through a purpose-built `git` tool, not bash: the workspace bash guardrail
 * blocks `git push` in every bash-capable session, and the commit-agent/rebase-resolver precedent
 * (subcommand allowlist, editor/gpg disabled, refusal-as-text) already establishes the in-process
 * shape. Every refusal is instructive text, so the agent self-corrects within the same run — the
 * branch-collision suffix rule (R9) executes itself: push refuses an existing name, the agent
 * retries with `-2`.
 */

/** Used by the proposal run, which drives a bare headless side-run (the maintenance `Runner` idiom). */
export type Runner = Pick<SideRunner, "run">;

/**
 * The proposal-branch namespace and shape (R9): `skill-evolution/<skill>-<slug>`, the slug segment
 * lowercase letters/digits/single hyphens, starting with a letter or digit. Pinned in the design —
 * the push guard, the worktree `-b` guard, and the sweep's local-branch listing all key on it.
 */
export const BRANCH_NAME_PATTERN = /^skill-evolution\/[a-z0-9][a-z0-9-]*$/;

/** Prefix every proposal branch carries — also the `ls-remote` pattern the stages scan with. */
export const BRANCH_NAMESPACE = "skill-evolution/";

/** The `ls-remote` ref pattern listing the whole proposal namespace on the remote. */
export const REMOTE_BRANCH_PATTERN = `${BRANCH_NAMESPACE}*`;

/** One entry of the agent's `report_proposals` call — its self-report, pre-verification. */
export interface ReportedProposal {
  branch: string;
  /** Skill directory name under `skills/` (existing or the new one). */
  skill: string;
  /** Pattern page filename within the store dir, exactly as the prompt listed it. */
  pattern: string;
  description: string;
}

/**
 * The closure box the `report_proposals` execute handler writes and the host reads after the run
 * (the tasks `update_goal` idiom: a terminal intent captured in a variable the loop trusts only
 * after the tool's own validation passes). Last call wins.
 */
export interface ProposalCapture {
  proposals: ReportedProposal[];
}

/**
 * A proposal run that died partway. Carries whatever `report_proposals` captured before the death:
 * the processor still hands those entries to verification, so a branch the agent pushed AND
 * reported before dying is verified, logged, swept, and reported even though the run failed
 * (R8/R10 partial-failure guarantees). The run's failure surfaces separately — the processor
 * rethrows this error after verification settles.
 */
export class ProposalRunError extends Error {
  /** The capture at the moment the run died — possibly empty. */
  readonly proposals: ReportedProposal[];

  constructor(message: string, options: { cause: unknown; proposals: ReportedProposal[] }) {
    super(message, { cause: options.cause });
    this.proposals = options.proposals;
  }
}

/**
 * Resolve `path` against `root` and return it only when it lands inside `root` (inclusive).
 * The `resolve.ts` `resolvePath` shape with a bound root: absolute paths pass through, relative
 * paths resolve against the root — and anything escaping the root is a refusal, not an error.
 */
export const resolveUnderRoot = (root: string, path: string): string | null => {
  const bound = resolve(root);
  const absolute = resolve(bound, path);

  return absolute === bound || absolute.startsWith(`${bound}${sep}`) ? absolute : null;
};

/** What a tool's execute returns — `details` stays open for the pi host to fill. */
type ToolResult = ReturnType<typeof textResult>;

const textResult = (text: string) => {
  const { content, truncated } = truncateTail(text);

  return {
    content: [{ type: "text" as const, text: truncated ? `${content}\n\n[truncated]` : content }],
    details: undefined,
  };
};

const refusal = (reason: string, guidance: string) => textResult(`Refused: ${reason} ${guidance}`);

// Everything the file tools may touch lives under the tmp dir; this is what confines edits to
// the worktrees (R8's "the live working tree is never touched").
const ReadFileParams = Type.Object({
  path: Type.String({
    description: "Path of the file to read, inside a worktree under the tmp dir",
  }),
});

const WriteFileParams = Type.Object({
  path: Type.String({
    description: "Path of the file to write, inside a worktree under the tmp dir",
  }),
  content: Type.String({ description: "Full file contents to write" }),
});

const ListDirParams = Type.Object({
  path: Type.String({ description: "Directory to list, inside a worktree under the tmp dir" }),
});

const GitParams = Type.Object({
  args: Type.Array(Type.String(), {
    description:
      'git arguments, e.g. ["status"], ["worktree","add","-b","skill-evolution/x","<path>","refs/remotes/origin/main"], ["add","skills/x/SKILL.md"], ["push","origin","skill-evolution/x"]',
    minItems: 1,
  }),
  path: Type.Optional(
    Type.String({
      description:
        "Directory to run git in — a worktree under the tmp dir (that is how you target a specific worktree). Defaults to the workspace root.",
    }),
  ),
});

const ReportParams = Type.Object({
  proposals: Type.Array(
    Type.Object({
      branch: Type.String({
        description: "The pushed proposal branch, e.g. skill-evolution/deploy-add-env-flag",
      }),
      skill: Type.String({ description: "The skill directory name under skills/" }),
      pattern: Type.String({
        description: "The pattern page filename exactly as given in the prompt",
      }),
      description: Type.String({ description: "One-line summary of the proposed change" }),
    }),
    { description: "Every proposal authored this run, in push order" },
  ),
});

/** Read-only inspection plus staging/committing — the commit-agent allowlist verbatim. */
const SIMPLE_GIT_SUBCOMMANDS = new Set(["status", "diff", "log", "show", "add", "commit"]);

/**
 * Validate a `worktree` invocation: `add` must carry `-b <branch>` matching the naming pattern and
 * a worktree path under the tmp dir (plus an optional base commit-ish — nothing else); `remove`
 * takes exactly one path under the tmp dir.
 */
const validateWorktreeArgs = (
  tmpDir: string,
  args: readonly string[],
): { ok: true } | { ok: false; result: ToolResult } => {
  const [op, ...tail] = args;

  if (op !== "add" && op !== "remove") {
    return {
      ok: false,
      result: refusal(
        `git worktree ${op ?? ""} is not allowed.`,
        "Use only `worktree add -b <branch> <path> [base]` or `worktree remove <path>`.",
      ),
    };
  }

  if (op === "remove") {
    if (tail.length !== 1 || tail[0] === undefined || resolveUnderRoot(tmpDir, tail[0]) == null) {
      return {
        ok: false,
        result: refusal(
          "`worktree remove` takes exactly one path argument.",
          `The path must resolve inside ${tmpDir}.`,
        ),
      };
    }

    return { ok: true };
  }

  let branch: string | null = null;
  const positionals: string[] = [];

  for (let index = 0; index < tail.length; index += 1) {
    const token = tail[index];

    if (token === "-b") {
      branch = tail[index + 1] ?? null;
      index += 1;
    } else if (token?.startsWith("-")) {
      return {
        ok: false,
        result: refusal(`git worktree add ${token} is not allowed.`, "Use only `-b <branch>`."),
      };
    } else if (token != null) {
      positionals.push(token);
    }
  }

  if (branch == null || !BRANCH_NAME_PATTERN.test(branch)) {
    return {
      ok: false,
      result: refusal(
        branch == null
          ? "`worktree add` requires `-b <branch>`."
          : `branch name \`${branch}\` does not match ${BRANCH_NAME_PATTERN.source}.`,
        "Name branches `skill-evolution/<skill>-<slug>` — lowercase letters, digits, and single hyphens only.",
      ),
    };
  }

  const [worktreePath] = positionals;

  if (positionals.length < 1 || positionals.length > 2 || worktreePath == null) {
    return {
      ok: false,
      result: refusal(
        "`worktree add` takes a <path> and an optional <base>.",
        "Shape: worktree add -b <branch> <path-under-tmp> refs/remotes/origin/<default>.",
      ),
    };
  }

  if (resolveUnderRoot(tmpDir, worktreePath) == null) {
    return {
      ok: false,
      result: refusal(
        `worktree path \`${worktreePath}\` escapes the tmp dir.`,
        `Worktrees must live under ${tmpDir}.`,
      ),
    };
  }

  return { ok: true };
};

/**
 * Validate a `push` invocation: exactly `push origin <name>` — the name matches the namespace
 * pattern, is not the default branch, carries no flag (a force flag would break the exact shape),
 * and does not already exist on the remote (the injected view, consulted at push time so the
 * collision rule fires as a self-correcting refusal rather than a remote error).
 */
const validatePushArgs = async (
  defaultBranch: string,
  remoteBranchNames: () => Promise<Set<string>>,
  args: readonly string[],
): Promise<{ ok: true } | { ok: false; result: ToolResult }> => {
  const flags = args.filter((token) => token.startsWith("-"));

  if (flags.length > 0) {
    return {
      ok: false,
      result: refusal(
        `\`${flags.join(" ")}\` is not allowed on push.`,
        "Pushes are flag-less — force pushes and lease variants are never permitted.",
      ),
    };
  }

  const name = args[2];

  if (args.length !== 3 || args[1] !== "origin" || name == null) {
    return {
      ok: false,
      result: refusal(
        'push must be exactly ["push", "origin", "<branch>"].',
        "Only the origin remote is pushable.",
      ),
    };
  }

  if (!BRANCH_NAME_PATTERN.test(name)) {
    return {
      ok: false,
      result: refusal(
        `branch name \`${name}\` does not match ${BRANCH_NAME_PATTERN.source}.`,
        "Name branches `skill-evolution/<skill>-<slug>` — lowercase letters, digits, and single hyphens only.",
      ),
    };
  }

  if (name === defaultBranch) {
    return {
      ok: false,
      result: refusal(
        `\`${name}\` is the default branch.`,
        "Only new skill-evolution/* branches may be pushed.",
      ),
    };
  }

  if ((await remoteBranchNames()).has(name)) {
    return {
      ok: false,
      result: refusal(
        `\`${name}\` already exists on origin.`,
        "Pick a fresh branch name — e.g. append a suffix like `-2` — and push again.",
      ),
    };
  }

  return { ok: true };
};

export interface ProposalToolDeps {
  workspaceRoot: string;
  /** Feature-owned worktree namespace — every file operation and worktree path lands under it. */
  tmpDir: string;
  /** The remote default branch — the one branch push must never reach. */
  defaultBranch: string;
  /** Live remote view consulted at push time; the production backing is `listRemoteBranchTips`. */
  remoteBranchNames: () => Promise<Set<string>>;
  /** The closure box `report_proposals` writes; the host reads it after the run settles. */
  capture: ProposalCapture;
}

/**
 * Build the proposal agent's entire tool surface — exactly five tools. Bound to one workspace and
 * one tmp dir so the agent can never operate on another tree: the file tools refuse any path that
 * escapes the tmp dir, and `git` runs with the resolved `path` (or the workspace root) as cwd —
 * never `-C` string surgery. Editor/gpg are disabled exactly like the commit agent so an
 * unattended commit in a signing-configured repo cannot hang.
 */
export const buildProposalTools = (deps: ProposalToolDeps): ToolDefinition[] => {
  const { workspaceRoot, tmpDir, defaultBranch, remoteBranchNames, capture } = deps;
  const pathsGuidance = `Only paths inside ${tmpDir} are allowed.`;

  const guardPath = (path: string): string | null => resolveUnderRoot(tmpDir, path);

  return [
    {
      name: "read_file",
      label: "Read file",
      description:
        "Read a file inside a proposal worktree (e.g. a skill's SKILL.md before editing it).",
      parameters: ReadFileParams,
      async execute(_id, params) {
        const path = guardPath(params.path);

        if (path == null) return refusal(`\`${params.path}\` escapes the tmp dir.`, pathsGuidance);

        return textResult(await readFile(path, "utf8"));
      },
    } satisfies ToolDefinition<typeof ReadFileParams>,

    {
      name: "write_file",
      label: "Write file",
      description:
        "Write a file inside a proposal worktree with full contents (missing directories are created).",
      parameters: WriteFileParams,
      async execute(_id, params) {
        const path = guardPath(params.path);

        if (path == null) return refusal(`\`${params.path}\` escapes the tmp dir.`, pathsGuidance);

        await mkdir(dirname(path), { recursive: true });
        await writeFile(path, params.content, "utf8");
        return textResult(`Wrote ${params.path}`);
      },
    } satisfies ToolDefinition<typeof WriteFileParams>,

    {
      name: "list_dir",
      label: "List directory",
      description: "List a directory inside a proposal worktree (directories carry a trailing /).",
      parameters: ListDirParams,
      async execute(_id, params) {
        const path = guardPath(params.path);

        if (path == null) return refusal(`\`${params.path}\` escapes the tmp dir.`, pathsGuidance);

        const entries = await readdir(path, { withFileTypes: true });
        return textResult(
          entries
            .map((entry) => (entry.isDirectory() ? `${entry.name}/` : entry.name))
            .sort()
            .join("\n"),
        );
      },
    } satisfies ToolDefinition<typeof ListDirParams>,

    {
      name: "git",
      label: "Run git",
      description:
        "Run a git command in the workspace repository. Allowed: worktree add (path under the tmp dir, -b skill-evolution/<name>), worktree remove, status, diff, log, show, add, commit, and push origin <branch> — new skill-evolution/* branches only, no force. Set `path` to a worktree directory under the tmp dir to target that worktree; commands otherwise run at the workspace root.",
      parameters: GitParams,
      async execute(_id, params) {
        const [subcommand, ...rest] = params.args;

        if (subcommand == null || subcommand.startsWith("-")) {
          return refusal(
            `git ${subcommand ?? ""} is not allowed.`,
            "Use only worktree, status, diff, log, show, add, commit, or push origin <branch>.",
          );
        }

        // The optional cwd targets a worktree — validated like the file-tool paths, never -C.
        const cwd = (() => {
          if (params.path == null) return workspaceRoot;
          const guarded = guardPath(params.path);
          return guarded ?? null;
        })();

        if (cwd == null) {
          return refusal(`\`${params.path}\` escapes the tmp dir.`, pathsGuidance);
        }

        if (subcommand === "worktree") {
          const verdict = validateWorktreeArgs(tmpDir, rest);
          if (!verdict.ok) return verdict.result;
        } else if (subcommand === "push") {
          const verdict = await validatePushArgs(defaultBranch, remoteBranchNames, params.args);
          if (!verdict.ok) return verdict.result;
        } else if (!SIMPLE_GIT_SUBCOMMANDS.has(subcommand)) {
          return refusal(
            `git ${subcommand} is not allowed.`,
            "Use only worktree, status, diff, log, show, add, commit, or push origin <branch>.",
          );
        }

        const result = await runGitCapture(cwd, [
          "-c",
          "core.editor=true",
          "-c",
          "commit.gpgsign=false",
          ...params.args,
        ]);

        return textResult(
          `exit ${result.code}\n${[result.stdout, result.stderr].filter(Boolean).join("\n")}`.trim(),
        );
      },
    } satisfies ToolDefinition<typeof GitParams>,

    {
      name: "report_proposals",
      label: "Report proposals",
      description:
        "Report the proposals you authored — the required terminal step of this run. One entry per pushed branch: branch, skill (directory name), pattern (the pattern page filename from the prompt), description (one line).",
      parameters: ReportParams,
      async execute(_id, params) {
        for (const proposal of params.proposals) {
          if (!BRANCH_NAME_PATTERN.test(proposal.branch)) {
            return refusal(
              `branch name \`${proposal.branch}\` does not match ${BRANCH_NAME_PATTERN.source}.`,
              "Fix the entry and report again — every field must be non-empty and the branch must be a pushed skill-evolution/* branch.",
            );
          }

          if (proposal.skill === "" || proposal.pattern === "" || proposal.description === "") {
            return refusal(
              "a proposal entry has an empty skill, pattern, or description.",
              "Fill every field and report again.",
            );
          }
        }

        capture.proposals = params.proposals;
        return textResult(
          `Recorded ${params.proposals.length} proposal(s). Stop now — the host verifies each from git state.`,
        );
      },
    } satisfies ToolDefinition<typeof ReportParams>,
  ];
};

export interface ProposalAgentDeps {
  side: Runner;
  workspaceRoot: string;
  tmpDir: string;
  defaultBranch: string;
  /** Eligible pattern-page filenames — host-filtered (never-re-proposed is enforced input-side). */
  eligible: readonly string[];
  /** Current ledger rows — context for what is already open, never a write target. */
  impactLog: readonly ImpactLogEntry[];
  log: Logger;
}

/** One eligible pattern page, inlined for the prompt (a page that vanished mid-run is skipped). */
const patternPageSection = async (
  storeDir: string,
  name: string,
  log: Logger,
): Promise<string | null> => {
  const path = join(storeDir, name);

  try {
    return `### ${name}\n\n${await readFile(path, "utf8")}`;
  } catch (error) {
    log.warn(
      { path, err: error },
      "eligible pattern page unreadable — excluded from the proposal prompt",
    );
    return null;
  }
};

/** The workspace skills inventory: one block per skill directory, its SKILL.md inlined. */
const skillsInventorySection = async (workspaceRoot: string, log: Logger): Promise<string> => {
  const skillsDir = join(workspaceRoot, "skills");

  if (!(await fileExists(skillsDir))) return "(no skills/ directory in this workspace)";

  const blocks: string[] = [];

  for (const entry of (await readdir(skillsDir, { withFileTypes: true })).sort((a, b) =>
    a.name.localeCompare(b.name),
  )) {
    if (!entry.isDirectory()) continue;

    const skillPath = join(skillsDir, entry.name, "SKILL.md");
    const body = (await fileExists(skillPath))
      ? await readFile(skillPath, "utf8")
      : "(no SKILL.md — a skill directory without guidance)";

    blocks.push(`### skills/${entry.name}\n\n${body}`);
  }

  if (blocks.length === 0) {
    log.debug({ skillsDir }, "workspace has no skill directories");
    return "(skills/ is empty — new-skill proposals are the only possibility)";
  }

  return blocks.join("\n\n");
};

/**
 * Run the proposal agent (S7): one context-free headless run over the eligible pattern pages, the
 * impact log, and the workspace skills inventory. `isolatePrompt: true` over `run`'s default empty
 * built-in allowlist makes the five custom tools the entire surface — no grafted context files or
 * skills catalog. Returns the captured `report_proposals` list (possibly empty). A dying run
 * throws a {@link ProposalRunError} carrying the partial capture — the caller still verifies
 * whatever pushed-and-reported before the death, and its verify-and-sweep `finally` runs.
 */
export const runProposalAgent = async (deps: ProposalAgentDeps): Promise<ReportedProposal[]> => {
  const { side, workspaceRoot, tmpDir, defaultBranch, eligible, impactLog, log } = deps;
  const capture: ProposalCapture = { proposals: [] };

  const pages = await Promise.all(
    eligible.map(
      async (name) => await patternPageSection(skillEvolutionDir(workspaceRoot), name, log),
    ),
  );
  const pageBlocks = pages.filter((page): page is string => page != null);

  const prompt = [
    "Author skill-evolution proposals for the eligible patterns below, following your instructions.",
    "",
    "## Eligible patterns (no open or prior proposal)",
    "",
    pageBlocks.length > 0
      ? pageBlocks.join("\n\n")
      : "(none — report an empty proposal list and stop)",
    "",
    "## Skill impact log (current state — context only, never write it)",
    "",
    formatImpactLog(impactLog),
    "",
    "## Workspace skills inventory",
    "",
    await skillsInventorySection(workspaceRoot, log),
    "",
    "## Task",
    "",
    "For each eligible pattern that justifies a change, author exactly one proposal: worktree add from refs/remotes/origin/" +
      `${defaultBranch} → edit only under the worktree's skills/ → commit → push origin skill-evolution/<skill>-<slug>. ` +
      "Then call report_proposals once with every proposal (branch, skill, pattern, description) — " +
      "`pattern` is the page filename exactly as listed above. If nothing justifies a change, report an empty list.",
  ].join("\n");

  try {
    await side.run({
      system: proposalSystemPrompt(workspaceRoot, tmpDir, defaultBranch),
      prompt,
      customTools: buildProposalTools({
        workspaceRoot,
        tmpDir,
        defaultBranch,
        remoteBranchNames: async () =>
          new Set((await listRemoteBranchTips(workspaceRoot, REMOTE_BRANCH_PATTERN)).keys()),
        capture,
      }),
      tier: "processor",
      isolatePrompt: true,
    });
  } catch (error) {
    throw new ProposalRunError(
      `proposal agent run failed: ${error instanceof Error ? error.message : String(error)}`,
      { cause: error, proposals: capture.proposals },
    );
  }

  return capture.proposals;
};
