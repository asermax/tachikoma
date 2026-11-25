import { describe, expect, it } from "vitest";

import { ConfigError, parseWithSchema } from "../../src/config/parse.ts";
import { ConfigSchema, ThinkingLevelSchema } from "../../src/config/schema.ts";
import { ReadProcessOutputParams } from "../../src/extensions/detached-processes/tools.ts";
import { NotifyUserParams } from "../../src/extensions/notifications/tools.ts";
import { UpdateGoalParams } from "../../src/extensions/tasks/executor.ts";
import {
  CreateTaskParams,
  QueryTaskInstancesParams,
  RunTaskNowParams,
  UpdateTaskParams,
} from "../../src/extensions/tasks/tools.ts";
import { EndActionSchema, UpdateActionSchema } from "../../src/extensions/workflows/tools.ts";
import { StringEnum } from "../../src/util/string-enum.ts";

/**
 * The JSON pi-ai 0.84.2's `StringEnum` produced for each string union in the tree, captured on
 * 2026-11-25, before `src/util/string-enum.ts` replaced it. Providers receive this JSON
 * verbatim, so the builder has to reproduce it exactly; these literals are that record.
 */
const CAPTURED = {
  thinkingLevel: {
    type: "string",
    enum: ["off", "minimal", "low", "medium", "high", "xhigh", "max"],
    default: "medium",
  },
  rotateFrequency: {
    type: "string",
    enum: ["hourly", "daily"],
    default: "daily",
  },
  severity: {
    type: "string",
    enum: ["info", "warning", "urgent"],
    description:
      "Sets delivery priority: 'urgent' jumps the queue with the shortest wait, 'warning' is mid, 'info' (default) is lowest. All wait for a conversation pause and may be batched into a digest",
  },
  taskType: {
    type: "string",
    enum: ["session", "background"],
    description: "'session' (delivered during idle) or 'background' (isolated execution)",
  },
  taskStatus: {
    type: "string",
    enum: ["pending", "running", "waiting", "completed", "failed"],
    description: "Filter by instance status",
  },
  workflowAction: {
    type: "string",
    enum: ["start", "complete", "skip"],
    description: "Transition to apply to the step",
  },
  processStream: {
    type: "string",
    enum: ["stdout", "stderr"],
    description:
      "Which output stream to read — defaults to both; set 'stdout' or 'stderr' to read only one",
  },
  goalStatus: {
    type: "string",
    enum: ["completed", "not_completable"],
  },
  updateTaskType: {
    type: "string",
    enum: ["session", "background"],
    description: "Change type — 'session' or 'background'",
  },
  queryTaskType: {
    type: "string",
    enum: ["session", "background"],
    description: "Filter by task type",
  },
  runTaskNowType: {
    type: "string",
    enum: ["session", "background"],
    description: "Ad-hoc task type — 'session' or 'background' (default 'background')",
  },
  endWorkflowAction: {
    type: "string",
    enum: ["complete", "abort"],
    description: "Whether the workflow ended successfully or was abandoned",
  },
  noOptions: {
    type: "string",
    enum: ["a", "b"],
  },
  emptyDefault: {
    type: "string",
    enum: ["", "a"],
  },
};

// What a provider receives: the serialized schema, read back as plain data.
const serialized = (schema: unknown): unknown => JSON.parse(JSON.stringify(schema));

describe("StringEnum", () => {
  it("serializes to type and enum when given no options", () => {
    expect(serialized(StringEnum(["a", "b"]))).toEqual(CAPTURED.noOptions);
  });

  it.each(Object.entries(CAPTURED))(
    "reproduces the JSON pi-ai 0.84.2 produced for %s",
    (_field, captured) => {
      const { enum: values, ...options } = captured as {
        enum: string[];
        description?: string;
        default?: string;
      };

      expect(serialized(StringEnum(values, options))).toEqual(captured);
    },
  );

  it("emits type, enum, description and default in that order", () => {
    const schema = StringEnum(["a", "b"], { description: "d", default: "a" });

    expect(JSON.stringify(schema)).toBe(
      '{"type":"string","enum":["a","b"],"description":"d","default":"a"}',
    );
  });

  it("drops a falsy description or default instead of emitting it", () => {
    expect(serialized(StringEnum(["a", "b"], { description: "" }))).toEqual(CAPTURED.noOptions);
    expect(serialized(StringEnum(["", "a"], { default: "" }))).toEqual(CAPTURED.emptyDefault);
  });
});

describe("parseWithSchema", () => {
  it("applies an enum default and keeps a member", () => {
    const defaults = parseWithSchema(ConfigSchema, {}, "config");
    const hourly = parseWithSchema(
      ConfigSchema,
      { logging: { rotateFrequency: "hourly" } },
      "config",
    );

    expect(defaults.logging.rotateFrequency).toBe("daily");
    expect(hourly.logging.rotateFrequency).toBe("hourly");
  });

  it("rejects a value outside the enum with a ConfigError naming the path", () => {
    const load = () =>
      parseWithSchema(ConfigSchema, { logging: { rotateFrequency: "weekly" } }, "config");

    expect(load).toThrow(ConfigError);
    expect(load).toThrow("/logging/rotateFrequency");
  });
});

describe("the tree's enum fields", () => {
  // Each was built by pi-ai's StringEnum until the six import sites switched to Tachikoma's.
  it.each([
    ["thinking level", ThinkingLevelSchema, CAPTURED.thinkingLevel],
    [
      "log rotation frequency",
      ConfigSchema.properties.logging.properties.rotateFrequency,
      CAPTURED.rotateFrequency,
    ],
    ["notification severity", NotifyUserParams.properties.severity, CAPTURED.severity],
    ["task type", CreateTaskParams.properties.type, CAPTURED.taskType],
    ["task status", QueryTaskInstancesParams.properties.status, CAPTURED.taskStatus],
    ["workflow action", UpdateActionSchema, CAPTURED.workflowAction],
    ["process output stream", ReadProcessOutputParams.properties.stream, CAPTURED.processStream],
    ["goal status", UpdateGoalParams.properties.status, CAPTURED.goalStatus],
    ["update_task task_type", UpdateTaskParams.properties.task_type, CAPTURED.updateTaskType],
    [
      "query_task_instances task_type",
      QueryTaskInstancesParams.properties.task_type,
      CAPTURED.queryTaskType,
    ],
    ["run_task_now type", RunTaskNowParams.properties.type, CAPTURED.runTaskNowType],
    ["end_workflow action", EndActionSchema, CAPTURED.endWorkflowAction],
  ])("%s still serializes to the JSON pi-ai 0.84.2 produced", (_field, schema, captured) => {
    expect(serialized(schema)).toEqual(captured);
  });
});
