import { existsSync } from "node:fs";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import type { Logger } from "../../src/log.ts";
import { adaptConfig } from "../../src/migration/config.ts";

const fakeLog = { info: vi.fn(), warn: vi.fn() } as unknown as Logger;

const OLD_CONFIG = `channel = "telegram"

[workspace]
path = "~/tachi-test"

[agent]
model = "opus"
processor_model = "haiku"
session_resume_window = 3600

[logging]
level = "INFO"
console = true

[telegram]
bot_token = "123:abc"
authorized_chat_id = 42
push_notifications = false
inbound_reactions = true

[telegram.send_file]
extra_roots = ["~/exports"]

[tasks]
idle_window = 300
timezone = "America/Argentina/Buenos_Aires"

[updates]
enabled = true
`;

const makeConfig = async (content: string): Promise<string> => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-migration-config-"));
  const path = join(dir, "config.toml");
  await writeFile(path, content, "utf8");
  return path;
};

describe("adaptConfig", () => {
  it("translates an old-shape config, backs up the original, and reloads", async () => {
    const path = await makeConfig(OLD_CONFIG);

    const config = await adaptConfig(path, fakeLog, async () => true);

    expect(config).not.toBeNull();
    expect(config?.workspace.path).toBe("~/tachi-test");
    expect(config?.agent.main).toBe("anthropic/opus");
    expect(config?.agent.processor).toBe("anthropic/haiku");
    expect(config?.agent.searcher).toBeUndefined();
    expect(config?.logging.level).toBe("info");
    expect(config?.logging.pretty).toBe(true);
    expect(config?.channels.default).toBe("telegram");
    expect(config?.sessions.resumeWindowSeconds).toBe(3600);
    expect(config?.scheduler.timezone).toBe("America/Argentina/Buenos_Aires");
    expect(config?.extensions.telegram).toEqual({
      botToken: "123:abc",
      chatId: 42,
      pushNotifications: false,
      extraFileRoots: ["~/exports"],
    });

    await expect(readFile(`${path}.python-backup`, "utf8")).resolves.toBe(OLD_CONFIG);

    const rewritten = await readFile(path, "utf8");
    expect(rewritten).toContain("[extensions.telegram]");
    expect(rewritten).toContain('botToken = "123:abc"');
    expect(rewritten).not.toContain("bot_token");
  });

  it("leaves agent roles unset when the user declines the translation", async () => {
    const path = await makeConfig('[agent]\nmodel = "opus"\n');

    const config = await adaptConfig(path, fakeLog, async () => false);

    expect(config).not.toBeNull();
    expect(config?.agent.main).toBeUndefined();
  });

  it("carries provider-prefixed model values into the prompt as-is", async () => {
    const path = await makeConfig(
      'channel = "repl"\n\n[agent]\nmodel = "anthropic/claude-opus-4-5"\n',
    );
    const ask = vi.fn(async () => true);

    const config = await adaptConfig(path, fakeLog, ask);

    expect(ask.mock.calls[0]?.[0]).toContain('"anthropic/claude-opus-4-5"');
    expect(config?.agent.main).toBe("anthropic/claude-opus-4-5");
  });

  it("returns null and leaves a new-shape config untouched", async () => {
    const content = '[channels]\ndefault = "repl"\n\n[extensions.telegram]\nbotToken = "x"\n';
    const path = await makeConfig(content);

    const config = await adaptConfig(path, fakeLog, async () => true);

    expect(config).toBeNull();
    await expect(readFile(path, "utf8")).resolves.toBe(content);
    expect(existsSync(`${path}.python-backup`)).toBe(false);
  });

  it("returns null for a missing config file", async () => {
    const dir = await mkdtemp(join(tmpdir(), "tachi-migration-config-"));

    await expect(
      adaptConfig(join(dir, "config.toml"), fakeLog, async () => true),
    ).resolves.toBeNull();
  });

  it("is idempotent: the translated file no longer triggers detection", async () => {
    const path = await makeConfig(OLD_CONFIG);

    await adaptConfig(path, fakeLog, async () => true);
    const afterFirst = await readFile(path, "utf8");

    await expect(adaptConfig(path, fakeLog, async () => true)).resolves.toBeNull();
    await expect(readFile(path, "utf8")).resolves.toBe(afterFirst);
  });
});
