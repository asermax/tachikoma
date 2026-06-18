import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";

/**
 * Typed, named access to pi's session-tree primitives (`SessionManager`), centralized so the
 * daily-trunk model has one seam over the SDK. Tachikoma reverses the earlier "bypass the pi session
 * tree" decision (see ADR-014): trunk and branch state live on the session file as native tree
 * entries, not in a separate registry. These helpers wrap `session.sessionManager` so the convention
 * around branch summaries and custom entries is documented in one place.
 *
 * The verified pi seam is tracked in `docs/reference/pi-sdk-notes.md` and must be re-checked on a pi
 * upgrade.
 */

/** Details we persist on every `branch_summary` entry so a collapsed branch stays walkable. */
export interface BranchSummaryDetails {
  /** Custom-type tag distinguishing our branch summaries from pi-native ones. */
  customType: string;
  /** Deterministic `topic-N` id (N = count of branch_summary entries at collapse + 1). */
  branchId: string;
  /**
   * Leaf of the abandoned branch at collapse. `branchWithSummary` does NOT record the abandoned
   * leaf, so we store it ourselves — this is what makes the full branch reachable afterwards via
   * `getBranchEntries`/`AgentManager.branchFile` for `ask_branch` and per-branch extraction.
   */
  originalLeafId: string;
  /** The base (current trunk tip) the branch extended; extraction slices from here forward. */
  baseId: string | null;
  /** Why the shift happened (classifier/forced reason), for provenance. */
  reason?: string;
  /** Last user+assistant exchange of the branch, for cheap related-branch pointers. */
  lastExchange?: string | null;
}

/**
 * Collapse the current branch into a `branch_summary` entry appended at `branchFromId` and re-seat
 * the leaf there. `fromHook` is always true (the summary is extension-generated, not pi-native).
 * Returns the new summary entry id (the advanced base).
 */
export const branchWithSummary = (
  session: AgentSession,
  branchFromId: string | null,
  summary: string,
  details: BranchSummaryDetails,
): string => session.sessionManager.branchWithSummary(branchFromId, summary, details, true);

/** Walk from `fromId` (default: current leaf) to root, returning entries in path order. */
export const getBranchEntries = (session: AgentSession, fromId?: string): SessionEntry[] =>
  session.sessionManager.getBranch(fromId);

/**
 * The live branch's own entries: those on the leaf path strictly after `baseId` (or the whole path
 * when there is no base yet). The base is the latest collapse summary the live branch extends, so
 * this is "what's been said since the last topic shift" — used for the empty-branch guard and for
 * summarizing a branch at collapse.
 */
export const branchEntriesSinceBase = (
  session: AgentSession,
  baseId: string | null,
): SessionEntry[] => {
  const branch = getBranchEntries(session);
  const baseIndex = baseId == null ? -1 : branch.findIndex((entry) => entry.id === baseId);

  return branch.slice(baseIndex + 1);
};

/** All session entries (excludes header); shallow copy. */
export const enumerateEntries = (session: AgentSession): SessionEntry[] =>
  session.sessionManager.getEntries();

export const getEntry = (session: AgentSession, id: string): SessionEntry | undefined =>
  session.sessionManager.getEntry(id);

/**
 * Plain text of a message entry: the joined `text` blocks of its content. Returns `""` for
 * non-message entries or entries whose content isn't a block array, so callers can treat empty as
 * "no text". Centralized so branch-summary rendering and channel context recovery share one read of
 * pi's message shape.
 */
export const messageText = (entry: SessionEntry): string => {
  if (entry.type !== "message") return "";

  const content = (entry.message as { content?: unknown }).content;
  if (!Array.isArray(content)) return "";

  return content
    .filter(
      (block): block is { type: "text"; text: string } =>
        block != null &&
        typeof block === "object" &&
        (block as { type?: unknown }).type === "text" &&
        typeof (block as { text?: unknown }).text === "string",
    )
    .map((block) => block.text)
    .join("\n")
    .trim();
};

export const getLeafId = (session: AgentSession): string | null =>
  session.sessionManager.getLeafId();

/**
 * The session's creation instant (its header timestamp), or null if absent. Used to recover a
 * trunk's true calendar day when closing it late — the day it happened, not the day we got to it.
 */
export const sessionCreatedAt = (session: AgentSession): string | null =>
  session.sessionManager.getHeader()?.timestamp ?? null;

/**
 * Re-seat the leaf onto an earlier entry so the next turn extends it. On `SessionManager.open`
 * pi sets the leaf to the LAST file entry, not the active branch tip — the trunk lifecycle uses
 * this to re-seat onto the current base after a reload (see ADR-014, R9).
 */
export const reseatLeaf = (session: AgentSession, entryId: string): void =>
  session.sessionManager.branch(entryId);

/** Persist extension state as a custom entry (out of LLM context); returns the entry id. */
export const appendState = (session: AgentSession, customType: string, data: unknown): string =>
  session.sessionManager.appendCustomEntry(customType, data);

/**
 * Append a hidden custom message entry that participates in LLM context (display=false). Used to
 * inject cross-branch context (the related-branch pointer) without showing it in the channel.
 */
export const appendInContextEntry = (
  session: AgentSession,
  customType: string,
  content: string,
  details?: unknown,
): string => session.sessionManager.appendCustomMessageEntry(customType, content, false, details);

// Branching a single topic's full conversation into a throwaway file lives on `AgentManager`
// (`branchFile`/`shadowFork`), NOT here: pi's `createBranchedSession` rewrites the manager it runs
// on IN PLACE, so it must run on a manager loaded fresh from disk (which needs the sessions dir the
// manager owns), never on a live session's manager. See docs/reference/pi-sdk-notes.md.
