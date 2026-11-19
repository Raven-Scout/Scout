- The `scout-ask` skill now answers project, person and decision questions from any repository on a machine
  with a configured Scout vault, not only from inside the vault. It finds the vault with `scoutctl kb vault`,
  calls the vault's `scripts/scoutctl kb` with `--repo <vault>` on every read, and only reads. Outside the
  vault, the current repository counts as a shared place: KB text, `[private]` results, people notes and
  client details stay out of its commits, PRs, issues, code and files, and `[private]` results are skipped
  unless the question is personal. With no vault configured, it says so and stops.
