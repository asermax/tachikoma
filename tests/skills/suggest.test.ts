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
  // Default fake so the existing fake `filePath` fixtures work without the real filesystem;
  // tests that care about the body or read failures override it.
  const readSkill = overrides.readSkill ?? (() => "Injected skill body.");

  registerSkillSuggestion(pi, {
    classifier: { classify },
    isForking: overrides.isForking ?? (() => false),
    status,
    log: fakeLog,
    readSkill,
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
  it("injects the matched skill's full content as one hidden message (AC1)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools"] });
    const { handler } = register({ classifier: { classify } });

    const result = await handler(event("merge these pdfs", [pdf]), emptyCtx());

    expect(result?.message.customType).toBe("skill-content");
    expect(result?.message.display).toBe(false);
    expect(result?.message.content).toContain("injected for this session");
    expect(result?.message.content).toContain('<injected-skill name="pdf-tools">');
    expect(result?.message.content).toContain("Injected skill body.");
    expect(result?.message.content).not.toContain("/skill:");
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

  it("packs multiple matches into one message, each as an injected-skill section (AC10)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const csv = makeSkill("csv-tools", "Work with CSVs");
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools", "csv-tools"] });
    const { handler } = register({ classifier: { classify } });

    const result = await handler(event("process files", [pdf, csv]), emptyCtx());
    const content = result?.message.content ?? "";

    const sections = content.match(/<injected-skill /g) ?? [];
    expect(sections).toHaveLength(2);
    expect(content).toContain('<injected-skill name="pdf-tools">');
    expect(content).toContain('<injected-skill name="csv-tools">');
    // The wrapper carries only the skill name — no filesystem path, no /skill: command.
    expect(content).not.toContain("SKILL.md");
    expect(content).not.toContain("/skills/");
    expect(content).not.toContain("/skill:");
  });

  it("injects only the not-yet-injected skill when one was already injected (AC11)", async () => {
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

    expect(content).toContain('<injected-skill name="csv-tools">');
    expect(content).not.toContain('<injected-skill name="pdf-tools">');
  });

  it("injects the full file body returned by readSkill (AC1 body)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const readSkill = vi.fn(() => "## Step 1\nDo the thing.");
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools"] });
    const { handler } = register({ classifier: { classify }, readSkill });

    const result = await handler(event("merge pdfs", [pdf]), emptyCtx());

    expect(result?.message.content).toContain("## Step 1\nDo the thing.");
    expect(readSkill).toHaveBeenCalledWith("/skills/pdf-tools/SKILL.md");
  });

  it("skips an unreadable skill but still injects the rest, logging a warning (AC7)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const csv = makeSkill("csv-tools", "Work with CSVs");
    const readSkill = vi.fn((filePath: string) => {
      if (filePath.includes("pdf-tools")) throw new Error("ENOENT");
      return "csv body";
    });
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools", "csv-tools"] });
    const { handler } = register({ classifier: { classify }, readSkill });

    const result = await handler(event("process files", [pdf, csv]), emptyCtx());
    const content = result?.message.content ?? "";

    expect(content).not.toContain('<injected-skill name="pdf-tools">');
    expect(content).toContain('<injected-skill name="csv-tools">');
    expect(fakeLog.warn).toHaveBeenCalledWith(
      expect.objectContaining({ skill: "pdf-tools" }),
      expect.any(String),
    );
  });

  it("does not mark an unreadable skill as injected, so it can retry next turn (R4)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const readSkill = vi.fn(() => {
      throw new Error("ENOENT");
    });
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools"] });
    const { handler } = register({ classifier: { classify }, readSkill });

    const first = await handler(event("merge pdfs", [pdf]), emptyCtx());
    expect(first).toBeUndefined();

    // Still a candidate next turn: classify runs again and read is re-attempted.
    const second = await handler(event("merge more pdfs", [pdf]), emptyCtx());
    expect(second).toBeUndefined();
    expect(readSkill).toHaveBeenCalledTimes(2);
  });

  it("skips empty and whitespace-only content without marking injected (AC7)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const csv = makeSkill("csv-tools", "Work with CSVs");
    const txt = makeSkill("txt-tools", "Work with text");
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools", "csv-tools", "txt-tools"] });
    const { handler } = register({
      classifier: { classify },
      readSkill: (filePath) => {
        if (filePath.includes("pdf-tools")) return "";
        if (filePath.includes("csv-tools")) return "   \n";
        return "txt body";
      },
    });

    const result = await handler(event("process files", [pdf, csv, txt]), emptyCtx());
    const content = result?.message.content ?? "";

    expect(content).not.toContain('<injected-skill name="pdf-tools">');
    expect(content).not.toContain('<injected-skill name="csv-tools">');
    expect(content).toContain('<injected-skill name="txt-tools">');
    expect(fakeLog.debug).toHaveBeenCalledWith(
      expect.objectContaining({ skill: "pdf-tools" }),
      expect.any(String),
    );
    expect(fakeLog.debug).toHaveBeenCalledWith(
      expect.objectContaining({ skill: "csv-tools" }),
      expect.any(String),
    );
  });

  it("injects nothing and stays silent when every matched read fails (AC9)", async () => {
    const pdf = makeSkill("pdf-tools", "Work with PDFs");
    const csv = makeSkill("csv-tools", "Work with CSVs");
    const classify = vi.fn().mockResolvedValue({ skills: ["pdf-tools", "csv-tools"] });
    const { handler } = register({
      classifier: { classify },
      readSkill: () => {
        throw new Error("ENOENT");
      },
    });

    const result = await handler(event("process files", [pdf, csv]), emptyCtx());

    expect(result).toBeUndefined();
    expect(fakeLog.warn).toHaveBeenCalledTimes(2);
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
