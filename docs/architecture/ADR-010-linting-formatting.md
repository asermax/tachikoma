# ADR-010: Linting and Formatting

**Status**: Accepted
**Date**: 2026-06-11

## Context

The quality gate wants one fast tool for both linting and formatting, one config file, one command. The conventional TypeScript answer (ESLint + Prettier + a stack of plugins and peer dependencies) is notoriously heavy and slow.

The repository is not only TypeScript. It holds Markdown documents, `SKILL.md` guides whose YAML frontmatter a skill loader parses, and files that other tools read by their layout: `CHANGELOG.md`, which semantic-release rewrites on every release, and `docs/planning/DELTAS.md`, which the planning script reads. One formatter for all of it is the goal, as long as it leaves what those tools depend on intact.

## Decision

Use **Biome** for both linting and formatting.

- Single `biome.json` configuration. It carries the schema URL of the locked biome version, which `biome migrate` rewrites on an upgrade, and selects the recommended rules through `linter.rules.preset`
- `biome check .` as the lint gate, `biome check --write .` as the formatter (wired as `just lint` / `just fmt`)
- Recommended rule set as the baseline, adjusted only when a rule fights the house style
- One formatter for the whole repository: TypeScript, JSON, Markdown and YAML. Every Markdown and YAML edit ends with `just fmt`, like any source file
- YAML frontmatter is parsed, not excluded. `markdown.parser.frontmatter` is on, so a frontmatter block is formatted as YAML and keeps its content. The default Markdown parser treats the block as prose: it strips the indentation of a block scalar such as a skill's `description`, and the skill no longer loads
- Embed formatting stays at its default. A fenced code block tagged with a language biome knows is formatted as that language, a block biome cannot parse is left as written, and a `text` block is kept verbatim
- Two files are excluded through `files.includes`, because a tool reads each by its layout: `CHANGELOG.md`, which semantic-release rewrites with its own bullet style on every release, and `docs/planning/DELTAS.md`, whose status line the planning script matches directly under each delta's heading

The rule for any further file a tool reads by layout is the same: exclude it in `files.includes`, or in the repository's own `.gitignore`, which biome reads. The global git exclude file is not read, so it is no exclusion. The alternative is to make the tool parse the file; leaving the layout to the formatter is not.

## Consequences

### Positive

- One Rust-fast binary replaces ESLint + Prettier + plugin chains; whole-repo checks are effectively instant
- One config file, no plugin/peer-dependency matrix to maintain
- Formatter and linter never disagree (a classic ESLint/Prettier failure mode)
- Import organizing built in
- Documents get the same one-command formatting as the code, and files with YAML frontmatter need no exclusion

### Negative

- Smaller rule ecosystem than ESLint — in particular, no equivalent for some type-aware rules from typescript-eslint; `tsc --noEmit` (strict mode) covers the type-level ground
- Opinionated formatter with limited knobs; accepted
- No third-party plugin system yet, so project-specific custom rules aren't an option
- A hand-edited Markdown or YAML file that is not in biome's layout fails the lint gate, so every such edit ends with `just fmt`
- Embed formatting also reaches fenced examples inside documents: a `ts` or `yaml` example is reformatted as that language, which can move blank lines or add punctuation to an example laid out by hand. A block that must stay verbatim is tagged `text`
- Each file a tool reads by layout needs its own exclusion, and a new one is noticed only when the tool breaks

## Alternatives Considered

- **ESLint + Prettier**: maximum rule coverage, but two tools, slow runs, and a config/plugin surface that demands ongoing maintenance
- **oxlint**: even faster linting, but no formatter and a younger rule set — would still need a second tool
