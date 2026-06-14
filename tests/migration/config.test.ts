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

    await expect(readFile(`${path}.legacy-backup`, "utf8")).resolves.toBe(OLD_CONFIG);

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
    expect(existsSync(`${path}.legacy-backup`)).toBe(false);
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

  it("returns null and warns when the config cannot be parsed", async () => {
    const log = { info: vi.fn(), warn: vi.fn() } as unknown as Logger;
    const path = await makeConfig("this is = = not valid toml [[[");

    const config = await adaptConfig(path, log, async () => true);

    expect(config).toBeNull();
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ path }),
      expect.stringContaining("could not parse"),
    );
  });

  it("detects an old-shape config from a string channel alone", async () => {
    const path = await makeConfig('channel = "repl"\n');

    const config = await adaptConfig(path, fakeLog, async () => true);

    expect(config).not.toBeNull();
    expect(config?.channels.default).toBe("repl");
  });

  it("detects an old-shape config from a legacy agent role key alone", async () => {
    const path = await makeConfig('[agent]\nclassifier_model = "haiku"\n');

    const config = await adaptConfig(path, fakeLog, async () => true);

    expect(config).not.toBeNull();
    expect(config?.agent.classifier).toBe("anthropic/haiku");
  });

  it("treats an array-valued section as not a table during detection", async () => {
    const path = await makeConfig('telegram = ["a", "b"]\nchannel = "repl"\n');

    const config = await adaptConfig(path, fakeLog, async () => true);

    expect(config).not.toBeNull();
    expect(config?.extensions.telegram).toBeUndefined();
  });

  it("ignores empty-string and missing fields across legacy sections", async () => {
    const log = { info: vi.fn(), warn: vi.fn() } as unknown as Logger;
    const path = await makeConfig(
      [
        'channel = "repl"',
        "",
        "[workspace]",
        "path = 123",
        "",
        "[agent]",
        'model = ""',
        'session_resume_window = "nope"',
        "",
        "[logging]",
        "level = 5",
        'console = "yes"',
        "",
        "[telegram]",
        "bot_token = 99",
        'authorized_chat_id = "x"',
        'push_notifications = "no"',
        "inbound_reactions = true",
        "",
        "[telegram.send_file]",
        'extra_roots = ["~/ok", 7, "~/also"]',
      ].join("\n"),
    );

    const config = await adaptConfig(path, log, async () => true);

    expect(config).not.toBeNull();
    expect(config?.agent.main).toBeUndefined();
    expect(config?.extensions.telegram).toEqual({ extraFileRoots: ["~/ok", "~/also"] });

    const rewritten = await readFile(path, "utf8");
    expect(rewritten).not.toContain("[workspace]");
    expect(rewritten).not.toContain("[agent]");
    expect(rewritten).not.toContain("[sessions]");
    expect(rewritten).not.toContain("[logging]");
    expect(log.info).toHaveBeenCalledWith(expect.stringContaining("inbound_reactions"));
  });

  it("omits extraFileRoots when send_file.extra_roots is not an array", async () => {
    const path = await makeConfig(
      '[telegram]\nbot_token = "t:1"\n\n[telegram.send_file]\nextra_roots = "nope"\n',
    );

    const config = await adaptConfig(path, fakeLog, async () => true);

    expect(config).not.toBeNull();
    expect(config?.extensions.telegram).toEqual({ botToken: "t:1" });
  });

  it("does not log a dropped-sections notice when every section is carried", async () => {
    const info = vi.fn();
    const log = { info, warn: vi.fn() } as unknown as Logger;
    const path = await makeConfig('channel = "repl"\n');

    await adaptConfig(path, log, async () => true);

    const droppedNotice = info.mock.calls.find((call) =>
      typeof call[1] === "string" ? call[1].includes("without a pi equivalent") : false,
    );
    expect(droppedNotice).toBeUndefined();
  });
});
