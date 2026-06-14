import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    coverage: {
      provider: "v8",
      all: true,
      include: ["src/**/*.ts"],
      reporter: ["text", "html"],
      // Files with no branching logic worth covering: process entrypoints and
      // the composition root, extension wiring (defineExtension+setup that only
      // registers hooks), pure type declarations, drizzle schema definitions,
      // agent prompt-text constants, thin I/O seams (their orchestration is
      // tested against fakes), and the interactive migration prompt.
      exclude: [
        "src/main.ts",
        "src/app.ts",
        "src/channels/types.ts",
        // extension wiring entrypoints (boundary/memory/skills index files hold
        // real logic and are deliberately NOT listed here)
        "src/extensions/index.ts",
        "src/extensions/context/index.ts",
        "src/extensions/detached-processes/index.ts",
        "src/extensions/external/index.ts",
        "src/extensions/git/index.ts",
        "src/extensions/notifications/index.ts",
        "src/extensions/projects/index.ts",
        "src/extensions/repl/index.ts",
        "src/extensions/self-update/index.ts",
        "src/extensions/tasks/index.ts",
        "src/extensions/telegram/index.ts",
        "src/extensions/workflows/index.ts",
        "src/extensions/*/usage.ts",
        "src/extensions/*/schema.ts",
        "src/extensions/self-update/seams.ts",
        "src/migration/index.ts",
        "src/migration/ask.ts",
      ],
    },
  },
});
