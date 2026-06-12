import type { Static, TSchema } from "typebox";
import { Assert, Clone, Convert, Default, Errors } from "typebox/value";

export class ConfigError extends Error {}

/** Apply schema defaults, coerce compatible primitives, then assert. */
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
