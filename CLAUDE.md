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
- **Anonymize content, not structure.** Keep the load-bearing tokens the parser is
  tested on — the synthetic `[#TAG]` short-prefixes (`MIRO`, `AI3026`, `RSM`,
  `5864M`…), `**bold**`, `_(italic)_`, `[[wikilinks]]`, ` — ` separators,
  `` `code` ``. Only swap the words around them.
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
2. Copy it byte-for-byte into the sibling checkouts (cloned alongside this repo):
   - `../scout-plugin/engine/tests/fixtures/contract/parser-corpus.json` (canonical)
   - `../scout-ios/ScoutMobileTests/Fixtures/parser-corpus.json`
3. Update BOTH checksum guards to the new `shasum -a 256` of the file:
   - `canonicalSHA256` in `ScoutTests/ActionItems/ParserContractTests.swift`
   - `EXPECTED_SHA256` in `../scout-plugin/engine/tests/unit/test_parser_corpus_checksum.py`
4. Verify all three: this repo's `ParserContractTests` (on `platform=macOS`),
   scout-iOS `ParserContractTests`, and plugin
   `pytest tests/unit/test_parser_contract.py tests/unit/test_parser_corpus_checksum.py`.
