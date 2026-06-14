import type { Logger } from "../log.ts";
import { POST_PROCESSING_PHASES, type PostProcessor, type PostProcessorContext } from "./api.ts";

/** Single source of truth for the phase execution order, derived from the canonical phase map. */
export const POST_PROCESSING_PHASE_ORDER = Object.values(POST_PROCESSING_PHASES);

export interface RunPhasedPostProcessorsOptions {
  processors: PostProcessor[];
  context: PostProcessorContext;
  /** Logger used to report failures; each processor receives a child of it. */
  log: Logger;
  /** Skip a processor (e.g. already completed in a prior run). Defaults to running every processor. */
  shouldSkip?: (processor: PostProcessor) => boolean;
  /** Invoked just before a processor's `process` runs (e.g. to surface a progress line). */
  onProcessorStart?: (processor: PostProcessor) => void;
  /** Invoked once per processor with its settled outcome (e.g. to record per-session state). */
  onProcessorSettled?: (processor: PostProcessor, result: PromiseSettledResult<void>) => void;
}

/**
 * Iterate post-processors in phase order: within each phase, filter by the optional skip
 * predicate, run the survivors via `Promise.allSettled` (error-isolated), report rejections
 * through `log`, and surface each outcome via the optional callbacks. The per-session
 * state-tracking layer is fully expressed through `shouldSkip`/`onProcessorSettled`, so a
 * headless run simply omits them.
 */
export const runPhasedPostProcessors = async ({
  processors,
  context,
  log,
  shouldSkip,
  onProcessorStart,
  onProcessorSettled,
}: RunPhasedPostProcessorsOptions): Promise<void> => {
  for (const phase of POST_PROCESSING_PHASE_ORDER) {
    const phaseProcessors = processors.filter(
      (processor) =>
        (processor.phase ?? "main") === phase && (shouldSkip == null || !shouldSkip(processor)),
    );
    if (phaseProcessors.length === 0) continue;

    const results = await Promise.allSettled(
      phaseProcessors.map((processor) => {
        onProcessorStart?.(processor);
        return processor.process({ ...context, log: log.child({ processor: processor.name }) });
      }),
    );

    results.forEach((result, index) => {
      const processor = phaseProcessors[index];
      if (processor == null) return;

      if (result.status === "rejected") {
        log.error({ processor: processor.name, err: result.reason }, "post-processor failed");
      }

      onProcessorSettled?.(processor, result);
    });
  }
};
