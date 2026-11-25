import { type Static, type TUnsafe, Type } from "typebox";

/**
 * A string union as plain JSON Schema — `{ type: "string", enum: [...] }` — whose static type is
 * the literal union. One builder for tools, config and `classify` output alike (ADR-003).
 *
 * A flat `enum` rather than a `Type.Union` of literals, which serializes to `anyOf`/`const` and
 * is rejected by some providers. `Type.Unsafe` rather than `Type.Enum`, which omits
 * `type: "string"`. A falsy `description` or `default` is dropped, not emitted.
 */
export const StringEnum = <T extends readonly string[]>(
  values: T,
  options?: { description?: string; default?: T[number] },
): TUnsafe<T[number]> =>
  Type.Unsafe({
    type: "string",
    enum: values,
    ...(options?.description && { description: options.description }),
    ...(options?.default && { default: options.default }),
  });

// `Type.Unsafe` declares the static type instead of deriving it from the JSON, so nothing else
// notices when the two drift apart. This alias is exported because only `tsc --noEmit` evaluates
// it (tests are not type-checked), and it is the sole check on an enum field that no consumer
// narrows.
type Equal<A, B> =
  (<U>() => U extends A ? 1 : 2) extends <U>() => U extends B ? 1 : 2 ? true : false;
type Assert<Condition extends true> = Condition;
export type StringEnumResolvesLiterals = Assert<
  Equal<Static<ReturnType<typeof StringEnum<["a", "b"]>>>, "a" | "b">
>;
