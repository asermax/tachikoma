import type { TachikomaExtension } from "./api.ts";
import boundary from "./boundary/index.ts";
import context from "./context/index.ts";
import repl from "./repl/index.ts";

/** First-party extensions, in load order. */
export const firstPartyExtensions = [context, boundary, repl] as TachikomaExtension<never>[];
