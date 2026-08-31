# External Extensions

Out-of-tree features loaded through the same contract as first-party ones. Owned by the
external extension.

## Tools

| Tool | Role |
|------|------|
| `install_extension` | Clone a git source (or add a local path) and register it under an alias |
| `update_extension` | Update an installed extension to its latest version |
| `list_installed_extensions` | List what is installed |
| `uninstall_extension` | Remove one by alias |

All changes take effect on the next restart — the running process keeps its current set.
Say so when the person installs or removes one.

## How loading works

An installed extension is a module exporting `defineExtension(...)` — the same contract
first-party extensions use (`setup(app)` wiring into the host API). Sources come from
`[extensions.external]` `sources` (local paths, `~` expanded, workspace-relative allowed)
plus agent-installed records under `{dataDir}/extensions/<alias>`. A third-party `setup`
runs with a timeout (`setupTimeoutMs`, default 30 s): a hung setup is skipped, not fatal —
the rest of the harness comes up regardless.

## Configuration

`[extensions.external]`: `sources` (default `[]`), `setupTimeoutMs` (default `30000`).
