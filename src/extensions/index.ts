import type { TachikomaExtension } from "./api.ts";
import context from "./context/index.ts";
import repl from "./repl/index.ts";

/** First-party extensions, in load order. */
export const firstPartyExtensions = [context, repl] as TachikomaExtension<never>[];
