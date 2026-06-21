# Spike: SPIKE-DLT-064 - Expandable-blockquote entity emission path

**Delta**: [DLT-064](../delta-specs/DLT-064.md)
**Shape Part**: S4 - Expandable-blockquote payload emission
**Status**: Resolved

## Questions

| # | Question | Answer |
|---|----------|--------|
| 1 | Does grammY's installed `MessageEntity` type include `expandable_blockquote`, so the existing `beginSpan(type)` accepts it with no cast? | **Yes.** `@grammyjs/types@3.27.3` (grammY `1.43.0`, resolved in `package.json` `grammy@^1.43.0`) lists `"expandable_blockquote"` in the `MessageEntity.type` union (`message.d.ts:506`). The `Renderer.beginSpan(type, extra)` in `entities.ts` is typed `MessageEntity["type"]`, so emitting it type-checks directly — no local cast or type extension needed. |
| 2 | What is the emission path — renderer-driven structured payload (Option A) or a converter sentinel mapped from markdown (Option B)? | **Option A — renderer-driven structured payload.** The collapse region is a renderer concept: the agent's GFM markdown never expresses "collapsible" (GFM `>` maps to `blockquote`, not `expandable_blockquote`), so markdown-it's token walk cannot produce it. The collapse boundary is computed by the renderer from event timing (tool→text segment count), not from markdown structure. A sentinel would require a custom markdown-it block plugin to survive parsing — more invasive and fragile than building the payload structurally. |
| 3 | Can the blockquote carry the baked `_🔧 <summary>_` markers (italic + inline-code) as nested entities? | **Yes.** The Bot API nesting rules state only that `blockquote`/`expandable_blockquote` "can't be nested" *with each other*; `italic`, `code`, `bold`, etc. may be contained inside them. So wrapping the intermediate content's `{ text, entities }` with one outer `expandable_blockquote` span and keeping the inner summary-marker entities is valid. |
| 4 | Does an `expandable_blockquote` need a minimum amount of content to actually collapse? | **Yes — it needs multiple lines.** A single short line renders as a plain (expanded) quote. This is the "minimum-content guard." It is **naturally satisfied by the threshold**: collapse activates only once the boundary count exceeds the threshold (default 4 ⇒ the 5th boundary), so the block already holds several baked `_🔧 <summary>_` markers (multi-line). No synthetic padding is required at the default threshold. A deliberately low threshold (e.g. 1) could yield a 1-marker block that does not collapse — acceptable degradation (renders as an expanded quote, still readable), not enforced. |
| 5 | How does the emission compose with `compose()`/`finalize()` (which rebuild from a markdown buffer) and `splitMessageWithEntities` (entity-safe chunking across 4096)? | The collapse path adds a structured-payload branch alongside the existing markdown-string path: convert the *intermediate* markdown substring → payload, wrap with `expandable_blockquote`; convert the *live tail* markdown substring → payload; concat the two (rebase offsets). This replaces the single `toTelegramEntities(display)` call on the collapse path only. `splitMessageWithEntities` is reused on the resulting combined payload for overflow; it drops entities whose `length > limit`, but the collapsed block lives within one ≤4096 message (prior chunks are committed independently per R9, each evaluated for its own collapse), so it is not dropped. The `editMessageText`-replaces-full-text model means recomposing the blockquote contents on every flush works exactly like the existing header/body recomposition (DES-009). |

## Requirement Implications

None that change the spec's R table. The multi-line collapse constraint (Q4) reinforces the existing R10 ("the collapsed block is meaningful even for an all-intensive turn … non-empty and expandable") rather than altering it — the threshold-as-trigger model already guarantees a multi-marker (multi-line) block at the default threshold. The one borderline item — whether to enforce a minimum threshold floor so a user-set low threshold can't produce a non-collapsing single-line block — is recorded as a design decision to confirm with the user, not a new requirement.

## Conclusion

**Resolve S4 via Option A.** Emit `expandable_blockquote` as a renderer-driven structured payload, built with two pure helpers added to `entities.ts`:

- `wrapExpandable(payload)` — prepend `{ type: "expandable_blockquote", offset: 0, length: payload.text.length }` over a converted payload's entities (inner italic/code/bold entities nest validly inside).
- `concatPayloads(a, b, sep?)` — join two converted payloads, rebasing the second's entity offsets after `a.text` + separator.

The `StreamRenderer` gains collapse state (boundary counter + enable flag) and, when collapse is active, builds the live/finalized message as `wrapExpandable(convert(intermediate)) ⊕ convert(tail)` instead of `toTelegramEntities(display)`. The threshold naturally satisfies the multi-line collapse requirement; a low-threshold non-collapse is documented as acceptable degradation. grammY 1.43.0 / `@grammyjs/types@3.27.3` type the entity directly, so `beginSpan("expandable_blockquote")` needs no cast.

Sources:
- Telegram Bot API `MessageEntity` (`expandable_blockquote` = "collapsed-by-default block quotation"; nesting: `blockquote`/`expandable_blockquote` can't nest each other; `bold`/`italic`/`code` nest inside) — `https://core.telegram.org/bots/api#messageentity` (Bot API 10.1, June 2026).
- Stack Overflow confirmation of the multi-line collapse requirement — `https://stackoverflow.com/questions/79427631`.
- Installed `@grammyjs/types@3.27.3` `message.d.ts:506` (grammY `1.43.0`).
