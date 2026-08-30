import { existsSync } from "node:fs";

import type { AgentSession } from "@earendil-works/pi-coding-agent";

import type { AgentManager } from "../agent/manager.ts";
import {
  appendState,
  type BranchSummaryDetails,
  enumerateEntries,
  getEntry,
  reseatLeaf,
} from "../agent/session-tree.ts";
import type { KeyValueState } from "../db/state.ts";
import { localIsoDate } from "../util/dates.ts";

/**
 * Trunk identity and the on-file conversational state model (see ADR-014). The day is the unit of
 * conversation: one persistent pi session ("trunk") per local calendar day, identified by an
 * `app_state` pointer rather than a `sessions` table row. Topic shifts collapse into `branch_summary`
 * entries on the trunk; all boomerang/branch/marker state lives on the session file as custom entries
 * so it reloads natively and there is no DB↔file dual source of truth.
 */

/**
 * Custom-type tags for the entries this module reads and writes. Exported so the boundary rewrite
 * (DLT-181) and the trunk-close pipeline reuse the SAME tags — these strings are the contract between
 * the writer and every later reader, so they must never drift.
 */
export const BRANCH_SUMMARY = "tachikoma-branch-summary";
export const BOOMERANG_STATE = "tachikoma-boomerang-state";
export const COMPLETION_MARKER = "tachikoma-completion-marker";
/**
 * Marker appended at rollback (KD6) to orphan a `branch_summary` that a reversed automatic decision
 * left behind. A summary entry cannot be mutated (the tree is append-only), so its effective kind is
 * computed at read time from this marker — see {@link effectiveKind}.
 */
export const REVERSAL = "tachikoma-reversal";

/** Marker `kind`s distinguishing the idempotency markers stored under {@link COMPLETION_MARKER}. */
const MARKER_KIND = {
  branchExtracted: "extracted-marker",
  stepDone: "step-marker",
  skillEvolutionAnalyzed: "skill-evolution-analyzed",
} as const;

type MarkerKind = (typeof MARKER_KIND)[keyof typeof MARKER_KIND];

interface BranchExtractedMarker {
  kind: typeof MARKER_KIND.branchExtracted;
  branchId: string;
}

interface StepDoneMarker {
  kind: typeof MARKER_KIND.stepDone;
  step: string;
}

interface SkillEvolutionAnalyzedMarker {
  kind: typeof MARKER_KIND.skillEvolutionAnalyzed;
  summaryEntryId: string;
}

type CompletionMarker = BranchExtractedMarker | StepDoneMarker | SkillEvolutionAnalyzedMarker;

/** Payload of a {@link REVERSAL} marker naming the orphaned `branch_summary` entry. */
interface ReversalMarker {
  summaryEntryId: string;
}

/** The active-trunk pointer persisted under `app_state["trunk"]["active"]`. */
export interface TrunkPointer {
  sessionFile: string;
  /** Local calendar date (`YYYY-MM-DD`) in the configured timezone this trunk belongs to. */
  day: string;
  openedAt: string;
}

/** A collapsed topic, derived from a `branch_summary` entry's `details`. */
export interface BranchRecord {
  /** Deterministic `topic-N` id; N is this entry's 1-based position among branch summaries. */
  branchId: string;
  originalLeafId: string;
  baseId: string | null;
  summaryEntryId: string;
  lastExchange: string | null;
}

/**
 * An automatic branching decision recorded so `/rollback` can reverse it (KD5). Only `set-checkpoint`
 * and `new` are rollback targets; manual decisions, `continue`, and `summarize-to-checkpoint` are not
 * recorded. The triggering message is read from the tree at rollback time, so this stores the leaf
 * *before* the triggering exchange rather than a message id.
 */
export interface AutoDecision {
  kind: "set-checkpoint" | "new";
  /** The leaf id immediately before the triggering exchange (rollback rewinds here via `reseatLeaf`). */
  preDecisionLeafId: string;
}

/**
 * Boomerang snapshot rebuilt on reload; latest snapshot on the branch path wins (append-only, so the
 * last entry's `data` is authoritative). Checkpoint and last-auto-decision state (DLT-181) ride the
 * same snapshot — reusing the boomerang entry avoids a new persistence surface (KD1/KD5). Old snapshots
 * written before those fields existed are normalized on read with both defaulting to `null`.
 */
export interface BoomerangState {
  currentTopicBaseId: string | null;
  lastDecision: string | null;
  relatedBranchId: string | null;
  /** The main-line tip entry id of the active checkpoint, or null when none is set. */
  checkpointId: string | null;
  /** The most recent automatic branching decision (a `/rollback` target), or null. */
  lastAutoDecision: AutoDecision | null;
}

const ACTIVE_KEY = "active";
const UNCLOSED_KEY = "unclosed";

/**
 * Pointer + unclosed index over `app_state`. Enforces the write-ordering invariant (ADR-014): a trunk
 * is added to `unclosed` BEFORE it becomes `active`, and removed from `unclosed` ONLY after every
 * completion marker is present — so a crash can never lose a trunk from recovery.
 */
export class TrunkState {
  private readonly state: KeyValueState;

  constructor(state: KeyValueState) {
    this.state = state;
  }

  getActive(): TrunkPointer | null {
    return this.state.get<TrunkPointer>(ACTIVE_KEY);
  }

  setActive(pointer: TrunkPointer): void {
    this.state.set<TrunkPointer>(ACTIVE_KEY, pointer);
  }

  clearActive(): void {
    this.state.delete(ACTIVE_KEY);
  }

  listUnclosed(): string[] {
    return this.state.get<string[]>(UNCLOSED_KEY) ?? [];
  }

  addUnclosed(file: string): void {
    const current = this.listUnclosed();

    if (!current.includes(file)) {
      this.state.set<string[]>(UNCLOSED_KEY, [...current, file]);
    }
  }

  removeUnclosed(file: string): void {
    this.state.set<string[]>(
      UNCLOSED_KEY,
      this.listUnclosed().filter((entry) => entry !== file),
    );
  }

  /**
   * Make a freshly-created trunk active. Adds it to `unclosed` FIRST so that if the process crashes
   * between the two writes the trunk is still recoverable — the inverse order could orphan a live
   * trunk that recovery never learns about.
   */
  promoteToActive(pointer: TrunkPointer): void {
    this.addUnclosed(pointer.sessionFile);
    this.setActive(pointer);
  }

  /**
   * Drop a fully-closed trunk from the recovery index. Removes from `unclosed` only; the caller must
   * assert every per-branch/per-step marker is present before calling (the invariant's second half).
   */
  retireTrunk(file: string): void {
    this.removeUnclosed(file);
  }
}

/**
 * Local calendar date (`YYYY-MM-DD`) for an instant in a given IANA timezone. `now` is injected so
 * trunk-day logic is deterministic and testable across timezone/midnight boundaries. Delegates to the
 * shared `localIsoDate` (`src/util/dates.ts`) so the en-CA day-key format lives in one place.
 */
export const localDay = (now: () => Date, timezone: string | undefined): string =>
  localIsoDate(now(), timezone);

export interface TrunkDeps {
  agent: Pick<AgentManager, "open">;
  trunk: TrunkState;
  now: () => Date;
  timezone: string | undefined;
}

export interface OpenTrunkResult {
  session: AgentSession;
  day: string;
  sessionFile: string;
  isNew: boolean;
}

/**
 * Open today's trunk or create a fresh one.
 *
 * Same-day reopen re-seats the leaf onto the current base: pi's `SessionManager.open` sets the leaf to
 * the LAST file entry, not the active branch tip, so after a restart the next turn would otherwise
 * extend a stale collapsed branch. We re-seat onto the boomerang snapshot's `currentTopicBaseId` (or
 * the latest `branch_summary` id as a fallback), so the live conversation continues on the current
 * branch (ADR-014, R9).
 */
export const openOrCreateTrunk = async (
  deps: TrunkDeps,
  today: string,
): Promise<OpenTrunkResult> => {
  const active = deps.trunk.getActive();

  if (active != null && active.day === today && existsSync(active.sessionFile)) {
    const session = await deps.agent.open({ sessionFile: active.sessionFile });

    const baseId = currentBaseId(session);

    if (baseId != null) {
      reseatLeaf(session, baseId);
    }

    return { session, day: today, sessionFile: active.sessionFile, isNew: false };
  }

  const session = await deps.agent.open({});
  const sessionFile = session.sessionFile;

  if (sessionFile == null) {
    throw new Error("trunk session has no session file to track");
  }

  deps.trunk.promoteToActive({
    sessionFile,
    day: today,
    openedAt: deps.now().toISOString(),
  });

  return { session, day: today, sessionFile, isNew: true };
};

/** The base the live branch should extend after a reload: boomerang snapshot first, else latest summary. */
const currentBaseId = (session: AgentSession): string | null => {
  const boomerang = readBoomerangState(session);

  if (boomerang?.currentTopicBaseId != null) {
    return boomerang.currentTopicBaseId;
  }

  const summaries = branchSummaryEntries(session);

  return summaries.at(-1)?.id ?? null;
};

interface BranchSummaryLike {
  id: string;
  details?: BranchSummaryDetails;
}

/**
 * The id the live (un-collapsed) branch will carry when it collapses: the next `topic-N` after the
 * topic branches already recorded. Centralized so the id written mid-branch (Telegram routing, the
 * boundary collapse call) is computed the same way everywhere and matches the id `getBranchRecords`
 * assigns. Counts only the filtered topic set, so parked-away tangents/reversed summaries never perturb
 * the `topic-N` sequence (KD3).
 */
export const nextBranchId = (records: BranchRecord[]): string => `topic-${records.length + 1}`;

/**
 * Our `branch_summary` entries in file order. Filtered by both the pi `type` and our `customType` tag
 * so pi-native summaries (if any ever appear) never count toward branch ids.
 */
const branchSummaryEntries = (session: AgentSession): BranchSummaryLike[] =>
  enumerateEntries(session).filter(
    (entry): entry is typeof entry & BranchSummaryLike =>
      entry.type === "branch_summary" &&
      (entry as BranchSummaryLike).details?.customType === BRANCH_SUMMARY,
  );

interface ValidatedSummary {
  id: string;
  details: BranchSummaryDetails;
}

/**
 * Branch summaries with resolvable references, in file order. The shared base for every enumeration:
 * `getBranchRecords` (topics only), `getAllBranchRecords` (all), and `nextTangentId` (tangents only)
 * project from this, so the kind filter and reference validation each live in exactly one place.
 * Referenced ids are validated against the live tree (`getEntry`); an unresolvable reference drops the
 * record rather than handing a dangling pointer to `ask_branch`/extraction.
 */
const validatedSummaries = (session: AgentSession): ValidatedSummary[] =>
  branchSummaryEntries(session).flatMap((entry) => {
    const details = entry.details;
    if (details == null) return [];
    if (getEntry(session, details.originalLeafId) == null) return [];
    if (details.baseId != null && getEntry(session, details.baseId) == null) return [];
    return [{ id: entry.id, details }];
  });

/** Marker entries naming reversed `branch_summary` ids (mirrors the completion-marker scan). */
const readReversedSet = (session: AgentSession): Set<string> =>
  new Set(
    enumerateEntries(session)
      .filter((entry) => entry.type === "custom" && entry.customType === REVERSAL)
      .map((entry) => (entry as { data?: ReversalMarker }).data)
      .filter((data): data is ReversalMarker => data != null)
      .map((data) => data.summaryEntryId),
  );

/** Append a reversal marker orphaning `summaryEntryId` (KD6). */
export const markReversed = (session: AgentSession, summaryEntryId: string): string =>
  appendState(session, REVERSAL, { summaryEntryId } satisfies ReversalMarker);

/**
 * The kind of a summary given a precomputed reversal set — the inner reversal-aware computation shared
 * by every caller. Split out so {@link getBranchRecords}/{@link nextTangentId} build the set ONCE and
 * reuse it across every summary instead of rescanning the whole entry list per summary.
 */
const kindWith = (
  summaryEntryId: string,
  kind: "topic" | "tangent",
  reversed: Set<string>,
): "topic" | "tangent" | "reversed" => (reversed.has(summaryEntryId) ? "reversed" : kind);

/**
 * The kind a consumer should treat a branch summary as: `"reversed"` when a reversal marker names it
 * (KD6), otherwise its persisted `details.kind` (defaulting to `"topic"` for summaries written before
 * the discriminator existed). The single discriminator consulted by `getBranchRecords`, `ask_branch`,
 * DLT-182's `open_branch`, and extraction. Callers looping over many summaries should build the reversal
 * set once and use {@link kindWith} rather than calling this per id (it rescans on every call).
 */
export const effectiveKind = (
  session: AgentSession,
  summaryEntryId: string,
  kind: "topic" | "tangent" = "topic",
): "topic" | "tangent" | "reversed" => kindWith(summaryEntryId, kind, readReversedSet(session));

const toRecord = (summary: ValidatedSummary, index: number): BranchRecord => ({
  branchId: `topic-${index + 1}`,
  originalLeafId: summary.details.originalLeafId,
  baseId: summary.details.baseId,
  summaryEntryId: summary.id,
  lastExchange: summary.details.lastExchange ?? null,
});

/**
 * Rebuild EVERY collapsed branch's record from the file, unfiltered — topics, tangents, and reversed
 * summaries all included. Internal callers only: the tangent counter (`nextTangentId`) and rollback
 * target lookup (which resolves by `summaryEntryId`). Public branch-query consumers must use
 * {@link getBranchRecords} so tangents/reversed summaries stay parked away. `branchId` here is the raw
 * `topic-N`-by-position over all summaries and is NOT the public topic identity for non-topic records.
 */
export const getAllBranchRecords = (session: AgentSession): BranchRecord[] =>
  validatedSummaries(session).map(toRecord);

/**
 * Rebuild the topic branches only — the single kind-filter chokepoint (KD3/KD6/KD7). Tangents and
 * reversed summaries are excluded here, so `ask_branch`, related-branch matching, and trunk-close
 * memory extraction (which source this list through the coordinator) all skip them in one place, and
 * the `topic-N` ids stay clean. Branch ids are deterministic: the Nth topic summary in file order is
 * `topic-N`, so ids are stable across reloads.
 */
export const getBranchRecords = (session: AgentSession): BranchRecord[] => {
  const reversed = readReversedSet(session);
  return validatedSummaries(session)
    .filter(
      (summary) => kindWith(summary.id, summary.details.kind ?? "topic", reversed) === "topic",
    )
    .map(toRecord);
};

/**
 * The next `tangent-N` id for a tangent about to be summarized — tangents count on their own sequence,
 * independent of `topic-N`, counting only tangent-kind summaries so reversed tangents don't perturb it.
 */
export const nextTangentId = (session: AgentSession): string => {
  const reversed = readReversedSet(session);
  const count = validatedSummaries(session).filter(
    (summary) => kindWith(summary.id, summary.details.kind ?? "topic", reversed) === "tangent",
  ).length;

  return `tangent-${count + 1}`;
};

/** A snapshot with every field absent — the backward-compatible default for a pre-delta file. */
const emptyBoomerangState = (): BoomerangState => ({
  currentTopicBaseId: null,
  lastDecision: null,
  relatedBranchId: null,
  checkpointId: null,
  lastAutoDecision: null,
});

/**
 * Fill absent fields with their defaults so a snapshot written before `checkpointId`/`lastAutoDecision`
 * existed reads back complete (both default to `null`). The session file is the source of truth
 * (ADR-014) and outlives this code, so reads must tolerate the older shape.
 */
const normalizeBoomerangState = (
  data: Partial<BoomerangState> | undefined,
): BoomerangState | null => {
  if (data == null) return null;

  return { ...emptyBoomerangState(), ...data };
};

/** Latest boomerang snapshot on the branch path wins (custom entries are append-only). */
export const readBoomerangState = (session: AgentSession): BoomerangState | null => {
  const snapshots = enumerateEntries(session).filter(
    (entry) => entry.type === "custom" && entry.customType === BOOMERANG_STATE,
  );

  return normalizeBoomerangState(
    (snapshots.at(-1) as { data?: Partial<BoomerangState> } | undefined)?.data,
  );
};

export const writeBoomerangState = (session: AgentSession, snapshot: BoomerangState): string =>
  appendState(session, BOOMERANG_STATE, snapshot);

/**
 * Append a new boomerang snapshot that merges `patch` over the latest one. Because the snapshot is
 * latest-wins and append-only, a partial update must re-write the whole snapshot (merged with the prior
 * fields) — otherwise a later reader taking this entry's `data` would lose every field not in `patch`.
 */
const patchBoomerangState = (session: AgentSession, patch: Partial<BoomerangState>): void => {
  writeBoomerangState(session, {
    ...(readBoomerangState(session) ?? emptyBoomerangState()),
    ...patch,
  });
};

// ---- checkpoint + decision-log lifecycle (DLT-181) ----------------------------
//
// The boundary middleware's write path for checkpoint state (KD1) and the auto-decision log (KD5).
// These mutate boomerang-state directly rather than the read-only `TrunkInbound` snapshot the
// middleware receives; clearing is an append (a `null`/override snapshot), never a deletion —
// consistent with the append-only model.

/** Set the active checkpoint at `leafId`, overriding any prior one (R2). One is active at a time. */
export const setCheckpoint = (session: AgentSession, leafId: string): void =>
  patchBoomerangState(session, { checkpointId: leafId });

/** Clear the active checkpoint (e.g. after the tangent it marked is summarized away). */
export const clearCheckpoint = (session: AgentSession): void =>
  patchBoomerangState(session, { checkpointId: null });

/**
 * Set `currentTopicBaseId` to `baseId`. Used by rollback Case B: rewinding past an auto-`new` shift
 * leaves `currentTopicBaseId` pointing at the now-reversed (off-path) topic summary, so the base the
 * restored live branch actually extends — the reversed summary's own `baseId` — must be restored, or a
 * reopen would re-seat the leaf onto the dead summary. Patch-merges like the other lifecycle helpers.
 */
export const setCurrentTopicBase = (session: AgentSession, baseId: string | null): void =>
  patchBoomerangState(session, { currentTopicBaseId: baseId });

/** Record an automatic branching decision so `/rollback` can reverse it later (KD5). */
export const recordLastAutoDecision = (
  session: AgentSession,
  kind: AutoDecision["kind"],
  preDecisionLeafId: string,
): void => patchBoomerangState(session, { lastAutoDecision: { kind, preDecisionLeafId } });

/** Clear the auto-decision log once a reversal is staged (consumed by `/rollback`). */
export const clearLastAutoDecision = (session: AgentSession): void =>
  patchBoomerangState(session, { lastAutoDecision: null });

const completionMarkers = (session: AgentSession): CompletionMarker[] =>
  enumerateEntries(session)
    .filter((entry) => entry.type === "custom" && entry.customType === COMPLETION_MARKER)
    .map((entry) => (entry as { data?: CompletionMarker }).data)
    .filter((data): data is CompletionMarker => data != null);

const hasMarker = (
  session: AgentSession,
  kind: MarkerKind,
  predicate: (marker: CompletionMarker) => boolean,
): boolean =>
  completionMarkers(session).some((marker) => marker.kind === kind && predicate(marker));

export const markBranchExtracted = (session: AgentSession, branchId: string): string =>
  appendState(session, COMPLETION_MARKER, {
    kind: MARKER_KIND.branchExtracted,
    branchId,
  } satisfies BranchExtractedMarker);

export const isBranchExtracted = (session: AgentSession, branchId: string): boolean =>
  hasMarker(
    session,
    MARKER_KIND.branchExtracted,
    (marker) => marker.kind === MARKER_KIND.branchExtracted && marker.branchId === branchId,
  );

/**
 * Skill-evolution's per-branch analysis marker (DLT-080). Keyed by the branch summary's entry id,
 * NOT the positional `topic-N` branch id — a reversal renumbers the topic set, and the
 * crash-recovery re-close is exactly when that would bite (DES-008's entry-ids-not-positions rule).
 */
export const markBranchAnalyzed = (session: AgentSession, summaryEntryId: string): string =>
  appendState(session, COMPLETION_MARKER, {
    kind: MARKER_KIND.skillEvolutionAnalyzed,
    summaryEntryId,
  } satisfies SkillEvolutionAnalyzedMarker);

export const isBranchAnalyzed = (session: AgentSession, summaryEntryId: string): boolean =>
  hasMarker(
    session,
    MARKER_KIND.skillEvolutionAnalyzed,
    (marker) =>
      marker.kind === MARKER_KIND.skillEvolutionAnalyzed &&
      marker.summaryEntryId === summaryEntryId,
  );

export const markStepDone = (session: AgentSession, step: string): string =>
  appendState(session, COMPLETION_MARKER, {
    kind: MARKER_KIND.stepDone,
    step,
  } satisfies StepDoneMarker);

export const isStepDone = (session: AgentSession, step: string): boolean =>
  hasMarker(
    session,
    MARKER_KIND.stepDone,
    (marker) => marker.kind === MARKER_KIND.stepDone && marker.step === step,
  );
