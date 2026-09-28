# CLAUDE.md

Working notes for AI agents in this repo (the macOS desktop Scout app).

## Fixtures must be anonymized — this repo and its two siblings are public

Scout runs against a real person's vault, so anything lifted from it into a
fixture or an inline test string **must be scrubbed before it lands**. All three
Scout repos — this one, `scout-iOS-app`, and `scout-plugin` — are public.

- **No real identifiers.** Strip company/product names, real coworker names, real
  Linear IDs, GitHub repos, and Slack workspaces/channels. Use the shared
  stand-ins so fixtures stay internally consistent:
  - People: `Alex` / `Priya` / `Sam`; comment/proposal author `alex` / `Alex`.
  - Linear: `PROJ-1234` (use neutral team prefixes like `OPS-`, `DESK-`, `TEAM-`
    when you need variety) — never the real team prefixes (`AI-`, `KAI-`, `ST-`, …).
  - GitHub: `example-org/<repo>`.
  - Slack: `acme-co.slack.com/archives/C0123456789/p1700000000000000`.
  - Vendors/products: a generic noun ("the demo", "the tracing job"), not the brand.
- **Anonymize content, not structure.** Keep the load-bearing *shapes* the parser
  is tested on — `[#TAG]` short-prefixes, `**bold**`, `_(italic)_`,
  `[[wikilinks]]`, ` — ` separators, `` `code` ``. Only swap the words around them.
  A `[#TAG]`'s **shape** is load-bearing; its **letters are not**. Each corpus tag
  is chosen to exercise one grammar property, so a replacement must preserve that
  property — the `_note` on each case says which:
  - `IOTA` — 4 chars, contains I and O, i.e. *not* Crockford base32 (which omits
    I/L/O/U). A replacement must also contain one of those letters.
  - `XI7391` — 6 chars, contains I; longer than the old 4-char limit.
  - `NTX` — 3 chars, no bold, no separator.
  - `7391K` — digit-led with a trailing letter, so it is not a bare GitHub ref.

  The tags themselves are invented and must stay that way. **Verify a new tag
  against the vault before using it** (see below) — the previous set (`MIRO`,
  `AI3026`, `RSM`, `5864M`) was documented here as synthetic but was not: each
  appeared in dozens to hundreds of real vault files, and `AI3026` was a real
  Linear id under the real `AI-` prefix. Replaced in #111.
- **Check a literal against the vault before trusting it.** Count *files*, and
  exclude `~/Scout/.claude/` — those are session transcripts holding copies of
  this repo's own test source, so they inflate every literal, invented ones
  included. Real identifiers land in the tens-to-hundreds once excluded;
  invented ones land at zero.

  ```bash
  grep -rIow -F -f candidates.txt ~/Scout | sort -u \
    | grep -v '/Scout/\.claude/' | awk -F: '{print $NF}' | sort | uniq -c | sort -rn
  ```

  Two traps: compare priority-shaped tokens against their siblings (`P3` scored
  224 files where `P1`/`P2` scored ~50, so it was a codename, not a priority);
  and a prefix collision is not a leak (`CC-N` here is this repo's own Control
  Center bug numbering from `docs/control-center-bugs.md`, even though `CC-` is
  also a real Linear team prefix).
- **Preserve legitimate attribution** — these are NOT leaks, leave them: the
  `pyproject`/`marketplace.json` owner, `LICENSE`, and the project's own
  `github.com/<org>/…` URLs.

### `parser-corpus.json` is ONE byte-identical file living in three repos

`ScoutTests/Fixtures/parser-corpus.json` is byte-identical to the **canonical**
copy in `scout-plugin` and the copy in `scout-iOS-app`, and is checksum-guarded on
both the Swift and Python sides — so you cannot edit just one copy. On any change
(anonymizing counts):

1. Edit the corpus; keep every `expected` field consistent with the parser rules
   (`ParserContractTests` is the judge).
2. Copy it byte-for-byte into the sibling checkouts:
   - `../scout-plugin/engine/tests/fixtures/contract/parser-corpus.json` (canonical)
   - `ScoutMobileTests/Fixtures/parser-corpus.json` in `Raven-Scout/scout-iOS-app`,
     which is usually **not** checked out alongside this repo — clone it when you
     need to touch the corpus. It has no checksum guard of its own, so nothing
     fails locally if you forget it; the copy just silently drifts.
3. Update BOTH checksum guards to the new `shasum -a 256` of the file:
   - `canonicalSHA256` in `ScoutTests/ActionItems/ParserContractTests.swift`
   - `EXPECTED_SHA256` in `../scout-plugin/engine/tests/unit/test_parser_corpus_checksum.py`
4. Verify all three: this repo's `ParserContractTests` (on `platform=macOS`),
   scout-iOS `ParserContractTests`, and plugin
   `pytest tests/unit/test_parser_contract.py tests/unit/test_parser_corpus_checksum.py`.
   scout-iOS-app builds via **XcodeGen**, so `xcodegen generate` has to run before
   its tests will build at all — if that tool is not installed, say so rather than
   reporting the corpus as fully verified.
5. Open all three PRs and **merge them together.** The two checksum guards fail on
   either side the moment one lands alone.
