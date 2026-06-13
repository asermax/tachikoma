import type { Logger } from "../../log.ts";
import type { SessionsApi } from "../api.ts";

export type IdleSessions = Pick<SessionsApi, "onExchange" | "closeIfIdle">;

/**
 * Temporal session boundary: after each exchange, (re)arm a timer that closes
 * the session once the conversation has been silent for idleCloseSeconds.
 * closeIfIdle() no-ops when an exchange is in flight, and the timer is re-armed
 * by the next exchange — so a firing during activity loses nothing.
 */
export const registerIdleClose = (
  sessions: IdleSessions,
  idleCloseSeconds: number,
  log: Logger,
): void => {
  let timer: NodeJS.Timeout | null = null;

  sessions.onExchange({
    name: "idle-close-timer",

    async process() {
      if (timer != null) clearTimeout(timer);

      timer = setTimeout(() => {
        timer = null;

        void sessions
          .closeIfIdle()
          .then((closed) => {
            if (closed) log.info({ idleCloseSeconds }, "idle timeout reached — session closed");
          })
          .catch((error) => log.error({ err: error }, "idle close failed"));
      }, idleCloseSeconds * 1000);
      timer.unref();
    },
  });
};
