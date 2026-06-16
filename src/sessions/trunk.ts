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

/**
 * Trunk identity and the on-file conversational state model (see ADR-014). The day is the unit of
 * conversation: one persistent pi session ("trunk") per local calendar day, identified by an
 * `app_state` pointer rather than a `sessions` table row. Topic shifts collapse into `branch_summary`
 * entries on the trunk; all boomerang/branch/marker state lives on the session file as custom entries
 * so it reloads natively and there is no DB↔file dual source of truth.
 */

/**
 * Custom-type tags for the entries this module reads and writes. Exported so the boundary rewrite (B3)
 * and the trunk-close pipeline (B6) reuse the SAME tags — these strings are the contract between the
 * writer and every later reader, so they must never drift.
 */
export const BRANCH_SUMMARY = "tachikoma-branch-summary";
export const BOOMERANG_STATE = "tachikoma-boomerang-state";
export const COMPLETION_MARKER = "tachikoma-completion-marker";

/** Marker `kind`s distinguishing the two idempotency markers stored under {@link COMPLETION_MARKER}. */
const MARKER_KIND = {
  branchExtracted: "extracted-marker",
  stepDone: "step-marker",
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

/** Boomerang snapshot rebuilt on reload; latest snapshot on the branch path wins. */
export interface BoomerangState {
  currentTopicBaseId: string | null;
  lastDecision: string | null;
  relatedBranchId: string | null;
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
 * Local calendar date (`YYYY-MM-DD`) for an instant in a given IANA timezone. `en-CA` formats dates
 * ISO-style (YYYY-MM-DD), so it gives the day key directly. `now` is injected so trunk-day logic is
 * deterministic and testable across timezone/midnight boundaries.
 */
export const localDay = (now: () => Date, timezone: string | undefined): string =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now());

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
 * branches already recorded. Centralized so the id written mid-branch (Telegram routing, the boundary
 * collapse call) is computed the same way everywhere and matches the id `getBranchRecords` assigns.
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

/**
 * Rebuild every collapsed branch's record from the file. Branch ids are deterministic: the Nth
 * branch summary in file order is `topic-N`, so ids are stable across reloads. Referenced ids are
 * validated against the live tree (`getEntry`); an unresolvable reference drops the record rather than
 * handing a dangling pointer to `ask_branch`/extraction.
 */
export const getBranchRecords = (session: AgentSession): BranchRecord[] => {
  const records: BranchRecord[] = [];

  branchSummaryEntries(session).forEach((entry, index) => {
    const details = entry.details;

    if (details == null) return;
    if (getEntry(session, details.originalLeafId) == null) return;
    if (details.baseId != null && getEntry(session, details.baseId) == null) return;

    records.push({
      branchId: `topic-${index + 1}`,
      originalLeafId: details.originalLeafId,
      baseId: details.baseId,
      summaryEntryId: entry.id,
      lastExchange: details.lastExchange ?? null,
    });
  });

  return records;
};

/** Latest boomerang snapshot on the branch path wins (custom entries are append-only). */
export const readBoomerangState = (session: AgentSession): BoomerangState | null => {
  const snapshots = enumerateEntries(session).filter(
    (entry) => entry.type === "custom" && entry.customType === BOOMERANG_STATE,
  );

  return (snapshots.at(-1) as { data?: BoomerangState } | undefined)?.data ?? null;
};

export const writeBoomerangState = (session: AgentSession, snapshot: BoomerangState): string =>
  appendState(session, BOOMERANG_STATE, snapshot);

const completionMarkers = (session: AgentSession): Array<BranchExtractedMarker | StepDoneMarker> =>
  enumerateEntries(session)
    .filter((entry) => entry.type === "custom" && entry.customType === COMPLETION_MARKER)
    .map((entry) => (entry as { data?: BranchExtractedMarker | StepDoneMarker }).data)
    .filter((data): data is BranchExtractedMarker | StepDoneMarker => data != null);

const hasMarker = (
  session: AgentSession,
  kind: MarkerKind,
  predicate: (marker: BranchExtractedMarker | StepDoneMarker) => boolean,
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
