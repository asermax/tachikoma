import convert from "telegramify-markdown";

/**
 * Convert the agent's GitHub-flavored markdown into the Telegram MarkdownV2
 * dialect. Telegram's own parsers don't understand GFM and reject whole
 * messages on any unescaped punctuation (`.`, `-`, `!`, `(`, …); telegramify
 * rewrites the constructs Telegram supports (bold, italic, code, links, lists,
 * tables) and escapes everything else, mirroring the legacy Python channel.
 * "escape" renders unsupported HTML as visible escaped text rather than
 * silently dropping it.
 */
export const toTelegramMarkdown = (text: string): string => convert(text, "escape");
