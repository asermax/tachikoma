import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import type { ToolCall } from "@earendil-works/pi-ai";
import { validateToolArguments } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { selectExtensionFactories } from "../src/agent/manager.ts";
import { parseWithSchema } from "../src/config/parse.ts";
import { ConfigSchema } from "../src/config/schema.ts";
import { createDatabase, runMigrations } from "../src/db/index.ts";
import { EventBus } from "../src/events.ts";
import { BranchMatchSchema } from "../src/extensions/boundary/related.ts";
import { ExtensionHost, type HostServices } from "../src/extensions/host.ts";
import { firstPartyExtensions } from "../src/extensions/index.ts";
import { createRegistrations, type Registrations } from "../src/extensions/registrations.ts";
import { buildProposalTools } from "../src/extensions/skill-evolution/propose.ts";
import { SkillSelectionSchema } from "../src/extensions/skills/suggest.ts";
import { AskUserParams, UpdateGoalParams } from "../src/extensions/tasks/executor.ts";
import type { AgentRunner } from "../src/git/agent-tools.ts";
import { createCommitAgent } from "../src/git/commit-agent.ts";
import { createGitResolver } from "../src/git/resolve.ts";
import type { Logger } from "../src/log.ts";
import { StringEnum } from "../src/util/string-enum.ts";
import { Workspace } from "../src/workspace.ts";

/**
 * The schema guard (DLT-186 S4, R15). pi validates tool arguments as JSON Schema, so a schema that
 * is anything else — a TypeBox kind JSON cannot express, a class instance, a function — compiles
 * and type-checks. The model's call is no reliable check either: pi-schema converts a `bigint`
 * parameter (`"42"` to `42n`, `null` to `0n`) and takes `null` for `Type.Undefined()` as
 * `undefined`, so the parameter behaves in a way JSON Schema does not define, and only the
 * JavaScript-only kinds, such as `Type.Void()` and `Type.Function()`, reject every argument. This
 * suite makes each of them a test failure by collecting every schema Tachikoma hands to pi or to
 * its own validators.
 *
 * Nothing here runs a tool, a model or a session: the extensions load on a stubbed host, each
 * factory binds against a recording `pi` whose only job is to keep what is registered, and the
 * headless agents' builders hand their tools to a recording `run`.
 */

/** The three places a schema meets a validator (the design's Modeling table). */
type SchemaFamily = "tool parameters" | "classify output" | "config";

/** One schema as the guard reaches it. */
interface Collected {
  /** The tool's name; the schema's own name otherwise. */
  name: string;
  family: SchemaFamily;
  /** How the guard reached it. */
  source: string;
  schema: unknown;
}

interface MockLog {
  debug: ReturnType<typeof vi.fn>;
  info: ReturnType<typeof vi.fn>;
  warn: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
  child: () => MockLog;
}

const createLog = (): MockLog => {
  const log: MockLog = {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    child: () => log,
  };

  return log;
};

/** The fake logger, where a signature asks for pino's. */
const asLogger = (log: MockLog): Logger => log as unknown as Logger;

// ---- where the schemas come from ------------------------------------------------------------------

/** A tool definition as the recordings care to see it. */
interface RegisteredTool {
  name: string;
  parameters?: unknown;
}

const REGISTRIES = "the registries";
const PER_RUN = "the per-run schemas of a background task";
const COMMIT_AGENT = "createCommitAgent";
const GIT_RESOLVER = "createGitResolver";
const PROPOSAL_TOOLS = "buildProposalTools";

const asTools = (source: string, tools: readonly RegisteredTool[]): Collected[] =>
  tools.map((tool) => ({
    name: tool.name,
    family: "tool parameters",
    source,
    schema: tool.parameters,
  }));

/**
 * The slice of pi's `ExtensionAPI` a Tachikoma factory uses while it binds, and no more: a factory
 * that reaches for another member fails here, loudly, instead of being skipped in silence.
 */
const recordingPi = (registered: RegisteredTool[]): ExtensionAPI =>
  ({
    registerTool: (definition: RegisteredTool) => {
      registered.push(definition);
    },
    on: vi.fn(),
    registerCommand: vi.fn(),
  }) as unknown as ExtensionAPI;

/**
 * Every kind of session that binds extension factories, each with the options `AgentManager.open`
 * passes for it: a main session, a background task run and a delegated subagent run.
 */
const SESSION_KINDS = [
  ["main", {}],
  ["background", { bare: true, bindBackgroundFactories: true }],
  ["subagent", { bare: true, bindSubagentFactories: true }],
] as const;

/**
 * Bind every factory the agent manager binds into each kind of session, once per scope it is
 * registered under: `selectExtensionFactories` is the manager's own rule, and hands each factory its
 * scope. A factory may adapt to its scope, so each binding is recorded on its own.
 */
const collectRegisteredTools = async (regs: Registrations): Promise<Collected[]> => {
  const collected: Collected[] = [];

  for (const [scope, options] of SESSION_KINDS) {
    for (const factory of selectExtensionFactories(options, regs)) {
      const registered: RegisteredTool[] = [];
      await factory(recordingPi(registered));

      collected.push(...asTools(`${REGISTRIES}, ${scope} scope`, registered));
    }
  }

  return collected;
};

/**
 * Load every first-party extension the way `runApp` does, minus everything that talks to the
 * outside. The database is real and migrated, because extensions read their tables at setup; the
 * config is parsed for real, so defaults are applied; the scheduler records and never fires.
 */
const collectFromRegistries = async (
  dir: string,
): Promise<{ tools: Collected[]; loaded: string[] }> => {
  const db = createDatabase(join(dir, "guard.db"));
  const regs = createRegistrations();
  const log = createLog();
  const scheduler = { cron: vi.fn(), every: vi.fn(), stopAll: vi.fn() };

  const config = parseWithSchema(
    ConfigSchema,
    {
      workspace: { path: dir },
      extensions: {
        // The Telegram extension registers its tools only when both are set. Nothing is ever sent.
        telegram: { botToken: "123456:schema-guard", chatId: 1 },
      },
    },
    "config",
  );

  const services = {
    config,
    workspace: new Workspace(dir),
    log,
    db,
    events: new EventBus(asLogger(log)),
    scheduler,
    agent: { tiers: {}, isForking: () => false },
    coordinator: { activeTrunkSession: () => null },
    regs,
  } as unknown as HostServices;

  try {
    runMigrations(db);
    await new ExtensionHost(services).load(firstPartyExtensions);
    scheduler.stopAll();

    // Read now: vitest clears mock history before each test.
    const loaded = log.debug.mock.calls
      .filter(([, message]) => message === "extension loaded")
      .map(([fields]) => (fields as { extension: string }).extension);

    return { tools: await collectRegisteredTools(regs), loaded };
  } finally {
    db.$client.close();
  }
};

/**
 * Hand `start` a runner that records instead of running, and keep the `customTools` it passes.
 * Read on the spot, before vitest clears the mock.
 */
const toolsHandedToRun = async (
  source: string,
  start: (run: AgentRunner["run"]) => Promise<void>,
): Promise<Collected[]> => {
  const run = vi.fn<AgentRunner["run"]>(async () => ({ text: "" }));
  await start(run);

  return run.mock.calls.flatMap(([{ customTools }]) => asTools(source, customTools ?? []));
};

/** The tools the headless agents carry, which never pass through `app.agent.use`. */
const collectOutsideFactories = async (dir: string, log: MockLog): Promise<Collected[]> => {
  // Their `defineTool` calls sit inside `executeBackgroundInstance`, so only the parameter schemas
  // are exported.
  const perRun: Collected[] = [
    { name: "ask_user", family: "tool parameters", source: PER_RUN, schema: AskUserParams },
    { name: "update_goal", family: "tool parameters", source: PER_RUN, schema: UpdateGoalParams },
  ];

  const commitAgent = await toolsHandedToRun(COMMIT_AGENT, async (run) => {
    await createCommitAgent({ run }, "workspace")(dir, asLogger(log));
  });

  const gitResolver = await toolsHandedToRun(GIT_RESOLVER, async (run) => {
    await createGitResolver({ run })(dir, "main", asLogger(log));
  });

  const proposalTools = asTools(
    PROPOSAL_TOOLS,
    buildProposalTools({
      workspaceRoot: dir,
      tmpDir: join(dir, "proposals"),
      defaultBranch: "main",
      remoteBranchNames: async () => new Set(),
      capture: { proposals: [] },
    }),
  );

  return [...perRun, ...commitAgent, ...gitResolver, ...proposalTools];
};

/** The two `classify` schemas, the root config schema and every extension's. */
const collectClassifyAndConfig = (): Collected[] => [
  {
    name: "SkillSelectionSchema",
    family: "classify output",
    source: "skills/suggest.ts",
    schema: SkillSelectionSchema,
  },
  {
    name: "BranchMatchSchema",
    family: "classify output",
    source: "boundary/related.ts",
    schema: BranchMatchSchema,
  },
  { name: "ConfigSchema", family: "config", source: "config/schema.ts", schema: ConfigSchema },
  ...firstPartyExtensions.flatMap((extension) =>
    extension.configSchema == null
      ? []
      : [
          {
            name: extension.name,
            family: "config" as const,
            source: "its configSchema",
            schema: extension.configSchema,
          },
        ],
  ),
];

// ---- the source sweep -----------------------------------------------------------------------------

/** A tool-shaped object literal in `src`, and where it is. */
interface ToolLiteral {
  name: string;
  /** `src/extensions/git/tools.ts:297` */
  at: string;
}

/**
 * A tool-shaped object literal: `name` first, then `label`. That is how every tool in `src` is
 * written today, whether it goes to `registerTool`, to `defineTool`, or is a plain object. A tool
 * written with its properties in another order is not matched; the sweep reports it, and it is
 * then listed in NOT_NAMED_IN_SRC by hand.
 */
const TOOL_LITERAL = /\{\s*name:\s*"([\w-]+)"\s*,\s*label:/g;

/**
 * Names the sweep matches in neither direction, each with its reason. `bash`: pi supplies the
 * definition, and bash-description spreads `createBashToolDefinition()` in place of writing a
 * literal.
 */
const NOT_NAMED_IN_SRC = new Set(["bash"]);

const ROOT = join(import.meta.dirname, "..");
const SRC = join(ROOT, "src");

const sourceToolLiterals = async (): Promise<ToolLiteral[]> => {
  const literals: ToolLiteral[] = [];

  const walk = async (current: string): Promise<void> => {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const path = join(current, entry.name);

      if (entry.isDirectory()) {
        await walk(path);
      } else if (entry.name.endsWith(".ts")) {
        const text = await readFile(path, "utf8");

        for (const match of text.matchAll(TOOL_LITERAL)) {
          const line = text.slice(0, match.index).split("\n").length;
          literals.push({ name: match[1] as string, at: `${relative(ROOT, path)}:${line}` });
        }
      }
    }
  };

  await walk(SRC);
  return literals;
};

// ---- what the guard asserts -----------------------------------------------------------------------

/** The `type` names JSON Schema defines, and no others. */
const JSON_SCHEMA_TYPES = new Set([
  "array",
  "boolean",
  "integer",
  "null",
  "number",
  "object",
  "string",
]);

/**
 * Every `type` the schema declares, at any depth. A `type` that is neither a string nor an array of
 * them is not a type keyword: it is a property that happens to be named `type`, as `create_task`'s
 * is, and the schema under it is visited like any other.
 */
const declaredTypes = function* (node: unknown): Generator<string> {
  if (node == null || typeof node !== "object") return;

  const { type } = node as { type?: unknown };

  if (typeof type === "string") yield type;
  else if (Array.isArray(type)) yield* type as string[];

  for (const child of Object.values(node)) yield* declaredTypes(child);
};

/**
 * Fail unless `schema` is plain JSON Schema, naming `label` when it is not. Two checks, because
 * either alone lets something through. The JSON round-trip, unchanged, catches what JSON cannot
 * carry, such as a function or a class instance. The `type` allowlist catches what JSON carries
 * fine but behaves, under pi-schema, in a way JSON Schema does not define: a `Type.BigInt()`
 * parameter, `{ type: "bigint" }`, takes `"42"` and gives the tool `42n`, and `null` gives `0n`; a
 * `Type.Undefined()` parameter takes `null` as `undefined`. Only the JavaScript-only kinds, such as
 * `Type.Void()` and `Type.Function()`, reject every argument.
 */
const expectPlainJsonSchema = (label: string, schema: unknown): void => {
  expect(schema, `${label}: has no schema`).toBeTypeOf("object");
  expect(JSON.parse(JSON.stringify(schema)), `${label}: is not plain JSON`).toStrictEqual(schema);

  for (const type of declaredTypes(schema)) {
    expect(JSON_SCHEMA_TYPES, `${label}: type "${type}" is not JSON Schema`).toContain(type);
  }
};

const labelOf = (entry: Collected): string =>
  `${entry.family} "${entry.name}" from ${entry.source}`;

// ---- the suite ------------------------------------------------------------------------------------

let collected: Collected[] = [];
let loadedExtensions: string[] = [];
let toolLiterals: ToolLiteral[] = [];

beforeAll(async () => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-schema-guard-"));

  // DES-003: a test never reaches the network. A setup that tried would fail here, at the call.
  vi.stubGlobal("fetch", () =>
    Promise.reject(new Error("the schema guard does not use the network")),
  );

  try {
    const fromRegistries = await collectFromRegistries(dir);

    collected = [
      ...fromRegistries.tools,
      ...(await collectOutsideFactories(dir, createLog())),
      ...collectClassifyAndConfig(),
    ];
    loadedExtensions = fromRegistries.loaded;
    toolLiterals = await sourceToolLiterals();
  } finally {
    vi.unstubAllGlobals();
    await rm(dir, { recursive: true, force: true });
  }
});

const namesFrom = (source: string): string[] =>
  collected.filter((entry) => entry.source === source).map((entry) => entry.name);

/** The schemas that reached the guard through the registries. */
const registered = (): Collected[] =>
  collected.filter((entry) => entry.source.startsWith(REGISTRIES));

const registeredNames = (): string[] => registered().map((entry) => entry.name);

const toolNames = (): Set<string> =>
  new Set(
    collected.filter((entry) => entry.family === "tool parameters").map((entry) => entry.name),
  );

describe("schema guard", () => {
  describe("the registries", () => {
    it("loads every first-party extension, none skipped by a setup failure", () => {
      expect(loadedExtensions).toEqual(firstPartyExtensions.map((extension) => extension.name));
    });

    it("gives every registered tool a parameter schema", () => {
      const withoutParameters = registered()
        .filter((entry) => entry.schema == null || typeof entry.schema !== "object")
        .map(labelOf);

      expect(withoutParameters).toEqual([]);
    });

    it("reaches the two tools a search for registerTool call sites would mishandle", () => {
      // Registered by `pi.registerTool(createDelegateTool(...))` in skills/index.ts; the object
      // literal lives in delegate.ts.
      expect(registeredNames()).toContain("delegate_to_agent");
      // bash-description spreads pi's own definition and swaps in its parameter schema.
      expect(registeredNames()).toContain("bash");
    });
  });

  describe("tools built outside a factory", () => {
    it("collects the two per-run tools of a background task", () => {
      expect(namesFrom(PER_RUN)).toEqual(["ask_user", "update_goal"]);
    });

    it("collects what the commit agent and the git resolver hand to a run", () => {
      expect(namesFrom(COMMIT_AGENT)).toEqual(["read_file", "git"]);
      expect(namesFrom(GIT_RESOLVER)).toEqual(["read_conflict", "write_resolved", "git"]);
    });

    it("collects the proposal agent's tools", () => {
      expect(namesFrom(PROPOSAL_TOOLS)).toEqual([
        "read_file",
        "write_file",
        "delete_path",
        "list_dir",
        "git",
        "report_proposals",
      ]);
    });
  });

  describe("classify and config schemas", () => {
    it("collects both classify schemas", () => {
      expect(
        collected.filter((entry) => entry.family === "classify output").map((entry) => entry.name),
      ).toEqual(["SkillSelectionSchema", "BranchMatchSchema"]);
    });

    it("collects the root config schema and every extension schema that is declared", () => {
      const declaring = firstPartyExtensions
        .filter((extension) => extension.configSchema != null)
        .map((extension) => extension.name);

      expect(
        collected.filter((entry) => entry.family === "config").map((entry) => entry.name),
      ).toEqual(["ConfigSchema", ...declaring]);
    });

    it("holds all three schema families", () => {
      expect(new Set(collected.map((entry) => entry.family))).toEqual(
        new Set(["tool parameters", "classify output", "config"]),
      );
    });
  });

  describe("plain JSON Schema", () => {
    it("holds for every collected schema", () => {
      for (const entry of collected) {
        expectPlainJsonSchema(labelOf(entry), entry.schema);
      }
    });

    it("rejects a type JSON Schema does not define, which the round-trip alone lets through", () => {
      const bigInt = Type.Object({ big: Type.BigInt() });

      // `Type.BigInt()` serializes as `{ type: "bigint" }` and comes back unchanged.
      expect(JSON.parse(JSON.stringify(bigInt))).toStrictEqual(bigInt);
      expect(() => expectPlainJsonSchema("self-test", bigInt)).toThrow(
        'self-test: type "bigint" is not JSON Schema',
      );
    });

    it("rejects what JSON cannot carry", () => {
      const withFunction = Type.Object({ path: Type.String(), check: () => true });

      expect(() => expectPlainJsonSchema("self-test", withFunction)).toThrow(
        "self-test: is not plain JSON",
      );
    });

    it("accepts a property that is named type", () => {
      expectPlainJsonSchema("self-test", Type.Object({ type: Type.String() }));
    });
  });

  describe("the source sweep", () => {
    it("collects every tool-shaped object literal in src", () => {
      const names = toolNames();
      const uncovered = toolLiterals.filter(
        (literal) => !names.has(literal.name) && !NOT_NAMED_IN_SRC.has(literal.name),
      );

      expect(
        uncovered.map((literal) => `${literal.name} at ${literal.at}`),
        "tools the guard cannot reach: collect them, or list them in NOT_NAMED_IN_SRC",
      ).toEqual([]);
    });

    it("names every collected tool in src, or lists it", () => {
      const named = new Set(toolLiterals.map((literal) => literal.name));
      const unlisted = [...toolNames()].filter(
        (name) => !named.has(name) && !NOT_NAMED_IN_SRC.has(name),
      );

      expect(
        unlisted,
        "collected tools no literal in src names: the sweep missed their shape, so list them in NOT_NAMED_IN_SRC",
      ).toEqual([]);
    });
  });
});

/**
 * pi's agent loop runs `validateToolArguments` on every tool call, before `execute`. These hold pi
 * to the coercion Tachikoma's tools rely on, and to the message the model is shown when a call is
 * malformed (DLT-186 S10, R3).
 */
describe("validateToolArguments", () => {
  const tool = defineTool({
    name: "pin_tool",
    label: "Pin tool",
    description: "Shaped like a Tachikoma tool: required, optional, array and enum parameters.",
    parameters: Type.Object({
      path: Type.String({ description: "File path" }),
      limit: Type.Optional(Type.Number({ description: "Maximum rows" })),
      tags: Type.Array(Type.String(), { description: "Labels to filter by" }),
      mode: Type.Optional(Type.String({ description: "Output mode" })),
      severity: StringEnum(["info", "warning", "urgent"], { description: "How urgent" }),
    }),
    execute: async () => ({ content: [{ type: "text", text: "ok" }], details: {} }),
  });

  /** Validate one call's arguments the way the agent loop does. */
  const validate = (args: ToolCall["arguments"]): unknown =>
    validateToolArguments(tool, {
      type: "toolCall",
      id: "call-1",
      name: tool.name,
      arguments: args,
    });

  it("coerces a loosely typed call before the tool sees it", () => {
    // A numeric string, a single value for an array, and null for an omitted optional.
    expect(
      validate({ path: "a.txt", limit: "10", tags: "one", mode: null, severity: "warning" }),
    ).toEqual({ path: "a.txt", limit: 10, tags: ["one"], severity: "warning" });
  });

  it("returns well-formed arguments unchanged", () => {
    const sent = { path: "b.txt", limit: 5, tags: ["x", "y"], mode: "fast", severity: "info" };

    expect(validate(sent)).toEqual(sent);
  });

  it("rejects a malformed call with a message the model can act on", () => {
    expect(() => validate({ limit: "ten", tags: [], severity: "loud" })).toThrow(
      new Error(
        [
          'Validation failed for tool "pin_tool":',
          "  - path: missing",
          "  - limit: expected number, got string",
          '  - severity: expected one of ["info","warning","urgent"]',
          "",
          "Received arguments:",
          "{",
          '  "limit": "ten",',
          '  "tags": [],',
          '  "severity": "loud"',
          "}",
        ].join("\n"),
      ),
    );
  });
});
