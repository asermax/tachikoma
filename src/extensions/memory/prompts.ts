import type { MemoryStore } from "./layout.ts";

export const STORE_PURPOSE_SECTION = `## Store Purpose Definitions

Each information store serves a distinct purpose. Use these definitions to route information correctly:

- **Skills** (\`skills/\`): Installed skills under the workspace \`skills/\` directory. Each skill's SKILL.md owns the authoritative operational knowledge for its domain — the most authoritative store. When a skill covers a topic, defer to it rather than restating its content elsewhere.
- **Memory facts** (\`memories/facts/\`): Stable reference information — project state, technical decisions, configuration, people, system architecture. Most authoritative store for reference information.
- **Memory preferences** (\`memories/preferences/\`): Subjective choices with rationale — how the user wants things done, with context about why.
- **Memory episodic** (\`memories/episodic/\`): Date-stamped summaries of what happened — events, outcomes, and decisions tied to a specific day.
- **Context files** (workspace root): Concise summaries and pointers — personality (SOUL.md), user identity (USER.md), behavioral guidance (AGENTS.md). Context should reference facts rather than inlining their content.

Authority order: Skills > Memory facts > Context files. When information appears in multiple stores, the more authoritative source is correct. Do not write memory or context content that an installed skill already owns for its domain. Context files should contain summaries and pointers, not detailed operational content.`;

export const CLASSIFICATION_EXAMPLES_SECTION = `## Classification Examples

Use these examples to route information correctly between facts and preferences.

### IS a preference (subjective choice about HOW the user wants things done):
- "I prefer concise commit messages over detailed ones" → style preference
- "Always run tests before pushing" → workflow preference
- "Use dark theme for code editors" → tooling preference
- "I like getting a summary first, then details on request" → communication style preference
- "Prefer smaller, focused PRs over large ones" → workflow preference

### NOT a preference (describes HOW SOMETHING WORKS — belongs in facts or project docs):
- "The payment gateway has a 2% processing fee" → financial reference data → facts
- "The reconciliation process runs nightly at 3am" → financial reference data → facts
- "Environment variable FOO must be set to BAR for the service to start" → technical specification → facts
- "The SDK has a 200K token context window" → technical specification → facts
- "The API rate limit is 100 requests per minute" → technical specification → facts
- "The cleanup procedure involves stopping the service, clearing /tmp, and restarting" → procedural workflow → facts
- "We configured nginx to proxy /api to port 8080" → system configuration record → facts
- "The deployment uses blue-green strategy with health checks" → system configuration record → facts

### IS a fact (stable reference information that stays true across conversations):
- "The CI pipeline runs on GitHub Actions" → infrastructure fact
- "User X is the team lead for project Y" → organizational fact
- "The database uses PostgreSQL 16 with async replication" → technical fact
- "The API rate limit is 100 requests per minute" → technical fact

### NOT a fact (belongs in episodic memory or project docs):
- "We debugged the payment timeout issue on 2026-05-10" → one-time event → episodic
- "The full architecture of system X is..." → too long for facts → project docs`;

export const CONTEXT_DEDUP_SECTION = `## Context File Deduplication

Before creating any new memory file, check whether the information is already covered in the foundational context files at the workspace root. These files are maintained as the authoritative source for certain categories of information.

1. Read the context files:
   - \`$WORKSPACE/AGENTS.md\` — operational guidelines and workflow preferences
   - \`$WORKSPACE/USER.md\` — stable identity and high-level project awareness
   - \`$WORKSPACE/SOUL.md\` — personality and communication style

2. For each piece of information you're about to write:
   - If the SAME information is already in one of these files, do NOT create a memory file for it — the context file is the source of truth
   - If a context file partially covers the topic but the conversation adds genuinely new details not found there, you MAY create a file — but only for the new information, not a restatement of what's already in the context file
   - If no context file covers the topic, proceed normally

## Skill Deduplication

Installed skills under the workspace \`skills/\` directory are authoritative for their domain — each skill's SKILL.md owns the operational knowledge for the topic it covers.

- If a covered skill already owns the information you're about to write, do NOT write a memory or context entry for it — the skill is the source of truth.
- Only record genuinely new details a covered skill does not already capture, and never restate instructions a skill already provides.

This check prevents duplicating information across the memory system and the context files, which would create confusion about which is authoritative.`;

export const WORKSPACE_VALIDATION_SECTION = `## Workspace Validation

Before writing a memory that contains claims about workspace state — file paths, project structure, configuration values, implementation details — validate each claim against the actual workspace:

1. Identify verifiable claims in the memory you're about to write:
   - References to specific files or directories (do they exist? contain what's claimed?)
   - Configuration values (does the config file actually say that?)
   - Project state (is the project actually in that state?)

2. Verify each claim directly: read the relevant file(s) or grep for the referenced content.

3. Only include claims you could verify:
   - If a claim turns out to be false, omit it
   - If ALL claims are invalid, do not create the file

Do NOT validate: subjective information, preferences, general knowledge, conversation summaries, personal details — only verifiable claims about workspace state.`;

export const INDEX_UPDATE_SECTION = `## Memory Index

When you create, modify, or delete a memory file, you MUST also update MEMORY.md in the same directory to keep the index in sync.

### Entry Format

Each entry in MEMORY.md follows this format:

\`\`\`
[Human-readable Name](./filename.md): One-line description of contents
\`\`\`

### Rules

- **CREATE** a new file: add a new entry with an appropriate description
- **MODIFY** a file: update the description if the topic, focus, or scope meaningfully changed
- **DELETE** a file: remove the corresponding entry
- Always preserve the \`# Memory Index\` header
- Keep descriptions concise — one line, under 80 characters
- The name in brackets should be a human-readable topic name (Title Case)

### Example Entries

\`\`\`
# Memory Index

[API Design](./api-design.md): API architecture decisions and endpoint patterns
[Work Info](./work-info.md): Job details, team structure, and work schedule
[Tech Stack](./tech-stack.md): Primary languages, frameworks, and tools used
\`\`\``;

export const INDEX_LIGHT_MAINTENANCE_SECTION = `## Memory Index Consistency

Verify index consistency between MEMORY.md and actual files in the directory.

### Steps

1. **List all files**: list all \`.md\` files in the directory (excluding \`MEMORY.md\` itself) using the ls or find tool.
2. **Read MEMORY.md**: Parse the existing index entries.
3. **Add missing entries**: For files that exist in the directory but have no entry in MEMORY.md, add a placeholder entry:
   \`\`\`
   [Topic](./filename.md): Description pending update
   \`\`\`
4. **Remove stale entries**: For entries in MEMORY.md that reference files that no longer exist, remove those entries.
5. **Preserve existing descriptions**: Do NOT regenerate or modify existing descriptions — leave them unchanged even if stale. Description regeneration is handled by the weekly full rebuild.

### Rules

- Always preserve the \`# Memory Index\` header.
- When you create, modify, or delete memory files during this maintenance run, also update MEMORY.md per the standard index update rules (add new entries, update descriptions on meaningful changes, remove entries for deleted files).
- The consistency check runs in addition to your normal maintenance tasks.`;

export const scopeSection = (store: MemoryStore): string => `## Scope

You can read files anywhere in the workspace (needed for validation and deduplication). Only create or modify files within \`$WORKSPACE/memories/${store}/\`. You have no delete tool: when a file must go away (merged into another, obsolete, or misnamed), overwrite it with empty content — empty files are cleaned up automatically after you finish.`;
