# DES-011: Post-Processing Agent Shapes — Conversation-Aware Fork vs Context-Free Headless Run

**Scope**: Project-wide
**Date**: 2026-08-30
**Last Updated**: 2026-08-30

## Pattern

Post-conversation LLM work picks one of two shapes, selected by a single question: **does the work need the live conversation?**

- **Conversation-aware fork** — `agent.forkAndContinue(branchFile, instruction, "processor", FILE_EDIT_TOOLS)`: the branch's turns live in the fork's history and the composed persona is intact, so the same assistant that had the conversation reflects on it. Used when the *evidence* is in the conversation: memory's per-branch extraction, skill-evolution's per-branch analysis.
- **Context-free headless run** — `side.run({ system, prompt, customTools })`: an ephemeral session with no persona and no conversation; the host assembles every input (store contents, diffs, pattern inventories) and scopes custom tools to exactly what the run needs. Used when the inputs are enumerable by the host: memory maintenance, skill-evolution maintenance and proposals, the git commit agent, the rebase resolver, task-goal extraction.

The rule is mechanical: if the signal lives in what was said and tried (tool calls, thinking, workarounds), fork; if the host can write down the complete input as prompt sections, run headless. The pi-side mechanics of both shapes are documented canonically in [../reference/pi-sdk-notes.md](../reference/pi-sdk-notes.md) (Post-processing patterns) — this DES records the selection rule and the repo's consumers.

## Rationale

Replaying a transcript as text into a headless prompt is lossy: tool activity and thinking are where failures and workarounds actually show, and a replay flattens them. Forking for work whose inputs the host can assemble wastes the persona, pays conversational context for deterministic work, and drags conversational noise into a run that should be reproducible from its prompt alone. Picking by the one question keeps the cost and fidelity trade-off automatic instead of per-feature taste.

## Examples

### Do This

```
// evidence is conversational → fork the branch's session, file tools only
await agent.forkAndContinue(branchFile, analyzeInstruction, "processor", FILE_EDIT_TOOLS)

// inputs are enumerable → headless run over host-assembled prompt sections
await side.run({ system: maintenancePrompt, prompt: storeContents, customTools: [...] })
// (side.run's built-in allowlist is empty by default — the custom tools are the whole surface)
```

**Why**: The fork sees the real conversation (full-fidelity evidence) with exactly the file tools it needs; the headless run gets a complete, self-contained input so it is reproducible and its tool surface is exactly what was granted.

### Don't Do This

```
// transcript replay into a headless prompt                       // ❌ lossy — tool calls and thinking disappear
// a persona fork for store maintenance or branch authoring        // ❌ nothing conversational to inherit
// a headless run granted tools it does not use                    // ❌ surface without a reason
```

**Why**: Replay discards the strongest signal; a persona fork pays context and persona cost for work that never needed it; unneeded tools widen the blast radius of a confused run for zero benefit.

## Exceptions

- A **conversation excerpt as an input** (boundary classification over recent messages, rolling summaries) is still the context-free shape: the excerpt is a host-assembled prompt section, not a live session history. The shape is about whether the run *continues* a session, not whether conversation text appears anywhere in the prompt.
- **Persistent background sessions** (`side.openBackgroundSession`) are a third mechanism but not a third shape for post-processing: they serve multi-turn interactive task runs, not post-conversation work.

## Related

- See also: [../reference/pi-sdk-notes.md](../reference/pi-sdk-notes.md) — the canonical pi-side description of both shapes
- See also: [DES-005](DES-005-base-prompt-ownership.md) — where the prompts for these shapes live (core base prompts vs feature-local side-run task prompts)
- Pattern rule: [DES-002](DES-002-extension-authoring.md) — `FILE_EDIT_TOOLS` and the `SideRunner` live in neutral modules (`src/agent/`)
- Related features: [memory](../feature-designs/memory.md) (forks for extraction, headless for maintenance), [skill-evolution](../feature-designs/skill-evolution.md) (fork for analysis, headless for maintenance and proposals), [git-workspace](../feature-designs/git-workspace.md) (headless commit agent and rebase resolver)
- Implementation: `src/agent/side-run.ts`, `src/agent/file-tools.ts`, `src/agent/manager.ts` (`forkAndContinue`)
