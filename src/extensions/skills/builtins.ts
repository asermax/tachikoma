import { buildSubagentSystemPrompt, SUBAGENT_SYSTEM_PROMPT } from "../../agent/prompts.ts";
import type { SkillAgent } from "./agents.ts";

/**
 * Agents that ship with Tachikoma rather than being bundled in a skill, so the main agent always
 * has something to delegate to. The bare name (no `<skill>/` namespace) cannot collide with
 * discovered skill agents; `tools: null` falls back to the delegate tool's default read-only set
 * and `model: null` runs on the side-runner's default tier. `dynamicPrompt` rebuilds the worker
 * prompt from the granted tools so the built-in agent's read-only stance tracks what it can
 * actually do (its `systemPrompt` stays as the fallback).
 */
export const BUILTIN_AGENTS: SkillAgent[] = [
  {
    name: "general-purpose",
    description:
      "Explore or search files and gather information, reporting findings back. Read-only by default; runs in its own context to keep the main conversation's context clear.",
    tools: null,
    extensionTools: null,
    model: null,
    systemPrompt: SUBAGENT_SYSTEM_PROMPT,
    dynamicPrompt: (tools) => buildSubagentSystemPrompt({ tools }),
    skill: "built-in",
  },
];
