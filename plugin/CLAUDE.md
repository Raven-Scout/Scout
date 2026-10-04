# CLAUDE.md

Working notes for AI agents in this repo (the Python Scout engine/plugin).

## Fixtures must be anonymized — this repo and its two siblings are public

Scout runs against a real person's vault, so anything lifted from it into a
fixture or an inline test string **must be scrubbed before it lands**. All three
Scout repos — this one, `Scout` (desktop), and `scout-iOS-app` — are public.

- **No real identifiers.** Strip company/product names, real coworker names, real
  Linear IDs, GitHub repos, and Slack workspaces/channels. Use the shared
  stand-ins so fixtures stay internally consistent:
  - People: `Alex` / `Priya` / `Sam`; comment author `alex`.
  - Linear: `PROJ-1234` (neutral prefixes like `OPS-`, `DESK-` for variety) —
    never the real team prefixes (`AI-`, `KAI-`, `ST-`, …).
  - GitHub: `example-org/<repo>`.
  - Slack: `acme-co.slack.com/archives/C0123456789/p1700000000000000`.
  - Vendors/products: a generic noun, not the brand.
- **Anonymize content, not structure.** Keep the tokens the parser is tested on
  (synthetic `[#TAG]` prefixes, `**bold**`, `_(italic)_`, `[[wikilinks]]`,
  ` — ` separators, `` `code` ``). Only swap the words around them.
- **Don't hardcode personal config in source.** The Linear deep-link workspace is
  read from `SCOUT_LINEAR_WORKSPACE` (default `your-workspace`), not baked in;
  keep new connector/workspace specifics config- or env-driven the same way.
- **Preserve legitimate attribution** — NOT leaks, leave them: `pyproject` authors,
  `.claude-plugin/marketplace.json` / `plugin.json` owner, `LICENSE`, and the
  project's own `github.com/<org>/…` URLs (including the self-update URL).

### `parser-corpus.json` is ONE byte-identical file across the plugin and every client

`engine/tests/fixtures/contract/parser-corpus.json` is the **canonical** copy. The
macOS app's copy is in this monorepo (`apps/macos/ScoutTests/Fixtures/parser-corpus.json`,
compared byte-for-byte by `contract.yml`); `scout-iOS-app` vendors another in its
own repo. Both are checksum-guarded on the Python and Swift sides — so you cannot
edit just one copy. On any change (anonymizing counts):

1. Edit the corpus here; keep every `expected` field consistent with the parser
   (`pytest tests/unit/test_parser_contract.py` is the judge).
2. Update BOTH checksum guards to the new `shasum -a 256` of the file:
   - `EXPECTED_SHA256` in `engine/tests/unit/test_parser_corpus_checksum.py`
   - `canonicalSHA256` in `apps/macos/ScoutTests/ActionItems/ParserContractTests.swift`
     (from the repo root)
3. Copy it over the app's copy, from the repo root:
   `cp plugin/engine/tests/fixtures/contract/parser-corpus.json apps/macos/ScoutTests/Fixtures/parser-corpus.json`
   — and into `ScoutMobileTests/Fixtures/parser-corpus.json` in `Raven-Scout/scout-iOS-app`
   (a separate repo; nothing here fails if that copy drifts).
4. Verify: `pytest tests/unit/test_parser_contract.py tests/unit/test_parser_corpus_checksum.py`,
   the app's `ParserContractTests`, and scout-iOS's `ParserContractTests`. The plugin
   and app changes land in ONE commit — each checksum guard fails the moment the
   corpus changes without it.
