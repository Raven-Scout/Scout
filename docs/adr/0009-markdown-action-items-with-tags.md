---
status: accepted
---

# Action items stay in Markdown, identified by tags the engine enforces

The daily action-items file stays the source of truth, readable and editable by hand, and every action item carries a tag (`[#XXXX]`) that the app and the engine use to find it across days. Moving action items to JSON, SQLite or an event log was considered and not pursued: the Markdown option was already mostly built and sufficed. Tag coverage is enforced by the engine, not the prompt: a capitalized prompt rule still left more than half the action items untagged (18 of 40 lines tagged), so the engine backfills missing tags deterministically after each run. Decided in [the stable-ID contract spec](../superpowers/specs/2026-06-04-stable-id-contract-design.md); landed in [#113](https://github.com/Raven-Scout/Scout/pull/113) (engine backfill) and [scout-app-legacy#26](https://github.com/Raven-Scout/scout-app-legacy/pull/26) (app safety net and contract test), both 2026-06-05, and pinned by the shared parser corpus.

## Consequences

The general rule this taught: an invariant that matters is enforced by the engine, never left to a prompt.
