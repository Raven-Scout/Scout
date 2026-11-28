- **Track S, a KB eval of set-valued tasks where a graph should beat files** (`engine/tests/eval/harness/track_s.py`,
  `python -m tests.eval.harness.cli track-s-build` and `track-s-run`). Gold comes from the vault itself, with no
  hand labelling: hindsight meeting prep (the notes a meeting's Synthesis later linked, asked of the vault as it
  stood before that day), link recovery (body wikilinks and typed relationships masked in a copy of the vault
  and replayed on every arm's worktree), and blast-radius, shared-stakeholder and touched-both questions whose
  answers are enumerated from the typed relationships the run's reader can see. Each sample is scored on gold
  recall, recall beyond the notes prep reads anyway, precision and privacy leaks, and the report applies the
  plan's decision rule per family (+15 points recall at no more than 1.1× tokens, with no leaks) against arms A
  and R. Items, masks and results are written owner-only and refused inside this repo.
  - Prep notes are found in `meetings/<slug>/`, `knowledge-base/meetings/<slug>/` and the older
    `action-items/meeting-prep/` (`--prep-glob` for others); a prep note with no post-meeting section uses its
    own links as a weaker gold, reported apart (`--hindsight-only` drops them). People questions leave out
    people on most projects (`--hub-share`, `--exclude-person`) and say so; masks skip links the reader can't
    follow, issue keys and placeholders; and link precision leaves out listed links the note names but
    doesn't link, since those may be real missing links.
- **Three more eval arms**: R (`files-ranked`, files plus ranked search only), BF (`relate-verbs-forced`, the
  graph verbs with an instruction to start from them) and O (`files-oracle`, the gold notes named up front, a
  retrieval ceiling). Arm B's overlay now names the graph verb for each step (meeting prep, decision
  propagation, missing links, conflicting claims, dependencies), Arm C's points at `graph_context` in the
  session context, and Track R and Track S rows record per-verb counts and whether `graph_context` was read.
