import type { AssistantMessage } from "@earendil-works/pi-ai";
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentManager } from "../../src/agent/manager.ts";
import { extractJson, lastAssistantText, SideRunner } from "../../src/agent/side-run.ts";
import type { Logger } from "../../src/log.ts";

const completeSimpleMock = vi.fn();

vi.mock("@earendil-works/pi-ai/compat", () => ({
  completeSimple: (...args: unknown[]) => completeSimpleMock(...args),
}));

const assistantMessage = (
  text: string,
  overrides: Partial<AssistantMessage> = {},
): AssistantMessage =>
  ({
    role: "assistant",
    content: [{ type: "text", text }],
    api: "anthropic",
    provider: "anthropic",
    model: "model",
    usage: {},
    stopReason: "stop",
    timestamp: 0,
    ...overrides,
  }) as AssistantMessage;

const makeLogger = (): Logger => {
  const logger = { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() };
  return Object.assign(logger, { child: () => logger }) as unknown as Logger;
};

interface ManagerStub {
  manager: AgentManager;
  resolve: ReturnType<typeof vi.fn>;
  apiKeyFor: ReturnType<typeof vi.fn>;
  open: ReturnType<typeof vi.fn>;
}

const makeManager = (
  overrides: {
    resolve?: { model: { provider: string; id: string }; fromPiDefaults: boolean };
    apiKey?: string | undefined;
    open?: ReturnType<typeof vi.fn>;
  } = {},
): ManagerStub => {
  const resolve = vi.fn(
    () =>
      overrides.resolve ?? {
        model: { provider: "anthropic", id: "model-x" },
        fromPiDefaults: false,
      },
  );

  const apiKeyFor = vi.fn(async () => overrides.apiKey);

  const open = overrides.open ?? vi.fn();

  const manager = {
    tiers: { resolve },
    apiKeyFor,
    open,
  } as unknown as AgentManager;

  return { manager, resolve, apiKeyFor, open };
};

const GRANT_SESSION_TOOLS = [
  { name: "read" },
  { name: "grep" },
  { name: "find" },
  { name: "ls" },
  { name: "web_search" },
];

/** A fake in-memory session exposing the source-agnostic primitives the grant path leans on. */
const makeGrantSession = (messages: AssistantMessage[] = []) => {
  const getAllTools = vi.fn(() => GRANT_SESSION_TOOLS);
  const setActiveToolsByName = vi.fn();
  const prompt = vi.fn(async () => undefined);
  const dispose = vi.fn();
  const session = { prompt, dispose, getAllTools, setActiveToolsByName, messages };
  return {
    session: session as unknown as AgentSession,
    getAllTools,
    setActiveToolsByName,
    prompt,
    dispose,
  };
};

describe("lastAssistantText", () => {
  it("returns the joined text of the last assistant message", () => {
    const messages = [
      { role: "user", content: [{ type: "text", text: "hi" }] },
      assistantMessage("first"),
      {
        role: "assistant",
        content: [
          { type: "text", text: "a" },
          { type: "thinking", text: "ignored" },
          { type: "text", text: "b" },
        ],
      },
    ];

    expect(lastAssistantText(messages as never)).toBe("ab");
  });

  it("skips trailing non-assistant messages", () => {
    const messages = [assistantMessage("kept"), { role: "toolResult" }];

    expect(lastAssistantText(messages as never)).toBe("kept");
  });

  it("skips null entries in the log", () => {
    const messages = [null, assistantMessage("after-null")];

    expect(lastAssistantText(messages as never)).toBe("after-null");
  });

  it("returns empty string when there is no assistant message", () => {
    expect(lastAssistantText([{ role: "user" }] as never)).toBe("");
    expect(lastAssistantText([])).toBe("");
  });
});

describe("SideRunner.complete", () => {
  beforeEach(() => {
    completeSimpleMock.mockReset();
  });

  it("passes system prompt and api key, and returns the assistant text", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage("done"));

    const { manager, apiKeyFor } = makeManager({ apiKey: "secret-key" });
    const runner = new SideRunner(manager, makeLogger());

    const result = await runner.complete({ system: "be brief", user: "question" });

    expect(result).toBe("done");
    expect(apiKeyFor).toHaveBeenCalledWith("anthropic");

    const [model, context, options] = completeSimpleMock.mock.calls[0];
    expect(model).toEqual({ provider: "anthropic", id: "model-x" });
    expect(context.systemPrompt).toBe("be brief");
    expect(context.messages[0]).toMatchObject({
      role: "user",
      content: [{ type: "text", text: "question" }],
    });
    expect(options).toEqual({ apiKey: "secret-key" });
  });

  it("omits the system prompt and api-key options when neither is provided", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage("ok"));

    const { manager } = makeManager({ apiKey: undefined });
    const runner = new SideRunner(manager, makeLogger());

    await runner.complete({ user: "just user" });

    const [, context, options] = completeSimpleMock.mock.calls[0];
    expect(context.systemPrompt).toBeUndefined();
    expect(options).toBeUndefined();
  });

  it("forwards maxTokens, temperature, and an abort signal to the provider", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage("ok"));

    const { manager } = makeManager({ apiKey: "secret-key" });
    const runner = new SideRunner(manager, makeLogger());
    const controller = new AbortController();

    await runner.complete({
      user: "u",
      maxTokens: 128,
      temperature: 0.2,
      signal: controller.signal,
    });

    expect(completeSimpleMock.mock.calls[0][2]).toEqual({
      apiKey: "secret-key",
      maxTokens: 128,
      temperature: 0.2,
      signal: controller.signal,
    });
  });

  it("logs a debug line when the tier falls back to a pi default", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage("ok"));

    const log = makeLogger();
    const { manager } = makeManager({
      resolve: { model: { provider: "openai", id: "gpt" }, fromPiDefaults: true },
    });
    const runner = new SideRunner(manager, log);

    await runner.complete({ user: "u", tier: "processor" });

    expect(log.debug).toHaveBeenCalledWith(
      { tier: "processor", model: "openai/gpt" },
      "tier unset — using pi default model",
    );
  });

  it("throws when the completion stops with an error reason", async () => {
    completeSimpleMock.mockResolvedValue(
      assistantMessage("", { stopReason: "error", errorMessage: "boom" }),
    );

    const { manager } = makeManager();
    const runner = new SideRunner(manager, makeLogger());

    await expect(runner.complete({ user: "u" })).rejects.toThrow("side completion failed: boom");
  });

  it("falls back to the stop reason when no error message is present on abort", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage("", { stopReason: "aborted" }));

    const { manager } = makeManager();
    const runner = new SideRunner(manager, makeLogger());

    await expect(runner.complete({ user: "u" })).rejects.toThrow("side completion failed: aborted");
  });
});

describe("SideRunner.run", () => {
  it("opens an isolated bare in-memory session with an explicit tool allowlist", async () => {
    const dispose = vi.fn();
    const prompt = vi.fn(async () => undefined);
    const session = { prompt, dispose, messages: [assistantMessage("result text")] };
    const open = vi.fn(async () => session as unknown as AgentSession);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    const custom = [{ name: "myTool" }] as never;
    const result = await runner.run({
      prompt: "go",
      system: "sys",
      tools: ["read"],
      customTools: custom,
      model: "anthropic/claude",
      isolatePrompt: true,
    });

    expect(result).toEqual({ text: "result text" });
    expect(prompt).toHaveBeenCalledWith("go");
    expect(dispose).toHaveBeenCalledOnce();

    const opts = open.mock.calls[0][0];
    expect(opts).toMatchObject({
      inMemory: true,
      bare: true,
      tier: "processor",
      model: "anthropic/claude",
      isolatePrompt: true,
      systemPrompt: "sys",
    });
    expect(opts.tools).toEqual(["read", "myTool"]);
    expect(opts.customTools).toBe(custom);
    expect(opts.bindBackgroundFactories).toBeUndefined();
  });

  it("binds background factories without a tool allowlist and uses defaults", async () => {
    const session = { prompt: vi.fn(async () => undefined), dispose: vi.fn(), messages: [] };
    const open = vi.fn(async () => session as unknown as AgentSession);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    const result = await runner.run({ prompt: "go", backgroundExtensions: true });

    expect(result).toEqual({ text: "" });

    const opts = open.mock.calls[0][0];
    expect(opts.bindBackgroundFactories).toBe(true);
    expect(opts.tools).toBeUndefined();
    expect(opts.customTools).toBeUndefined();
    expect(opts.systemPrompt).toBeUndefined();
    expect(opts.isolatePrompt).toBeUndefined();
    expect(opts.model).toBeUndefined();
  });

  it("disposes the session even when the prompt throws", async () => {
    const dispose = vi.fn();
    const session = {
      prompt: vi.fn(async () => {
        throw new Error("prompt blew up");
      }),
      dispose,
      messages: [],
    };
    const open = vi.fn(async () => session as unknown as AgentSession);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    await expect(runner.run({ prompt: "go" })).rejects.toThrow("prompt blew up");
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("grants a resolved extension tool alongside the built-ins", async () => {
    const { session, getAllTools, setActiveToolsByName, prompt, dispose } = makeGrantSession([
      assistantMessage("researched"),
    ]);
    const open = vi.fn(async () => session);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    const result = await runner.run({
      prompt: "go",
      tools: ["read"],
      extensionTools: ["web_search"],
    });

    expect(result).toEqual({ text: "researched" });

    const opts = open.mock.calls[0][0];
    expect(opts).toMatchObject({
      inMemory: true,
      bare: true,
      bindSubagentFactories: true,
    });
    // NO `tools` allowlist — bound factory tools must register and enumerate.
    expect(opts.tools).toBeUndefined();
    expect(opts.bindBackgroundFactories).toBeUndefined();

    expect(getAllTools).toHaveBeenCalledOnce();
    expect(setActiveToolsByName).toHaveBeenCalledWith(["read", "web_search"]);
    expect(prompt).toHaveBeenCalledWith("go");
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("throws before the prompt when an extension tool does not resolve, listing grantables", async () => {
    const { session, getAllTools, setActiveToolsByName, prompt, dispose } = makeGrantSession();
    const open = vi.fn(async () => session);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    let caught: unknown;
    try {
      await runner.run({ prompt: "go", extensionTools: ["web_srch"] });
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(Error);
    const message = (caught as Error).message;
    expect(message).toMatch(/web_srch/);
    // Lists the grantable (non-builtin) names, not the built-ins.
    expect(message).toContain("web_search");
    expect(message).not.toContain("grep");

    expect(getAllTools).toHaveBeenCalledOnce();
    expect(prompt).not.toHaveBeenCalled();
    expect(setActiveToolsByName).not.toHaveBeenCalled();
    // Disposes the session even when validation throws.
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("fails the whole request when only some extension tools resolve", async () => {
    const { session, setActiveToolsByName, prompt, dispose } = makeGrantSession();
    const open = vi.fn(async () => session);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    await expect(
      runner.run({ prompt: "go", extensionTools: ["web_search", "web_srch"] }),
    ).rejects.toThrow(/web_srch/);

    expect(prompt).not.toHaveBeenCalled();
    expect(setActiveToolsByName).not.toHaveBeenCalled();
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("treats an empty extensionTools array like omitting it (built-in allowlist path)", async () => {
    const { session, getAllTools, setActiveToolsByName, prompt, dispose } = makeGrantSession();
    const open = vi.fn(async () => session);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    await runner.run({ prompt: "go", tools: ["read"], extensionTools: [] });

    const opts = open.mock.calls[0][0];
    expect(opts.tools).toEqual(["read"]);
    expect(opts.bindSubagentFactories).toBeUndefined();
    expect(opts.bindBackgroundFactories).toBeUndefined();
    // Never the grant path — and never "no tools".
    expect(getAllTools).not.toHaveBeenCalled();
    expect(setActiveToolsByName).not.toHaveBeenCalled();
    expect(prompt).toHaveBeenCalledWith("go");
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("omitting extensionTools keeps the built-in allowlist path (regression)", async () => {
    const { session, getAllTools, setActiveToolsByName } = makeGrantSession();
    const open = vi.fn(async () => session);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    const custom = [{ name: "myTool" }] as never;
    await runner.run({ prompt: "go", tools: ["read"], customTools: custom });

    const opts = open.mock.calls[0][0];
    expect(opts.tools).toEqual(["read", "myTool"]);
    expect(opts.bindSubagentFactories).toBeUndefined();
    expect(getAllTools).not.toHaveBeenCalled();
    expect(setActiveToolsByName).not.toHaveBeenCalled();
  });
});

describe("SideRunner.openBackgroundSession", () => {
  it("opens a persistent session binding background factories", async () => {
    const session = {} as AgentSession;
    const open = vi.fn(async () => session);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    const custom = [{ name: "t" }] as never;
    const out = await runner.openBackgroundSession({
      system: "sys",
      customTools: custom,
      sessionFile: "/path/session.json",
      tier: "processor",
    });

    expect(out).toBe(session);
    expect(open).toHaveBeenCalledWith({
      tier: "processor",
      bindBackgroundFactories: true,
      systemPrompt: "sys",
      customTools: custom,
      sessionFile: "/path/session.json",
    });
  });

  it("omits the session file when none is given", async () => {
    const open = vi.fn(async () => ({}) as AgentSession);

    const { manager } = makeManager({ open });
    const runner = new SideRunner(manager, makeLogger());

    await runner.openBackgroundSession({ system: "sys", customTools: [] });

    expect(open.mock.calls[0][0]).not.toHaveProperty("sessionFile");
  });
});

describe("SideRunner.classify", () => {
  beforeEach(() => {
    completeSimpleMock.mockReset();
  });

  const schema = Type.Object({ label: Type.String() });

  it("parses fenced JSON on the first attempt", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage('```json\n{"label":"a"}\n```'));

    const { manager } = makeManager();
    const runner = new SideRunner(manager, makeLogger());

    expect(await runner.classify({ system: "s", user: "u", schema })).toEqual({ label: "a" });
    expect(completeSimpleMock).toHaveBeenCalledOnce();
  });

  it("extracts a bare JSON object when not fenced", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage('prefix {"label":"b"} suffix'));

    const { manager } = makeManager();
    const runner = new SideRunner(manager, makeLogger());

    expect(await runner.classify({ system: "s", user: "u", schema })).toEqual({ label: "b" });
  });

  it("retries once with a reminder when the first parse fails, then succeeds", async () => {
    completeSimpleMock
      .mockResolvedValueOnce(assistantMessage("not json at all"))
      .mockResolvedValueOnce(assistantMessage('{"label":"c"}'));

    const log = makeLogger();
    const { manager } = makeManager();
    const runner = new SideRunner(manager, log);

    expect(await runner.classify({ system: "s", user: "u", schema })).toEqual({ label: "c" });
    expect(completeSimpleMock).toHaveBeenCalledTimes(2);
    expect(log.debug).toHaveBeenCalledWith(
      { err: expect.any(SyntaxError) },
      "classification parse failed — retrying once",
    );

    const retryContext = completeSimpleMock.mock.calls[1][1];
    expect(retryContext.messages[0].content[0].text).toContain("output ONLY the JSON object");
  });

  it("propagates the error when the retry also fails", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage("never valid json"));

    const { manager } = makeManager();
    const runner = new SideRunner(manager, makeLogger());

    await expect(runner.classify({ system: "s", user: "u", schema })).rejects.toBeInstanceOf(Error);
    expect(completeSimpleMock).toHaveBeenCalledTimes(2);
  });

  it("applies classification defaults (maxTokens 256, temperature 0) to the provider call", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage('{"label":"a"}'));

    const { manager } = makeManager();
    const runner = new SideRunner(manager, makeLogger());

    await runner.classify({ system: "s", user: "u", schema });

    expect(completeSimpleMock.mock.calls[0][2]).toMatchObject({ maxTokens: 256, temperature: 0 });
  });

  it("forwards an abort signal and skips the retry once already aborted", async () => {
    completeSimpleMock.mockResolvedValue(assistantMessage("not json"));

    const { manager } = makeManager();
    const runner = new SideRunner(manager, makeLogger());
    const controller = new AbortController();
    controller.abort();

    await expect(
      runner.classify({ system: "s", user: "u", schema, signal: controller.signal }),
    ).rejects.toBeInstanceOf(Error);
    // Aborted → no second attempt.
    expect(completeSimpleMock).toHaveBeenCalledOnce();
  });
});

describe("extractJson", () => {
  it("returns the contents of a fenced json block", () => {
    expect(extractJson('```json\n{"a":1}\n```')).toBe('{"a":1}');
  });

  it("extracts the first balanced object, ignoring trailing prose", () => {
    expect(extractJson('{"skills":[]} no skills needed here')).toBe('{"skills":[]}');
  });

  it("extracts only the first of two concatenated objects", () => {
    expect(extractJson('{"a":1}{"b":2}')).toBe('{"a":1}');
  });

  it("ignores braces that appear inside string values", () => {
    expect(extractJson('{"a":"}{"}')).toBe('{"a":"}{"}');
  });

  it("handles nested objects with surrounding prose", () => {
    expect(extractJson('prefix {"a":{"b":1}} suffix')).toBe('{"a":{"b":1}}');
  });

  it("falls back to the remainder when braces are unbalanced", () => {
    expect(extractJson('{"a":1')).toBe('{"a":1');
  });

  it("returns trimmed text when there is no object at all", () => {
    expect(extractJson("  no json here  ")).toBe("no json here");
  });
});
