import { createInterface, type Interface } from "node:readline";

import type { Channel, ChannelRuntime, Delivery, Exchange } from "../../channels/types.ts";
import { textMessage } from "../../domain/message.ts";
import { defineExtension } from "../api.ts";
import { renderMarkdown } from "./markdown.ts";

const DIM = "\x1b[2m";
const RED = "\x1b[31m";
const RESET = "\x1b[0m";

interface ReplOptions {
  /** Abort the in-flight exchange — wired from sessions.abortExchange. */
  abort(): Promise<void>;
}

class ReplChannel implements Channel {
  readonly name = "repl";

  private readline: Interface | null = null;
  private streaming = false;
  private readonly options: ReplOptions;

  constructor(options: ReplOptions) {
    this.options = options;
  }

  async start(runtime: ChannelRuntime): Promise<void> {
    this.readline = createInterface({
      input: process.stdin,
      output: process.stdout,
      prompt: "you> ",
    });

    this.readline.on("line", (line) => {
      const text = line.trim();

      if (text.length > 0) {
        runtime.submit(textMessage(this.name, text));
      } else {
        this.readline?.prompt();
      }
    });

    // readline intercepts Ctrl-C and emits "SIGINT" rather than letting the
    // process default fire, so the abort/exit decision lives here.
    this.readline.on("SIGINT", () => {
      if (this.streaming) {
        void this.options
          .abort()
          .catch((error) => runtime.log.warn({ err: error }, "exchange abort failed"));
        return;
      }

      this.readline?.close();
    });

    this.readline.on("close", () => {
      // Pending exchanges must not touch the closed interface while shutdown runs.
      this.readline = null;
      process.kill(process.pid, "SIGINT");
    });

    this.readline.prompt();
  }

  async respond({ events }: Exchange): Promise<void> {
    this.streaming = true;

    let buffer = "";

    // Inline markdown spans can straddle stream chunks, so text is buffered and
    // rendered as a block. flushText returns whether it emitted a trailing
    // newline, letting interrupting events avoid a spurious blank line (R4).
    const flushText = (): boolean => {
      if (buffer.length === 0) return false;

      process.stdout.write(`${renderMarkdown(buffer)}\n`);
      buffer = "";
      return true;
    };

    try {
      for await (const event of events) {
        switch (event.kind) {
          case "text":
            buffer += event.text;
            break;

          case "tool-start":
            flushText();
            process.stdout.write(`${DIM}🔧 ${event.toolName}${RESET}\n`);
            break;

          case "status":
            flushText();
            process.stdout.write(`${DIM}${event.text}${RESET}\n`);
            break;

          case "error": {
            flushText();
            const hint = event.recoverable ? "" : ` (${event.errorKind}, not recoverable)`;
            process.stdout.write(`${RED}error: ${event.message}${hint}${RESET}\n`);
            break;
          }

          case "result":
            flushText();
            if (event.result != null) {
              const cost = event.result.costUsd.toFixed(4);
              const tokens = event.result.usage.totalTokens;
              process.stdout.write(`${DIM}· $${cost} · ${tokens} tokens${RESET}\n`);
            }
            process.stdout.write("\n");
            this.readline?.prompt();
            break;

          default:
            break;
        }
      }
    } finally {
      flushText();
      this.streaming = false;
    }
  }

  async deliver(delivery: Delivery): Promise<void> {
    process.stdout.write(`\n📥 ${delivery.text}\n`);
    this.readline?.prompt();
  }

  status(text: string): void {
    process.stdout.write(`${DIM}· ${text}${RESET}\n`);
  }

  async stop(): Promise<void> {
    this.readline?.close();
    this.readline = null;
  }
}

export default defineExtension({
  name: "repl",

  setup(app) {
    app.channels.register(new ReplChannel({ abort: () => app.sessions.abortExchange() }));
  },
});
