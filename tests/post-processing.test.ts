import { describe, expect, it, vi } from "vitest";
import { POST_PROCESSING_PHASES, type PostProcessor } from "../src/extensions/api.ts";
import {
  POST_PROCESSING_PHASE_ORDER,
  runPhasedPostProcessors,
} from "../src/extensions/post-processing.ts";
import type { Logger } from "../src/log.ts";

type MockLog = {
  warn: ReturnType<typeof vi.fn>;
  info: ReturnType<typeof vi.fn>;
  debug: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
  child: ReturnType<typeof vi.fn>;
};

const createLog = (): MockLog => {
  const log: MockLog = {
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
    child: vi.fn(() => log),
  };

  return log;
};

const context = (log: Logger) => ({ session: null, transcriptPath: null, log });

describe("POST_PROCESSING_PHASE_ORDER", () => {
  it("derives from the canonical phase map", () => {
    expect(POST_PROCESSING_PHASE_ORDER).toEqual(Object.values(POST_PROCESSING_PHASES));
    expect(POST_PROCESSING_PHASE_ORDER).toEqual(["main", "preFinalize", "finalize"]);
  });
});

describe("runPhasedPostProcessors", () => {
  it("runs processors in phase order and defaults a missing phase to main", async () => {
    const order: string[] = [];
    const processors: PostProcessor[] = [
      { name: "fin", phase: "finalize", process: async () => void order.push("fin") },
      { name: "pre", phase: "preFinalize", process: async () => void order.push("pre") },
      { name: "implicitMain", process: async () => void order.push("implicitMain") },
    ];
    const log = createLog();

    await runPhasedPostProcessors({
      processors,
      context: context(log as unknown as Logger),
      log: log as unknown as Logger,
    });

    expect(order).toEqual(["implicitMain", "pre", "fin"]);
  });

  it("passes each processor a child logger scoped to its name", async () => {
    const seen: unknown[] = [];
    const log = createLog();

    await runPhasedPostProcessors({
      processors: [{ name: "alpha", process: async (ctx) => void seen.push(ctx.log) }],
      context: context(log as unknown as Logger),
      log: log as unknown as Logger,
    });

    expect(log.child).toHaveBeenCalledWith({ processor: "alpha" });
    expect(seen).toEqual([log]);
  });

  it("isolates failures, logs them, and still runs the rest of the phase", async () => {
    const survivor = vi.fn(async () => {});
    const log = createLog();

    await runPhasedPostProcessors({
      processors: [
        {
          name: "boom",
          process: async () => {
            throw new Error("nope");
          },
        },
        { name: "survivor", process: survivor },
      ],
      context: context(log as unknown as Logger),
      log: log as unknown as Logger,
    });

    expect(survivor).toHaveBeenCalledTimes(1);
    expect(log.error).toHaveBeenCalledWith(
      expect.objectContaining({ processor: "boom" }),
      "post-processor failed",
    );
  });

  it("honors the skip predicate without invoking skipped processors", async () => {
    const skipped = vi.fn(async () => {});
    const ran = vi.fn(async () => {});
    const log = createLog();

    await runPhasedPostProcessors({
      processors: [
        { name: "skip", process: skipped },
        { name: "run", process: ran },
      ],
      context: context(log as unknown as Logger),
      log: log as unknown as Logger,
      shouldSkip: (processor) => processor.name === "skip",
    });

    expect(skipped).not.toHaveBeenCalled();
    expect(ran).toHaveBeenCalledTimes(1);
  });

  it("reports start and settled outcomes per processor", async () => {
    const started: string[] = [];
    const settled: Array<[string, string]> = [];
    const log = createLog();

    await runPhasedPostProcessors({
      processors: [
        { name: "ok", process: async () => {} },
        {
          name: "bad",
          process: async () => {
            throw new Error("x");
          },
        },
      ],
      context: context(log as unknown as Logger),
      log: log as unknown as Logger,
      onProcessorStart: (processor) => started.push(processor.name),
      onProcessorSettled: (processor, result) => settled.push([processor.name, result.status]),
    });

    expect(started).toEqual(["ok", "bad"]);
    expect(settled).toEqual([
      ["ok", "fulfilled"],
      ["bad", "rejected"],
    ]);
  });
});
