# Git

Workspace versioning and its guardrails. Owned by the git extension.

## What bash may not do

A blocklist of dangerous git patterns fails outright at the tool layer: `git push`, `git
reset`, `git checkout .` / `git restore .`, `git clean`, `git remote` mutations, `git rebase`,
`git filter-repo`. It is a pattern list, not all mutating git — other mutations (e.g. `git
commit`, `git checkout <branch>`) still run through bash, so check yourself before anything
history-rewriting or remote-touching. Read-only git (`status`, `log`, `diff`, `show`,
`branch`) and `git clone` are never blocked. The blocklist exists so history-rewriting and
remote-touching operations go through dedicated, safer tools:

- `commit_workspace` — commit everything in the workspace (and push, including project
  submodules with commits ahead of their remotes; `push=false` skips the push).
- `scrub` — purge paths from history, in the workspace repo or a project under `projects/`.

## How persistence runs

Mid-session, workspace changes are committed and pushed in the background once
`scheduler.commitDebounceMinutes` (default 5) of exchange quiet elapses; `0` disables that,
leaving close-time persistence only. At trunk close everything dirty is committed with a
generated message and pushed where a remote is configured. Push conflicts on the workspace
remote are recovered by rebasing the local state onto the remote and pushing again.

## Configuration

`[extensions.git]`: `enabled` (default `true`).
