# Configuration

Tachikoma is configured by a TOML file, created from a template on first run at
`~/.config/tachikoma/config.toml` (override the directory with `XDG_CONFIG_HOME`). When the
person asks what the assistant can do or how a behavior is tuned, read this file — you can
see every option that shapes what you are. Most changes take effect on the next restart.

## Core options

| Option | Default | Shapes |
|--------|---------|--------|
| `workspace.path` | `~/tachikoma` | Where your memories, context files, and notes live |
| `agent.main` / `agent.searcher` / `agent.processor` / `agent.classifier` | unset | Model per role, as `provider/model-id[:thinkingLevel]` (unset roles fall back along classifier → processor → main, then to pi's own resolution) |
| `channels.default` | `telegram` | Which channel you are spoken to over — Telegram is the only built-in channel, so any other value fails at startup |
| `scheduler.timezone` | system | Zone for your date header, scheduling, and commit timestamps |
| `scheduler.nightlyCloseHour` | `4` | Hour the daily conversation is closed out and post-processed |
| `scheduler.commitDebounceMinutes` | `5` | Minutes of quiet before workspace (and registered-project) changes are committed and pushed mid-session (`0` = only at close) |
| `coordinator.pendingInputTtlMs` | `120000` | How long a bare argument-taking command waits for its argument |
| `env` | `{}` | Environment variables applied app-wide at startup |
| `logging.*` | `info` / `true` / `true` / `daily` / `7` | `level`, `pretty`, `toFile`, `rotateFrequency`, `retentionDays` (diagnostics only) |

## Extension options

Each loaded feature reads its own `[extensions.<name>]` table, validated against that
feature's own schema — an unknown key there is silently ignored, and only some features carry
an `enabled = false` switch. When a capability seems missing or mis-tuned, check the feature's
own guidance (its usage section's reference names the knobs that matter) and this file before
concluding it doesn't exist — and mention the option to the person rather than working around
it.
