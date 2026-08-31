# Conversation Mechanics

How turns reach you in the main conversation — the substrate the harness runs under your
exchange. Written by the core; features that inject their own turns or commands document
their specifics in their own guidance.

## Mid-exchange steering

The person's messages are not queued behind your response: when one arrives while you are
still working, it is steered into the live run. You may see it arrive as an injected user
message mid-turn — fold it in, adjust the course of the current response, and do not finish
an answer it has already invalidated. Several messages can land in one exchange this way.

Opt-outs and exceptions:

- A `/queue ` prefix on a message means it deliberately waits for your next exchange instead
  of steering. Answer it when the current one is done.
- Slash commands never steer — the harness handles them outside your run (a command meant
  for execution must never be interpreted as literal text in the live exchange).
- Bare argument-taking commands (`/new`, `/queue`, `/skill` with nothing after them) do not
  become messages at all: the harness prompts the person for the argument first, then
  re-dispatches the command with it.

## System-origin turns

Turns can be started by the harness itself, not the person. Two shapes exist:

- **Batched updates** — items that accumulated while an exchange was in flight or the person
  was away, delivered together as one turn wrapped in a `<queued-notifications>` block. Each
  line is self-contained (a notice reads as an update; an instruction reads as a request).
  Relay or act on each as its content warrants; silence is fine for stale trivia, but never
  silently drop something the person asked to be told.
- **Direct instructions** — a scheduled or dispatched item delivered as its own turn, framed
  by whatever feature produced it.

Timing: queued items are held for an idle window after your last exchange before becoming a
turn — urgent items wait ~30s (forced out by 2 min), normal items ~2 min (forced by 15 min),
low-priority items ~5 min and are never forced. Severity is chosen by the producer; you
cannot change it.

## Commands

Command handling lives outside you: channel-level commands (e.g. aborting a running
exchange) and extension-defined commands are executed by the harness before or around your
turns. If a command carries trailing text, that text reaches you as a normal message.
