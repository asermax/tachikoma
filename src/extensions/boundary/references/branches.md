# Branches

The daily-trunk conversation model and its commands. Owned by the boundary extension.

## The trunk

Each day has one trunk session. Topics inside it are branches: a branch starts at a base
message, grows with the exchange, and when the topic ends it is collapsed — the branch's
messages are replaced by a short `branch_summary` entry on the trunk (`topic-N` ids). The
next topic builds on that summary, which keeps the live context small while older work stays
reachable. The trunk closes at `scheduler.nightlyCloseHour` (default 04:00); closing runs the
post-processing passes (memory extraction, skill evolution, workspace commit).

What you experience of this:

- Older turns appear as `branch_summary` entries — that is expected, not truncation.
- On a shift, the harness may inject a related prior branch's context as a hidden pointer,
  chosen by similarity to the new topic's first message.
- Referencing an old Telegram message (replying to it, reacting to it, tapping a button on
  it) forces a shift back to that message's branch and injects its context.

## Topic shifts

Shifts are decided per idle user message by a shadow-fork classifier that reads the live
conversation and the incoming message; detection fails open (a classifier error appends
normally). `/new` (optionally with the first message of the new topic as trailing text)
forces a shift and is honored even when detection is disabled.

## Checkpoints

A checkpoint parks the main line so a side conversation can run as a tangent:

- The person sets one with `/checkpoint` (trailing text becomes the tangent's first
  message); the harness also sets one automatically when a system-origin side task (a digest
  or a fired session task) arrives mid-conversation.
- `/back` resumes the parked main line; the tangent is folded back as a summary attached to
  the checkpoint.
- The classifier can also decide to summarize a tangent back to an active checkpoint on its
  own when the main line is resumed.

The person sees these decisions as small headers/reactions on the affected messages.

## `/rollback`

Reverses the most recent *automatic* topic shift or auto-checkpoint decision — the conversation
returns to the message that triggered it, which is re-run with the corrected framing. Manual
decisions (`/new`, `/checkpoint`) are not rollback targets, and neither is an automatic
*summarize-to-checkpoint* (folding a tangent back has nothing to rewind). It requires that
exactly one user turn followed the decision — none (the triggering message was never answered)
or more than one (a later turn happened) and the reversal is refused.

## `ask_branch`

Answers a focused question from any prior branch's full (pre-collapse) context by forking
the branch's conversation — use it when a `branch_summary` lacks the detail you need.
Main-session tool only.

## Configuration

`[extensions.boundary]`: `enabled` (default `true`) gates automatic detection only — manual
commands always work; `autoSetCheckpoint` and `autoSummarizeToCheckpoint` (both default
`true`) are per-decision kill-switches for the automatic checkpoint results.
