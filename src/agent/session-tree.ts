import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";

import { BRANCH_SUMMARY } from "../sessions/trunk.ts";

/**
 * Typed, named access to pi's session-tree primitives (`SessionManager`), centralized so the
 * daily-trunk model has one seam over the SDK. Tachikoma reverses the earlier "bypass the pi session
 * tree" decision (see ADR-014): trunk and branch state live on the session file as native tree
 * entries, not in a separate registry. These helpers wrap `session.sessionManager` so the convention
 * around branch summaries and custom entries is documented in one place.
 *
 * The verified pi seam is tracked in `docs/reference/pi-sdk-notes.md` and must be re-checked on a pi
 * upgrade. That re-check covers how pi builds a request, not only the primitive signatures: since pi
 * 0.86.0 a request is projected from the session file's leaf path, and its prompt sections and tools
 * are replayed from the system messages on it. Two conventions keep a request complete (see
 * `rootDeclarationId` and `syncTranscriptToLeaf`): the summary chain descends from the session's
 * system message, and every leaf move brings the agent's in-memory transcript onto the new leaf.
 */

/** Details we persist on every `branch_summary` entry so a collapsed branch stays walkable. */
export interface BranchSummaryDetails {
  /** Custom-type tag distinguishing our branch summaries from pi-native ones. */
  customType: string;
  /** Deterministic `topic-N` (topics) or `tangent-N` (tangents) id; N = position among same-kind summaries + 1. */
  branchId: string;
  /**
   * What this summary records: `"topic"` (a collapsed prior topic — the default) or `"tangent"` (a
   * summarized side conversation parked at a checkpoint, DLT-181). Absent on summaries written before
   * the discriminator existed; readers treat absence as `"topic"`. `"reversed"` is never persisted — it
   * is computed at read time from a reversal marker (see `effectiveKind` in `sessions/trunk.ts`).
   */
  kind?: "topic" | "tangent";
  /**
   * Leaf of the abandoned branch at collapse. `branchWithSummary` does NOT record the abandoned
   * leaf, so we store it ourselves — this is what makes the full branch reachable afterwards via
   * `getBranchEntries`/`AgentManager.branchFile` for `ask_branch` and per-branch extraction.
   */
  originalLeafId: string;
  /**
   * The base (current trunk tip) the branch extended; extraction slices from here forward. `null` when
   * no summary precedes it: the branch that starts the day's trunk. Its entry then hangs under the
   * session's system message (see `branchWithSummary`); the `null` says only that no summary is above.
   */
  baseId: string | null;
  /** Why the shift happened (classifier/forced reason), for provenance. */
  reason?: string;
  /** Last user+assistant exchange of the branch, for cheap related-branch pointers. */
  lastExchange?: string | null;
}

/**
 * Bring the agent's in-memory transcript onto the leaf the session file now sits on.
 *
 * pi builds every request from the session file: the messages, the tools and the prompt sections the
 * model may see are replayed from the leaf path. Whether anything new must be declared is decided
 * against the in-memory transcript (`agent.state.messages`). pi re-syncs that transcript after every
 * change it commits through the session layer, its own leaf navigation included. Every write made
 * through `SessionManager` directly bypasses that layer, and a leaf move is the one that matters: it
 * puts the leaf on a different path while the transcript still describes the old one. Left there, a
 * request built from a path that declares less than the transcript reports goes out with the
 * difference missing — every tool, on a path with no system message. `refreshContext()` is pi's own
 * re-sync: the transcript follows the leaf, together with the bookkeeping pi keeps beside it, and pi's
 * diff then declares exactly what the new path lacks.
 *
 * The trunk's summary chain descends from the session's system message (`rootDeclarationId`), so the
 * path already carries the prompt sections and tools after a collapse or a reopen onto it, and there
 * is nothing to declare. The re-sync still earns its place where that is not so: on a trunk whose
 * chain was written before it was rooted, which gets a full declaration again, and for a tool switched
 * on inside the abandoned topic, which comes back as a small delta on the next one.
 *
 * Call only between exchanges: the in-memory transcript must be fully persisted, or the re-sync drops
 * its unpersisted tail from the declaration baseline.
 *
 * It runs once the raw move has taken effect. If it threw, the session file would already carry the
 * move — a collapse's summary entry and leaf, or a re-seat's leaf — and only the in-memory transcript
 * would lag behind it.
 */
const syncTranscriptToLeaf = (session: AgentSession): void => {
  session.refreshContext();
};

/**
 * The entry a summary with no summary above it hangs under: the session's system message, the
 * root-most declaration on the live branch's path. pi replays a request's prompt sections and tool
 * declarations from the system messages on the leaf path, so a summary chain that starts there
 * carries them on every path the trunk can continue from — the chain itself, a rollback rewind, a
 * reopen's re-seat. It is the root-most one on purpose: a later system message (a tool switched on
 * mid-topic) sits below the topic's own turns, so a summary hung under it would carry them onto the
 * trunk.
 *
 * `null` when the live path carries no system message; the summary is then a parentless root, as pi
 * writes it.
 */
const rootDeclarationId = (session: AgentSession): string | null =>
  getBranchEntries(session).find(
    (entry) => entry.type === "message" && entry.message.role === "system",
  )?.id ?? null;

/**
 * Collapse the current branch into a `branch_summary` entry appended at `branchFromId` and re-seat
 * the leaf there. `fromHook` is always true (the summary is extension-generated, not pi-native).
 * Returns the new summary entry id (the advanced base). The agent's in-memory transcript follows the
 * leaf (`syncTranscriptToLeaf`).
 *
 * A `null` base is rooted at the session's system message (`rootDeclarationId`) instead of becoming
 * a parentless root, so the topic chain descends from the day's declaration. A summary with a base
 * extends it and a tangent summary hangs at its checkpoint; both already descend from that entry.
 */
export const branchWithSummary = (
  session: AgentSession,
  branchFromId: string | null,
  summary: string,
  details: BranchSummaryDetails,
): string => {
  // Resolved before the move, while the leaf is still on the live branch.
  const summaryId = session.sessionManager.branchWithSummary(
    branchFromId ?? rootDeclarationId(session),
    summary,
    details,
    true,
  );

  syncTranscriptToLeaf(session);

  return summaryId;
};

export interface CollapseTangentOptions {
  /** The tangent's own `tangent-N` id (separate sequence from `topic-N`). */
  tangentId: string;
  /** The abandoned tip (leaf of the tangent branch at collapse), stored so the branch stays reachable. */
  originalLeafId: string;
}

/**
 * Collapse a tangent rooted at `checkpointId` into a `branch_summary` and re-seat the leaf onto it, so
 * the main line resumes at the checkpoint (R5). The checkpoint entry is the `branchFromId`, so the
 * tangent folds into a summary rooted at the main-line tip — only the tangent is parked away, never the
 * main line (the defining difference from a topic shift). `details.kind = "tangent"` marks it for the
 * kind-filter chokepoint (KD3); the summary is then excluded from `getBranchRecords`/`ask_branch`/
 * extraction. Returns the new summary entry id (the resumed main-line tip).
 *
 * See ADR-014: the tree is append-only, so this is a collapse + re-seat, never a move/delete.
 */
export const collapseTangent = (
  session: AgentSession,
  checkpointId: string,
  summary: string,
  options: CollapseTangentOptions,
): string =>
  branchWithSummary(session, checkpointId, summary, {
    customType: BRANCH_SUMMARY,
    branchId: options.tangentId,
    kind: "tangent",
    originalLeafId: options.originalLeafId,
    baseId: checkpointId,
  });

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
 * Whether at least one conversational turn follows the checkpoint — the empty-tangent guard (KD2). A
 * turn is a message entry (user/assistant) on the leaf path strictly after the checkpoint. Infrastructure
 * entries do NOT count: `setCheckpoint` appends a boomerang-state entry, which advances the leaf past the
 * checkpoint message, so a raw `getLeafId !== checkpointId` check would falsely report a tangent after
 * every `/checkpoint`. Walking the path for a real message (the same pattern
 * `hasAssistantTurnSinceBase` uses) is robust to those appended markers. When no turn follows, `/back`
 * short-circuits before the append-only collapse primitive, which would otherwise create a vacuous summary.
 */
export const checkpointHasTangent = (session: AgentSession, checkpointId: string): boolean =>
  branchEntriesSinceBase(session, checkpointId).some((entry) => entry.type === "message");

/**
 * The session's creation instant (its header timestamp), or null if absent. Used to recover a
 * trunk's true calendar day when closing it late — the day it happened, not the day we got to it.
 */
export const sessionCreatedAt = (session: AgentSession): string | null =>
  session.sessionManager.getHeader()?.timestamp ?? null;

/**
 * Re-seat the leaf onto an earlier entry so the next turn extends it, and bring the agent's in-memory
 * transcript along (`syncTranscriptToLeaf`). On `SessionManager.open` pi sets the leaf to the LAST
 * file entry, not the active branch tip — the trunk lifecycle uses this to re-seat onto the current
 * base after a reload (see ADR-014, R9).
 */
export const reseatLeaf = (session: AgentSession, entryId: string): void => {
  session.sessionManager.branch(entryId);

  syncTranscriptToLeaf(session);
};

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
