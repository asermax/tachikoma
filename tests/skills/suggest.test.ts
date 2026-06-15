import type { ExtensionAPI, ExtensionContext, Skill } from "@earendil-works/pi-coding-agent";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  registerSkillSuggestion,
  SKILL_CLASSIFY_TIMEOUT_MS,
  type SkillSuggestionDeps,
} from "../../src/extensions/skills/suggest.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = { debug: vi.fn(), warn: vi.fn(), info: vi.fn() } as unknown as Logger;

beforeEach(() => {
  vi.clearAllMocks();
});

const makeSkill = (
  name: string,
  description: string,
  options: { disableModelInvocation?: boolean } = {},
): Skill =>
  ({
    name,
    description,
    filePath: `/skills/${name}/SKILL.md`,
    baseDir: `/skills/${name}`,
    disableModelInvocation: options.disableModelInvocation ?? false,
  }) as Skill;

type Handler = (
  event: { prompt: string; systemPromptOptions: { skills?: Skill[] } },
  ctx: Pick<ExtensionContext, "sessionManager">,
) => Promise<{ message: { customType: string; content: string; display: false } } | undefined>;

const register = (
  overrides: Partial<SkillSuggestionDeps> = {},
): {
  handler: Handler;
  classify: ReturnType<typeof vi.fn>;
  status: ReturnType<typeof vi.fn>;
} => {
  let handler: Handler | undefined;
  const pi = {
    on: (event: string, registered: Handler) => {
      if (event === "before_agent_start") handler = registered;
    },
  } as unknown as ExtensionAPI;

  const classify = overrides.classifier?.classify ?? vi.fn();
  const status = (overrides.status as ReturnType<typeof vi.fn>) ?? vi.fn();

  registerSkillSuggestion(pi, {
    classifier: { classify },
    isForking: overrides.isForking ?? (() => false),
    status,
    log: fakeLog,
  });

  if (handler == null) throw new Error("handler not registered");
  return { handler, classify, status };
};

// Most cases need no prior conversation: empty branch → "first message".
const emptyCtx = (): Pick<ExtensionContext, "sessionManager"> =>
  ({ sessionManager: { getEntries: () => [], getLeafId: () => null } }) as never;

const event = (prompt: string, skills: Skill[]) => ({
  prompt,
  systemPromptOptions: { skills },
});

describe("registerSkillSuggestion", () => {
  it("recommends the matched skill as one hidden message (AC1)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools"] });
    const { handler } = register({ classifier: { classify } });

    const result = await handler(event("merge these pdfs", [pdf]), emptyCtx());

    expect(result?.message.customType).toBe("skill-recommendation");
    expect(result?.message.display).toBe(false);
    expect(result?.message.content).toContain("/skill:pdf-tools");
    expect(result?.message.content).toContain("Work with PDFs");
  });

  it("skips entirely (no classify) when isForking() is true (AC2)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn();
    const { handler, status } = register({ classifier: { classify }, isForking: () => true });

    const result = await handler(event("merge pdfs", [pdf]), emptyCtx());

    expect(result).toBeUndefined();
    expect(classify).not.toHaveBeenCalled();
    expect(status).not.toHaveBeenCalled();
  });

  it("does not re-recommend a skill already recommended this session (AC3)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools"] });
    const { handler } = register({ classifier: { classify } });

    const first = await handler(event("merge pdfs", [pdf]), emptyCtx());
    expect(first).toBeDefined();

    // Second turn: still in the catalog, but already recommended → filtered out as a candidate.
    const second = await handler(event("merge more pdfs", [pdf]), emptyCtx());
    expect(second).toBeUndefined();
    expect(classify).toHaveBeenCalledTimes(1);
  });

  it("drops classifier names not in the eligible catalog (AC4)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn().mockResolvedValue({ skills: ["made-up-skill"] });
    const { handler } = register({ classifier: { classify } });

    const result = await handler(event("do something", [pdf]), emptyCtx());

    expect(result).toBeUndefined();
  });

  it("never considers disableModelInvocation skills (AC5)", async () => {
    const hidden = makeSkill("secret", "Hidden", { disableModelInvocation: true });
    const classify = vi.fn();
    const { handler } = register({ classifier: { classify } });

    const result = await handler(event("do something", [hidden]), emptyCtx());

    expect(result).toBeUndefined();
    expect(classify).not.toHaveBeenCalled();
  });

  it("returns undefined and logs when classify throws (AC6)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn().mockRejectedValue(new Error("boom"));
    const { handler } = register({ classifier: { classify } });

    const result = await handler(event("merge pdfs", [pdf]), emptyCtx());

    expect(result).toBeUndefined();
    expect(fakeLog.warn).toHaveBeenCalled();
  });

  it("returns undefined when classify exceeds the timeout (AC7)", async () => {
    vi.useFakeTimers();
    try {
      const pdf = makeSkill("pdf-tools", "Work with PDFs");
      const classify = vi.fn(() => new Promise(() => {}));
      const { handler } = register({ classifier: { classify } });

      const pending = handler(event("merge pdfs", [pdf]), emptyCtx());
      await vi.advanceTimersByTimeAsync(SKILL_CLASSIFY_TIMEOUT_MS);

      await expect(pending).resolves.toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not call classify or status when no skills are eligible (AC8)", async () => {
    const classify = vi.fn();
    const { handler, status } = register({ classifier: { classify } });

    const result = await handler(event("anything", []), emptyCtx());

    expect(result).toBeUndefined();
    expect(classify).not.toHaveBeenCalled();
    expect(status).not.toHaveBeenCalled();
  });

  it("packs multiple matches into one message, each by /skill name, without paths (AC10)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const csv = makeSkill("csv-tools", "Work with CSVs");
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools", "csv-tools"] });
    const { handler } = register({ classifier: { classify } });

    const result = await handler(event("process files", [pdf, csv]), emptyCtx());
    const content = result?.message.content ?? "";

    const recommendations = content.match(/\/skill:/g) ?? [];
    expect(recommendations).toHaveLength(2);
    expect(content).toContain("/skill:pdf-tools");
    expect(content).toContain("/skill:csv-tools");
    // No filesystem path leaked.
    expect(content).not.toContain("SKILL.md");
    expect(content).not.toContain("/skills/");
  });

  it("recommends only the not-yet-recommended skill when one was already loaded (AC11)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const csv = makeSkill("csv-tools", "Work with CSVs");

    const classify = vi
      .fn()
      .mockResolvedValueOnce({ skills: ["pdf-tools"] })
      .mockResolvedValueOnce({ skills: ["pdf-tools", "csv-tools"] });
    const { handler } = register({ classifier: { classify } });

    await handler(event("merge pdfs", [pdf, csv]), emptyCtx());
    const second = await handler(event("now the csv too", [pdf, csv]), emptyCtx());
    const content = second?.message.content ?? "";

    expect(content).toContain("/skill:csv-tools");
    expect(content).not.toContain("/skill:pdf-tools");
  });

  it("calls status exactly once when eligible and never when not (AC13)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn().mockResolvedValue({ skills: [] });
    const { handler, status } = register({ classifier: { classify } });

    await handler(event("merge pdfs", [pdf]), emptyCtx());
    expect(status).toHaveBeenCalledTimes(1);

    await handler(event("nothing relevant", []), emptyCtx());
    expect(status).toHaveBeenCalledTimes(1);
  });

  it("passes the prior conversation, latest message, and catalog to classify (AC14)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn().mockResolvedValue({ skills: [] });
    const { handler } = register({ classifier: { classify } });

    const ctx = {
      sessionManager: {
        getEntries: () => [
          {
            id: "e1",
            type: "message",
            timestamp: 1,
            message: {
              role: "user",
              content: [{ type: "text", text: "earlier talk about invoices" }],
              timestamp: 1,
            },
          },
        ],
        getLeafId: () => "e1",
      },
    } as never;

    await handler(event("merge the invoice pdfs", [pdf]), ctx);

    const call = classify.mock.calls[0]?.[0];
    expect(call.system).toContain("pdf-tools: Work with PDFs");
    expect(call.user).toContain("earlier talk about invoices");
    expect(call.user).toContain("merge the invoice pdfs");
  });

  it("returns undefined on an empty classifier selection (AC8 boundary)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn().mockResolvedValue({ skills: [] });
    const { handler } = register({ classifier: { classify } });

    const result = await handler(event("merge pdfs", [pdf]), emptyCtx());

    expect(result).toBeUndefined();
  });
});
