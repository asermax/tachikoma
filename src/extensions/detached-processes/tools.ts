import { constants } from "node:os";

import { StringEnum } from "@earendil-works/pi-ai";
import { type ExtensionFactory, truncateTail } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { Logger } from "../../log.ts";
import { type ScopeInspector, scopeUnitName } from "./cgroup.ts";
import type { ProcessLimiter } from "./limits.ts";
import { readOutputTail } from "./output.ts";
import { type ProcessNotification, type ReconcileDeps, reconcileExit } from "./reconcile.ts";
import type { ProcessRepository } from "./repository.ts";
import { type DetachedProcessRecord, STOP_REASON_OOM_KILLED } from "./schema.ts";
import { isAlive, spawnProcess, terminate } from "./spawn.ts";

export interface ProcessToolDeps {
  repository: ProcessRepository;
  limiter: ProcessLimiter;
  processesDir: string;
  notify: (notification: ProcessNotification) => void;
  scopeInspector: ScopeInspector;
  defaultMemoryLimitMb: number | null;
  log: Logger;
  now?: () => Date;
}

const reconcileDeps = (deps: ProcessToolDeps): ReconcileDeps => ({
  repository: deps.repository,
  processesDir: deps.processesDir,
  notify: deps.notify,
  scopeInspector: deps.scopeInspector,
  log: deps.log,
  now: deps.now,
});

export const DispatchProcessParams = Type.Object({
  name: Type.String({ description: "Display label for the process (non-unique)" }),
  command: Type.String({ description: "Shell command to run (supports pipes, &&, etc.)" }),
  cwd: Type.Optional(Type.String({ description: "Working directory (defaults to Tachikoma's)" })),
  env: Type.Optional(
    Type.Record(Type.String(), Type.String(), {
      description: "Environment variable overrides merged onto the OS environment",
    }),
  ),
  memory_limit_mb: Type.Optional(
    Type.Number({ description: "Memory limit in MB (overrides the configured default)" }),
  ),
});

export const QueryProcessParams = Type.Object({
  process_id: Type.Optional(
    Type.String({ description: "ID of a specific process to inspect; omit to list" }),
  ),
  archived: Type.Optional(
    Type.Boolean({ description: "When listing, set true to show exited processes" }),
  ),
});

export const ReadProcessOutputParams = Type.Object({
  process_id: Type.String({ description: "ID of the process" }),
  stream: Type.Optional(
    StringEnum(["stdout", "stderr"] as const, {
      description: "Which output stream to read (default stdout)",
    }),
  ),
});

export const TerminateProcessParams = Type.Object({
  process_id: Type.String({ description: "ID of the process to terminate" }),
  signal: Type.Optional(
    Type.String({ description: "Signal name (default SIGTERM), e.g. SIGTERM, SIGINT, SIGKILL" }),
  ),
  grace_seconds: Type.Optional(
    Type.Number({
      description: "Seconds before escalating to SIGKILL (default 10); 0 = signal and return",
    }),
  ),
});

const notFound = (processId: string): Error => new Error(`Process '${processId}' not found.`);

const describeProcess = (record: DetachedProcessRecord): string[] => {
  const lines = [
    `- [${record.id}] **${record.name}** (pid ${record.pid}) ${record.status}`,
    `  Command: ${record.command}`,
    `  Started: ${record.startedAt.toISOString()}`,
  ];

  if (record.exitedAt != null) {
    const oomSuffix = record.stopReason === STOP_REASON_OOM_KILLED ? ", OOM-killed" : "";
    lines.push(
      `  Exited: ${record.exitedAt.toISOString()} (code: ${record.exitCode ?? "unknown"}${oomSuffix})`,
    );
  }

  lines.push("");
  return lines;
};

export const handleDispatchProcess = async (
  deps: ProcessToolDeps,
  args: Static<typeof DispatchProcessParams>,
): Promise<string> => {
  if (args.memory_limit_mb != null && args.memory_limit_mb < 1) {
    throw new Error(`Invalid memory_limit_mb: ${args.memory_limit_mb}. Minimum value is 1.`);
  }

  const record = await spawnProcess(deps, {
    name: args.name,
    command: args.command,
    cwd: args.cwd,
    env: args.env,
    memoryLimitMb: args.memory_limit_mb ?? deps.defaultMemoryLimitMb,
  });

  return [
    `Process '${record.name}' started.`,
    `- ID: ${record.id}`,
    `- PID: ${record.pid}`,
    `- Stdout: ${record.stdoutPath}`,
    `- Stderr: ${record.stderrPath}`,
  ].join("\n");
};

export const handleQueryProcess = async (
  deps: ProcessToolDeps,
  args: Static<typeof QueryProcessParams>,
): Promise<string> => {
  const { repository } = deps;

  if (args.process_id != null) {
    let record = repository.get(args.process_id);

    if (record == null) throw notFound(args.process_id);

    if (record.status === "running" && !isAlive(record.pid)) {
      await reconcileExit(reconcileDeps(deps), record.id);
      record = repository.get(args.process_id);

      if (record == null) throw notFound(args.process_id);
    }

    const lines = [
      `# ${record.name}`,
      "",
      `- ID: ${record.id}`,
      `- Status: ${record.status}`,
      `- PID: ${record.pid}`,
      `- Command: ${record.command}`,
      `- CWD: ${record.cwd}`,
      `- Stdout: ${record.stdoutPath}`,
      `- Stderr: ${record.stderrPath}`,
      `- Started: ${record.startedAt.toISOString()}`,
    ];

    if (record.exitedAt != null) {
      lines.push(`- Exited: ${record.exitedAt.toISOString()}`);
      lines.push(`- Exit code: ${record.exitCode ?? "unknown"}`);

      if (record.stopReason === STOP_REASON_OOM_KILLED) lines.push("- Stopped: OOM-killed");
    }

    if (record.memoryLimitMb != null) {
      lines.push(`- Memory limit: ${record.memoryLimitMb}MB`);

      if (record.status === "running") {
        const usedMb = await deps.scopeInspector.readMemoryCurrentMb(scopeUnitName(record.id));

        if (usedMb != null) lines.push(`- Memory usage: ${usedMb}MB`);
      }
    }

    return lines.join("\n");
  }

  let records: DetachedProcessRecord[];

  if (args.archived === true) {
    records = repository.listExited();
  } else {
    records = [];

    for (const record of repository.listRunning()) {
      if (isAlive(record.pid)) {
        records.push(record);
      } else {
        await reconcileExit(reconcileDeps(deps), record.id);
      }
    }
  }

  if (records.length === 0) {
    return `No ${args.archived === true ? "exited" : "running"} processes found.`;
  }

  return ["# Detached Processes", "", ...records.flatMap(describeProcess)].join("\n");
};

export const handleReadProcessOutput = async (
  deps: ProcessToolDeps,
  args: Static<typeof ReadProcessOutputParams>,
): Promise<string> => {
  const record = deps.repository.get(args.process_id);

  if (record == null) throw notFound(args.process_id);

  const path = args.stream === "stderr" ? record.stderrPath : record.stdoutPath;
  const raw = await readOutputTail(path);

  if (raw == null || raw === "") return "No output yet.";

  const { content, truncated } = truncateTail(raw);

  return truncated ? `[earlier output truncated]\n${content}` : content;
};

export const handleTerminateProcess = async (
  deps: ProcessToolDeps,
  args: Static<typeof TerminateProcessParams>,
): Promise<string> => {
  const { repository, log } = deps;
  const graceSeconds = args.grace_seconds ?? 10;

  let record = repository.get(args.process_id);

  if (record == null) throw notFound(args.process_id);

  if (record.status === "running" && !isAlive(record.pid)) {
    // Lazy reconcile — stop-initiated exits don't notify the user.
    await reconcileExit(reconcileDeps(deps), record.id, { dispatchNotification: false });
    record = repository.get(args.process_id);

    if (record == null) throw notFound(args.process_id);

    return `Process '${record.name}' already stopped (exit code: ${record.exitCode ?? "unknown"}).`;
  }

  if (record.status === "exited") {
    return `Process '${record.name}' already stopped (exit code: ${record.exitCode ?? "unknown"}).`;
  }

  const signal = (args.signal ?? "SIGTERM") as NodeJS.Signals;

  if (constants.signals[signal] == null) throw new Error(`Unknown signal: ${args.signal}`);

  try {
    repository.markStopInitiated(record.id);
  } catch (error) {
    log.error({ id: record.id, err: error }, "failed to mark stop initiated — signalling anyway");
  }

  try {
    await terminate(record, log, { signal, graceSeconds });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EPERM") {
      try {
        repository.clearStopReason(record.id);
      } catch {
        // Best effort — a stale stop reason only suppresses one notification.
      }

      throw new Error(`Permission denied: cannot signal process ${record.pid}.`);
    }

    throw error;
  }

  // grace 0 is fire-and-forget — the watcher will observe the exit.
  if (graceSeconds === 0) return `Signal sent to process '${record.name}'.`;

  await reconcileExit(reconcileDeps(deps), record.id, { dispatchNotification: false });

  const updated = repository.get(args.process_id);

  return `Process '${record.name}' stopped (exit code: ${updated?.exitCode ?? "unknown"}).`;
};

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

/** pi extension factory exposing the detached process management tools to the agent. */
export const createProcessToolsFactory =
  (deps: ProcessToolDeps): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "dispatch_detached_process",
      label: "Dispatch Detached Process",
      description:
        "Start a detached shell command that runs independently of Tachikoma and survives restarts. Output is captured to per-process stdout/stderr log files. Returns the record ID, PID, and log paths.",
      promptSnippet: "Run long-lived shell commands detached from the conversation",
      promptGuidelines: [
        "Use dispatch_detached_process for long-running work (servers, builds, downloads) instead of blocking on bash.",
      ],
      parameters: DispatchProcessParams,
      async execute(_toolCallId, params) {
        return textResult(await handleDispatchProcess(deps, params));
      },
    });

    pi.registerTool({
      name: "query_process",
      label: "Query Process",
      description:
        "Inspect detached processes. With process_id, returns full details for that process; without it, lists running processes (archived=true lists exited ones). Each entry includes the ID needed by the other process tools.",
      promptSnippet: "List or inspect detached processes",
      promptGuidelines: [
        "Check query_process before dispatch_detached_process to avoid starting duplicate work.",
      ],
      parameters: QueryProcessParams,
      async execute(_toolCallId, params) {
        return textResult(await handleQueryProcess(deps, params));
      },
    });

    pi.registerTool({
      name: "read_process_output",
      label: "Read Process Output",
      description:
        "Read the tail of a detached process's captured output. Defaults to stdout; pass stream='stderr' for the error stream. Large logs are truncated to the most recent output.",
      promptSnippet: "Read captured output of a detached process",
      promptGuidelines: [
        "Use read_process_output to check on a detached process's progress or failures.",
      ],
      parameters: ReadProcessOutputParams,
      async execute(_toolCallId, params) {
        return textResult(await handleReadProcessOutput(deps, params));
      },
    });

    pi.registerTool({
      name: "terminate_process",
      label: "Terminate Process",
      description:
        "Stop a running detached process: sends the signal (default SIGTERM) to its process group and escalates to SIGKILL after grace_seconds. Reports 'already stopped' without error when the process has exited.",
      promptSnippet: "Stop a running detached process",
      promptGuidelines: [
        "Use terminate_process when the user asks to stop, cancel, or kill a detached process.",
      ],
      parameters: TerminateProcessParams,
      async execute(_toolCallId, params) {
        return textResult(await handleTerminateProcess(deps, params));
      },
    });
  };
