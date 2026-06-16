export const DEFAULT_CONFIG_TEMPLATE = `# Tachikoma configuration
# Generated with defaults on first run — every value below matches the built-in default.

[workspace]
# Root of the agent's persistent workspace (memories, context files, git repo).
path = "~/tachikoma"

[agent]
# Per-role model selection, "provider/model-id[:thinkingLevel]" as known to pi's
# model registry. Roles left unset fall back along classifier -> processor -> main
# (searcher -> main); a fully unset chain uses pi's own resolution (settings
# defaultModel, else the first model with credentials).
#
# Everything else about the agent — default model, thinking budgets, compaction,
# retry, custom providers — is configured in pi's own files under
# {workspace}/.tachikoma/pi/ (settings.json, models.json, auth.json).
#
# main = "anthropic/claude-opus-4-5:medium"
# searcher = "anthropic/claude-opus-4-5"
# processor = "anthropic/claude-haiku-4-5"   # extraction / mechanical work
# classifier = "anthropic/claude-haiku-4-5"  # discrete classification

[logging]
level = "info"
pretty = true
# Persist structured JSON logs under {workspace}/.tachikoma/logs. stderr output
# is kept regardless. Files roll automatically by rotateFrequency while running
# ("hourly" | "daily"); a stable current.log symlinks to the active file.
toFile = true
rotateFrequency = "daily"
# Days of logs to retain; older rolled files are pruned on rotation.
retentionDays = 7

[channels]
# Channel started by default. Telegram is the only built-in channel; a local
# terminal interface returns in a later delta.
default = "telegram"

[scheduler]
# IANA timezone for cron schedules; defaults to the detected system timezone
# when unset. An unrecognized zone fails config validation.
# timezone = "America/Argentina/Buenos_Aires"

[env]
# Environment variables applied to process.env at startup, available app-wide and
# to anything inheriting the process environment (pi sessions, spawned tools,
# detached processes). Config-defined values overwrite existing same-named vars.
# GH_TOKEN = "..."
# TZ = "America/Argentina/Buenos_Aires"

# Per-extension settings live under [extensions.<name>], for example:
#
# [extensions.boundary]
# # Gate topic-shift detection. Forced shifts ("/new") are honored even when off.
# enabled = true
#
# [extensions.telegram]
# botToken = "..."
# chatId = 0
`;
