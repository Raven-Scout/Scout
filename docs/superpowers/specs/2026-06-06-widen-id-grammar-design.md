# Widen the stable-ID grammar to variable-length `[#TAG]`

**Date:** 2026-06-06
**Issue/origin:** scout-plugin#117 — "Stable-ID shape is too narrow: vault uses variable-length semantic `[#TAG]`s, not 4-char Crockford `[#XXXX]`." Follow-up to the stable-ID contract (scout-app#10, merged via scout-plugin#113 + scout-app#26).
**Repos touched:** `scout-plugin` (ids/parser/`_common`/prompt/corpus/tests) and `scout-app` (parser + corpus copy + contract test).

## Problem

The merged stable-ID work assumes every action-item identifier is **exactly 4 Crockford chars** (`scout.ids`: `SHORT_PREFIX_LEN=4`, `CROCKFORD_ALPHABET` excludes I/L/O/U, `_PREFIX_REGEX`; mirrored in scout-app `ActionItemsParser.extractShortPrefix`). The live vault doesn't follow this: the model writes **semantic mnemonic tags** of mixed length and charset (`[#MIRO]`, `[#NAHSEND]`, `[#AI3026]`, `[#P3WISH]`, `[#RSM]` — 3–7 chars, containing I/L/O), and task bodies cross-reference each other by them.

Consequences on the real vault:
- `extractShortPrefix` returns nil for any non-4-Crockford tag → scout-app falls back to brittle `--subject` matching for exactly the most-cross-referenced lines. #10's brittleness is **not actually fixed** for the lines that matter most.
- `backfill-prefixes` treats `[#TAG]` lines as unprefixed and **prepends a second prefix** (`[#AASN] [#AI3026]`); a dry-run wanted to do this to 27 lines. So the M1 post-session backfill is currently unsafe against the real vault.

## Decision

Widen the **recognition** of an identifier to `[A-Z0-9]{2,8}` (uppercase, start-anchored) everywhere it's parsed, and reconcile the generation prompt to encourage semantic tags. Recognition-only — what `new_short_prefix` *mints* is unchanged.

Approved decisions (from brainstorming):
- **Grammar:** 2–8 chars of `[A-Z0-9]` that **must contain ≥1 letter** — recognition regex `\[#(?=[A-Z0-9]{2,8}\])([A-Z0-9]*[A-Z][A-Z0-9]*)\]` (group 1 = bare tag), anchored at the start of the task body for extraction. The ≥1-letter rule rejects pure-numeric tokens like `[#555]`, which the codebase's `GitHubRefLinkifier` renders for GitHub issue refs — without it, a body ref shape would be indistinguishable from a stable-ID tag. `new_short_prefix` is updated to guarantee minted prefixes contain a letter, so Crockford-4 mints remain a valid (recognizable) subset.
- **Prompt guidance:** rewrite the "Hard Rule" to encourage a short meaningful mnemonic `[#TAG]`, with `scoutctl action-items new-prefix` as the fallback when nothing fits; carry-forward copies the tag verbatim; self-check grep becomes `[#[A-Z0-9]{2,8}]`.
- **`--by-id` becomes ambiguity-aware:** today `_common.resolve_target` `next()`-picks the first item whose `short_prefix` matches; with reusable semantic tags, 2+ open matches must raise the existing ambiguous error (ActionItemError, exit 21 — same class as the --by-subject ambiguity). (Random Crockford codes effectively never collided; human tags can.)
- **`new_short_prefix` unchanged:** keeps minting random 4-char Crockford as the fallback identifier.

## Architecture — the one lever and its ripples

The identifier shape is defined once in `scout.ids` and consumed everywhere. Widening the recognition pattern cascades correctly because downstream code already keys off "is there a recognized prefix":

```
scout.ids pattern widened  ──►  parser.py extracts [#TAG] into ActionItem.short_prefix
                            │       └─► _common.resolve_target --by-id already lazy-registers
                            │           any extracted prefix → works for [#TAG] unchanged
                            ├──►  backfill.py candidates = (short_prefix is None) → [#TAG]
                            │       lines now count as prefixed → NO double-prefixing
                            └──►  add_prefix_to_line guard (same pattern) → also refuses
                                  to double-prefix a [#TAG] line

scout-app extractShortPrefix widened  ──►  ActionTask.shortPrefix populated for [#TAG]
                                            └─► ActionItemsWriter already emits --by-id
                                                when shortPrefix != nil → the important
                                                lines take the structural path, not --subject
```

## Components

### scout-plugin

**`engine/scout/ids.py`** (canonical grammar):
- Replace the Crockford-only recognition constant/regex with the widened pattern. Charset for *recognition* = `[A-Z0-9]`, length 2–8.
- Add a **start-anchored** extraction pattern (e.g. `leading_prefix_pattern()` → `^\s*\[#([A-Z0-9]{2,8})\]`) used for pulling the leading tag off a task title. Keep an **unanchored** `short_prefix_pattern()` (`\[#([A-Z0-9]{2,8})\]`) for the "does this line already carry a tag?" guard.
- `new_short_prefix` is unchanged (still mints 4-char Crockford, collision-checked against id-map). `CROCKFORD_ALPHABET`/minting stays; only recognition widens.

**`engine/scout/action_items/parser.py`**: extract the leading tag with the **anchored** pattern instead of today's `.search()` (with a wider charset, `.search()` could match a bracketed token mid-title; anchoring to the title start is correct and matches the Swift parser's `^`).

**`engine/scout/action_items/_common.py`** (`resolve_target`, `--by-id` branch): collect *all* open items whose `short_prefix == by_id`; if more than one, raise `ActionItemError` ("ambiguous id …", ActionItemError exit 21) mirroring the `--by-subject` ambiguity path; if exactly one, proceed (lazy-register into id-map as today). Single-match behavior and lazy registration are unchanged.

**`engine/scout/action_items/backfill.py`** and **`writer.add_prefix_to_line`**: no logic change — both inherit the widened pattern (candidates still `short_prefix is None`; the guard still refuses lines that already match).

### scout-app

**`Scout/ActionItems/ActionItemsParser.swift`** (`extractShortPrefix`): widen the regex to `^\[#([A-Z0-9]{2,8})\]\s*`. This is the only app source change; the writer already routes `shortPrefix != nil` ops through `--by-id`.

### Generation prompt

**`phases/core/action-items.md`** (scout-plugin): rewrite the "Hard Rule" section + the self-check grep per the approved guidance (semantic mnemonic preferred, `new-prefix` fallback, carry-forward verbatim, grep `^\s*- \[[ x]\] \[#[A-Z0-9]{2,8}\] `).

### Contract corpus + tests

- Add `[#TAG]` entries to the **canonical** corpus (`scout-plugin/engine/tests/fixtures/contract/parser-corpus.json`): variable length + non-Crockford chars, e.g. `[#MIRO]`, `[#AI3026]`, `[#P3WISH]`, `[#RSM]`. Re-copy byte-identically to `scout-app/ScoutTests/Fixtures/parser-corpus.json` and recompute the embedded SHA-256 in `ParserContractTests`.
- Expectation consistency with the existing split: the new entries' `short_prefix` now **passes** on the Python side (parser.py widened); their `subject`/`plain_subject` remain `xfail(strict=True)` under the render.py prefix-strip bug (scout-plugin#114) — same pattern as the existing prefixed entries. The Swift side reproduces **all** fields (it strips the prefix), so Swift passes with no xfail.

## Testing

**scout-plugin unit tests:**
- `test_ids`: the recognition pattern accepts `MIRO`/`AI3026`/`RSM`/`P3WISH`/4-char Crockford, and rejects `<2` chars, `>8` chars, lowercase, and embedded punctuation.
- `test_action_items_parser`: a line led by `[#AI3026]`/`[#MIRO]` yields `short_prefix == "AI3026"`/`"MIRO"` with the tag stripped from the title; a mid-title bracketed token is **not** mistaken for the prefix (anchoring).
- `test_action_items_common`: `--by-id` on a tag shared by two open items raises ambiguous (ActionItemError, exit 21); single match still resolves + lazy-registers.
- `test_post_session_backfill` / backfill unit: a `[#TAG]` line is left untouched (no second prefix added); only genuinely bare lines get one.

**scout-app tests:**
- `ParserContractTests`: the widened corpus passes (checksum guard + all-field assertions over the new `[#TAG]` entries).
- existing `ActionItemsParserTests` / `ActionItemsWriterTests` stay green (the widened `extractShortPrefix` is a superset).

## Migration & safety

- **No data migration** — widening recognition makes existing `[#TAG]` files work as-is. Verify against a sample of real files that no body token is mis-extracted as a prefix (anchoring + the leading-position convention make this safe).
- **The M1 post-session backfill becomes safe** once recognition is widened (it no longer double-prefixes `[#TAG]` lines), so the "don't `/scout-update`" hold can lift after this ships.
- Net outcome: #10's `--by-id` guarantee finally applies to the real vault, not just Crockford-shaped lines.

## Non-goals

- Not changing what `new_short_prefix` mints (stays Crockford-4).
- Not fixing render.py's prefix-strip (scout-plugin#114 — separate; its xfails persist, now extended to the new `[#TAG]` entries on the Python side).
- No global cross-file tag-uniqueness enforcement (YAGNI); within-file ambiguity is surfaced by the `--by-id` change.
- No scope on the comment/done crash (already fixed in scout-app#27).

## Milestones (sequenced in the plan)

1. **M-A (scout-plugin):** widen `scout.ids` recognition + anchored extraction; `parser.py` uses it; `--by-id` ambiguity-aware; unit tests.
2. **M-B (scout-plugin):** rewrite the generation prompt's Hard Rule + self-check grep.
3. **M-C (scout-app):** widen `extractShortPrefix`; keep existing suites green.
4. **M-D (contract):** add `[#TAG]` cases to canonical corpus + Python suite, re-copy + recompute checksum + Swift suite.

Two PRs (scout-plugin: M-A/M-B/M-D-canonical; scout-app: M-C/M-D-copy), as with the prior cycle.
