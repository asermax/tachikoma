import type { AgentSession } from "@earendil-works/pi-coding-agent";

import { appendInContextEntry } from "../../agent/session-tree.ts";
import type { Logger } from "../../log.ts";
import { setCheckpoint } from "../../sessions/trunk.ts";

/**
 * Tangent focus injection (issue-411). When a checkpoint is active a side task runs as a tangent off
 * the parked main line, but the main line's turns remain visible in the branch and the assistant tends
 * to rush the side task to get back to them. A hidden in-context instruction injected at checkpoint-set
 * time tells it to give the side task its full, unhurried focus — the same depth it would give a
 * standalone conversation — with no pressure to return to the parked topic.
 *
 * The entry is a `custom_message` (display:false, in LLM context) appended right after the checkpoint
 * is set, so it precedes the first tangent turn and rides every tangent turn. It is automatically
 * parked away with the tangent when `/back`/`summarize-to-checkpoint` calls `collapseTangent` (the leaf
 * re-seats onto the summary rooted at the checkpoint). It is type `custom_message`, so it is NOT counted
 * by the empty-tangent guard (`checkpointHasTangent` counts only `message` entries) and NOT rendered
 * into the tangent summary (`renderBranchTranscript` renders only `message` entries) — no guard
 * breakage, no summary pollution. This mirrors the related-branch pointer injection in `related.ts`.
 */
export const TANGENT_FOCUS_CUSTOM_TYPE = "tachikoma-tangent-focus";

const TANGENT_FOCUS_INSTRUCTION = [
  "You are now handling a self-contained side task that was interleaved into our conversation.",
  "The previous main line is parked and will be resumed separately — automatically, or with /back —",
  "once this side task is done. Treat this side task as the primary focus of your full attention and",
  "engage with it as thoroughly and completely as you would if it were the only thing we were discussing.",
  "Do not abbreviate, rush, defer, or wrap up early to get back to the parked topic: there is no pressure",
  "to return to it, and a curt or half-finished answer here is not helpful. Give this side task the same",
  "depth and care as any other request.",
].join("\n");

/**
 * Inject the tangent-focus instruction as a hidden in-context entry on the live session. Called by
 * {@link setCheckpointAndFocus} so the focus guidance is present for the entire tangent without each
 * caller duplicating it.
 */
export const injectTangentFocus = (session: AgentSession, log: Logger): void => {
  appendInContextEntry(session, TANGENT_FOCUS_CUSTOM_TYPE, TANGENT_FOCUS_INSTRUCTION);

  log.debug("injected tangent focus instruction");
};

/**
 * Set a checkpoint and inject the tangent-focus instruction in one step. Every checkpoint-set site —
 * manual `/checkpoint`, the classifier's auto `set-checkpoint`, the system-origin side-task checkpoint,
 * and rollback Case B — goes through here, so the focus guidance cannot be forgotten at a new site: the
 * pairing is structural, not remembered per caller. The decision header (which varies by site) stays the
 * caller's concern.
 */
export const setCheckpointAndFocus = (session: AgentSession, leafId: string, log: Logger): void => {
  setCheckpoint(session, leafId);
  injectTangentFocus(session, log);
};
