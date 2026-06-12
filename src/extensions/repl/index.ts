import { createInterface, type Interface } from "node:readline";

import type { Channel, ChannelRuntime, Delivery, Exchange } from "../../channels/types.ts";
import { textMessage } from "../../domain/message.ts";
import { defineExtension } from "../api.ts";

const DIM = "\x1b[2m";
const RED = "\x1b[31m";
const RESET = "\x1b[0m";

class ReplChannel implements Channel {
  readonly name = "repl";

  private readline: Interface | null = null;

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

    this.readline.on("close", () => {
      // Pending exchanges must not touch the closed interface while shutdown runs.
      this.readline = null;
      process.kill(process.pid, "SIGINT");
    });

    this.readline.prompt();
  }

  async respond({ events }: Exchange): Promise<void> {
    let inText = false;

    for await (const event of events) {
      switch (event.kind) {
        case "text":
          process.stdout.write(event.text);
          inText = true;
          break;

        case "tool-start":
          if (inText) process.stdout.write("\n");
          process.stdout.write(`${DIM}⚙ ${event.toolName}${RESET}\n`);
          inText = false;
          break;

        case "status":
          if (inText) process.stdout.write("\n");
          process.stdout.write(`${DIM}${event.text}${RESET}\n`);
          inText = false;
          break;

        case "error":
          if (inText) process.stdout.write("\n");
          process.stdout.write(`${RED}error: ${event.message}${RESET}\n`);
          inText = false;
          break;

        case "result":
          process.stdout.write("\n");
          this.readline?.prompt();
          break;

        default:
          break;
      }
    }
  }

  async deliver(delivery: Delivery): Promise<void> {
    process.stdout.write(`\n📥 ${delivery.text}\n`);
    this.readline?.prompt();
  }

  async stop(): Promise<void> {
    this.readline?.close();
    this.readline = null;
  }
}

export default defineExtension({
  name: "repl",

  setup(app) {
    app.channels.register(new ReplChannel());
  },
});
