- **What the macOS app's inbox review reads (#550 V4b3).**
  - **`delegate status --json` reports each agent's inbox.** A new optional `inbox` per delegate holds when it was
    linked and last pulled, a failed pull's code with a fixed sentence (`INBOX_REASONS`), and counts: what waits
    for review, what is taken but not settled, and files no pull took. `can_review` says the review may run. A
    new top-level `inbox_state` (`ok`, `none`, `unverified`, `unsafe`, `unreadable`) covers the signed inbox state
    as a whole; only `ok` shows any inbox. The fields are additive, so `schema_version` stays 1.
  - **`inbox review promote <ULID> --preview --json` writes nothing.** It returns everything promote publishes,
    with hidden characters shown and look-alikes checked: the note's text, and the agent's `about` and `source`
    (its title and `agent_source`). It also returns the note's `sha256` and its `audience`: the delegates who would
    see it, found by classifying the note as promote would write it. `--to` narrows it.
  - **`promote --previewed <sha>` checks that hash.** Promote runs only while the note is still exactly the
    previewed bytes, as well as the accepted ones. Without `--previewed` it previews first, as before.
  - **`unpromote <ULID> --preview --json`** returns the text and who sees it now, and `unpromote --previewed <sha>`
    checks the hash the same way. Unpromote still needs no `--confirm`, because it only narrows who sees the note.
  - **`inbox review --accepted --json`** lists the accepted observations, with ids and times only: whether each
    is promoted, and to whom.
  - **A new contract fixture**, `tests/fixtures/contract/inbox-review.json`, is generated from a builder vault.
    The app keeps a byte copy, and `contract.yml` compares the two.
- **`author` is reserved, never a delegate id**, since `promote --to author` means the note's author. A
  `privacy.delegates` entry named `author` is skipped with the usual bad-entry warning.
- **The inbox's `review` count matches the review list**: it now includes an op over the day's limit while it can
  still apply.
- **A live run's `busy` refusal names the step it refused** (accept, promote, unpromote or the inbox sync), not
  always "inbox apply and revert".
