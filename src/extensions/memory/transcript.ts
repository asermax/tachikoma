import { readFile } from "node:fs/promises";

export interface ConversationTurn {
  role: "user" | "assistant";
  text: string;
}

interface ContentBlock {
  type: string;
  text?: string;
}

interface TranscriptMessage {
  role?: string;
  content?: string | ContentBlock[];
}

const textOfContent = (content: string | ContentBlock[]): string => {
  if (typeof content === "string") return content;

  return content
    .filter((block) => block.type === "text" && block.text != null)
    .map((block) => block.text as string)
    .join("\n");
};

/**
 * Parse a pi session JSONL transcript into conversation turns. Keeps only
 * user/assistant text — tool calls, tool results, thinking, and non-message
 * entries are noise for memory extraction. Malformed lines are skipped.
 */
export const parseTranscript = (jsonl: string): ConversationTurn[] => {
  const turns: ConversationTurn[] = [];

  for (const line of jsonl.split("\n")) {
    if (line.trim() === "") continue;

    let entry: { type?: string; message?: TranscriptMessage };
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }

    if (entry.type !== "message" || entry.message == null) continue;

    const { role, content } = entry.message;
    if ((role !== "user" && role !== "assistant") || content == null) continue;

    const text = textOfContent(content).trim();
    if (text === "") continue;

    turns.push({ role, text });
  }

  return turns;
};

/**
 * Render turns as a readable "role: text" conversation capped at maxChars.
 * When over budget the newest turns win — the tail of a session carries the
 * conclusions worth remembering.
 */
export const renderConversation = (turns: ConversationTurn[], maxChars: number): string => {
  const kept: string[] = [];
  let total = 0;
  let truncated = false;

  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn == null) continue;

    const line = `${turn.role}: ${turn.text}`;

    if (total + line.length > maxChars) {
      if (kept.length === 0) {
        const budget = Math.max(0, maxChars - turn.role.length - 3);
        kept.push(`${turn.role}: …${turn.text.slice(-budget)}`);
      }

      truncated = true;
      break;
    }

    kept.push(line);
    total += line.length + 2;
  }

  if (truncated) kept.push("[earlier conversation truncated]");

  return kept.reverse().join("\n\n");
};

/** Read, parse, and render a transcript file. Returns "" when unreadable or empty. */
export const loadConversation = async (path: string, maxChars: number): Promise<string> => {
  let raw: string;

  try {
    raw = await readFile(path, "utf8");
  } catch {
    return "";
  }

  return renderConversation(parseTranscript(raw), maxChars);
};
