/**
 * Strip lone UTF-16 surrogate code points that cannot be encoded as UTF-8.
 *
 * Streamed model output occasionally contains unpaired surrogate code points
 * (a high surrogate U+D800–U+DBFF without a trailing low surrogate, or a low
 * surrogate U+DC00–U+DFFF on its own). Left in place they throw when downstream
 * consumers re-encode the string as UTF-8 — the Telegram API and the
 * post-processing transcript writers both do. Valid surrogate pairs (which
 * encode astral characters like emoji) are matched first and kept intact, so
 * only the unpaired halves are removed; all other code points pass through.
 */
export const sanitizeText = (text: string): string =>
  text.replace(
    /[\uD800-\uDBFF][\uDC00-\uDFFF]|([\uD800-\uDFFF])/g,
    (match, lone: string | undefined) => (lone == null ? match : ""),
  );
