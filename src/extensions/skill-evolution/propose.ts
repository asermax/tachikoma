import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve, sep } from "node:path";

import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import { ALLOWED_GIT_SUBCOMMANDS, refusal, runGitTool, textResult } from "../../git/agent-tools.ts";
import { listRemoteBranchTips } from "../../git/remote.ts";
import type { Logger } from "../../log.ts";
import { builtinSkillsDir } from "../../util/builtin-skills.ts";
import { fileExists } from "../../util/markdown-store.ts";
import type { Runner } from "./analyze.ts";
import { skillEvolutionDir } from "./layout.ts";
import { AUTHORING_GUIDE_SKILLS, proposalSystemPrompt } from "./prompts.ts";
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

/**
 * The proposal-branch namespace and shape (R9): `skill-evolution/<skill>-<slug>`, the slug segment
 * lowercase letters/digits/single hyphens, starting with a letter or digit. Pinned in the design —
 * the push guard, the worktree `-b` guard, and the sweep's local-branch listing all key on it.
 */
export const BRANCH_NAME_PATTERN = /^skill-evolution\/[a-z0-9][a-z0-9-]*$/;

/** Prefix every proposal branch carries — also the `ls-remote` pattern the stages scan with. */
export const BRANCH_NAMESPACE = "skill-evolution/";

/**
 * The feature's stable worktree area under the OS temp dir — worktrees are scratch space that
 * lives outside the workspace repo entirely, so nothing proposal-side can dirty the live tree.
 * The path is stable per workspace (a hash of the root): the sweep must find one run's orphans
 * from the next run, which a fresh `mkdtemp` per run would break.
 */
export const proposalTmpDir = (workspaceRoot: string): string =>
  join(
    tmpdir(),
    "tachikoma-skill-evolution",
    createHash("sha256").update(resolve(workspaceRoot)).digest("hex").slice(0, 16),
  );

/** The proposal-namespace glob: the `ls-remote` pattern for the remote view, the `branch --list` glob for the local sweep. */
export const REMOTE_BRANCH_PATTERN = `${BRANCH_NAMESPACE}*`;

/**
 * One entry of the agent's `report_proposals` call — its self-report, pre-verification. The
 * `problem`/`rootCause`/`evidence` triple is the proposal's reasoning, restated from the acted-on
 * pattern page and tailored to the authored change: it rides the reporting dispatch to the
 * reviewer-facing surface and is never persisted to the impact log.
 */
export interface ReportedProposal {
  branch: string;
  /** Skill directory name under `skills/` — the skill the proposal adds to, edits, or removes. */
  skill: string;
  /** Pattern page filename within the store dir, exactly as the prompt listed it. */
  pattern: string;
  /** What the change does — one line. */
  description: string;
  /** The observable problem the change fixes — the pattern page's Problem section. */
  problem: string;
  /**
   * The gap in the skill's guidance or bundled tooling producing the problem — the page's Root
   * cause section.
   */
  rootCause: string;
  /** The dated observations backing the pattern — the page's Evidence bullets, condensed. */
  evidence: string;
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

const DeletePathParams = Type.Object({
  path: Type.String({
    description:
      "Path of the file or directory to delete, inside a worktree under the tmp dir (directories delete recursively)",
  }),
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
        "Working directory for the git command (defaults to the workspace root). To target a proposal worktree, set it to that worktree's directory under the tmp dir — absolute, since a relative path resolves against different bases for different subcommands. add and commit accept only a worktree path under the tmp dir.",
    }),
  ),
});

const ReportParams = Type.Object({
  proposals: Type.Array(
    Type.Object({
      branch: Type.String({
        description: "The pushed proposal branch, e.g. skill-evolution/deploy-add-env-flag",
      }),
      skill: Type.String({
        description:
          "The skill directory name under skills/ — the one added to, edited, or removed",
      }),
      pattern: Type.String({
        description: "The pattern page filename exactly as given in the prompt",
      }),
      description: Type.String({ description: "One-line summary of the proposed change" }),
      problem: Type.String({
        description:
          "The observable problem this change fixes — the pattern page's Problem section, restated for the change you authored (a few lines)",
      }),
      rootCause: Type.String({
        description:
          "The gap in the skill's guidance or bundled tooling that produced the problem — the pattern page's Root cause section, restated for the change you authored (a few lines)",
      }),
      evidence: Type.String({
        description:
          "The dated observations backing the pattern — the pattern page's Evidence bullets, condensed to the entries that justify this change",
      }),
    }),
    { description: "Every proposal authored this run, in push order" },
  ),
});

/** The report fields the blank check tests — `branch` has its own pattern check instead. */
const REPORT_REQUIRED_FIELDS = [
  "skill",
  "pattern",
  "description",
  "problem",
  "rootCause",
  "evidence",
] as const;

/**
 * The working-tree-mutating pair — the only subcommands whose `path` is checked. `add`/`commit`
 * stage and commit whatever tree their cwd resolves to, so they refuse to run anywhere but
 * inside a proposal worktree — and they execute at exactly the validated path, so a relative
 * `path` can never validate against the tmp dir while git runs against the workspace. R8's "the
 * live working tree is never touched" is tool-enforced, not prompt-enforced. (A missing `path`
 * defaults the cwd to the workspace root, i.e. the live tree — hence the requirement.)
 */
const WORKTREE_REQUIRED_SUBCOMMANDS = new Set(["add", "commit"]);

/**
 * Whether a directory already known to sit under the tmp dir is inside a proposal worktree: a
 * worktree carries its own `.git` marker, while the tmp dir itself has none. The walk stops at
 * the tmp dir boundary so the main repo's `.git` can never satisfy the check.
 */
const isInsideWorktree = async (tmpDir: string, path: string): Promise<boolean> => {
  for (let dir = resolve(path); ; dir = dirname(dir)) {
    if (await fileExists(join(dir, ".git"))) return true;
    if (dir === resolve(tmpDir) || dir === dirname(dir)) return false;
  }
};

/**
 * Validate a `worktree` invocation: `add` must carry `-b <branch>` matching the naming pattern and
 * a worktree path under the tmp dir (plus an optional base commit-ish — nothing else); `remove`
 * takes exactly one path under the tmp dir. Positionals are resolved against `cwd` — the base git
 * itself uses — before the containment check, so validation and execution can never disagree
 * about where a relative path lands.
 */
const validateWorktreeArgs = (
  tmpDir: string,
  cwd: string,
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
    if (
      tail.length !== 1 ||
      tail[0] === undefined ||
      resolveUnderRoot(tmpDir, resolve(cwd, tail[0])) == null
    ) {
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

  // Resolve the positional the way git will (against the run's cwd) before the containment
  // check — otherwise a relative path could validate against the tmp dir while git creates the
  // worktree inside the live tree.
  if (resolveUnderRoot(tmpDir, resolve(cwd, worktreePath)) == null) {
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
 * Build the proposal agent's entire tool surface — exactly six tools, bound to one workspace and
 * one tmp dir so the agent can never operate on another tree. The file tools refuse any path that
 * escapes the tmp dir; `git` treats `path` as a plain cwd, validated to name a worktree under the
 * tmp dir only for `add`/`commit` — and never via `-C` string surgery. Editor/gpg are disabled
 * exactly like the commit agent so an unattended commit in a signing-configured repo cannot hang.
 */
export const buildProposalTools = (deps: ProposalToolDeps): ToolDefinition[] => {
  const { workspaceRoot, tmpDir, defaultBranch, remoteBranchNames, capture } = deps;
  const pathsGuidance = `Only paths inside ${tmpDir} are allowed.`;
  const tmpRoot = resolve(tmpDir);

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
      name: "delete_path",
      label: "Delete path",
      description:
        "Delete a file or directory (recursively) inside a proposal worktree — how a removal is authored, from redundant guidance or a broken script to a whole skill directory.",
      parameters: DeletePathParams,
      async execute(_id, params) {
        const path = guardPath(params.path);

        if (path == null) return refusal(`\`${params.path}\` escapes the tmp dir.`, pathsGuidance);

        // The guard is root-inclusive; the tmp dir itself holds every worktree, so deleting it
        // is always a mistake, never a proposal.
        if (path === tmpRoot) {
          return refusal(
            "`delete_path` cannot remove the tmp dir itself.",
            `${pathsGuidance} Delete paths inside a proposal worktree, not the worktree area root.`,
          );
        }

        if (!(await fileExists(path))) {
          return refusal(
            `\`${params.path}\` does not exist.`,
            "List the worktree (`list_dir`) and delete a path that is there.",
          );
        }

        await rm(path, { recursive: true, force: true });
        return textResult(`Deleted ${params.path}`);
      },
    } satisfies ToolDefinition<typeof DeletePathParams>,

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
        "Run a git command in the workspace repository. Allowed: worktree add (path under the tmp dir, -b skill-evolution/<name>), worktree remove, status, diff, log, show, add, commit, and push origin <branch> — new skill-evolution/* branches only, no force. Commands run with `path` as their working directory (the workspace root when it is omitted); add and commit are refused unless `path` names a worktree under the tmp dir.",
      parameters: GitParams,
      async execute(_id, params) {
        const [subcommand, ...rest] = params.args;

        if (subcommand == null || subcommand.startsWith("-")) {
          return refusal(
            `git ${subcommand ?? ""} is not allowed.`,
            "Use only worktree, status, diff, log, show, add, commit, or push origin <branch>.",
          );
        }

        // `path` is a plain cwd for the inspection commands (never -C string surgery); the
        // mutating pair below validates it and then runs exactly where it validated.
        let cwd = params.path == null ? workspaceRoot : resolve(workspaceRoot, params.path);

        if (subcommand === "worktree") {
          const verdict = validateWorktreeArgs(tmpDir, cwd, rest);
          if (!verdict.ok) return verdict.result;
        } else if (subcommand === "push") {
          const verdict = await validatePushArgs(defaultBranch, remoteBranchNames, params.args);
          if (!verdict.ok) return verdict.result;
        } else if (!ALLOWED_GIT_SUBCOMMANDS.has(subcommand)) {
          return refusal(
            `git ${subcommand} is not allowed.`,
            "Use only worktree, status, diff, log, show, add, commit, or push origin <branch>.",
          );
        }

        // The staging/committing pair is confined to a worktree — the one case `path` is
        // checked: under the tmp dir and inside a worktree, and executed AT the validated
        // path, so the live tree stays untouched no matter what the agent tries (R8, enforced
        // here rather than by prompt).
        if (WORKTREE_REQUIRED_SUBCOMMANDS.has(subcommand)) {
          const bound = params.path == null ? null : resolveUnderRoot(tmpDir, params.path);

          if (bound == null) {
            return refusal(
              `git ${subcommand} requires the path parameter set to a worktree under ${tmpDir}.`,
              pathsGuidance,
            );
          }

          if (!(await isInsideWorktree(tmpDir, bound))) {
            return refusal(
              `\`${params.path}\` is not inside a proposal worktree.`,
              `Set path to the worktree directory (or a subdirectory of it) under ${tmpDir}.`,
            );
          }

          cwd = bound;
        }

        return textResult(await runGitTool(cwd, params.args, ["commit.gpgsign=false"]));
      },
    } satisfies ToolDefinition<typeof GitParams>,

    {
      name: "report_proposals",
      label: "Report proposals",
      description:
        "Report the proposals you authored — the required terminal step of this run. One entry per pushed branch: branch, skill (directory name), pattern (the pattern page filename from the prompt), description (one line), and the proposal's reasoning restated from that pattern page — problem, rootCause, evidence — tailored to the change you authored.",
      parameters: ReportParams,
      async execute(_id, params) {
        for (const proposal of params.proposals) {
          if (!BRANCH_NAME_PATTERN.test(proposal.branch)) {
            return refusal(
              `branch name \`${proposal.branch}\` does not match ${BRANCH_NAME_PATTERN.source}.`,
              "Fix the entry and report again — every field must be non-empty and the branch must be a pushed skill-evolution/* branch.",
            );
          }

          // Whitespace-only is as blank as absent (the tasks `update_goal` trim rule): every
          // field carries meaning downstream, so the refusal names the offenders.
          const empty = REPORT_REQUIRED_FIELDS.filter((field) => proposal[field].trim() === "");
          if (empty.length > 0) {
            return refusal(
              `a proposal entry has an empty ${empty.join(", ")}.`,
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

  const entries = (await readdir(skillsDir, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory())
    .sort((a, b) => a.name.localeCompare(b.name));

  const blocks = await Promise.all(
    entries.map(async (entry) => {
      const skillPath = join(skillsDir, entry.name, "SKILL.md");
      const body = (await fileExists(skillPath))
        ? await readFile(skillPath, "utf8")
        : "(no SKILL.md — a skill directory without guidance)";

      return `### skills/${entry.name}\n\n${body}`;
    }),
  );

  if (blocks.length === 0) {
    log.debug({ skillsDir }, "workspace has no skill directories");
    return "(skills/ is empty — new-skill proposals are the only possibility)";
  }

  return blocks.join("\n\n");
};

/**
 * Run the proposal agent (S7): one context-free headless run over the eligible pattern pages, the
 * impact log, and the workspace skills inventory. `isolatePrompt: true` over `run`'s default empty
 * built-in allowlist makes the six custom tools the entire surface — no grafted context files;
 * the only skills in the run are the two authoring guides, discovered via `skillPaths` and
 * force-loaded through pi's catalog (`forceLoadSkills`). Returns the captured `report_proposals`
 * list (possibly empty). A dying run throws a {@link ProposalRunError} carrying the partial
 * capture — the caller still verifies whatever pushed-and-reported before the death, and its
 * verify-and-sweep `finally` runs.
 */
export const runProposalAgent = async (deps: ProposalAgentDeps): Promise<ReportedProposal[]> => {
  const { side, workspaceRoot, tmpDir, defaultBranch, eligible, impactLog, log } = deps;
  const capture: ProposalCapture = { proposals: [] };

  // The two prompt sections read disjoint directories — build them concurrently.
  const storeDir = skillEvolutionDir(workspaceRoot);
  const inventory = skillsInventorySection(workspaceRoot, log);
  const pages = await Promise.all(eligible.map((name) => patternPageSection(storeDir, name, log)));
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
    await inventory,
    "",
    "## Task",
    "",
    "For each eligible pattern that justifies a change, author exactly one proposal: worktree add from refs/remotes/origin/" +
      `${defaultBranch} → change only under the worktree's skills/ (create, edit, or delete files) → commit → push origin skill-evolution/<skill>-<slug>. ` +
      "Then call report_proposals once with every proposal (branch, skill, pattern, description, problem, rootCause, evidence) — " +
      "`pattern` is the page filename exactly as listed above, and `problem`/`rootCause`/`evidence` restate that page's analysis for the change you authored. " +
      "If nothing justifies a change, report an empty list.",
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
      // The authoring guides ride the run as actual skills: the loader discovers them from the
      // bundled dir (isolation-composable `skillPaths`) and their bodies are force-loaded from
      // pi's catalog on the run's single prompt — no guide content is assembled here.
      skillPaths: [builtinSkillsDir],
      forceLoadSkills: [...AUTHORING_GUIDE_SKILLS],
    });
  } catch (error) {
    throw new ProposalRunError(
      `proposal agent run failed: ${error instanceof Error ? error.message : String(error)}`,
      { cause: error, proposals: capture.proposals },
    );
  }

  return capture.proposals;
};
