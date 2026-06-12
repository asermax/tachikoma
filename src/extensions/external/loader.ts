import { existsSync, statSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import type { Logger } from "../../log.ts";
import type { TachikomaExtension } from "../api.ts";

const INDEX_CANDIDATES = ["index.ts", "index.js"];

/** Resolve a source (file or directory) to its extension module file, or null. */
export const resolveExtensionModule = (source: string): string | null => {
  if (!existsSync(source)) return null;

  if (statSync(source).isFile()) {
    return source.endsWith(".ts") || source.endsWith(".js") ? source : null;
  }

  for (const candidate of INDEX_CANDIDATES) {
    const path = join(source, candidate);

    if (existsSync(path)) return path;
  }

  return null;
};

export type ExtensionValidation =
  | { ok: true; extension: TachikomaExtension<never> }
  | { ok: false; reason: string };

/** Check that a module's default export satisfies the defineExtension contract. */
export const validateExtensionShape = (value: unknown): ExtensionValidation => {
  if (value == null || typeof value !== "object") {
    return { ok: false, reason: "default export is not an object" };
  }

  const candidate = value as Partial<TachikomaExtension<never>>;

  if (typeof candidate.name !== "string" || candidate.name.trim() === "") {
    return { ok: false, reason: "missing a non-empty 'name' string" };
  }

  if (typeof candidate.setup !== "function") {
    return { ok: false, reason: "missing a 'setup' function" };
  }

  if (candidate.configSchema != null && typeof candidate.configSchema !== "object") {
    return { ok: false, reason: "'configSchema' must be a TypeBox schema object" };
  }

  return { ok: true, extension: candidate as TachikomaExtension<never> };
};

/**
 * Load a Tachikoma extension from a source path: resolve the module file,
 * import it natively, and validate its default export. Invalid sources are
 * logged and skipped (null) so one bad external extension never aborts startup.
 */
export const loadExtensionModule = async (
  source: string,
  log: Logger,
): Promise<TachikomaExtension<never> | null> => {
  const modulePath = resolveExtensionModule(source);

  if (modulePath == null) {
    log.warn(
      { source },
      "external extension source has no loadable extension module (.ts/.js) — skipping",
    );
    return null;
  }

  let module: { default?: unknown };

  try {
    module = await import(pathToFileURL(modulePath).href);
  } catch (error) {
    log.warn(
      { source: modulePath, err: error },
      "external extension module failed to import — skipping",
    );
    return null;
  }

  const validation = validateExtensionShape(module.default);

  if (!validation.ok) {
    log.warn(
      { source: modulePath, reason: validation.reason },
      "invalid external extension — skipping",
    );
    return null;
  }

  return validation.extension;
};
