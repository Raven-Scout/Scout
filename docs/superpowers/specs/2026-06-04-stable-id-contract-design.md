# Stable-ID contract for Action Items — ratify as-built, close the coverage & contract-test gaps

**Date:** 2026-06-04
**Issue/origin:** scout-app #10 ("Markdown-as-canonical-source for Action Items is brittle — evaluate structured-state alternatives"). This doc is the design deliverable named in that issue's Definition of Done, and selects **Option A (stable IDs in the markdown)**.
**Repos touched:** `scout-app` (Swift parser/writer/tests) and `scout-plugin` (Python ids/id-map/CLI/session runners/tests).

## Problem (restated)

Action Items use markdown as both canonical state and presentation. Every read/write re-derives a task's *identity* from its display text, so any drift in whitespace/casing/emoji/punctuation between the three consumers (scout-app Swift, scout-plugin Python, Obsidian) silently misroutes or fails a write. The reported symptom: clicking "Mark done" produced `no open task matched subject: " 🔥 🆕 Update kai-pricing-calculator-app …"` because the app sent a fragile `--subject` substring to `scoutctl` and the match missed.

## Key finding — Option A is ~80% already built

Between when #10 was filed (2026-05-19) and now, most of Option A shipped without #10 being closed:

| Piece | Status | Location |
|---|---|---|
| ULID + 4-char Crockford `[#XXXX]` short prefix | **Built** | `scout-plugin engine/scout/ids.py` |
| Prefix↔ULID map, atomic-rename writes, last-position metadata for fuzzy reattach | **Built** | `scout-plugin engine/scout/id_map.py` (`$SCOUT_DATA_DIR/.scout-state/id-map.json`) |
| `action-items new-prefix` (mint one) + `action-items backfill-prefixes` (idempotent) | **Built** | `scout-plugin engine/scout/action_items/cli.py`, `backfill.py` |
| `--by-id` structural match; lazy ULID mint+register for legacy `--subject` hits | **Built** | `scout-plugin engine/scout/action_items/_common.py` |
| App parser extracts `[#XXXX]`; `ActionTask.shortPrefix`; writer prefers `--by-id` | **Built** | `scout-app ActionItemsParser.extractShortPrefix`, `ActionItemsWriter` (v0.5.5) |
| Python unit tests for ids/id-map/parser | **Built** | `scout-plugin engine/tests/unit/test_ids.py`, `test_id_map.py`, … |
| "Hard Rule — every task line has a `[#XXXX]` prefix" in generation prompt | **Built (but ineffective)** | `scout-plugin phases/core/action-items.md:85` |

**The gaps that remain are enforcement and proof, not infrastructure:**

1. **Coverage.** In the live vault only ~50% of task lines carry a prefix (18/40 on 2026-06-04). The generation prompt already contains an explicit, capitalized "Hard Rule" mandating a prefix per line *and still produces ~50% coverage* — definitive evidence that an LLM-authored, prompt-enforced invariant is not load-bearing. Every unprefixed line falls back to the brittle `--subject` matcher, which is the failure in #10.
2. **No cross-language contract test.** Each repo has its own parser tests, but nothing proves the Swift and Python parsers agree byte-for-byte on a shared corpus — the "coincidence, not a contract" core complaint (#10 acceptance criterion 5).
3. **No design doc.** #10's literal DoD (this document).

## Decision

Keep markdown canonical (Option A). Make `--by-id` the **guaranteed** write path by closing coverage deterministically, lock parser agreement with a cross-language contract test, and ratify the as-built contract in this doc. The `--subject` substring matcher is demoted to dead-code-grade last resort.

Approved decisions from brainstorming:

- **Coverage source:** deterministic idempotent **auto-backfill**, not prompt-hardening.
- **Backfill trigger:** **plugin session-end (primary) + app one-shot safety-net on the write path (secondary)** — never an on-load app write, to avoid adding to the file-watcher churn implicated in #22.
- **Contract corpus sync:** **canonical in plugin, copy in app, checksum-guarded.**

## Architecture

```
GENERATION (plugin session)        ENFORCEMENT (new)          CONSUMPTION (app)
─────────────────────────          ─────────────────          ──────────────────
prompt mints [#XXXX]        ─┐
(unreliable ~50%)            ├──► run-*.sh.tmpl runs      ──►  parser reads [#XXXX]
"Hard Rule" in prompt       ─┘     backfill-prefixes            ActionItemsWriter
                                   as final step,               prefers --by-id
                                   pre-commit (M1)              │
                                                               └─ unprefixed line on
                                                                  write → backfill once
                                                                  → reparse → --by-id
                                                                  retry (M2)

CONTRACT TEST (M3): parser-corpus.json — canonical in plugin, copied to app,
checksum-guarded; Python + Swift tests assert identical
{short_prefix, subject, plainSubject, body} over the historically-broken corpus.
```

## The seven acceptance items (#10 DoD)

### 1. The contract
- **Identity:** every open task line is `- [ ] [#XXXX] <body>` where `XXXX` is 4 chars from the Crockford alphabet `0123456789ABCDEFGHJKMNPQRSTVWXYZ` (excludes I, L, O, U). The prefix sits **after** the checkbox marker and **before** the bold subject. The canonical durable identifier is the ULID in `id-map.json`; the `[#XXXX]` prefix is its human-facing surface form. Carry-forward across days **reuses** the original prefix (identity is stable across files).
- **Source of truth for the schema:** `scout.ids` (`CROCKFORD_ALPHABET`, `SHORT_PREFIX_LEN=4`, `_PREFIX_REGEX`) is the single Python definition; the Swift parser's `extractShortPrefix` regex (`^\[#([0-9A-HJKMNP-TV-Z]{4})\]\s*`) mirrors it. The contract test (item 5) is what *enforces* that the mirror stays faithful.

### 2. The write protocol
- App builds a `WriteOp` carrying the parsed `shortPrefix`. `ActionItemsWriter` emits `--by-id <prefix>` when present, else `--subject` (legacy fallback).
- `scoutctl action-items <op> --by-id` resolves structurally via `id-map.json`; a `--subject` hit lazily mints+registers a ULID so the line gains durable identity on first touch.
- **M2 safety-net:** if a write targets a line with no prefix (`shortPrefix == nil`), or a `--subject` call returns the `noMatch` classification, the app runs `backfill-prefixes` once, reparses, re-resolves the target line to its freshly-minted prefix, and retries with `--by-id`. One attempt; on second failure surface the existing red-banner error.
- Concurrency: app writes are already serialized through the `ActionItemsWriter` actor's serial tail; `id-map.json` writes are atomic-rename last-writer-wins (acceptable at single-digit registrations/day; documented in `id_map.py`).

### 3. The read protocol
- App: FSEvents → 250 ms debounce → `ActionItemsParser.parse` → `[#XXXX]` extracted into `ActionTask.shortPrefix`, stripped from the displayed subject.
- Plugin: `parser.py` extracts the same prefix via `short_prefix_pattern()`.
- Obsidian: renders the prefix as literal text inside the line; harmless, hand-removable (recovery in item 6).
- Stale-cache behavior is unchanged; the prefix is data carried in the same line, so no new staleness surface is introduced.

### 4. The migration path
- No data migration needed for the *format* — it's the existing markdown. Adoption = running `backfill-prefixes` over existing files, which M1 does automatically going forward and which is already available as a manual one-shot (`scoutctl action-items backfill-prefixes [PATH] [--dry-run]`).
- Idempotent: prefixed lines are untouched; only open unprefixed tasks gain a prefix. Rollback = none required (additive text); a prefix can be hand-deleted and re-minted.
- Past files are backfilled lazily — on the next session that touches a given day's file, or on demand. No bulk rewrite of historical files is mandated.

### 5. The contract test suite
- A golden corpus `parser-corpus.json`: array of `{ "line": "<raw task line>", "expected": { "short_prefix": "...|null", "subject": "...", "plain_subject": "...", "body": "..." } }`.
- **Canonical** at `scout-plugin/engine/tests/fixtures/contract/parser-corpus.json`; **copy** at `scout-app/ScoutTests/Fixtures/parser-corpus.json`; a checksum guard (a test that compares a committed SHA-256, or a `make`/CI step) fails on drift between the two copies.
- A Python test and a Swift `ParserContractTests` each assert their parser reproduces every entry's expected fields exactly.
- Seed corpus = the failure modes #10 enumerates plus the live bug: title-case `Scout`; emoji-prefixed bold (`🔥 🆕 **…**`); sub-bullet vs blockquote comments; Linear (`AI-3026`), GitHub PR, and Slack deeplinks; `🛌 Snoozed until` suffixes; `_(carried in from YYYY-MM-DD[, was <kind>])_` markers; underscores/parens; the exact `kai-pricing-calculator-app` line from the report.

### 6. The hand-edit story
- A hand-added line in Obsidian without a prefix is covered by M1 (next session) and M2 (next app write) — it gains a prefix deterministically rather than failing.
- A line whose `[#XXXX]` was accidentally deleted is recoverable: `id-map.json` holds last-known title/file/position so the diff engine can fuzzy-reattach the orphaned line to its prior identity (already implemented; documented in `id_map.py`). The contract documents this as the intended behavior.

### 7. The fallback story
- If `id-map.json` is missing/corrupt: `--by-id` lookups miss; the system degrades to `--subject` matching (today's behavior) rather than failing hard, and a subsequent `backfill-prefixes` rebuilds entries from the prefixes present in the markdown.
- If `scoutctl` is unavailable entirely: the markdown remains fully hand-editable in Obsidian/vim — the non-negotiable outage requirement from #10 is preserved because nothing was moved out of the `.md`.

## Constraints preserved (from #10)
- **Obsidian primary surface:** unchanged — prefixes are inline literal text.
- **Git audit trail:** unchanged — the `.md` diff is still the record; backfill commits land in the session's commit (M1).
- **Cross-device portability:** `id-map.json` is small JSON, file-sync-friendly; no binary/DB.
- **Hand-editability under outage:** preserved (item 7).

## Milestones
- **M1 — Deterministic coverage (plugin):** add `scoutctl action-items backfill-prefixes "$DAILY_FILE"` as a post-session step in `run-scout.sh.tmpl`, `run-dreaming.sh.tmpl`, `run-research.sh.tmpl`, ordered **before** the session's vault git commit so minted prefixes land in the same commit. Independently shippable; delivers the user-visible fix.
- **M2 — App safety-net (scout-app):** in `ActionItemsWriter`, backfill-once-then-retry-`--by-id` on unprefixed-line / `noMatch` writes; write-path only, never on load. Independently shippable.
- **M3 — Cross-language contract test:** corpus + Python test + Swift `ParserContractTests` + checksum guard.
- **M4 — Doc + close #10:** finalize this doc, link the M1–M3 PRs, close #10.

Sequence: M1 → M2 → M3 → M4.

## Non-goals
- No move to JSON/SQLite/event-sourcing (Options B/C/D) — Option A suffices and is built.
- No change to the FSEvents file-watcher; #22 (idle freeze) is tracked separately. M1 reduces vault write churn marginally but is not a fix for #22.
- No UI changes; no drag-to-restatus (#15), though M1+M2 supply the durable key it was blocked on.
- No widening of the prefix to 5 chars; the 4-char space is far from saturation.

## Open implementation detail to confirm during M1
Exact location of the per-session vault git commit (likely in `run-scout.sh.tmpl` or a hook) so the `backfill-prefixes` step is ordered immediately before it. To be resolved in the implementation plan.
