import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { Logger } from "../../log.ts";
import { type DefinitionPatch, isTerminalStatus, type TaskRepository } from "./repository.ts";
import { formatInTimezone, formatSchedule, parseSchedule } from "./schedule.ts";
import type { TaskDefinitionRecord } from "./schema.ts";

export interface ToolDeps {
  repository: TaskRepository;
  timezone: string | undefined;
  now: () => Date;
  /**
   * Abort an in-flight background run by instance ID. Optional so operational
   * tool handlers that don't need it (and test deps objects) can omit it; the
   * live-abort step is skipped when absent, leaving the DB write as the effect.
   */
  cancelRunningInstance?: (instanceId: string) => Promise<boolean>;
  log: Logger;
}

const SCHEDULE_DESCRIPTION =
  "Cron expression (e.g., '0 9 * * *' for daily at 9 AM), bare ISO datetime interpreted in the configured timezone (e.g., '2026-04-01T15:00:00'), or ISO datetime with 'Z'/offset for an explicit zone";

export const CreateTaskParams = Type.Object({
  name: Type.String({ description: "Human-readable task name" }),
  schedule: Type.String({ description: SCHEDULE_DESCRIPTION }),
  type: StringEnum(["session", "background"] as const, {
    description: "'session' (delivered during idle) or 'background' (isolated execution)",
  }),
  prompt: Type.String({ description: "Instruction the agent follows when the task fires" }),
  enabled: Type.Optional(
    Type.Boolean({ description: "Whether the task is active (default true)" }),
  ),
});

export const UpdateTaskParams = Type.Object({
  task_id: Type.String({ description: "ID of the task to update (get IDs from list_tasks)" }),
  name: Type.Optional(Type.String({ description: "New human-readable name" })),
  schedule: Type.Optional(Type.String({ description: SCHEDULE_DESCRIPTION })),
  task_type: Type.Optional(
    StringEnum(["session", "background"] as const, {
      description: "Change type — 'session' or 'background'",
    }),
  ),
  prompt: Type.Optional(Type.String({ description: "New agent instruction" })),
  enabled: Type.Optional(Type.Boolean({ description: "Enable or disable the task" })),
});

export const ListTasksParams = Type.Object({
  archived: Type.Optional(
    Type.Boolean({
      description: "Set true to show disabled (archived) tasks instead of active ones",
    }),
  ),
});

export const QueryTaskInstancesParams = Type.Object({
  status: Type.Optional(
    StringEnum(["pending", "running", "waiting", "completed", "failed"] as const, {
      description: "Filter by instance status",
    }),
  ),
  task_type: Type.Optional(
    StringEnum(["session", "background"] as const, { description: "Filter by task type" }),
  ),
  definition_id: Type.Optional(Type.String({ description: "Filter by parent task definition ID" })),
  limit: Type.Optional(Type.Number({ description: "Maximum entries to return (default 20)" })),
});

export const GetTaskParams = Type.Object({
  task: Type.String({
    description: "ID or exact name of the task definition to inspect (get IDs from list_tasks)",
  }),
});

export const DeleteTaskParams = Type.Object({
  task: Type.String({
    description: "ID or exact name of the task definition to delete (get IDs from list_tasks)",
  }),
});

export const RunTaskNowParams = Type.Object({
  task: Type.Optional(
    Type.String({
      description:
        "ID or exact name of an existing task definition to run immediately (by-reference mode)",
    }),
  ),
  prompt: Type.Optional(
    Type.String({
      description: "Ad-hoc instruction to run immediately without a stored definition",
    }),
  ),
  type: Type.Optional(
    StringEnum(["session", "background"] as const, {
      description: "Ad-hoc task type — 'session' or 'background' (default 'background')",
    }),
  ),
  name: Type.Optional(
    Type.String({ description: "Human-readable label for an ad-hoc run (ad-hoc mode only)" }),
  ),
});

export const RespondToTaskParams = Type.Object({
  task_instance_id: Type.String({
    description: "ID of the waiting task instance, as given in the input-request notification",
  }),
  response: Type.String({ description: "The user's reply to relay to the waiting task" }),
});

export const StopTaskParams = Type.Object({
  task_instance_id: Type.String({
    description: "ID of the task instance to cancel (get IDs from query_task_instances)",
  }),
});

const describeDefinition = (
  definition: TaskDefinitionRecord,
  timezone: string | undefined,
): string[] => {
  const status = definition.enabled ? "✓ enabled" : "✗ disabled";
  const lastFired =
    definition.lastFiredAt != null
      ? ` (last: ${formatInTimezone(definition.lastFiredAt, timezone)})`
      : "";

  return [
    `- [${definition.id}] **${definition.name}** [${definition.taskType}] ${status}`,
    `  Schedule: ${formatSchedule(definition.schedule, timezone)}${lastFired}`,
    "",
  ];
};

export const handleCreateTask = (
  { repository, timezone, now, log }: ToolDeps,
  args: Static<typeof CreateTaskParams>,
): string => {
  const schedule = parseSchedule(args.schedule, timezone, now());

  const created = repository.createDefinition({
    name: args.name,
    schedule,
    taskType: args.type,
    prompt: args.prompt,
    enabled: args.enabled ?? true,
  });

  log.info(
    { definitionId: created.id, name: created.name, taskType: created.taskType },
    "task definition created",
  );

  return [
    `Task '${created.name}' created successfully.`,
    `- ID: ${created.id}`,
    `- Type: ${created.taskType}`,
    `- Schedule: ${formatSchedule(created.schedule, timezone)}`,
    `- Enabled: ${created.enabled}`,
  ].join("\n");
};

export const handleUpdateTask = (
  { repository, timezone, now, log }: ToolDeps,
  args: Static<typeof UpdateTaskParams>,
): string => {
  const existing = repository.getDefinition(args.task_id);

  if (existing == null) throw new Error(`Task '${args.task_id}' not found.`);

  const patch: DefinitionPatch = {};

  if (args.name != null) patch.name = args.name;
  if (args.prompt != null) patch.prompt = args.prompt;
  if (args.enabled != null) patch.enabled = args.enabled;
  if (args.task_type != null) patch.taskType = args.task_type;

  if (args.schedule != null) {
    patch.schedule = parseSchedule(args.schedule, timezone, now());
    // The old fire time is meaningless for a new schedule — one-shots require a
    // null lastFiredAt to fire, and the cron anchor falls back gracefully.
    patch.lastFiredAt = null;
  }

  if (Object.keys(patch).length === 0) return "No updates provided.";

  repository.updateDefinition(args.task_id, patch);

  log.info({ definitionId: args.task_id, fields: Object.keys(patch) }, "task definition updated");

  return `Task '${args.task_id}' updated successfully.`;
};

export const handleListTasks = (
  { repository, timezone }: ToolDeps,
  args: Static<typeof ListTasksParams>,
): string => {
  const archived = args.archived ?? false;
  const definitions = archived
    ? repository.listDisabledDefinitions()
    : repository.listEnabledDefinitions();

  if (definitions.length === 0) return `No ${archived ? "archived" : "active"} tasks found.`;

  return [
    "# Task Definitions",
    "",
    ...definitions.flatMap((definition) => describeDefinition(definition, timezone)),
  ].join("\n");
};

export const handleQueryTaskInstances = (
  { repository, timezone }: ToolDeps,
  args: Static<typeof QueryTaskInstancesParams>,
): string => {
  const instances = repository.queryInstances({
    status: args.status,
    taskType: args.task_type,
    definitionId: args.definition_id,
    limit: args.limit ?? 20,
  });

  if (instances.length === 0) return "No task instances found.";

  const lines = ["# Task Instances", ""];

  for (const instance of instances) {
    lines.push(
      `- [${instance.id}] [${instance.taskType}] ${instance.status} — scheduled for ${formatInTimezone(instance.scheduledFor, timezone)}`,
    );

    if (instance.definitionId != null) lines.push(`  Definition: ${instance.definitionId}`);
    if (instance.result != null) lines.push(`  Result: ${instance.result}`);

    lines.push("");
  }

  return lines.join("\n");
};

export const handleGetTask = (
  { repository, timezone }: ToolDeps,
  args: Static<typeof GetTaskParams>,
): string => {
  const definition = repository.resolveDefinition(args.task);

  if (definition == null) throw new Error(`Task '${args.task}' not found.`);

  const lastFired =
    definition.lastFiredAt != null ? formatInTimezone(definition.lastFiredAt, timezone) : "never";

  const lines = [
    `# ${definition.name}`,
    "",
    `- ID: ${definition.id}`,
    `- Type: ${definition.taskType}`,
    `- Status: ${definition.enabled ? "✓ enabled" : "✗ disabled"}`,
    `- Schedule: ${formatSchedule(definition.schedule, timezone)}`,
    `- Last run: ${lastFired}`,
    `- Created: ${formatInTimezone(definition.createdAt, timezone)}`,
  ];

  const latest = repository.getLatestInstanceForDefinition(definition.id);

  if (latest != null) {
    lines.push(
      "",
      "## Latest instance",
      `- [${latest.id}] ${latest.status} — scheduled for ${formatInTimezone(latest.scheduledFor, timezone)}`,
    );

    if (latest.result != null) lines.push(`- Result: ${latest.result}`);
  }

  lines.push("", "## Prompt", "", definition.prompt);

  return lines.join("\n");
};

export const handleDeleteTask = (
  { repository, log }: ToolDeps,
  args: Static<typeof DeleteTaskParams>,
): string => {
  const definition = repository.resolveDefinition(args.task);

  if (definition == null) throw new Error(`Task '${args.task}' not found.`);

  repository.deleteDefinition(definition.id);

  log.info(
    { definitionId: definition.id, name: definition.name, taskType: definition.taskType },
    "task definition deleted",
  );

  return `Task '${definition.name}' deleted.`;
};

export const handleRunTaskNow = (
  { repository, now, log }: ToolDeps,
  args: Static<typeof RunTaskNowParams>,
): string => {
  const byReference = args.task != null;
  const adHoc = args.prompt != null;

  if (byReference && adHoc) {
    throw new Error("Provide exactly one of 'task' or 'prompt', not both.");
  }

  if (!byReference && !adHoc) {
    throw new Error("Either 'task' or 'prompt' is required.");
  }

  // Ad-hoc instances carry the prompt directly; by-reference instances snapshot
  // the definition's prompt without mutating the definition (schedule, enabled,
  // and lastFiredAt are untouched, so auto-disabled one-shots still run).
  if (byReference) {
    if (args.name != null) throw new Error("'name' can only be used with 'prompt'.");

    const definition = repository.resolveDefinition(args.task as string);

    if (definition == null) throw new Error(`Task '${args.task}' not found.`);

    const instance = repository.createInstance({
      definitionId: definition.id,
      taskType: definition.taskType,
      prompt: definition.prompt,
      scheduledFor: now(),
    });

    log.info(
      { instanceId: instance.id, taskType: definition.taskType, byReference: true },
      "task run-now queued",
    );

    return `${definition.taskType} task '${definition.name}' queued. Instance ID: ${instance.id}`;
  }

  const taskType = args.type ?? "background";

  const instance = repository.createInstance({
    definitionId: null,
    taskType,
    prompt: args.prompt as string,
    scheduledFor: now(),
  });

  const label = args.name ?? (args.prompt as string).slice(0, 80);

  log.info({ instanceId: instance.id, taskType, byReference: false }, "task run-now queued");

  return `${taskType} task '${label}' queued. Instance ID: ${instance.id}`;
};

export const handleRespondToTask = (
  { repository, log }: ToolDeps,
  args: Static<typeof RespondToTaskParams>,
): string => {
  const response = args.response.trim();

  if (response.length === 0) throw new Error("Response cannot be empty.");

  const instance = repository.getInstance(args.task_instance_id);

  if (instance == null) throw new Error(`Task instance '${args.task_instance_id}' not found.`);

  if (instance.status !== "waiting") throw new Error("Task is not waiting for input.");

  if (instance.userResponse != null) {
    throw new Error("A response is already pending for this task.");
  }

  // Status stays `waiting`; the background runner resumes the instance on its
  // next tick once it sees a userResponse, injecting the reply into the run.
  repository.updateInstance(instance.id, { userResponse: response });

  log.info({ instanceId: instance.id }, "user response relayed to waiting task");

  return "Response sent. The task will resume with your reply.";
};

export const handleStopTask = async (
  { repository, cancelRunningInstance, log }: ToolDeps,
  args: Static<typeof StopTaskParams>,
): Promise<string> => {
  const instance = repository.getInstance(args.task_instance_id);

  if (instance == null) throw new Error(`Task instance '${args.task_instance_id}' not found.`);

  const previousStatus = instance.status;

  if (isTerminalStatus(instance.status)) {
    throw new Error(
      `Task instance '${args.task_instance_id}' is already finished (status: ${instance.status}).`,
    );
  }

  // The initiator owns the terminal write: pending/waiting instances never run
  // again, and a running one is marked failed before its live run is signalled
  // to abort (the executor's abort path writes nothing, so there is no race).
  repository.cancelInstance(instance.id, "Task cancelled by user");

  const aborted = (await cancelRunningInstance?.(args.task_instance_id)) ?? false;

  log.info({ instanceId: instance.id, previousStatus, aborted }, "task instance cancelled by user");

  return `Task instance '${args.task_instance_id}' cancelled (was ${previousStatus}).`;
};

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

/**
 * pi extension factory exposing the operational task management tools. These are
 * safe to bind into background task runs; the interactive `respond_to_task` tool
 * is split out into its own factory so it stays out of background-bound toolsets.
 */
export const createTaskToolsFactory =
  (deps: ToolDeps): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "create_task",
      label: "Create Task",
      description:
        "Create a new scheduled task definition. Tasks fire on a cron schedule (recurring) or at an ISO datetime (one-shot); 'session' tasks are delivered into the conversation during idle time, 'background' tasks run autonomously in an isolated agent.",
      promptSnippet: "Schedule recurring or one-shot tasks (reminders, checks, background work)",
      promptGuidelines: [
        "Use create_task when the user asks to be reminded, to schedule recurring work, or to run something later.",
      ],
      parameters: CreateTaskParams,
      async execute(_toolCallId, params) {
        return textResult(handleCreateTask(deps, params));
      },
    });

    pi.registerTool({
      name: "update_task",
      label: "Update Task",
      description:
        "Update an existing task definition. Only provided fields are updated; omitted fields remain unchanged. Disable a task with enabled=false instead of deleting it.",
      promptSnippet: "Modify or enable/disable an existing scheduled task",
      promptGuidelines: [
        "Use update_task with enabled=false to archive a task the user no longer wants.",
      ],
      parameters: UpdateTaskParams,
      async execute(_toolCallId, params) {
        return textResult(handleUpdateTask(deps, params));
      },
    });

    pi.registerTool({
      name: "list_tasks",
      label: "List Tasks",
      description:
        "List task definitions. Each entry includes the task ID (needed for update_task and query_task_instances), name, type, schedule, and status. Pass archived=true to show disabled tasks.",
      promptSnippet: "List scheduled task definitions",
      promptGuidelines: [
        "Check list_tasks before create_task to avoid scheduling duplicate tasks.",
      ],
      parameters: ListTasksParams,
      async execute(_toolCallId, params) {
        return textResult(handleListTasks(deps, params));
      },
    });

    pi.registerTool({
      name: "get_task",
      label: "Get Task",
      description:
        "Fetch the full details of a single task definition by ID or exact name, including its complete prompt and a summary of its most recent instance.",
      promptSnippet: "Inspect one scheduled task definition in full",
      promptGuidelines: [
        "Use get_task to read a task's full prompt or check its latest run before editing it.",
      ],
      parameters: GetTaskParams,
      async execute(_toolCallId, params) {
        return textResult(handleGetTask(deps, params));
      },
    });

    pi.registerTool({
      name: "delete_task",
      label: "Delete Task",
      description:
        "Permanently delete a task definition by ID or exact name. This cannot be undone — to keep it but stop it firing, use update_task with enabled=false instead.",
      promptSnippet: "Permanently remove a scheduled task definition",
      promptGuidelines: [
        "Prefer update_task with enabled=false over delete_task unless the user wants the task gone for good.",
      ],
      parameters: DeleteTaskParams,
      async execute(_toolCallId, params) {
        return textResult(handleDeleteTask(deps, params));
      },
    });

    pi.registerTool({
      name: "run_task_now",
      label: "Run Task Now",
      description:
        "Run a task immediately, bypassing the schedule. By-reference: pass 'task' (ID or name) of an existing definition — its prompt is snapshotted and the definition is left unchanged. Ad-hoc: pass 'prompt' (and optionally 'type' and a readable 'name') to fire a one-off task with no stored definition. Provide exactly one of 'task' or 'prompt'. The queued instance is dispatched on the next scheduler tick.",
      promptSnippet: "Run a task right now (by reference or ad-hoc)",
      promptGuidelines: [
        "Use run_task_now when the user wants a scheduled task to run immediately, or to fire a one-off background job without scheduling it.",
      ],
      parameters: RunTaskNowParams,
      async execute(_toolCallId, params) {
        return textResult(handleRunTaskNow(deps, params));
      },
    });

    pi.registerTool({
      name: "query_task_instances",
      label: "Query Task Instances",
      description:
        "Query task execution instances (individual firings of task definitions), optionally filtered by status, task type, or parent definition. Use this to check whether and when a task ran and what it produced.",
      promptSnippet: "Inspect past and pending runs of scheduled tasks",
      promptGuidelines: [
        "Use query_task_instances when the user asks whether a scheduled task ran or what its outcome was.",
      ],
      parameters: QueryTaskInstancesParams,
      async execute(_toolCallId, params) {
        return textResult(handleQueryTaskInstances(deps, params));
      },
    });

    pi.registerTool({
      name: "stop_task",
      label: "Stop Task",
      description:
        "Cancel a task instance by ID so it never runs or runs no further. Works on any non-terminal instance (pending, running, or waiting): a running instance has its live execution aborted promptly, while pending/waiting ones are marked failed outright. Already-finished (completed/failed) or unknown instances fail with a clear error. Get the instance ID from query_task_instances or a task notification.",
      promptSnippet: "Cancel a queued or running task instance",
      promptGuidelines: [
        "Use stop_task when a task is doing the wrong thing, running too long, or is no longer needed — instead of waiting for the timeout or restarting.",
      ],
      parameters: StopTaskParams,
      async execute(_toolCallId, params) {
        return textResult(await handleStopTask(deps, params));
      },
    });
  };

/**
 * pi extension factory exposing the interactive `respond_to_task` tool. Kept
 * apart from the operational factory so it is never bound into background runs:
 * a background task answering another instance's waiting question is a footgun.
 */
export const createTaskInteractiveToolsFactory =
  (deps: ToolDeps): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "respond_to_task",
      label: "Respond to Task",
      description:
        "Relay the user's reply to a background task that paused to ask a question (status 'waiting'). Pass the task_instance_id from the input-request notification and the user's response. The task resumes automatically with the reply injected. Errors if the instance is not waiting or already has a pending response.",
      promptSnippet: "Answer a background task waiting for user input",
      promptGuidelines: [
        "Use respond_to_task when a notification says a background task is waiting for input and the user provides their answer.",
      ],
      parameters: RespondToTaskParams,
      async execute(_toolCallId, params) {
        return textResult(handleRespondToTask(deps, params));
      },
    });
  };
