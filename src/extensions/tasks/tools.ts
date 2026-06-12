import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { DefinitionPatch, TaskRepository } from "./repository.ts";
import { formatInTimezone, formatSchedule, parseSchedule } from "./schedule.ts";
import type { TaskDefinitionRecord } from "./schema.ts";

export interface ToolDeps {
  repository: TaskRepository;
  timezone: string | undefined;
  now: () => Date;
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
  { repository, timezone, now }: ToolDeps,
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

  return [
    `Task '${created.name}' created successfully.`,
    `- ID: ${created.id}`,
    `- Type: ${created.taskType}`,
    `- Schedule: ${formatSchedule(created.schedule, timezone)}`,
    `- Enabled: ${created.enabled}`,
  ].join("\n");
};

export const handleUpdateTask = (
  { repository, timezone, now }: ToolDeps,
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

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

/** pi extension factory exposing the task management tools to the agent. */
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
  };
