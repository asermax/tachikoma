import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  loadExtensionModule,
  resolveExtensionModule,
  validateExtensionShape,
} from "../../src/extensions/external/loader.ts";
import type { Logger } from "../../src/log.ts";

const createFakeLog = () =>
  ({ error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() }) as unknown as Logger;

const VALID_MODULE = `export default {
  name: "demo-extension",
  setup() {},
};
`;

const INVALID_MODULE = `export default {
  name: "broken-extension",
};
`;

let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-external-loader-"));
});

describe("validateExtensionShape", () => {
  it("accepts a minimal extension object", () => {
    const result = validateExtensionShape({ name: "demo", setup: () => {} });

    expect(result.ok).toBe(true);
  });

  it("rejects non-objects, missing names, and missing setup", () => {
    expect(validateExtensionShape(null)).toMatchObject({ ok: false });
    expect(validateExtensionShape("nope")).toMatchObject({ ok: false });
    expect(validateExtensionShape({ setup: () => {} })).toMatchObject({
      ok: false,
      reason: expect.stringContaining("name"),
    });
    expect(validateExtensionShape({ name: "demo" })).toMatchObject({
      ok: false,
      reason: expect.stringContaining("setup"),
    });
  });

  it("rejects a non-object configSchema", () => {
    expect(
      validateExtensionShape({ name: "demo", setup: () => {}, configSchema: "nope" }),
    ).toMatchObject({ ok: false, reason: expect.stringContaining("configSchema") });
  });
});

describe("resolveExtensionModule", () => {
  it("resolves a directory to its index module", async () => {
    await mkdir(join(dir, "my-extension"));
    await writeFile(join(dir, "my-extension", "index.ts"), VALID_MODULE);

    expect(resolveExtensionModule(join(dir, "my-extension"))).toBe(
      join(dir, "my-extension", "index.ts"),
    );
  });

  it("returns null for missing paths and non-module files", async () => {
    await writeFile(join(dir, "readme.md"), "hello");

    expect(resolveExtensionModule(join(dir, "missing"))).toBeNull();
    expect(resolveExtensionModule(join(dir, "readme.md"))).toBeNull();
    expect(resolveExtensionModule(dir)).toBeNull();
  });
});

describe("loadExtensionModule", () => {
  it("loads a valid extension module file", async () => {
    const file = join(dir, "valid.ts");
    await writeFile(file, VALID_MODULE);

    const extension = await loadExtensionModule(file, createFakeLog());

    expect(extension?.name).toBe("demo-extension");
    expect(typeof extension?.setup).toBe("function");
  });

  it("loads a directory external extension through its index module", async () => {
    await mkdir(join(dir, "my-extension"));
    await writeFile(join(dir, "my-extension", "index.ts"), VALID_MODULE);

    const extension = await loadExtensionModule(join(dir, "my-extension"), createFakeLog());

    expect(extension?.name).toBe("demo-extension");
  });

  it("logs and skips a module whose default export is invalid", async () => {
    const log = createFakeLog();
    const file = join(dir, "invalid.ts");
    await writeFile(file, INVALID_MODULE);

    expect(await loadExtensionModule(file, log)).toBeNull();
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ reason: expect.stringContaining("setup") }),
      expect.stringContaining("skipping"),
    );
  });

  it("logs and skips a module that fails to import", async () => {
    const log = createFakeLog();
    const file = join(dir, "broken.ts");
    await writeFile(file, "throw new Error('boom at import time');\nexport default {};\n");

    expect(await loadExtensionModule(file, log)).toBeNull();
    expect(log.warn).toHaveBeenCalled();
  });

  it("logs and skips a source without a resolvable module", async () => {
    const log = createFakeLog();

    expect(await loadExtensionModule(join(dir, "missing"), log)).toBeNull();
    expect(log.warn).toHaveBeenCalled();
  });
});
