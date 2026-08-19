/**
 * Liveness via signal 0. EPERM means the pid exists but belongs to another
 * user, so it still counts as alive. Liveness alone cannot distinguish a
 * reused pid — callers that need stronger identity layer their own check
 * (e.g. `src/instance-lock.ts` compares `/proc/<pid>/stat` start times).
 */
export const isAlive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
};
