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
  - A pre-filter that cannot do its job never means "allow" in a run. If `prefilter.sh` is missing or
    empty, the entry starts the Python hook directly. If it is broken (does not parse, is cut off, is
    killed, exits with anything but 0 or 2), a gate entry (`kb-read-gate`, `kb-write-gate`,
    `outbound-gate`) blocks the run's call (exit 2). The whole script is one `{ … }` group, so a copy
    cut off anywhere fails to parse. An owner session is never blocked by any of this.
  - **Fixed: a run whose PATH lacked `cat`, `grep` or `tr` let every Bash call through unjudged**, and a
    `grep` error counted as "no match". The filters now take their tools from `/usr/bin` or `/bin` first,
    and any tool status other than 0 or 1 sends the call to Python (or, when the payload itself can't be
    read, blocks a run's call).
  - A hook started with its stdin closed is blocked at once in a run, instead of `cat` waiting forever
    on the pipe that took fd 0.
  - CI's `plugin-lint` now runs shellcheck on `hooks/prefilter.sh`.
  - The run-only entries test for a run before anything else, so an owner session's call now leaves
    without starting any program (before, it filtered PATH first).
  - The filters now always run under `/bin/sh`, whatever shell Claude Code starts hooks with.
  - `engine/tests/unit/test_hook_prefilter_differential.py` runs the old commands (kept as
    `engine/tests/fixtures/hooks/hooks-inline-574f7dc2.json`) and the new ones over every string the gate
    tests use plus the forms the `description` names, under each reader, in and out of a vault, with
    Python succeeding and failing, and requires the same exit code, output and Python start for each.
