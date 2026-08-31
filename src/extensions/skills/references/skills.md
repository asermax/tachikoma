# Skills

Workspace skills and delegation. Owned by the skills extension.

## Loading

Skills live as directories under the workspace `skills/` (each with a `SKILL.md`; a
`references/` subdirectory holds detail the skill's instructions point into on demand).
Loading has two paths:

- **Proactive injection** (default on): each turn a conversation-aware classifier picks
  relevant skills and injects their full instructions directly — hidden from the user, no
  `/skill` needed. Injection state resets on a topic shift, so a new branch re-evaluates
  from scratch.
- **The catalog**: every skill's name and description is browsable; when one fits, read its
  `SKILL.md` and follow it before proceeding on your own.

`/skill` (pi-native) loads a skill explicitly. New or edited skills are picked up without a
restart on the next session.

## Built-in authoring skills

The extension ships built-in authoring skills (`skill-authoring`, `workflow-authoring`) —
find them in the catalog like any other.

## Delegation (`delegate_to_agent`)

Hands a self-contained sub-task to a subagent that runs in its own context and reports back
— use it to keep your own context clear on focused, context-heavy work. The roster is the
built-in general-purpose agent plus any agents bundled with workspace skills (a skill
directory can define its own). Grant tools the work needs through `extensionTools` (e.g.
web search/scrape) so they execute in the isolated subagent; without them the subagent has
only its file tools.

## Configuration

`[extensions.skills]`: `enabled` (default `true`); `proactiveLoading` (default `true`)
disables proactive injection, falling back to catalog + `/skill` loading only — the guidance
above stays useful either way.
