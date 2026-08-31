# Tasks

Scheduled work — the mechanics beyond the usage summary. Owned by the tasks extension.

## How tasks reach the conversation

- **Session tasks** fire while the person is away: the instance's prompt is delivered as a
  system-origin turn headed `📋 Scheduled task: <name>`, which you then handle as an
  instruction in the conversation (subject to the usual idle-window delivery timing).
- **Background tasks** run headless in their own session with their own base prompt. Their
  notices (including failure notices) flow back as notifications, not replies.
- **Waiting questions**: a background run blocked on `ask_user` holds in `waiting` until
  answered. In the main conversation, answer with `respond_to_task` (main-session tool
  only — a background run must never answer another instance's question). Unanswered
  questions expire (default 2h) and fail the run.

## Run limits and lifecycle

- A background run iterates up to `backgroundMaxIterations` (default 10); a run that never
declares a terminal outcome (`update_goal` `completed` / `not_completable`) fails at the
cap — an omitted `goal` is derived from the prompt on the first run.
- At most `backgroundMaxConcurrent` (default 3) runs execute at once; surplus instances wait
  for a free slot on a later tick.
- A run held in `running` longer than `runningTimeoutSeconds` (default 30 min) is presumed
  dead and failed by a sweep; instances caught mid-flight by a restart are failed at boot.
- Auto-disabled one-shots (and their terminal instances) are pruned after
  `oneShotRetentionSeconds` (default 48h).

The scheduler ticks every 60s: due instances are generated from definitions, then delivered
or dispatched.

## Configuration

`[extensions.tasks]`: `timezone` (else `scheduler.timezone`) for schedule evaluation;
`backgroundMaxIterations`, `backgroundMaxConcurrent`, `waitTimeoutSeconds`,
`runningTimeoutSeconds`, `oneShotRetentionSeconds` as above.
