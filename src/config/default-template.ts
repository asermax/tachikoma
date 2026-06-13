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

[channels]
# Channel started by default: "repl" or "telegram".
default = "repl"

[sessions]
# How far back closed sessions are considered for topic resumption.
resumeWindowSeconds = 86400

[scheduler]
# IANA timezone for cron schedules; defaults to the detected system timezone
# when unset. An unrecognized zone fails config validation.
# timezone = "America/Argentina/Buenos_Aires"

# Per-extension settings live under [extensions.<name>], for example:
#
# [extensions.boundary]
# # Seconds of conversation silence before the active session closes (0 disables).
# idleCloseSeconds = 900
#
# [extensions.telegram]
# botToken = "..."
# chatId = 0
`;
