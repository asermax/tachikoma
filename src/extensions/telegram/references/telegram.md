# Telegram

The Telegram channel and its chat affordances. Owned by the telegram extension; active only
when a bot token and chat are configured (`[extensions.telegram]`).

## Tools

| Tool | Use it for |
|------|------------|
| `send_telegram_file` | Delivering files (image, audio, video, document) instead of pasting their contents. Pass 2-10 same-type paths to send them as one grouped album (photos and videos group together; documents only with documents, audio only with audio), with a shared or per-file caption. May read from the workspace, the OS temp dir, or a configured `extraFileRoots` |
| `react_to_message` | Lightweight acknowledgements — an emoji reaction (e.g. 👍 on a quick confirmation) instead of a full reply. Only a fixed emoji set is accepted |
| `pin_message` | Making the last response easy to find again (reminders, important info) |
| `unpin_message` | Clearing a pin that is no longer relevant |
| `send_message_with_buttons` | Structured choices (yes/no, multiple choice, confirm/cancel) as tappable inline buttons instead of asking the person to type an option. Their tap comes back as a message routed to the branch the button's message belongs to |

## Conversation behavior

The chat is a single authorized conversation. Media the person sends (`allowMedia`, default
on) is downloaded into the workspace data dir and referenced to you as files. Referencing an
old message — replying to it, reacting to it, tapping a button on it — routes the new
message back to that message's branch, and the notification you see names the referenced
message's id (e.g. `The user reacted ❤ to a previous message (message_id: 42).`), often with
a `Replied to:`/`Reacted to:` quote of its content; `send_telegram_file` names the same ids in
its result (`File sent: chart.png (message_id: 12)` for one file,
`Files sent: a.png, b.png (message_id: 12, 13)` for an album), so a later notification
referencing one of those ids is the person reacting to or answering that file. `/stop` in the chat aborts your
running exchange. Long exchanges can be rendered compactly (activity collapsed to markers)
per `collapseIntensiveWork` / `intensiveWorkThreshold`, and completed replies can be pushed
as a notification (`pushNotifications`, `pushNotificationMinSeconds`) when the exchange
streamed for at least that many seconds.

## Configuration

`[extensions.telegram]`: `botToken` and `chatId` (the channel stays off until both are
set), `allowMedia`, `pushNotifications` (default `true`), `pushNotificationMinSeconds`
(default `10`), `collapseIntensiveWork` (default `true`), `intensiveWorkThreshold`
(default `4`), `extraFileRoots` (default `[]`).
