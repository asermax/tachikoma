import { resolve } from "node:path";

/**
 * Location of the skills extension's bundled `builtin-skills/` directory (the `skill-authoring`
 * and `workflow-authoring` guides). Lives in this neutral module — not the extension itself —
 * because two extensions consume it: skills (contributes it as a skill source) and
 * skill-evolution (discovers the authoring guides for its proposal runs), and DES-002 bars
 * direct imports between extension directories. The climb from `src/util/` reaches
 * `src/extensions/skills/builtin-skills`; `scripts/copy-assets.mjs` mirrors the same relative
 * layout into `dist/`, so it resolves identically from either (parity-tested at the mirroring
 * level — `tests/scripts/copy-assets.test.ts`; no test resolves the constant from a built
 * `dist/`).
 */
export const builtinSkillsDir = resolve(import.meta.dirname, "../extensions/skills/builtin-skills");
