import { afterEach, describe, expect, it, vi } from "vitest";

import { applyConfigEnv } from "../../src/config/env.ts";
import type { Logger } from "../../src/log.ts";

const createLogger = () =>
  ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }) as unknown as Logger;

describe("applyConfigEnv", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("applies config-defined variables to process.env", () => {
    const log = createLogger();

    applyConfigEnv({ TACHI_TEST_ONE: "alpha", TACHI_TEST_TWO: "beta" }, log);

    expect(process.env.TACHI_TEST_ONE).toBe("alpha");
    expect(process.env.TACHI_TEST_TWO).toBe("beta");
    expect(log.info).toHaveBeenCalledWith(
      { keys: ["TACHI_TEST_ONE", "TACHI_TEST_TWO"] },
      expect.any(String),
    );
  });

  it("overwrites an existing same-named variable", () => {
    vi.stubEnv("TACHI_TEST_EXISTING", "from-shell");

    applyConfigEnv({ TACHI_TEST_EXISTING: "from-config" }, createLogger());

    expect(process.env.TACHI_TEST_EXISTING).toBe("from-config");
  });

  it("does nothing and logs nothing for an empty section", () => {
    const log = createLogger();

    applyConfigEnv({}, log);

    expect(log.info).not.toHaveBeenCalled();
  });
});
