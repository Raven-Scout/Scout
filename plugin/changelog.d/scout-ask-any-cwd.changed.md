- The `scout-ask` skill now answers project, person and decision questions from any repository on a machine
  with a configured Scout vault, not only from inside the vault. It finds the vault and the engine with
  `scoutctl kb vault --json`, choosing the engine by absolute path only (the plugin's own, else
  `~/.local/bin/scoutctl`, else the engine pointer's), and never runs a bare `scoutctl` or anything inside the
  vault. Every read passes `--repo <vault>`. It runs only read commands, which write nothing but Scout's own
  cache and read log. Outside the vault, the current repository counts as a shared place: KB text, `[private]`
  results, people notes and client details stay out of its commits, PRs, issues, code, files, CLAUDE.md and
  auto-memory, subagent prompts and cloud or remote sessions, and `[private]` results are skipped unless the
  question is about the user's own life. With no usable vault, it says why and stops.
