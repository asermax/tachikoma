export const DEFAULT_CONFIG_TEMPLATE = `# Tachikoma configuration
# Generated with defaults on first run — every value below matches the built-in default.

[workspace]
# Root of the agent's persistent workspace (memories, context files, git repo).
path = "~/tachikoma"

[agent]
# Model references are "provider/model-id" as known to pi's model registry.
model = "anthropic/claude-opus-4-5"
# off | minimal | low | medium | high | xhigh
thinkingLevel = "medium"
# Cheaper tiers for delegated work:
searcherModel = "anthropic/claude-opus-4-5"     # retrieval / routing
processorModel = "anthropic/claude-haiku-4-5"   # extraction / mechanical work
classifierModel = "anthropic/claude-haiku-4-5"  # discrete classification

[logging]
level = "info"
pretty = true

[channels]
# Channel started by default: "repl" or "telegram".
default = "repl"

[sessions]
# Seconds of conversation silence before the active session closes and post-processing runs.
idleCloseSeconds = 900
# How far back closed sessions are considered for topic resumption.
resumeWindowSeconds = 86400

[scheduler]
# IANA timezone for cron schedules; defaults to the system timezone when unset.
# timezone = "America/Argentina/Buenos_Aires"

# Per-extension settings live under [extensions.<name>], for example:
#
# [extensions.telegram]
# botToken = "..."
# chatId = 0
`;
