import { describe, expect, it, vi } from "vitest";

import { SystemdRunLimiter } from "../../src/extensions/detached-processes/limits.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = () => ({ info: vi.fn(), warn: vi.fn() }) as unknown as Logger;

describe("SystemdRunLimiter", () => {
  it("wraps the command in a named scope when available and a limit is set", async () => {
    const limiter = new SystemdRunLimiter(fakeLog());
    await limiter.detect(async () => "ok");

    const wrapped = limiter.wrap("abc-123", "echo hi", 512);

    expect(wrapped).toEqual({
      file: "systemd-run",
      args: [
        "--user",
        "--scope",
        "--quiet",
        "--unit=tachikoma-abc-123.scope",
        "-p",
        "MemoryMax=512M",
        "--",
        "sh",
        "-c",
        "echo hi",
      ],
      limited: true,
    });
  });

  it("falls back to a plain shell and warns when systemd-run is unavailable", async () => {
    const log = fakeLog();
    const limiter = new SystemdRunLimiter(log);
    await limiter.detect(async () => {
      throw new Error("not found");
    });

    const wrapped = limiter.wrap("abc-123", "echo hi", 512);

    expect(wrapped).toEqual({ file: "sh", args: ["-c", "echo hi"], limited: false });
    expect(log.warn).toHaveBeenCalled();
  });

  it("uses the real systemd-run probe by default without throwing", async () => {
    const limiter = new SystemdRunLimiter(fakeLog());

    await expect(limiter.detect()).resolves.toBeUndefined();
  });

  it("never wraps when no limit is requested", async () => {
    const limiter = new SystemdRunLimiter(fakeLog());
    await limiter.detect(async () => "ok");

    expect(limiter.wrap("abc-123", "echo hi", null)).toEqual({
      file: "sh",
      args: ["-c", "echo hi"],
      limited: false,
    });
  });
});
