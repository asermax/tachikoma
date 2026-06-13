import type { Static, TSchema } from "typebox";
import { Assert, Clone, Convert, Default, Errors } from "typebox/value";

export class ConfigError extends Error {}

/**
 * Apply schema defaults, coerce compatible primitives, then assert.
 *
 * The Clone → Default → Convert → Assert pipeline is spelled out by hand on purpose: TypeBox 1.1
 * made "corrective parse" (defaulting + coercion before checking) opt-in because it was costly on
 * large invalid values, so a bare `Assert` does NONE of it. Collapsing this into `Assert(schema,
 * value)` would silently drop every schema default and reject TOML primitives that only need
 * coercion (e.g. a number written as a string) — do not "simplify" it.
 *
 * No `Clean` step is included: config is intentionally tolerant of unknown keys. The root
 * `ConfigSchema.extensions` is a `Type.Record(..., Type.Unknown())` whose sections are validated
 * later, per-extension, in the host — a global `Clean` would strip that passthrough and break
 * extension config. (StringEnum, the project's union-of-literals helper, lives in pi-ai and drops
 * falsy `default` values; if a falsy enum default is ever needed, use `Type.Union` with a default
 * instead.)
 */
export const parseWithSchema = <S extends TSchema>(
  schema: S,
  value: unknown,
  label: string,
): Static<S> => {
  const prepared = Convert(schema, Default(schema, Clone(value)));

  try {
    Assert(schema, prepared);
  } catch {
    const details = [...Errors(schema, prepared)]
      .map((issue) => `  ${issue.instancePath || "/"}: ${issue.message}`)
      .join("\n");

    throw new ConfigError(`Invalid ${label}:\n${details}`);
  }

  return prepared as Static<S>;
};
