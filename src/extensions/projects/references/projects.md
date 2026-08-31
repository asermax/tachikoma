# Projects

External repositories as workspace submodules. Owned by the projects extension.

## Registration and sync

`register_project` clones a git URL (or adds a local path) as a submodule under `projects/`;
`list_projects` reports live state; `deregister_project` removes one. Every startup syncs
registered projects (pulling where possible), and a session-start snapshot of each project's
state is injected into your context — branch or detached commit, uncommitted-change count.

## Persistence

Dirty projects are committed and pushed automatically: once `scheduler.commitDebounceMinutes`
(default 5; `0` disables mid-session) of exchange quiet elapses, and always at trunk close —
project commits land *before* the workspace commit, so updated submodule pointers are
committed together with the workspace in the same pass. Clean projects whose branch is ahead
of their remote are pushed too. You never need to run git inside a project for this.

## Auth

Git credentials (SSH keys, tokens) are the person's responsibility — Tachikoma does not
store them. If a clone or push fails on authentication, say so and point them at configuring
credentials externally rather than retrying.

## Configuration

`[extensions.projects]`: `enabled` (default `true`).
