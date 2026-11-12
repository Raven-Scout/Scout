# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `domain-modeling` skill creates them lazily when terms or decisions actually get resolved.

## File structure

Layout:

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-monorepo-with-contract-gate.md
│   └── ...
├── plugin/
└── apps/macos/
```

This repo is single-context: there is no `CONTEXT-MAP.md`, and one `CONTEXT.md` covers `plugin/` and `apps/macos/`.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for the `domain-modeling` skill).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR 0007 (dreaming edits brain skills directly), but worth reopening because..._
