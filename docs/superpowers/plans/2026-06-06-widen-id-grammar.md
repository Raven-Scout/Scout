# Widen Stable-ID Grammar to `[#TAG]` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the action-item stable-ID parsers recognize variable-length semantic `[#TAG]` identifiers (not just 4-char Crockford), so `--by-id` fires on the real vault's tags and `backfill-prefixes` stops double-prefixing them.

**Architecture:** Widen the *recognition* grammar to **2–8 chars of `[A-Z0-9]` containing ≥1 letter** in the one place each parser defines it (`scout.ids` in scout-plugin; `ActionItemsParser`/`ActionItemsWriter` in scout-app). Recognition-only — `new_short_prefix` keeps minting 4-char Crockford. Downstream code (backfill, `--by-id`, the app writer) keys off "is there a recognized prefix", so widening cascades. Add `--by-id` ambiguity detection (reusable human tags can collide where random codes couldn't), reconcile the generation prompt, and extend the cross-language contract corpus.

**Tech Stack:** Python 3.11/3.12 + pytest + ruff (scout-plugin); Swift + swift-testing + XCTest bundle (scout-app); bash; git.

**The canonical grammar (used verbatim in every task):**
- **Tag** = 2–8 characters from `[A-Z0-9]`, at least one of which is `[A-Z]`.
- **Recognition regex** (unanchored; captures the bare tag in group 1):
  `\[#(?=[A-Z0-9]{2,8}\])([A-Z0-9]*[A-Z][A-Z0-9]*)\]`
- **Leading/extraction regex** (anchored at start): the same, prefixed with `^\s*` (Python) / `^` (Swift).
- Accepts: `[#RSM]`, `[#MIRO]`, `[#AI3026]`, `[#P3WISH]`, `[#5864M]`, `[#7W9A]` (Crockford-4 mints). Rejects: `[#555]` (pure digits → GitHub ref), `[#A]` (<2), `[#ABCDEFGHI]` (>8), `[#a3f7]` (lowercase).

**Repos & branches:**
- `scout-plugin` — **create** branch `feat/widen-id-grammar-issue-117`. Tasks A*, B*, D1.
- `scout-app` — branch `feat/widen-id-grammar-issue-117` (already created off post-fix `main` `45c775b`; holds the spec). Tasks C1, D2.

**Reference spec:** `docs/superpowers/specs/2026-06-06-widen-id-grammar-design.md`

**Milestone order (dependencies):** M-A → M-D-python depends on A1+A2 (corpus `short_prefix` needs the widened parser). M-C → M-D-swift. M-B is independent. Recommended sequence: A1, A2, A3, A4, B1, C1, D1, D2.

---

## Milestone A — scout-plugin recognition widening

### Task A0: Branch
- [ ] **Step 1: Create the plugin branch**

Run:
```bash
cd plugin && git checkout main && git pull --ff-only && git checkout -b feat/widen-id-grammar-issue-117
```
Expected: on a fresh branch off the latest `main`.

### Task A1: Widen the grammar in `scout.ids`

**Files:**
- Modify: `engine/scout/ids.py`
- Test: `engine/tests/unit/test_ids.py`

- [ ] **Step 1: Update the existing pattern test to the new grammar (write failing test)**

In `engine/tests/unit/test_ids.py`, REPLACE `test_short_prefix_pattern_matches_well_formed_prefix` (currently lines 41–50) with:
```python
def test_short_prefix_pattern_matches_well_formed_prefix() -> None:
    rx = short_prefix_pattern()
    # 4-char Crockford (minted) still valid.
    assert rx.fullmatch("[#A3F7]")
    # Variable length 2–8, semantic tags (incl. non-Crockford I/L/O/U).
    assert rx.fullmatch("[#RSM]")        # 3 chars
    assert rx.fullmatch("[#MIRO]")       # contains I and O
    assert rx.fullmatch("[#AI3026]")     # 6 chars, contains I
    assert rx.fullmatch("[#5864M]")      # digit-led, 5 chars
    # Rejections.
    assert not rx.fullmatch("[#a3f7]")   # lowercase
    assert not rx.fullmatch("[#A-37]")   # hyphen
    assert not rx.fullmatch("[#A]")      # too short (<2)
    assert not rx.fullmatch("[#ABCDEFGHI]")  # too long (>8)
    assert not rx.fullmatch("[#555]")    # pure digits → GitHub issue ref, not a tag
    assert not rx.fullmatch("[#0000]")   # pure digits
```
Add a new test for the anchored extraction pattern:
```python
def test_leading_prefix_pattern_anchors_at_start() -> None:
    from scout.ids import leading_prefix_pattern

    rx = leading_prefix_pattern()
    m = rx.match("[#MIRO] **Miro 1:1**")
    assert m is not None and m.group(1) == "MIRO"
    # Does NOT match a tag that isn't at the very start (e.g. a body GitHub ref).
    assert rx.match("see [#AI3026] in body") is None
    assert rx.match("[#555] pure digits") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd plugin/engine && python -m pytest tests/unit/test_ids.py -v`
Expected: FAIL — `[#RSM]`/`[#AI3026]` not matched by the current 4-Crockford pattern; `leading_prefix_pattern` import error.

- [ ] **Step 3: Implement the widened grammar**

In `engine/scout/ids.py`, KEEP `CROCKFORD_ALPHABET`, `SHORT_PREFIX_LEN = 4`, and `new_short_prefix`/`new_ulid` exactly as-is (minting is unchanged). REPLACE the `_PREFIX_REGEX` definition (currently line 23) and `short_prefix_pattern` (currently lines 51–57) with:
```python
# Recognition grammar for a stable-ID tag: 2–8 chars of [A-Z0-9] with at least
# one letter. The letter requirement disambiguates from pure-numeric GitHub
# issue refs like `[#555]` (rendered by scout-app's GitHubRefLinkifier).
# NOTE: this is the RECOGNITION grammar (what counts as an existing tag).
# `new_short_prefix` still MINTS 4-char Crockford codes, a strict subset.
_TAG_BODY = r"(?=[A-Z0-9]{2,8}\])([A-Z0-9]*[A-Z][A-Z0-9]*)"
_PREFIX_REGEX = re.compile(r"\[#" + _TAG_BODY + r"\]")
_LEADING_PREFIX_REGEX = re.compile(r"^\s*\[#" + _TAG_BODY + r"\]")


def short_prefix_pattern() -> re.Pattern[str]:
    """Regex matching a `[#TAG]` token ANYWHERE in a string (unanchored).

    `group(0)` is the full bracketed token; `group(1)` is the bare tag. Used
    by the "does this line already carry a tag?" guard. TAG = 2–8 `[A-Z0-9]`
    with ≥1 letter.
    """
    return _PREFIX_REGEX


def leading_prefix_pattern() -> re.Pattern[str]:
    """Regex matching a `[#TAG]` only at the START of a (whitespace-led) string.

    Use for EXTRACTING the leading identifier off a task title, so a `[#TAG]`
    appearing mid-text (e.g. a GitHub ref in the body) is never mistaken for
    the task's id.
    """
    return _LEADING_PREFIX_REGEX
```
(Leave the module docstring's mention of Crockford for minting; add a one-line note that recognition is wider. `SHORT_PREFIX_LEN` stays — `new_short_prefix` uses it.)

- [ ] **Step 4: Run to verify pass**

Run: `cd plugin/engine && python -m pytest tests/unit/test_ids.py -v`
Expected: PASS (all, including the unchanged `new_short_prefix` tests).

- [ ] **Step 5: Lint**

Run: `cd plugin/engine && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests`
Expected: clean. (If `ruff format --check` flags the edited files, run `.venv/bin/ruff format scout tests` and re-check.)

- [ ] **Step 6: Commit**

```bash
cd plugin
git add engine/scout/ids.py engine/tests/unit/test_ids.py
git commit -m "feat(ids): recognize variable-length [#TAG] (2-8 A-Z0-9, >=1 letter)

Recognition-only; new_short_prefix still mints Crockford-4. Adds anchored
leading_prefix_pattern for extraction. Refs #117."
```

### Task A2: Anchored extraction in `parser.py`

**Files:**
- Modify: `engine/scout/action_items/parser.py` (import + lines 199–206)
- Test: `engine/tests/unit/test_action_items_parser.py`

- [ ] **Step 1: Write failing tests**

Append to `engine/tests/unit/test_action_items_parser.py`:
```python
def test_parser_extracts_semantic_tag(tmp_path) -> None:
    from scout.action_items.parser import parse_file

    f = tmp_path / "action-items-2026-06-06.md"
    f.write_text(
        "# T\n\n## 🔴 Urgent\n\n- [ ] [#AI3026] **Validate tracing** — overnight\n",
        encoding="utf-8",
    )
    items = parse_file(f)
    assert len(items) == 1
    assert items[0].short_prefix == "AI3026"
    assert "[#AI3026]" not in items[0].title  # stripped from the title


def test_parser_does_not_extract_midbody_or_numeric_tag(tmp_path) -> None:
    from scout.action_items.parser import parse_file

    f = tmp_path / "action-items-2026-06-06.md"
    # A GitHub-ref-shaped token mid-title and a pure-numeric token must NOT be
    # mistaken for the leading stable-ID prefix.
    f.write_text(
        "# T\n\n## 🔴 Urgent\n\n- [ ] **Review [#555] in acme/api** — body\n",
        encoding="utf-8",
    )
    items = parse_file(f)
    assert len(items) == 1
    assert items[0].short_prefix is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd plugin/engine && python -m pytest tests/unit/test_action_items_parser.py -k "semantic_tag or midbody" -v`
Expected: FAIL — `short_prefix` is None for `[#AI3026]` (current `.search()` with 4-Crockford pattern doesn't match it).

- [ ] **Step 3: Switch extraction to the anchored pattern**

In `engine/scout/action_items/parser.py`:
- line 18, change the import to: `from scout.ids import leading_prefix_pattern, short_prefix_pattern` (keep `short_prefix_pattern` only if still referenced elsewhere in the file; if not, import just `leading_prefix_pattern`).
- Replace line 200 `_prefix_match = short_prefix_pattern().search(title)` with:
```python
    _prefix_match = leading_prefix_pattern().match(title)
```
(The surrounding block at lines 201–206 is unchanged: it reads `group(1)`, slices `title[:m.start()] + title[m.end():]`, collapses double spaces. With an anchored match `m.start()` is 0, so this cleanly drops the leading tag.)

- [ ] **Step 4: Run to verify pass (and the whole parser suite)**

Run: `cd plugin/engine && python -m pytest tests/unit/test_action_items_parser.py -v`
Expected: PASS, including pre-existing parser tests.

- [ ] **Step 5: Lint + commit**

```bash
cd plugin/engine && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests
cd plugin
git add engine/scout/action_items/parser.py engine/tests/unit/test_action_items_parser.py
git commit -m "feat(parser): extract leading [#TAG] with anchored pattern

Anchoring prevents mid-body GitHub refs / numeric tokens from being read as
the task's id. Refs #117."
```

### Task A3: `--by-id` ambiguity detection in `_common.py`

**Files:**
- Modify: `engine/scout/action_items/_common.py` (the `by_id` branch, lines 120–148)
- Test: `engine/tests/unit/test_action_items_common.py`

- [ ] **Step 1: Write failing test**

Append to `engine/tests/unit/test_action_items_common.py`:
```python
def test_resolve_target_ambiguous_id_raises(fake_data_dir: Path) -> None:
    """Two open tasks sharing a [#TAG] is ambiguous for --by-id; raise rather
    than silently acting on the first (reusable human tags can collide)."""
    items = [
        ActionItem(
            priority="🔴", title="Miro 1:1 follow-through", status="open",
            section="To Do", context_links=[], notes=[], details=[],
            raw_line="- [ ] [#MIRO] Miro 1:1 follow-through", line_number=5,
            short_prefix="MIRO",
        ),
        ActionItem(
            priority="🟡", title="Miro design doc review", status="open",
            section="To Do", context_links=[], notes=[], details=[],
            raw_line="- [ ] [#MIRO] Miro design doc review", line_number=9,
            short_prefix="MIRO",
        ),
    ]
    with pytest.raises(ActionItemError, match="ambiguous id"):
        resolve_target(items=items, data_dir=fake_data_dir, by_id="MIRO", by_subject=None)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd plugin/engine && python -m pytest tests/unit/test_action_items_common.py -k ambiguous_id -v`
Expected: FAIL — current code `next()`-picks the first match, no error raised.

- [ ] **Step 3: Implement ambiguity detection**

In `engine/scout/action_items/_common.py`, in the `if by_id is not None:` branch, replace the single-match lookup (currently line 122 `match = next((i for i in items if i.short_prefix == by_id), None)`) with a collect-all + ambiguity guard:
```python
        candidates = [i for i in items if i.short_prefix == by_id]
        if len(candidates) > 1:
            raise ActionItemError(
                f"ambiguous id [#{by_id}]; matched {len(candidates)} tasks:\n"
                + "\n".join(f"  - {c.title}" for c in candidates)
            )
        match = candidates[0] if candidates else None
```
Everything else in the branch (the `entry is None` auto-register path using `match`, the `match is None` errors, the final `return match, entry.ulid, "id"`) stays unchanged — `match` keeps the same meaning (the single matching item or `None`).

- [ ] **Step 4: Run to verify pass (whole common suite)**

Run: `cd plugin/engine && python -m pytest tests/unit/test_action_items_common.py -v`
Expected: PASS (new ambiguity test + all pre-existing `resolve_target` tests, which use unique prefixes).

- [ ] **Step 5: Lint + commit**

```bash
cd plugin/engine && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests
cd plugin
git add engine/scout/action_items/_common.py engine/tests/unit/test_action_items_common.py
git commit -m "feat(action-items): --by-id errors on duplicate open tags

Reusable semantic tags can collide; surface ambiguity (exit 3) instead of
silently picking the first match. Refs #117."
```

### Task A4: Backfill regression — skip `[#TAG]` lines

**Files:**
- Test: `engine/tests/unit/test_action_items_backfill.py` (create if absent; otherwise append)

`backfill.py` and `add_prefix_to_line` need NO code change (they inherit the widened pattern via `short_prefix is None` / `short_prefix_pattern().search`). This task locks that with a regression test so the double-prefix hazard can't return.

- [ ] **Step 1: Find the existing backfill test module**

Run: `cd plugin && ls engine/tests/unit/ | grep -i backfill || echo "none — create test_action_items_backfill.py"`

- [ ] **Step 2: Write the regression test**

Add (to the existing backfill test module, or a new `engine/tests/unit/test_action_items_backfill.py` with the standard header `"""Unit tests for scout.action_items.backfill."""` + `from __future__ import annotations` + `from pathlib import Path` + `from scout.action_items.backfill import backfill_prefixes`):
```python
def test_backfill_skips_lines_with_semantic_tag(fake_data_dir: Path, tmp_path: Path) -> None:
    """A line already carrying a variable-length [#TAG] must NOT get a second
    prefix prepended (the double-prefix hazard that motivated #117)."""
    f = tmp_path / "action-items-2026-06-06.md"
    f.write_text(
        "# T\n\n## 🔴 Urgent\n\n"
        "- [ ] [#MIRO] **Miro 1:1** — sends\n"        # semantic tag: skip
        "- [ ] [#AI3026] **Validate tracing**\n"      # 6-char tag: skip
        "- [ ] **Bare unprefixed task** — needs id\n", # bare: gets a prefix
        encoding="utf-8",
    )
    added = backfill_prefixes(target=f, data_dir=fake_data_dir, dry_run=True)
    titles = {title for _, _, title in added}
    assert all("Miro" not in t and "Validate tracing" not in t for t in titles)
    assert len(added) == 1  # only the bare line
```
> Use the `fake_data_dir` fixture (same one `test_action_items_common.py` uses); confirm its name in `engine/tests/conftest.py` and adjust if different.

- [ ] **Step 3: Run**

Run: `cd plugin/engine && python -m pytest tests/unit/test_action_items_backfill.py -k semantic_tag -v`
Expected: PASS (depends on A1+A2 being in place — the parser now reports `short_prefix` for the tag lines, so backfill skips them).

- [ ] **Step 4: Lint + commit**

```bash
cd plugin/engine && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests
cd plugin
git add engine/tests/unit/test_action_items_backfill.py
git commit -m "test(backfill): regression — never double-prefix a [#TAG] line

Refs #117."
```

---

## Milestone B — scout-plugin generation prompt

### Task B1: Rewrite the Hard Rule to encourage semantic tags

**Files:**
- Modify: `phases/core/action-items.md` (the "Hard Rule — Every Task Line Has a Stable `[#XXXX]` Prefix" section, lines 85–121)

- [ ] **Step 1: Replace the section body**

Replace lines 85–121 (from the `### Hard Rule — Every Task Line Has a Stable …` heading through the paragraph ending `…fragile subject-matching for those lines.`) with:
```markdown
### Hard Rule — Every Task Line Has a Stable `[#TAG]`

**Every new task line you write MUST start with a stable `[#TAG]` identifier** — 2–8 uppercase letters/digits with at least one letter (e.g. `[#NAHSEND]`, `[#AI3026]`, `[#RSM]`). The tag is the structural identifier scout-app uses to mark tasks done, snooze them, and attach comments — without it, the app falls back to brittle markdown-substring matching that fails on emoji, italics, em-dashes, embedded links, or any non-ASCII drift. Issue #10 of scout-app catalogs the failure modes.

**Prefer a short, meaningful mnemonic** that hints at the task and is easy to cross-reference from other lines (e.g. `[#NAHSEND]`, `[#MIRO]`, `[#AI3026]`). When nothing meaningful fits, mint a random one:

```bash
PFX=$(scoutctl action-items new-prefix)   # random 4-char fallback id
echo "- [ ] [#${PFX}] **${SUBJECT}** ${BODY}" >> "$DAILY_FILE"
```

**Canonical task line shape:**
```
- [ ] [#TAG] **<bold subject>** <optional body, links, italic context>
```
The tag goes **after** the checkbox marker and **before** the bold subject. Exactly that order — the parser keys off the leading position.

**Tag rules:**
- 2–8 chars, `[A-Z0-9]`, at least one letter. (Pure-numeric like `[#555]` is reserved for GitHub issue refs and is NOT a valid tag.)
- **Unique within the file** — never give two open tasks the same tag (scout-app's `--by-id` will refuse an ambiguous tag).
- **Carry-forward keeps the original tag verbatim.** When propagating an item from yesterday into today, copy its `[#TAG]` exactly — do NOT mint a new one. The tag is the task's identity across days.

**Existing unprefixed lines (legacy carryover):** when you find a task that lacks a `[#TAG]`, give it one on first touch, or run the idempotent one-shot backfill (it leaves already-tagged lines alone):
```bash
scoutctl action-items backfill-prefixes "$DAILY_FILE"
```

**Self-check before commit:** every `- [ ]`/`- [x]` line MUST carry a `[#TAG]`. A heuristic grep catches drift:
```bash
grep -nE '^\s*- \[[ x]\] ' "$DAILY_FILE" | grep -vE ' \[#[A-Z0-9]{2,8}\] ' && \
    echo "ERROR: lines missing [#TAG] prefix above — fix before commit" >&2
```
If that grep finds anything, the file is non-compliant and scout-app's writes will fall back to fragile subject-matching for those lines.
```
> The `### Hard Rule — Trim by Demotion…` section that follows (line 123+) is unchanged. The `[#XXXX]` mentions in the illustrative templates (lines ~46–72) and the continuity-dropoff audit (line ~147) read as generic ids; leave them, or optionally s/`[#XXXX]`/`[#TAG]`/ for consistency (cosmetic).

- [ ] **Step 2: Verify the file still reads coherently**

Run: `cd plugin && sed -n '83,122p' phases/core/action-items.md`
Expected: the new section is present, grep uses `[#[A-Z0-9]{2,8}]`, no leftover "4-char Crockford" mandate.

- [ ] **Step 3: Commit**

```bash
cd plugin
git add phases/core/action-items.md
git commit -m "docs(prompt): encourage semantic [#TAG]s, new-prefix as fallback

Reconciles the generation prompt with the widened recognition grammar. Refs #117."
```

---

## Milestone C — scout-app parser widening

### Task C1: Widen `extractShortPrefix` and the writer's line reader

**Files:**
- Modify: `Scout/ActionItems/ActionItemsParser.swift` (`extractShortPrefix`, the `^\[#([0-9A-HJKMNP-TV-Z]{4})\]\s*` regex)
- Modify: `Scout/ActionItems/ActionItemsWriter.swift` (`shortPrefix(inFile:atLine:)`, the `^\s*- \[[ xX]\] \[#([0-9A-HJKMNP-TV-Z]{4})\]` regex)
- Test: `ScoutTests/ActionItems/ActionItemsParserTests.swift`

- [ ] **Step 1: Write failing tests**

Append to `ScoutTests/ActionItems/ActionItemsParserTests.swift` (inside the existing `@Suite struct ActionItemsParserTests`, or a new `@Suite`):
```swift
@Test func extractsVariableLengthSemanticTag() throws {
    let url = URL(fileURLWithPath: "/tmp/action-items-2026-06-06.md")
    let text = "# T\n\n## 🔴 Urgent\n\n- [ ] [#AI3026] **Validate tracing** — overnight\n"
    let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
    let t = try #require(doc.sections.flatMap { $0.tasks }.first)
    #expect(t.shortPrefix == "AI3026")
    #expect(t.subject == "**Validate tracing**")
}

@Test func doesNotExtractNumericGitHubRefAsPrefix() throws {
    let url = URL(fileURLWithPath: "/tmp/action-items-2026-06-06.md")
    // Leading numeric token must NOT be taken as a tag (it's a GitHub ref shape).
    let text = "# T\n\n## 🔴 Urgent\n\n- [ ] [#555] **fix the bug**\n"
    let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
    let t = try #require(doc.sections.flatMap { $0.tasks }.first)
    #expect(t.shortPrefix == nil)
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/macos && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ActionItemsParserTests/extractsVariableLengthSemanticTag 2>&1 | tail -25`
Expected: FAIL (shortPrefix nil for `[#AI3026]` under the 4-Crockford regex).
> SourceKit may emit spurious "Cannot find type" diagnostics; only the `xcodebuild` result counts.

- [ ] **Step 3: Widen both regexes**

Define the shared pattern once and use it in both sites. In `Scout/ActionItems/ActionItemsParser.swift`, change `extractShortPrefix`'s regex literal from
`#"^\[#([0-9A-HJKMNP-TV-Z]{4})\]\s*"#` to
`#"^\[#(?=[A-Z0-9]{2,8}\])([A-Z0-9]*[A-Z][A-Z0-9]*)\]\s*"#`.

In `Scout/ActionItems/ActionItemsWriter.swift`, change `shortPrefix(inFile:atLine:)`'s regex literal from
`#"^\s*- \[[ xX]\] \[#([0-9A-HJKMNP-TV-Z]{4})\]"#` to
`#"^\s*- \[[ xX]\] \[#(?=[A-Z0-9]{2,8}\])([A-Z0-9]*[A-Z][A-Z0-9]*)\]"#`.

(NSRegularExpression supports the `(?=…)` lookahead. The capture group index stays 1 in both.)

- [ ] **Step 4: Run the Action Items suites**

Run: `cd apps/macos && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ActionItemsParserTests -only-testing:ScoutTests/ActionItemsWriterTests 2>&1 | tail -25`
Expected: `** TEST SUCCEEDED **` — new tests pass; the existing writer test `readsShortPrefixAtLineNumber` (uses `[#AB12]`, still valid) and the `[#A3F7]`/`[#AB12]` cases stay green.

- [ ] **Step 5: Commit**

```bash
cd apps/macos
git add Scout/ActionItems/ActionItemsParser.swift Scout/ActionItems/ActionItemsWriter.swift ScoutTests/ActionItems/ActionItemsParserTests.swift
git commit -m "feat(action-items): recognize variable-length [#TAG] in parser + line reader

2-8 A-Z0-9 with >=1 letter (rejects numeric GitHub refs). The writer now emits
--by-id for the vault's semantic-tag lines. Refs scout-plugin#117, #10."
```

---

## Milestone D — extend the cross-language contract corpus

### Task D1: Add `[#TAG]` entries to the canonical corpus + Python suite

**Files:**
- Modify: `scout-plugin/engine/tests/fixtures/contract/parser-corpus.json` (canonical)

Depends on A1+A2 (the Python parser must extract semantic tags). The contract test is fully parametrized over the corpus, so adding entries auto-extends `test_short_prefix`/`test_body`/`test_subject`/`test_plain_subject` with no test-code change. New entries have `short_prefix != null`, so `test_subject`/`test_plain_subject` are `strict`-xfailed (render.py prefix-strip bug #114) while `test_short_prefix`/`test_body` pass.

- [ ] **Step 1: Update `_doc` and append entries**

In `parser-corpus.json`: update the `_doc` string's `short_prefix` clause from "bare 4-char Crockford [#XXXX] code" to: `short_prefix = bare 2–8 char [A-Z0-9] tag (>=1 letter) extracted+removed from the line, else null`. Then add these four objects to the `entries` array:
```json
    {
      "name": "semantic-tag-4char-non-crockford",
      "line": "- [ ] [#MIRO] **Miro 1:1 follow-through** — 3 quick sends",
      "expected": {
        "short_prefix": "MIRO",
        "subject": "**Miro 1:1 follow-through**",
        "plain_subject": "Miro 1:1 follow-through",
        "body": "3 quick sends"
      },
      "_note": "4-char tag containing I and O (NOT Crockford). Exercises the widened recognition grammar (#117). subject/plain_subject xfail on Python (render.py prefix-strip #114)."
    },
    {
      "name": "semantic-tag-6char",
      "line": "- [ ] [#AI3026] **Validate kai-agent tracing**",
      "expected": {
        "short_prefix": "AI3026",
        "subject": "**Validate kai-agent tracing**",
        "plain_subject": "Validate kai-agent tracing",
        "body": ""
      },
      "_note": "6-char tag containing I. Length > the old 4-char limit."
    },
    {
      "name": "semantic-tag-3char",
      "line": "- [ ] [#RSM] Rossum SL tester",
      "expected": {
        "short_prefix": "RSM",
        "subject": "Rossum SL tester",
        "plain_subject": "Rossum SL tester",
        "body": ""
      },
      "_note": "3-char tag, no bold, no separator."
    },
    {
      "name": "semantic-tag-digit-led",
      "line": "- [ ] [#5864M] **Merge ui PR** — APPROVED",
      "expected": {
        "short_prefix": "5864M",
        "subject": "**Merge ui PR**",
        "plain_subject": "Merge ui PR",
        "body": "APPROVED"
      },
      "_note": "Digit-led 5-char tag with one trailing letter — accepted (has a letter); pure-digit would be rejected as a GitHub ref."
    }
```
(Append after the last existing entry `wikilink-alias-and-code`; mind the comma between array elements.)

- [ ] **Step 2: Run the Python contract suite**

Run: `cd plugin/engine && python -m pytest tests/unit/test_parser_contract.py -v`
Expected: the four new `test_short_prefix[...]` and `test_body[...]` PASS; the four new `test_subject[...]`/`test_plain_subject[...]` are `xfailed` (strict). Total: `20 passed, 20 xfailed` (was 16/12; +4 passed short_prefix +4 passed body, +8 xfail).
> If a `test_body` mismatches, fix the expectation in the JSON to render.py's actual token-aware split (per the M3.1 reconciliation rule — don't loosen the test); if `test_short_prefix` fails, A1/A2 aren't in place on this branch.

- [ ] **Step 3: Validate JSON + lint + commit**

```bash
cd plugin/engine && python -c "import json,pathlib; json.loads(pathlib.Path('tests/fixtures/contract/parser-corpus.json').read_text())" && echo "JSON ok"
.venv/bin/ruff check tests
cd plugin
git add engine/tests/fixtures/contract/parser-corpus.json
git commit -m "test(contract): add variable-length [#TAG] corpus entries

Exercises the widened grammar across both parsers. Refs #117."
```

### Task D2: Re-copy corpus to scout-app + recompute checksum + Swift suite

**Files:**
- Modify: `scout-app/ScoutTests/Fixtures/parser-corpus.json` (re-copied)
- Modify: `scout-app/ScoutTests/ActionItems/ParserContractTests.swift` (embedded `canonicalSHA256`)

Depends on D1 (canonical corpus final) and C1 (Swift parser widened).

- [ ] **Step 1: Re-copy byte-identically + compute new digest**

```bash
cp plugin/engine/tests/fixtures/contract/parser-corpus.json \
   apps/macos/ScoutTests/Fixtures/parser-corpus.json
shasum -a 256 apps/macos/ScoutTests/Fixtures/parser-corpus.json | awk '{print $1}'
```
Note the new 64-char digest.

- [ ] **Step 2: Update the embedded digest**

In `ScoutTests/ActionItems/ParserContractTests.swift`, replace the `static let canonicalSHA256 = "…"` value with the digest from Step 1.

- [ ] **Step 3: Run the Swift contract suite**

Run: `cd apps/macos && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ParserContractTests 2>&1 | tail -25`
Expected: `** TEST SUCCEEDED **` — `corpusMatchesCanonicalChecksum` passes (copy byte-identical), `parserMatchesContract` passes for ALL entries including the four new `[#TAG]` ones (the Swift parser strips the tag, so subject/plain_subject match; no xfail on the Swift side).

- [ ] **Step 4: Commit**

```bash
cd apps/macos
git add ScoutTests/Fixtures/parser-corpus.json ScoutTests/ActionItems/ParserContractTests.swift
git commit -m "test(contract): sync [#TAG] corpus to app + update checksum

Swift parser reproduces the new variable-length tag entries. Refs scout-plugin#117, #10."
```

---

## Self-Review

**Spec coverage:** ids grammar widening → A1; anchored extraction → A2; `--by-id` ambiguity → A3; backfill no-double-prefix → A4 (regression test; no code change, as the spec states); prompt rewrite → B1; app `extractShortPrefix` (+ the writer's line reader, which the spec implies via "the app parser") → C1; corpus + both suites → D1/D2; `new_short_prefix` unchanged → explicitly preserved in A1; migration/no-data-change + `/scout-update` hold lift → covered by the behavior (no task needed). render.py (#114) and the crash (#27) are non-goals — not in scope. ✓

**Placeholder scan:** No TBD/TODO. Two "confirm the real symbol" steps remain (the `fake_data_dir` fixture name in A3/A4, and whether `short_prefix_pattern` is still imported in parser.py A2) — each names the exact command to verify and is a real codebase check, not a hidden gap.

**Type/identifier consistency:** `leading_prefix_pattern()`/`short_prefix_pattern()` are defined in A1 and consumed in A2; the regex `\[#(?=[A-Z0-9]{2,8}\])([A-Z0-9]*[A-Z][A-Z0-9]*)\]` is identical across A1 (Python) and C1 (Swift, with `(?=…)` lookahead); corpus JSON keys (`short_prefix`/`subject`/`plain_subject`/`body`) match the parametrized test and the Swift `Entry.Expected` decoder; the `ActionItem(...)` constructor fields in A3/A4 tests match `test_action_items_common.py`'s existing usage. ✓
