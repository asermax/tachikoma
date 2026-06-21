import type { Logger } from "../../log.ts";
import type { DebouncedTask } from "../../util/debouncer.ts";
import type { ExchangeProcessor } from "../api.ts";

export interface GitExchangeProcessorDeps {
  debouncer: DebouncedTask;
  log: Logger;
}

/**
 * Per-exchange signal for the debounced workspace commit-push. Each exchange resets
 * the debounce timer; the commit-and-push itself runs in the background, once, after
 * the configured quiet window elapses with no further exchange (see
 * `commitAndPushWorkspace` and `createDebouncedTask`). Nothing is committed on the
 * exchange path — the trunk-close finalize pass remains the persistence backstop.
 */
export const createGitExchangeProcessor = ({
  debouncer,
  log,
}: GitExchangeProcessorDeps): ExchangeProcessor => ({
  name: "git-exchange-signal",

  async process() {
    debouncer.touch();
    log.debug("workspace commit-push signal — debounce timer reset");
  },
});
