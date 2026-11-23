- **The shell stage of the tool hooks moved out of `hooks/hooks.json` into `hooks/prefilter.sh`**, with no
  change in which calls reach Python. Each hook's pre-filter (the part that decides, in the shell, whether
  `scoutctl hook <name>` starts at all) was a one-line command inside the JSON; the `kb-read-gate` one for
  `Grep|Glob|Bash` was 4,089 characters. They are now named filters in one POSIX script, and each
  `hooks.json` entry is a short call: `/bin/sh "${CLAUDE_PLUGIN_ROOT}/hooks/prefilter.sh" <filter>
  "${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl" hook <name>`.
  - The Bash pattern is built from one commented alternation group per concern (run identity, KB globs,
    recursive reads, detaching, hook-skip forms, writers, protected paths, secrets, the delegate inbox).
    The old pattern held one 1,117-character block twice; the copies were identical runs of whole
    alternatives, so one was dropped.
  - The PATH filter and the vault walk, repeated in every entry before, are written once.
  - A pre-filter that cannot do its job never means "allow" in a run. If `prefilter.sh` is missing, the
    entry starts the Python hook directly. If it is broken (does not parse, is killed, exits with anything
    but 0 or 2), a gate entry (`kb-read-gate`, `kb-write-gate`, `outbound-gate`) blocks the run's call
    (exit 2). An owner session is never blocked by either.
  - The run-only entries test for a run before anything else, so an owner session's call now leaves
    without starting any program (before, it filtered PATH first).
  - The filters now always run under `/bin/sh`, whatever shell Claude Code starts hooks with.
  - `engine/tests/unit/test_hook_prefilter_differential.py` runs the old commands (kept as
    `engine/tests/fixtures/hooks/hooks-inline-574f7dc2.json`) and the new ones over every string the gate
    tests use plus the forms the `description` names, under each reader, in and out of a vault, with
    Python succeeding and failing, and requires the same exit code, output and Python start for each.
